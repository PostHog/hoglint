//! The facts of a snapshot given as blob ids: the shell around `hoglint_facts::extract` and the
//! fact database.
//!
//! An entry is stored under (extract config id, blob oid) in the database of the facts id
//! (`factdb`): the link's facts in binary (`link::store`), so a hit parses no JSON, and the
//! record's JSON text, which packs read and which is read only when they do. A miss reads the blob with one `git cat-file --batch`
//! for the whole snapshot (or the working tree file, for a file git has no blob of yet), extracts
//! it, and stores the entry. Site-packages files are not in git; they are read and keyed by
//! content hash.

use crate::factdb::{FactDb, NewEntry, Row};
use hoglint_facts::link::facts::{FactSet, FileFacts, decode_record};
use hoglint_facts::link::intern::{Interner, Sid};
use hoglint_facts::link::store::{decode_body, encode_facts, facts_strings};
use hoglint_facts::{
    ExtractConfig, Facts, FileRecord, Kinds, Status, extract, record_line, sha256_hex,
};
use rayon::prelude::*;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

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
}

pub fn configs(
    first_party: Vec<String>,
    kinds: &[String],
    watch: (Vec<String>, Vec<String>, Vec<String>),
    lite_kinds: &[String],
) -> Result<(ExtractConfig, ExtractConfig), String> {
    let base = ExtractConfig::new(first_party).watching(watch.0, watch.1, watch.2);
    let full = base.clone().with_kinds(Kinds::parse(kinds)?);
    let lite = base.with_kinds(Kinds::parse(lite_kinds)?);
    Ok((full, lite))
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

/// One file of the fact set, in fact order: blobs, then third-party files.
struct Slot<'r> {
    path: &'r str,
    /// The entry key: the blob oid, or a third-party file's content hash. None for a file that
    /// could not be read, whose read-error record is never stored.
    key: Option<String>,
    lite: bool,
    found: Option<Row>,
    /// The record JSON of a file that was not stored.
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
        found: None,
        fresh: None,
    });
    let third_party = request
        .external
        .iter()
        .zip(hashes)
        .map(|((path, _), key)| Slot {
            path,
            key,
            lite: true,
            found: None,
            fresh: None,
        });
    (blobs.chain(third_party).collect(), external)
}

