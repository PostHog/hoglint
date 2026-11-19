//! The facts of a snapshot given as blob ids: the shell around `hoglint_facts::extract` and the
//! fact database.
//!
//! An entry is stored under (extract config id, blob oid) in the database of the facts id
//! (`factdb`): the link's facts in binary (`link::store`), so a hit parses no JSON, and the
//! record's JSON text, compressed, which packs read and which is read only when they do. A miss
//! reads the blob with one `git cat-file --batch` for the whole snapshot (or the working tree
//! file, for a file git has no blob of yet), extracts it, and stores the entry. Site-packages files are not
//! in git; they are read and keyed by content hash.
//!
//! The database is a cache, never an input:
//! - an entry is stored only under the id of the bytes it was extracted from. A working tree
//!   file whose bytes no longer hash to the snapshot's oid changed during the run: the run
//!   uses what it read, stores nothing for it, and reports it as drifted;
//! - an entry that went away is a miss, and the file is extracted again;
//! - an error opening or reading the database, or a write that finds it damaged, is logged once
//!   and the run goes on without it; a damaged database is moved aside;
//! - no read transaction is held while files are extracted.

use crate::factdb::{DbError, FactDb, NewEntry, Row, move_aside, pack};
use hoglint_facts::link::facts::{FactSet, FileFacts, RecordText, decode_record};
use hoglint_facts::link::intern::{Interner, Sid};
use hoglint_facts::link::store::{MAGIC, decode_body, encode_facts, facts_strings};
use hoglint_facts::{
    ExtractConfig, Facts, FileRecord, Kinds, Status, extract, record_line, sha256_hex,
};
use rayon::prelude::*;
use serde_json::value::RawValue;
use sha1::{Digest, Sha1};
use sha2::Sha256;
use std::collections::BTreeMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::ops::Range;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, Ordering};

/// One source file of a snapshot: its path, blob oid, and whether its bytes come from the
/// working tree (dirty or untracked) rather than from git's object store.
pub struct Blob {
    pub path: String,
    pub oid: String,
    pub from_disk: bool,
}

pub struct BlobRequest {
    pub repo: PathBuf,
    pub blobs: Vec<Blob>,
    /// (path as the records name it, file to read): third-party sources.
    pub external: Vec<(String, PathBuf)>,
    pub full: ExtractConfig,
    pub lite: ExtractConfig,
    /// The fact database file; None runs without a cache.
    pub db: Option<PathBuf>,
}

