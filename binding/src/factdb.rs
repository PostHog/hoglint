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
//! at once wait on each other's page faults. The daily prune lives in `hoglint/native/factdb.py`.

use rusqlite::{Connection, OptionalExtension, params};
use std::path::Path;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

/// The table layout. A database with another `user_version` is emptied and set up again.
const LAYOUT: i64 = 2;
/// Entries per write transaction.
const ROWS_PER_WRITE: usize = 256;
const BUSY_TIMEOUT: Duration = Duration::from_secs(10);

fn fail(e: rusqlite::Error) -> String {
    format!("fact database: {e}")
}

/// Days since the Unix epoch, in UTC.
fn today() -> i64 {
    let seconds = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    (seconds / 86_400) as i64
}

/// A stored entry: its id, the day a run last read it, and the length of its JSON text.
#[derive(Clone, Copy)]
pub struct Row {
    pub id: i64,
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
    pub fn open(path: &Path) -> Result<FactDb, String> {
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir).map_err(|e| format!("create {}: {e}", dir.display()))?;
        }
        let conn = Connection::open(path).map_err(|e| format!("open {}: {e}", path.display()))?;
        conn.busy_timeout(BUSY_TIMEOUT).map_err(fail)?;
        conn.execute_batch(
            "PRAGMA auto_vacuum = INCREMENTAL;
             PRAGMA journal_mode = WAL;
             PRAGMA synchronous = NORMAL;",
        )
        .map_err(fail)?;
        let db = FactDb {
            conn,
            today: today(),
        };
        db.set_up()?;
        Ok(db)
    }

    fn layout(&self) -> Result<i64, String> {
        self.conn
            .query_row("PRAGMA user_version", [], |r| r.get(0))
            .map_err(fail)
    }

    /// Creates the tables when the layout differs. The check repeats under the write lock, so
    /// runs that open a new database at once set it up once.
    fn set_up(&self) -> Result<(), String> {
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
    pub fn begin_read(&self) -> Result<(), String> {
        self.conn.execute_batch("BEGIN").map_err(fail)
    }

    pub fn end_read(&self) -> Result<(), String> {
        self.conn.execute_batch("COMMIT").map_err(fail)
    }

    pub fn find(&self, config: &str, oid: &str) -> Result<Option<Row>, String> {
        let mut stmt = self
            .conn
            .prepare_cached("SELECT id, used, json_len FROM keys WHERE config = ?1 AND oid = ?2")
            .map_err(fail)?;
        stmt.query_row(params![config, oid], |r| {
            Ok(Row {
                id: r.get(0)?,
                used: r.get(1)?,
                json_len: r.get::<_, i64>(2)? as usize,
            })
        })
        .optional()
        .map_err(fail)
    }

    /// Calls `f` with the blob `sql` selects for entry `id`.
    fn read<R>(&self, sql: &str, id: i64, f: impl FnOnce(&[u8]) -> R) -> Result<R, String> {
        let mut stmt = self.conn.prepare_cached(sql).map_err(fail)?;
        let mut rows = stmt.query([id]).map_err(fail)?;
        let found = rows
            .next()
            .map_err(fail)?
            .ok_or("fact database: an entry went away during the run; run again")?;
        let blob = found
            .get_ref(0)
            .map_err(fail)?
            .as_blob()
            .map_err(|e| fail(e.into()))?;
        Ok(f(blob))
    }

    pub fn read_facts<R>(&self, row: Row, f: impl FnOnce(&[u8]) -> R) -> Result<R, String> {
        self.read("SELECT body FROM facts WHERE id = ?1", row.id, f)
    }

    pub fn read_json<R>(&self, row: Row, f: impl FnOnce(&[u8]) -> R) -> Result<R, String> {
        self.read("SELECT json FROM records WHERE id = ?1", row.id, f)
    }

    /// Stores new entries; a key already stored keeps its entry.
    pub fn store(&self, entries: &[NewEntry]) -> Result<(), String> {
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
    pub fn touch(&self, keys: &[(&str, &str)]) -> Result<(), String> {
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

    fn write(&self, f: impl FnOnce(&Connection) -> rusqlite::Result<()>) -> Result<(), String> {
        self.conn.execute_batch("BEGIN IMMEDIATE").map_err(fail)?;
        if let Err(e) = f(&self.conn) {
            let _ = self.conn.execute_batch("ROLLBACK");
            return Err(fail(e));
        }
        self.conn.execute_batch("COMMIT").map_err(fail)
    }
}