/// Extracts every slot the database did not have; returns how many.
fn extract_missing(
    request: &BlobRequest,
    profiles: (&Profile, &Profile),
    slots: &mut [Slot],
    mut external: Vec<Option<Vec<u8>>>,
) -> Result<usize, String> {
    let blobs = request.blobs.len();
    let missing: Vec<usize> = (0..slots.len())
        .filter(|&i| slots[i].found.is_none())
        .collect();
    let from_git: Vec<usize> = missing
        .iter()
        .copied()
        .filter(|&i| i < blobs && !request.blobs[i].from_disk)
        .collect();
    let oids: Vec<&str> = from_git
        .iter()
        .map(|&i| request.blobs[i].oid.as_str())
        .collect();
    let mut raws: Vec<(usize, Option<Vec<u8>>)> = from_git
        .into_iter()
        .zip(cat_blobs(&request.repo, &oids)?)
        .collect();
    for &i in &missing {
        if i >= blobs {
            raws.push((i, external[i - blobs].take()));
        } else if request.blobs[i].from_disk {
            raws.push((
                i,
                std::fs::read(request.repo.join(&request.blobs[i].path)).ok(),
            ));
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
    Ok(missing.len())
}

/// A stored entry's body, decoded in parallel once every entry's strings are interned.
struct Pending {
    file: usize,
    map: Vec<Sid>,
    body: Vec<u8>,
    start: usize,
    json_len: usize,
}

/// Where one record's JSON text comes from when something first reads the fact text.
enum Json {
    Stored(Row),
    Fresh(String),
}

/// The fact text: every record's JSON in fact order, one per line. Stored records are read in
/// one transaction of a new connection, since the text is read after the load returns.
fn read_text(db: Option<&Path>, records: Vec<Json>, len: usize) -> Result<Vec<u8>, String> {
    let db = db.map(FactDb::open).transpose()?;
    if let Some(db) = &db {
        db.begin_read()?;
    }
    let mut text = Vec::with_capacity(len);
    for record in records {
        match (record, &db) {
            (Json::Fresh(json), _) => text.extend_from_slice(json.as_bytes()),
            (Json::Stored(row), Some(db)) => db.read_json(row, |json| {
                if json.len() != row.json_len {
                    return Err("fact database: a record has another length than its key says");
                }
                text.extend_from_slice(json);
                Ok(())
            })??,
            (Json::Stored(_), None) => return Err("a stored record without a database".into()),
        }
        text.push(b'\n');
    }
    if let Some(db) = &db {
        db.end_read()?;
    }
    Ok(text)
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

/// The fact set of a snapshot: its blobs, then its third-party files. Interning runs in fact
/// order, so string ids do not depend on threads. New entries are stored on the way out. The
/// JSON text is read only when something asks for it (`FactSet::text`).
pub fn load(request: &BlobRequest, it: &mut Interner) -> Result<(FactSet, BlobStats), String> {
    let clock = std::time::Instant::now();
    let mut marks: Vec<(&'static str, f64)> = Vec::new();
    let mut mark = |name: &'static str| marks.push((name, clock.elapsed().as_secs_f64()));
    let full = Profile::new(&request.full);
    let lite = Profile::new(&request.lite);
    let db = request.db.as_deref().map(FactDb::open).transpose()?;
    let (mut slots, external) = slots(request);
    if let Some(db) = &db {
        db.begin_read()?;
        for slot in slots.iter_mut() {
            let profile = if slot.lite { &lite } else { &full };
            if let Some(key) = &slot.key {
                slot.found = db.find(&profile.id, key)?;
            }
        }
    }
    mark("facts.find");
    let extracted = extract_missing(request, (&full, &lite), &mut slots, external)?;
    mark("facts.extract");
    let mut start = 0;
    let mut files: Vec<Option<FileFacts>> = Vec::with_capacity(slots.len());
    let mut pending: Vec<Pending> = Vec::new();
    for (file, slot) in slots.iter().enumerate() {
        match (&slot.fresh, slot.found, &db) {
            (Some(json), _, _) => {
                let facts = decode_record(it, json.as_bytes(), start, slot.path)
                    .map_err(|e| e.to_string())?;
                files.push(Some(facts));
                start += json.len();
            }
            (None, Some(row), Some(db)) => {
                let (map, body) = db.read_facts(row, |blob| -> Result<_, String> {
                    let (map, at) = facts_strings(it, blob)?;
                    Ok((map, blob[at..].to_vec()))
                })??;
                pending.push(Pending {
                    file,
                    map,
                    body,
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
    if let Some(db) = &db {
        db.end_read()?;
    }
    mark("facts.read");
    let decoded: Vec<(usize, Result<FileFacts, String>)> = pending
        .into_par_iter()
        .map(|p| {
            let path = slots[p.file].path;
            let facts = decode_body(&p.map, &p.body, p.start, p.json_len, path);
            (p.file, facts)
        })
        .collect();
    for (file, facts) in decoded {
        files[file] = Some(facts?);
    }
    let files: Vec<FileFacts> = files
        .into_iter()
        .map(|f| f.expect("every file has facts"))
        .collect();
    mark("facts.decode");
    if let Some(db) = &db {
        save(db, it, &slots, &files, (&full, &lite));
    }
    mark("facts.save");
    let records: Vec<Json> = slots
        .iter_mut()
        .map(|slot| match (slot.fresh.take(), slot.found) {
            (Some(json), _) => Json::Fresh(json),
            (None, Some(row)) => Json::Stored(row),
            (None, None) => Json::Fresh(String::new()),
        })
        .collect();
    let path = request.db.clone();
    let text = Box::new(move || read_text(path.as_deref(), records, start));
    let stats = BlobStats {
        hits: slots.len() - extracted,
        extracted,
        stages: cumulative_to_spans(marks),
    };
    Ok((FactSet::deferred(files, text), stats))
}

/// Stores the new entries and marks the read ones as used today. A failed write only costs a
/// later re-extraction or an early prune, so it never fails the run.
fn save(
    db: &FactDb,
    it: &Interner,
    slots: &[Slot],
    files: &[FileFacts],
    profiles: (&Profile, &Profile),
) {
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
            let body = encode_facts(it, facts, facts.line.start);
            Some((profile_id(slot), key, body, json.as_bytes()))
        })
        .collect();
    let _ = db.store(&entries);
    let stale: Vec<(&str, &str)> = slots
        .iter()
        .filter(|slot| slot.found.is_some_and(|row| row.used < db.today))
        .filter_map(|slot| Some((profile_id(slot), slot.key.as_deref()?)))
        .collect();
    let _ = db.touch(&stale);
}
