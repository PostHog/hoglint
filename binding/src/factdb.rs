//! The fact database: one SQLite file per facts id, shared by every worktree and every run on
//! the machine.
//!
//! - `keys`: (extract config id, blob oid) to the entry id, the day a run last read it
//!   (`used`), the length of its JSON text and the bytes it holds.
//! - `facts`: the binary facts the link reads (`hoglint_facts::link::store`).
//! - `records`: the record JSON text that packs read, compressed in frames (`pack`): a pack
//!   that reads one key decompresses that key alone. Apart from the facts, so a run that never
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
pub const LAYOUT: i64 = 4;
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

/// An entry to store.
pub struct NewEntry<'a> {
    pub config: &'a str,
    pub oid: &'a str,
    /// The binary facts.
    pub body: Vec<u8>,
    /// The record's JSON text, compressed (`pack`).
    pub record: Vec<u8>,
    /// The length of the JSON text.
    pub json_len: usize,
}

/// How many times lz4 can expand a block at most, with room for its last literals: a stored
/// length past that is damage, and allocating it could abort the run.
const MAX_EXPANSION: usize = 255;
const EXPANSION_SLACK: usize = 64;

/// The most text `packed` compressed bytes can hold.
fn expands_to(packed: usize) -> usize {
    packed
        .saturating_mul(MAX_EXPANSION)
        .saturating_add(EXPANSION_SLACK)
}

/// A record's JSON text, compressed for the `records` table in frames: one from the start of
/// each key's value (`values`, ranges of the text) to the start of the next, and one for the
/// text before the first, so the frames hold the text whole and a key's value sits in one.
///
/// Layout: the length of the index (u32), the index (per frame in text order, the length of its
/// text and of its compressed bytes, each a LEB128 number), then the compressed frames.
pub fn pack(json: &[u8], values: &[Range<usize>]) -> Vec<u8> {
    let mut cuts = vec![0, json.len()];
    cuts.extend(values.iter().map(|range| range.start.min(json.len())));
    cuts.sort_unstable();
    cuts.dedup();
    let mut index = Vec::new();
    let mut frames = Vec::new();
    for cut in cuts.windows(2) {
        let packed = lz4_flex::block::compress(&json[cut[0]..cut[1]]);
        put_varint(&mut index, cut[1] - cut[0]);
        put_varint(&mut index, packed.len());
        frames.extend_from_slice(&packed);
    }
    let mut out = Vec::with_capacity(4 + index.len() + frames.len());
    out.extend_from_slice(&(index.len() as u32).to_le_bytes());
    out.extend_from_slice(&index);
    out.extend_from_slice(&frames);
    out
}

fn put_varint(out: &mut Vec<u8>, mut value: usize) {
    while value >= 0x80 {
        out.push((value as u8 & 0x7f) | 0x80);
        value >>= 7;
    }
    out.push(value as u8);
}

/// The LEB128 number at `bytes[*at..]`, moving `at` past it; None when it runs off the end.
fn varint(bytes: &[u8], at: &mut usize) -> Option<usize> {
    let mut out = 0usize;
    for shift in (0..64).step_by(7) {
        let byte = *bytes.get(*at)?;
        *at += 1;
        out |= usize::from(byte & 0x7f) << shift;
        if byte < 0x80 {
            return Some(out);
        }
    }
    None
}

/// One compressed frame of a record: where its text starts and how long it is, and where the
/// frame sits in the stored blob and how long it is.
struct Frame {
    text: Range<usize>,
    packed: Range<usize>,
}

fn u32_at(bytes: &[u8], at: usize) -> usize {
    u32::from_le_bytes([bytes[at], bytes[at + 1], bytes[at + 2], bytes[at + 3]]) as usize
}

/// The frames of a record's `index`, in a blob of `blob_len` bytes; None when they do not
/// cover `json_len` bytes of text, or run past the blob.
fn frames(index: &[u8], json_len: usize, blob_len: usize) -> Option<Vec<Frame>> {
    let mut out = Vec::new();
    let mut read = 0;
    let mut text: usize = 0;
    let mut at = 4 + index.len();
    while read < index.len() {
        let len = varint(index, &mut read)?;
        let packed = varint(index, &mut read)?;
        let (Some(text_end), Some(packed_end)) = (text.checked_add(len), at.checked_add(packed))
        else {
            return None;
        };
        if packed_end > blob_len || text_end > json_len {
            return None;
        }
        out.push(Frame {
            text: text..text_end,
            packed: at..packed_end,
        });
        text = text_end;
        at = packed_end;
    }
    (text == json_len).then_some(out)
}