/// Counts of what one run read, and seconds per loading stage.
#[derive(Default, Debug)]
pub struct BlobStats {
    pub hits: usize,
    pub extracted: usize,
    pub stages: Vec<(&'static str, f64)>,
    /// The cache key of each record in fact order: blob oid, or content hash for a third-party
    /// file; None for a file that could not be read or that changed during the run.
    pub keys: Vec<Option<String>>,
    /// Working tree files whose bytes did not hash to the snapshot's oid when they were read.
    pub drifted: Vec<String>,
}

/// What the extractor watches (`ExtractConfig`): callees by last name and by prefix, dict keys,
/// hook names, and full names watched through any import alias.
pub struct Watch {
    pub callees: Vec<String>,
    pub prefixes: Vec<String>,
    pub keys: Vec<String>,
    pub hooks: Vec<String>,
    pub aliased: Vec<String>,
}

pub fn configs(
    first_party: Vec<String>,
    kinds: &[String],
    watch: Watch,
    lite_kinds: &[String],
) -> Result<(ExtractConfig, ExtractConfig), String> {
    let base = ExtractConfig::new(first_party)
        .watching(watch.callees, watch.prefixes, watch.keys)
        .watching_hooks(watch.hooks)
        .watching_aliased(watch.aliased);
    let full = base.clone().with_kinds(Kinds::parse(kinds)?);
    let lite = base.with_kinds(Kinds::parse(lite_kinds)?);
    Ok((full, lite))
}

/// The git blob id of `bytes`, as `git hash-object --no-filters` prints it: SHA-1, or SHA-256
/// when `like` (an oid of the repo) has the length of one.
fn git_blob_id(bytes: &[u8], like: &str) -> String {
    let header = format!("blob {}\0", bytes.len());
    let digest: Vec<u8> = if like.len() == 64 {
        Sha256::new()
            .chain_update(header.as_bytes())
            .chain_update(bytes)
            .finalize()
            .to_vec()
    } else {
        Sha1::new()
            .chain_update(header.as_bytes())
            .chain_update(bytes)
            .finalize()
            .to_vec()
    };
    digest.iter().map(|b| format!("{b:02x}")).collect()
}

static WARNED: AtomicBool = AtomicBool::new(false);
static REPAIRED: AtomicBool = AtomicBool::new(false);

/// Logs a fact cache error, once per process: the run goes on without the cache.
fn warn(error: &DbError) {
    if !WARNED.swap(true, Ordering::Relaxed) {
        eprintln!(
            "hoglint: the fact cache is not usable, so this run extracts without it: {}",
            error.message
        );
    }
}

/// The fact database at `path`, or None when it cannot be used (logged; a damaged one is moved
/// aside).
fn open_cache(path: &Path) -> Option<FactDb> {
    match FactDb::open(path) {
        Ok(db) => Some(db),
        Err(error) => {
            give_up(path, &error);
            None
        }
    }
}

fn give_up(path: &Path, error: &DbError) {
    warn(error);
    if error.corrupt {
        move_aside(path);
    }
}

/// An extract config and its id, which keys its entries.
struct Profile<'c> {
    config: &'c ExtractConfig,
    id: String,
}

