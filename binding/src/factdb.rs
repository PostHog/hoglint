//! The fact database: one SQLite file per facts id, shared by every worktree and every run on
//! the machine.
//!
//! - `keys`: (extract config id, blob oid) to the entry id, the day a run last read it
//!   (`used`), the length of its JSON text and the bytes it holds.
//! - `facts`: the binary facts the link reads (`hoglint_facts::link::store`).
//! - `records`: the record JSON text that packs read, apart from the facts so a run that never
//!   reads it never touches its pages.
//!
//! WAL mode lets runs read while another run writes. Writes skip keys already stored and run in
//! short transactions, so runs that extract the same blob at once store it once and hold the
//! write lock briefly. Reads go through `pread`, not a memory map: ten runs that map one file
//! at once wait on each other's page faults. Every read is by natural key (config, oid): an
//! entry id can be reused after a prune. The daily prune lives in `hoglint/native/factdb.py`.
//!
//! The database is a cache. An entry that is gone is a miss, and an error is the caller's to
//! log and run past (`blobs.rs`).

use rusqlite::{Connection, ErrorCode, MAIN_DB, OptionalExtension, params};
use std::ops::Range;
use std::path::Path;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

/// The table layout. It is part of the database file name (`hoglint/native/factdb.py`), so two
/// builds with different layouts never share a file; a file with another `user_version` is
/// emptied and set up again.
pub const LAYOUT: i64 = 2;
/// Entries per write transaction.
const ROWS_PER_WRITE: usize = 256;
const BUSY_TIMEOUT: Duration = Duration::from_secs(10);

/// A fact database error; `corrupt` when the file is no database or a damaged one.
#[derive(Debug)]
pub struct DbError {
    pub message: String,
    pub corrupt: bool,
}

impl From<rusqlite::Error> for DbError {
    fn from(e: rusqlite::Error) -> DbError {
        let corrupt = matches!(
            e.sqlite_error_code(),
            Some(ErrorCode::DatabaseCorrupt | ErrorCode::NotADatabase)
        );
        DbError {
            message: format!("fact database: {e}"),
            corrupt,
        }
    }
}

impl From<std::io::Error> for DbError {
    fn from(e: std::io::Error) -> DbError {
        DbError {
            message: format!("fact database: {e}"),
            corrupt: false,
        }
    }
}

type Result<T> = std::result::Result<T, DbError>;

/// Days since the Unix epoch, in UTC.
fn today() -> i64 {
    let seconds = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    (seconds / 86_400) as i64
}

/// A stored entry: the day a run last read it, and the length of its JSON text.
#[derive(Clone, Copy)]
pub struct Row {
    pub used: i64,
    pub json_len: usize,
}

/// An entry to store: (config id, oid, binary facts, JSON text).
pub type NewEntry<'a> = (&'a str, &'a str, Vec<u8>, &'a [u8]);

pub struct FactDb {
    conn: Connection,
    pub today: i64,
}

impl FactDb {
    pub fn open(path: &Path) -> Result<FactDb> {
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        let conn = Connection::open(path)?;
        conn.busy_timeout(BUSY_TIMEOUT)?;
        conn.execute_batch(
            "PRAGMA auto_vacuum = INCREMENTAL;
             PRAGMA journal_mode = WAL;
             PRAGMA synchronous = NORMAL;",
        )?;
        let db = FactDb {
            conn,
            today: today(),
        };
        db.set_up()?;
        Ok(db)
    }

    fn layout(&self) -> Result<i64> {
        Ok(self
            .conn
            .query_row("PRAGMA user_version", [], |r| r.get(0))?)
    }

    /// Creates the tables when the layout differs. The check repeats under the write lock, so
    /// runs that open a new database at once set it up once.
    fn set_up(&self) -> Result<()> {
        if self.layout()? == LAYOUT {
            return Ok(());
        }
        self.write(|conn| {
            if conn.query_row("PRAGMA user_version", [], |r| r.get::<_, i64>(0))? == LAYOUT {
                return Ok(());
            }
            conn.execute_batch(&format!(
                "DROP TABLE IF EXISTS keys;
                 DROP TABLE IF EXISTS facts;
                 DROP TABLE IF EXISTS records;
                 CREATE TABLE keys (
                     config TEXT NOT NULL,
                     oid TEXT NOT NULL,
                     id INTEGER NOT NULL,
                     used INTEGER NOT NULL,
                     json_len INTEGER NOT NULL,
                     bytes INTEGER NOT NULL,
                     PRIMARY KEY (config, oid)
                 ) WITHOUT ROWID;
                 CREATE TABLE facts (id INTEGER PRIMARY KEY, body BLOB NOT NULL);
                 CREATE TABLE records (id INTEGER PRIMARY KEY, json BLOB NOT NULL);
                 PRAGMA user_version = {LAYOUT};"
            ))
        })
    }

    /// Starts a read transaction: every read until `end_read` sees one state.
    pub fn begin_read(&self) -> Result<()> {
        Ok(self.conn.execute_batch("BEGIN")?)
    }

    pub fn end_read(&self) -> Result<()> {
        Ok(self.conn.execute_batch("COMMIT")?)
    }