/// The text of `frames`, back to back, from `bytes`, which hold the blob from offset `base`
/// on; None when a frame does not decompress to its length.
fn unpack(bytes: &[u8], base: usize, frames: &[Frame]) -> Option<Vec<u8>> {
    let mut out = Vec::new();
    for frame in frames {
        let packed = bytes.get(frame.packed.start - base..frame.packed.end - base)?;
        if frame.text.len() > expands_to(packed.len()) {
            return None;
        }
        let text = lz4_flex::block::decompress(packed, frame.text.len()).ok()?;
        if text.len() != frame.text.len() {
            return None;
        }
        out.extend_from_slice(&text);
    }
    Some(out)
}

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

    /// The entry of (config, oid); None when there is none, or when its stored lengths cannot be
    /// those of a record: a damaged row is a miss.
    pub fn find(&self, config: &str, oid: &str) -> Result<Option<Row>> {
        let mut stmt = self.conn.prepare_cached(
            "SELECT used, json_len, bytes FROM keys WHERE config = ?1 AND oid = ?2",
        )?;
        let found = stmt
            .query_row(params![config, oid], |r| {
                Ok((
                    r.get::<_, i64>(0)?,
                    r.get::<_, i64>(1)?,
                    r.get::<_, i64>(2)?,
                ))
            })
            .optional()?;
        let Some((used, json_len, bytes)) = found else {
            return Ok(None);
        };
        let (Ok(json_len), Ok(bytes)) = (usize::try_from(json_len), usize::try_from(bytes)) else {
            return Ok(None);
        };
        if json_len > expands_to(bytes) || json_len > u32::MAX as usize {
            return Ok(None);
        }
        Ok(Some(Row { used, json_len }))
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

    /// The record JSON of an entry; None when the entry is gone, or when its frames do not
    /// decompress to `json_len` bytes: a miss, never a wrong answer.
    pub fn read_json(&self, config: &str, oid: &str, json_len: usize) -> Result<Option<Vec<u8>>> {
        let Some(blob) = self.read(
            "SELECT r.json FROM records r JOIN keys k ON k.id = r.id WHERE k.config = ?1 AND k.oid = ?2",
            config,
            oid,
        )?
        else {
            return Ok(None);
        };
        if blob.len() < 4 {
            return Ok(None);
        }
        let Some(index) = blob.get(4..4 + u32_at(&blob, 0)) else {
            return Ok(None);
        };
        let Some(all) = frames(index, json_len, blob.len()) else {
            return Ok(None);
        };
        Ok(unpack(&blob, 0, &all))
    }

    /// Bytes `range` of an entry's record JSON, read in one read transaction and decompressed
    /// from the frames that hold them alone; None when the entry is gone or its frames do not
    /// give `json_len` bytes.
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
        if blob.len() < 4 || range.end > json_len {
            return Ok(None);
        }
        let mut length = [0u8; 4];
        blob.read_at_exact(&mut length, 0)?;
        let length = u32::from_le_bytes(length) as usize;
        if 4 + length > blob.len() {
            return Ok(None);
        }
        let mut index = vec![0; length];
        blob.read_at_exact(&mut index, 4)?;
        let Some(all) = frames(&index, json_len, blob.len()) else {
            return Ok(None);
        };
        let wanted: Vec<Frame> = all
            .into_iter()
            .filter(|frame| frame.text.start < range.end && frame.text.end > range.start)
            .collect();
        let (Some(first), Some(last)) = (wanted.first(), wanted.last()) else {
            return Ok(Some(Vec::new()));
        };
        let base = first.packed.start;
        let mut bytes = vec![0; last.packed.end - base];
        blob.read_at_exact(&mut bytes, base)?;
        let offset = first.text.start;
        Ok(unpack(&bytes, base, &wanted).and_then(|text| {
            text.get(range.start - offset..range.end - offset)
                .map(<[u8]>::to_vec)
        }))
    }

    /// Stores new entries; a key already stored, or met before in `entries`, keeps its entry.
    ///
    /// A chunk's bodies go in before its records. A run reads every body and few records, so
    /// the bodies lie in long runs of pages: a run whose pages left the page cache reads them
    /// back in a few large reads, and does not read the records between them.
    pub fn store(&self, entries: &[NewEntry]) -> Result<()> {
        for chunk in entries.chunks(ROWS_PER_WRITE) {
            self.write(|conn| {
                let mut exists = conn.prepare_cached("SELECT 1 FROM keys WHERE config = ?1 AND oid = ?2")?;
                let mut facts = conn.prepare_cached("INSERT INTO facts (body) VALUES (?1)")?;
                let mut records = conn.prepare_cached("INSERT INTO records (id, json) VALUES (?1, ?2)")?;
                let mut keys = conn.prepare_cached(
                    "INSERT INTO keys (config, oid, id, used, json_len, bytes) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                )?;
                let mut new: Vec<&NewEntry> = Vec::new();
                for entry in chunk {
                    let repeated = new
                        .iter()
                        .any(|other| other.config == entry.config && other.oid == entry.oid);
                    if repeated || exists.exists(params![entry.config, entry.oid])? {
                        continue;
                    }
                    new.push(entry);
                }
                let mut ids = Vec::with_capacity(new.len());
                for entry in &new {
                    ids.push(facts.insert([&entry.body])?);
                }
                for (entry, id) in new.iter().zip(&ids) {
                    records.execute(params![id, entry.record])?;
                }
                for (entry, id) in new.iter().zip(&ids) {
                    let bytes = (entry.body.len() + entry.record.len()) as i64;
                    let json_len = entry.json_len as i64;
                    keys.execute(params![entry.config, entry.oid, id, self.today, json_len, bytes])?;
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