impl<'c> Profile<'c> {
    fn new(config: &'c ExtractConfig) -> Profile<'c> {
        Profile {
            config,
            id: config.config_id(),
        }
    }

    /// The record JSON (with an empty path) of one file's bytes; None bytes is a read error.
    fn record(&self, raw: Option<&[u8]>) -> Result<String, String> {
        let record = match raw {
            Some(raw) => extract(raw, sha256_hex(raw), self.config),
            None => FileRecord {
                sha256: String::new(),
                bytes: 0,
                status: Status::ReadError,
                facts: Facts::default(),
            },
        };
        record_line(&record, &self.id, "").map_err(|e| format!("encode a record: {e}"))
    }
}

/// The contents of `oids` from git's object store, by one `git cat-file --batch`. A missing
/// object maps to None.
fn cat_blobs(repo: &Path, oids: &[&str]) -> Result<Vec<Option<Vec<u8>>>, String> {
    if oids.is_empty() {
        return Ok(Vec::new());
    }
    let mut child = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(["cat-file", "--batch"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .map_err(|e| format!("git cat-file: {e}"))?;
    let mut stdin = child.stdin.take().expect("piped stdin");
    let request: String = oids.iter().map(|oid| format!("{oid}\n")).collect();
    let writer = std::thread::spawn(move || stdin.write_all(request.as_bytes()));
    let mut reader = BufReader::new(child.stdout.take().expect("piped stdout"));
    let mut out = Vec::with_capacity(oids.len());
    for _ in oids {
        let mut header = String::new();
        reader
            .read_line(&mut header)
            .map_err(|e| format!("git cat-file: {e}"))?;
        let parts: Vec<&str> = header.split_whitespace().collect();
        if parts.len() != 3 {
            out.push(None);
            continue;
        }
        let size: usize = parts[2]
            .parse()
            .map_err(|_| format!("git cat-file header {header:?}"))?;
        let mut content = vec![0; size + 1];
        reader
            .read_exact(&mut content)
            .map_err(|e| format!("git cat-file: {e}"))?;
        content.pop();
        out.push(if parts[1] == "blob" {
            Some(content)
        } else {
            None
        });
    }
    writer
        .join()
        .map_err(|_| "git cat-file writer panicked".to_string())?
        .map_err(|e| e.to_string())?;
    child.wait().map_err(|e| format!("git cat-file: {e}"))?;
    Ok(out)
}

/// Where a file's bytes come from, so they can be read again.
#[derive(Clone)]
enum Source {
    Git(String),
    Disk(PathBuf),
    External(PathBuf),
}

/// One file of the fact set, in fact order: blobs, then third-party files.
struct Slot<'r> {
    path: &'r str,
    /// The entry key: the blob oid, or a third-party file's content hash. None for a file that
    /// could not be read or that changed during the run.
    key: Option<String>,
    lite: bool,
    source: Source,
    found: Option<Row>,
    /// Where the binary facts of a found entry sit in the load's body buffer.
    stored: Option<Range<usize>>,
    /// The record JSON of a file extracted in this run.
    fresh: Option<String>,
}

/// The slots of a request, and the bytes of its third-party files: their key is their content.
fn slots(request: &BlobRequest) -> (Vec<Slot<'_>>, Vec<Option<Vec<u8>>>) {
    let external: Vec<Option<Vec<u8>>> = request
        .external
        .par_iter()
        .map(|(_, file)| std::fs::read(file).ok())
        .collect();
    let hashes: Vec<Option<String>> = external
        .par_iter()
        .map(|raw| raw.as_deref().map(sha256_hex))
        .collect();
    let blobs = request.blobs.iter().map(|blob| Slot {
        path: &blob.path,
        key: Some(blob.oid.clone()),
        lite: false,
        source: if blob.from_disk {
            Source::Disk(request.repo.join(&blob.path))
        } else {
            Source::Git(blob.oid.clone())
        },
        found: None,
        stored: None,
        fresh: None,
    });
    let third_party = request
        .external
        .iter()
        .zip(hashes)
        .map(|((path, file), key)| Slot {
            path,
            key,
            lite: true,
            source: Source::External(file.clone()),
            found: None,
            stored: None,
            fresh: None,
        });
    (blobs.chain(third_party).collect(), external)
}

/// The entries of every slot with a key, in one read transaction.
fn find(db: &FactDb, slots: &mut [Slot], profiles: (&Profile, &Profile)) -> Result<(), DbError> {
    db.begin_read()?;
    for slot in slots.iter_mut() {
        let profile = if slot.lite { profiles.1 } else { profiles.0 };
        if let Some(key) = &slot.key {
            slot.found = db.find(&profile.id, key)?;
        }
    }
    db.end_read()
}

/// The u32 at `bytes[*at..]`, moving `at` past it; None when it runs off the end.
fn next_u32(bytes: &[u8], at: &mut usize) -> Option<usize> {
    let raw = bytes.get(*at..)?.get(..4)?;
    *at += 4;
    Some(u32::from_le_bytes([raw[0], raw[1], raw[2], raw[3]]) as usize)
}

/// The length of the JSON text that a stored body's key ranges imply: the text is compact JSON,
/// so it ends one byte after the value of its last key. Reads the layout of `link::store` with
/// no interning: MAGIC, the string table, then the body, which starts with the key ranges.
fn implied_json_len(body: &[u8]) -> Option<usize> {
    let mut at = MAGIC.len();
    for _ in 0..next_u32(body, &mut at)? {
        let len = next_u32(body, &mut at)?;
        at = at.checked_add(len)?;
    }
    let mut end = 0;
    for _ in 0..next_u32(body, &mut at)? {
        let len = next_u32(body, &mut at)?;
        at = at.checked_add(len)?;
        next_u32(body, &mut at)?;
        end = end.max(next_u32(body, &mut at)?);
    }
    Some(end + 1)
}

/// The binary facts of every found entry, in one read transaction, appended to `bodies`. An
/// entry that went away since `find`, or whose body does not imply its stored text length, is a
/// miss: the length lays out the record's text before any record is read. One buffer, not one
/// per entry: freed after the decode, it goes back whole instead of leaving a heap of small free
/// blocks.
fn read_found(
    db: &FactDb,
    slots: &mut [Slot],
    profiles: (&Profile, &Profile),
    bodies: &mut Vec<u8>,
) -> Result<(), DbError> {
    db.begin_read()?;
    for slot in slots.iter_mut() {
        let (Some(row), Some(key)) = (slot.found, &slot.key) else {
            continue;
        };
        let profile = if slot.lite { profiles.1 } else { profiles.0 };
        let body = db
            .read_facts(&profile.id, key)?
            .filter(|body| implied_json_len(body) == Some(row.json_len));
        slot.stored = body.map(|body| {
            let start = bodies.len();
            bodies.extend_from_slice(&body);
            start..bodies.len()
        });
        if slot.stored.is_none() {
            slot.found = None;
        }
    }
    db.end_read()
}

/// Extracts every slot without stored facts; returns how many, and the working tree files that
/// changed during the run.
fn extract_missing(
    request: &BlobRequest,
    profiles: (&Profile, &Profile),
    slots: &mut [Slot],
    mut external: Vec<Option<Vec<u8>>>,
) -> Result<(usize, Vec<String>), String> {
    let missing: Vec<usize> = (0..slots.len())
        .filter(|&i| slots[i].stored.is_none())
        .collect();
    let from_git: Vec<(usize, String)> = missing
        .iter()
        .filter_map(|&i| match &slots[i].source {
            Source::Git(oid) => Some((i, oid.clone())),
            _ => None,
        })
        .collect();
    let oids: Vec<&str> = from_git.iter().map(|(_, oid)| oid.as_str()).collect();
    let mut raws: Vec<(usize, Option<Vec<u8>>)> = from_git
        .iter()
        .map(|(i, _)| *i)
        .zip(cat_blobs(&request.repo, &oids)?)
        .collect();
    let blobs = request.blobs.len();
    let mut drifted = Vec::new();
    for &i in &missing {
        match &slots[i].source {
            Source::Git(_) => {}
            Source::External(_) => raws.push((i, external[i - blobs].take())),
            Source::Disk(file) => {
                let raw = std::fs::read(file).ok();
                let oid = &request.blobs[i].oid;
                if raw
                    .as_deref()
                    .is_some_and(|raw| git_blob_id(raw, oid) != *oid)
                {
                    drifted.push(slots[i].path.to_string());
                    slots[i].key = None;
                }
                raws.push((i, raw));
            }
        }
    }
    let made: Vec<(usize, Result<String, String>, bool)> = raws
        .into_par_iter()
        .map(|(i, raw)| {
            let profile = if i < blobs { profiles.0 } else { profiles.1 };
            (i, profile.record(raw.as_deref()), raw.is_some())
        })
        .collect();
    for (i, json, readable) in made {
        slots[i].fresh = Some(json?);
        if !readable {
            slots[i].key = None;
        }
    }
    Ok((missing.len(), drifted))
}

/// A stored entry's body, decoded in parallel once every entry's strings are interned.
struct Pending {
    file: usize,
    map: Vec<Sid>,
    at: usize,
    start: usize,
    json_len: usize,
}

/// A record whose JSON text the database holds: its natural key, and how to make it again.
struct Stored {
    path: String,
    config: String,
    oid: String,
    lite: bool,
    source: Source,
    json_len: usize,
}

/// Where one record's JSON text comes from when something first reads the fact text.
enum Json {
    Stored(Stored),
    Fresh(String),
}

/// What making a record again needs.
struct Again {
    repo: PathBuf,
    full: ExtractConfig,
    lite: ExtractConfig,
}

/// The record JSON of a file whose entry went away, extracted again from the same bytes.
fn extract_again(again: &Again, record: &Stored) -> Result<String, String> {
    let raw = match &record.source {
        Source::Git(oid) => cat_blobs(&again.repo, &[oid.as_str()])?.pop().flatten(),
        Source::Disk(file) | Source::External(file) => std::fs::read(file).ok(),
    };
    if let Source::Disk(_) = record.source
        && raw
            .as_deref()
            .is_some_and(|raw| git_blob_id(raw, &record.oid) != record.oid)
    {
        return Err(format!("{} changed during the run; run again", record.path));
    }
    let config = if record.lite {
        &again.lite
    } else {
        &again.full
    };
    let json = Profile::new(config).record(raw.as_deref())?;
    if json.len() != record.json_len {
        return Err(format!(
            "the facts of {} changed during the run; run again",
            record.path
        ));
    }
    Ok(json)
}

/// The records' JSON text, one record at a time (`RecordText`): a fresh record from memory, a
/// stored one from the database by natural key over one connection opened on first use, and
/// one whose entry went away extracted again.
struct StoredRecords {
    db_path: Option<PathBuf>,
    db: Mutex<Option<FactDb>>,
    records: Vec<Json>,
    again: Again,
    /// The last record read: a pack reads several keys of one record in a row.
    last: Mutex<Option<(usize, Vec<u8>)>>,
}

impl StoredRecords {
    /// Bytes `range` of a stored record, decompressed from the frames that hold them; None
    /// when the database cannot give them.
    fn read(&self, stored: &Stored, range: Range<usize>) -> Option<Vec<u8>> {
        let path = self.db_path.as_deref()?;
        let mut db = self.db.lock().ok()?;
        if db.is_none() {
            *db = open_cache(path);
        }
        match db
            .as_ref()?
            .read_json(&stored.config, &stored.oid, stored.json_len, range)
        {
            Ok(found) => found,
            Err(error) => {
                give_up(path, &error);
                *db = None;
                None
            }
        }
    }

    /// Calls `use_text` with one record's JSON text. A stored record is read and decompressed
    /// whole once, and kept for the next key of it. The lock on the kept record is not held
    /// while a record is read or extracted again, so other readers do not wait on it.
    fn with_record<T>(
        &self,
        file: usize,
        use_text: impl FnOnce(&[u8]) -> Result<T, String>,
    ) -> Result<T, String> {
        let stored = match &self.records[file] {
            Json::Fresh(json) => return use_text(json.as_bytes()),
            Json::Stored(stored) => stored,
        };
        {
            let last = self.last.lock().map_err(|e| e.to_string())?;
            if let Some((at, json)) = last.as_ref()
                && *at == file
            {
                return use_text(json);
            }
        }
        let json = match self.read(stored, 0..stored.json_len) {
            Some(json) => json,
            None => {
                let json = extract_again(&self.again, stored)?;
                self.repair(stored, &json);
                json.into_bytes()
            }
        };
        let found = use_text(&json);
        *self.last.lock().map_err(|e| e.to_string())? = Some((file, json));
        found
    }

    /// Rewrites the stored record of an entry whose record could not be read, so the next run
    /// reads it from the cache again; an entry that went away is left alone. Logged once.
    fn repair(&self, stored: &Stored, json: &str) {
        let Ok(db) = self.db.lock() else {
            return;
        };
        let Some(db) = db.as_ref() else {
            return;
        };
        let record = pack(json.as_bytes(), &value_ranges(json));
        let repaired = db.repair_record(&stored.config, &stored.oid, &record, json.len());
        if repaired.is_ok() && !REPAIRED.swap(true, Ordering::Relaxed) {
            eprintln!(
                "hoglint: the fact cache could not give the record of {}, so it was extracted again and stored anew",
                stored.path
            );
        }
    }
}

impl RecordText for StoredRecords {
    fn record(&self, file: usize) -> Result<Vec<u8>, String> {
        self.with_record(file, |json| Ok(json.to_vec()))
    }

    /// Reads only the frames of `range` of a stored record. A record whose entry went away is
    /// made again whole, and kept for the next key of it.
    fn slice(&self, file: usize, range: Range<usize>) -> Result<Vec<u8>, String> {
        if let Json::Stored(stored) = &self.records[file]
            && let Some(found) = self.read(stored, range.clone())
        {
            return Ok(found);
        }
        self.with_record(file, |json| {
            json.get(range)
                .map(<[u8]>::to_vec)
                .ok_or_else(|| "a record is shorter than its keys say".to_string())
        })
    }
}

/// Turns (stage, seconds since start) marks into (stage, seconds the stage took).
fn cumulative_to_spans(marks: Vec<(&'static str, f64)>) -> Vec<(&'static str, f64)> {
    let mut last = 0.0;
    marks
        .into_iter()
        .map(|(name, at)| {
            let span = at - last;
            last = at;
            (name, span)
        })
        .collect()
}

/// Reads what the database has for the slots, in two short read transactions: keys, then
/// binary facts. On an error the run goes on without the database.
fn read_cache(
    db: &mut Option<FactDb>,
    path: Option<&Path>,
    slots: &mut [Slot],
    profiles: (&Profile, &Profile),
    bodies: &mut Vec<u8>,
) {
    let Some(found) = db.as_ref() else {
        return;
    };
    let read =
        find(found, slots, profiles).and_then(|()| read_found(found, slots, profiles, bodies));
    if let (Err(error), Some(path)) = (read, path) {
        give_up(path, &error);
        *db = None;
        bodies.clear();
        for slot in slots.iter_mut() {
            slot.found = None;
            slot.stored = None;
        }
    }
}

/// The fact set of a snapshot: its blobs, then its third-party files. Interning runs in fact
/// order, so string ids do not depend on threads. New entries are stored on the way out. The
/// JSON text is read only when something asks for it (`FactSet::text`).
pub fn load(request: &BlobRequest, it: &mut Interner) -> Result<(FactSet, BlobStats), String> {
    let clock = std::time::Instant::now();
    let mut marks: Vec<(&'static str, f64)> = Vec::new();
    let mut mark = |name: &'static str| marks.push((name, clock.elapsed().as_secs_f64()));
    let full = Profile::new(&request.full);
    let lite = Profile::new(&request.lite);
    let mut db = request.db.as_deref().and_then(open_cache);
    let (mut slots, external) = slots(request);
    let mut bodies: Vec<u8> = Vec::new();
    read_cache(
        &mut db,
        request.db.as_deref(),
        &mut slots,
        (&full, &lite),
        &mut bodies,
    );
    mark("facts.read");
    let (extracted, drifted) = extract_missing(request, (&full, &lite), &mut slots, external)?;
    mark("facts.extract");
    let mut start = 0;
    let mut files: Vec<Option<FileFacts>> = Vec::with_capacity(slots.len());
    let mut pending: Vec<Pending> = Vec::new();
    for (file, slot) in slots.iter().enumerate() {
        match (&slot.fresh, &slot.stored, slot.found) {
            (Some(json), _, _) => {
                let facts = decode_record(it, json.as_bytes(), start, slot.path)
                    .map_err(|e| e.to_string())?;
                files.push(Some(facts));
                start += json.len();
            }
            (None, Some(range), Some(row)) => {
                let (map, at) = facts_strings(it, &bodies[range.clone()])?;
                pending.push(Pending {
                    file,
                    map,
                    at,
                    start,
                    json_len: row.json_len,
                });
                files.push(None);
                start += row.json_len;
            }
            _ => return Err(format!("no facts for {}", slot.path)),
        }
        start += 1;
    }
    let decoded: Vec<(usize, Result<FileFacts, String>)> = pending
        .into_par_iter()
        .map(|p| {
            let slot = &slots[p.file];
            let range = slot.stored.clone().unwrap_or_default();
            let body = &bodies[range][p.at..];
            (
                p.file,
                decode_body(&p.map, body, p.start, p.json_len, slot.path),
            )
        })
        .collect();
    for (file, facts) in decoded {
        files[file] = Some(facts?);
    }
    // The binary facts are decoded: free them before the link allocates its tables.
    drop(bodies);
    let files: Vec<FileFacts> = files
        .into_iter()
        .map(|f| f.expect("every file has facts"))
        .collect();
    mark("facts.decode");
    let saved = match (db.take(), request.db.as_deref()) {
        (Some(found), Some(path)) => {
            let saved = save(&found, it, &slots, &files, (&full, &lite));
            // Closed first: a connection that closes after the move could delete the new `-wal`.
            drop(found);
            match saved {
                Ok(()) => true,
                Err(error) => {
                    if error.corrupt {
                        give_up(path, &error);
                    }
                    false
                }
            }
        }
        _ => false,
    };
    mark("facts.save");
    let records: Vec<Json> = slots
        .iter_mut()
        .map(|slot| {
            let config = if slot.lite { &lite.id } else { &full.id };
            let stored = |key: &String, json_len: usize| {
                Json::Stored(Stored {
                    path: slot.path.to_string(),
                    config: config.clone(),
                    oid: key.clone(),
                    lite: slot.lite,
                    source: slot.source.clone(),
                    json_len,
                })
            };
            match (slot.fresh.take(), slot.found, &slot.key) {
                // A saved record is read back from the database, so no fresh text stays.
                (Some(json), _, Some(key)) if saved => stored(key, json.len()),
                (Some(json), _, _) => Json::Fresh(json),
                (None, Some(row), Some(key)) => stored(key, row.json_len),
                _ => Json::Fresh(String::new()),
            }
        })
        .collect();
    let text = Box::new(StoredRecords {
        db_path: request.db.clone(),
        db: Mutex::new(None),
        records,
        last: Mutex::new(None),
        again: Again {
            repo: request.repo.clone(),
            full: request.full.clone(),
            lite: request.lite.clone(),
        },
    });
    let stats = BlobStats {
        hits: slots.len() - extracted,
        extracted,
        stages: cumulative_to_spans(marks),
        keys: slots.iter().map(|slot| slot.key.clone()).collect(),
        drifted,
    };
    Ok((FactSet::deferred(files, text), stats))
}

/// Where each top-level key's value sits in a record's JSON text, as `key_ranges` gives them.
fn value_ranges(json: &str) -> Vec<Range<usize>> {
    let Ok(map) = serde_json::from_str::<BTreeMap<&str, &RawValue>>(json) else {
        return Vec::new();
    };
    let start = json.as_ptr() as usize;
    map.values()
        .map(|raw| {
            let at = raw.get().as_ptr() as usize - start;
            at..at + raw.get().len()
        })
        .collect()
}

/// Where each key's value sits in a record's own JSON text.
fn key_ranges(facts: &FileFacts) -> Vec<Range<usize>> {
    let start = facts.line.start;
    facts
        .keys
        .iter()
        .map(|(_, range)| range.start - start..range.end - start)
        .collect()
}

/// Stores the new entries whose key is the id of the bytes they were extracted from, and marks
/// the read ones as used today. A failed write only costs a later re-extraction or an early
/// prune, so it never fails the run; the error of a failed store is returned, so a damaged
/// database can be moved aside.
fn save(
    db: &FactDb,
    it: &Interner,
    slots: &[Slot],
    files: &[FileFacts],
    profiles: (&Profile, &Profile),
) -> Result<(), DbError> {
    let profile_id = |slot: &Slot| -> &str {
        if slot.lite {
            &profiles.1.id
        } else {
            &profiles.0.id
        }
    };
    let entries: Vec<NewEntry> = slots
        .par_iter()
        .zip(files.par_iter())
        .filter_map(|(slot, facts)| {
            let json = slot.fresh.as_ref()?;
            let key = slot.key.as_deref()?;
            Some(NewEntry {
                config: profile_id(slot),
                oid: key,
                body: encode_facts(it, facts, facts.line.start),
                record: pack(json.as_bytes(), &key_ranges(facts)),
                json_len: json.len(),
            })
        })
        .collect();
    let stored = db.store(&entries);
    let stale: Vec<(&str, &str)> = slots
        .iter()
        .filter(|slot| slot.found.is_some_and(|row| row.used < db.today))
        .filter_map(|slot| Some((profile_id(slot), slot.key.as_deref()?)))
        .collect();
    let _ = db.touch(&stale);
    stored
}