    pub fn find(&self, config: &str, oid: &str) -> Result<Option<Row>> {
        let mut stmt = self
            .conn
            .prepare_cached("SELECT used, json_len FROM keys WHERE config = ?1 AND oid = ?2")?;
        Ok(stmt
            .query_row(params![config, oid], |r| {
                Ok(Row {
                    used: r.get(0)?,
                    json_len: r.get::<_, i64>(1)? as usize,
                })
            })
            .optional()?)
    }

    /// The blob `sql` selects for the entry of (config, oid), or None when the entry is gone.
    fn read(&self, sql: &str, config: &str, oid: &str) -> Result<Option<Vec<u8>>> {
        let mut stmt = self.conn.prepare_cached(sql)?;
        Ok(stmt
            .query_row(params![config, oid], |r| r.get::<_, Vec<u8>>(0))
            .optional()?)
    }

    /// The binary facts of an entry, or None when it is gone.
    pub fn read_facts(&self, config: &str, oid: &str) -> Result<Option<Vec<u8>>> {
        self.read(
            "SELECT f.body FROM facts f JOIN keys k ON k.id = f.id WHERE k.config = ?1 AND k.oid = ?2",
            config,
            oid,
        )
    }

    /// Bytes `range` of an entry's record JSON, read in place in one read transaction; None when
    /// the entry is gone or its record is not `json_len` bytes long.
    pub fn read_json_range(
        &self,
        config: &str,
        oid: &str,
        json_len: usize,
        range: Range<usize>,
    ) -> Result<Option<Vec<u8>>> {
        self.begin_read()?;
        let found = self.json_range(config, oid, json_len, range);
        self.end_read()?;
        found
    }

    fn json_range(
        &self,
        config: &str,
        oid: &str,
        json_len: usize,
        range: Range<usize>,
    ) -> Result<Option<Vec<u8>>> {
        let mut stmt = self
            .conn
            .prepare_cached("SELECT id FROM keys WHERE config = ?1 AND oid = ?2")?;
        let Some(id) = stmt
            .query_row(params![config, oid], |r| r.get::<_, i64>(0))
            .optional()?
        else {
            return Ok(None);
        };
        let blob = self
            .conn
            .blob_open(MAIN_DB, c"records", c"json", id, true)?;
        if blob.len() != json_len || range.end > json_len {
            return Ok(None);
        }
        let mut out = vec![0; range.len()];
        blob.read_at_exact(&mut out, range.start)?;
        Ok(Some(out))
    }

    /// The record JSON of an entry, or None when it is gone.
    pub fn read_json(&self, config: &str, oid: &str) -> Result<Option<Vec<u8>>> {
        self.read(
            "SELECT r.json FROM records r JOIN keys k ON k.id = r.id WHERE k.config = ?1 AND k.oid = ?2",
            config,
            oid,
        )
    }

    /// Stores new entries; a key already stored keeps its entry.
    pub fn store(&self, entries: &[NewEntry]) -> Result<()> {
        for chunk in entries.chunks(ROWS_PER_WRITE) {
            self.write(|conn| {
                let mut exists = conn.prepare_cached("SELECT 1 FROM keys WHERE config = ?1 AND oid = ?2")?;
                let mut facts = conn.prepare_cached("INSERT INTO facts (body) VALUES (?1)")?;
                let mut records = conn.prepare_cached("INSERT INTO records (id, json) VALUES (?1, ?2)")?;
                let mut keys = conn.prepare_cached(
                    "INSERT INTO keys (config, oid, id, used, json_len, bytes) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                )?;
                for (config, oid, body, json) in chunk {
                    if exists.exists(params![config, oid])? {
                        continue;
                    }
                    let id = facts.insert([body])?;
                    records.execute(params![id, json])?;
                    let bytes = (body.len() + json.len()) as i64;
                    keys.execute(params![config, oid, id, self.today, json.len() as i64, bytes])?;
                }
                Ok(())
            })?;
        }
        Ok(())
    }

    /// Records that this run read the entries of `keys` ((config id, oid)) today.
    pub fn touch(&self, keys: &[(&str, &str)]) -> Result<()> {
        for chunk in keys.chunks(ROWS_PER_WRITE) {
            self.write(|conn| {
                let mut stmt = conn.prepare_cached(
                    "UPDATE keys SET used = ?1 WHERE config = ?2 AND oid = ?3 AND used < ?1",
                )?;
                for (config, oid) in chunk {
                    stmt.execute(params![self.today, config, oid])?;
                }
                Ok(())
            })?;
        }
        Ok(())
    }

    fn write(&self, f: impl FnOnce(&Connection) -> rusqlite::Result<()>) -> Result<()> {
        self.conn.execute_batch("BEGIN IMMEDIATE")?;
        if let Err(e) = f(&self.conn) {
            let _ = self.conn.execute_batch("ROLLBACK");
            return Err(e.into());
        }
        Ok(self.conn.execute_batch("COMMIT")?)
    }
}

/// Moves a damaged database aside with its `-wal` and `-shm` files, so the next open starts an
/// empty one. A stale `-wal` left beside a new file would be replayed into it.
pub fn move_aside(path: &Path) {
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    for suffix in ["", "-wal", "-shm"] {
        let from = format!("{}{suffix}", path.display());
        let to = format!("{}.corrupt-{stamp}{suffix}", path.display());
        let _ = std::fs::rename(from, to);
    }
}
