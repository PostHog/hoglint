//! The facts of a snapshot given as blob ids: the shell around `hoglint_facts::extract` and the
//! link's cache entries.
//!
//! An entry is cached under (facts id, blob oid) and holds the record's JSON text, which packs
//! read, and the link's facts in binary (`link::store`), so a hit reads one file and parses no
//! JSON. A miss reads the blob with one `git cat-file --batch` for the whole snapshot (or the
//! working tree file, for a file git has no blob of yet), extracts it, and stores the entry.
//! Site-packages files are not in git; they are read and keyed by content hash.

use hoglint_facts::link::facts::{FactSet, FileFacts, decode_record};
use hoglint_facts::link::intern::Interner;
use hoglint_facts::link::store::{decode_entry, encode_entry, entry_json};
use hoglint_facts::{
    ExtractConfig, Facts, FileRecord, Kinds, Status, extract, record_line, sha256_hex,
};
use rayon::prelude::*;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};

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
    pub cache: Option<PathBuf>,
}

/// Counts of what one run read.
#[derive(Default, Debug)]
pub struct BlobStats {
    pub hits: usize,
    pub extracted: usize,
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

struct Store {
    dir: Option<PathBuf>,
    cfg: String,
    tmp_counter: AtomicU64,
}

impl Store {
    fn new(config: &ExtractConfig, cache: Option<&Path>) -> Result<Store, String> {
        let dir = cache.map(|base| base.join(config.cache_namespace()));
        if let Some(dir) = &dir {
            std::fs::create_dir_all(dir).map_err(|e| format!("create {}: {e}", dir.display()))?;
        }
        Ok(Store {
            dir,
            cfg: config.config_id(),
            tmp_counter: AtomicU64::new(0),
        })
    }

    /// A current-format entry, else None.
    fn get(&self, key: &str) -> Option<Vec<u8>> {
        let entry = std::fs::read(self.dir.as_ref()?.join(format!("{key}.hlf"))).ok()?;
        entry_json(&entry).is_some().then_some(entry)
    }

    /// Files with equal content race for one entry, so each writer renames its own temp file.
    /// A failed write only costs a later re-extraction.
    fn put(&self, key: &str, entry: &[u8]) {
        let Some(dir) = &self.dir else {
            return;
        };
        let n = self.tmp_counter.fetch_add(1, Ordering::Relaxed);
        let tmp = dir.join(format!("{key}.tmp{}-{n}", std::process::id()));
        if std::fs::write(&tmp, entry)
            .and_then(|()| std::fs::rename(&tmp, dir.join(format!("{key}.hlf"))))
            .is_err()
        {
            let _ = std::fs::remove_file(&tmp);
        }
    }

    /// The record JSON (with an empty path) of one file's bytes; None bytes is a read error.
    fn record(&self, raw: Option<&[u8]>, config: &ExtractConfig) -> Result<String, String> {
        let record = match raw {
            Some(raw) => extract(raw, sha256_hex(raw), config),
            None => FileRecord {
                sha256: String::new(),
                bytes: 0,
                status: Status::ReadError,
                facts: Facts::default(),
            },
        };
        record_line(&record, &self.cfg, "").map_err(|e| format!("encode a record: {e}"))
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

/// One file of the fact set: a cached entry, or a fresh record to decode and store.
enum Found {
    Entry(Vec<u8>),
    Fresh {
        json: String,
        key: Option<String>,
        lite: bool,
    },
}

fn blob_entries(
    request: &BlobRequest,
    full: &Store,
    stats: &mut BlobStats,
) -> Result<Vec<Found>, String> {
    let mut found: Vec<Option<Found>> = request
        .blobs
        .par_iter()
        .map(|blob| full.get(&blob.oid).map(Found::Entry))
        .collect();
    let missing: Vec<usize> = (0..found.len()).filter(|&i| found[i].is_none()).collect();
    stats.hits = found.len() - missing.len();
    stats.extracted = missing.len();
    let from_git: Vec<usize> = missing
        .iter()
        .copied()
        .filter(|&i| !request.blobs[i].from_disk)
        .collect();
    let oids: Vec<&str> = from_git
        .iter()
        .map(|&i| request.blobs[i].oid.as_str())
        .collect();
    let mut contents: Vec<(usize, Option<Vec<u8>>)> = from_git
        .iter()
        .copied()
        .zip(cat_blobs(&request.repo, &oids)?)
        .collect();
    contents.extend(
        missing
            .iter()
            .filter(|&&i| request.blobs[i].from_disk)
            .map(|&i| {
                (
                    i,
                    std::fs::read(request.repo.join(&request.blobs[i].path)).ok(),
                )
            }),
    );
    let made: Vec<(usize, Result<Found, String>)> = contents
        .par_iter()
        .map(|(i, raw)| {
            let key = raw.as_ref().map(|_| request.blobs[*i].oid.clone());
            (
                *i,
                full.record(raw.as_deref(), &request.full)
                    .map(|json| Found::Fresh {
                        json,
                        key,
                        lite: false,
                    }),
            )
        })
        .collect();
    for (i, item) in made {
        found[i] = Some(item?);
    }
    Ok(found
        .into_iter()
        .map(|item| item.expect("every blob is found"))
        .collect())
}

fn external_entries(request: &BlobRequest, lite: &Store) -> Result<Vec<Found>, String> {
    request
        .external
        .par_iter()
        .map(|(_, file)| {
            let Ok(raw) = std::fs::read(file) else {
                return Ok(Found::Fresh {
                    json: lite.record(None, &request.lite)?,
                    key: None,
                    lite: true,
                });
            };
            let key = sha256_hex(&raw);
            if let Some(entry) = lite.get(&key) {
                return Ok(Found::Entry(entry));
            }
            let json = lite.record(Some(&raw), &request.lite)?;
            Ok(Found::Fresh {
                json,
                key: Some(key),
                lite: true,
            })
        })
        .collect()
}

/// The fact set of a snapshot: its blobs, then its third-party files. Interning runs in file
/// order, so string ids do not depend on threads. New entries are stored on the way out.
pub fn load(request: &BlobRequest, it: &mut Interner) -> Result<(FactSet, BlobStats), String> {
    let full = Store::new(&request.full, request.cache.as_deref())?;
    let lite = Store::new(&request.lite, request.cache.as_deref())?;
    let mut stats = BlobStats::default();
    let mut found = blob_entries(request, &full, &mut stats)?;
    found.extend(external_entries(request, &lite)?);
    let paths = request
        .blobs
        .iter()
        .map(|b| b.path.as_str())
        .chain(request.external.iter().map(|(p, _)| p.as_str()));
    let mut text: Vec<u8> = Vec::new();
    let mut files: Vec<FileFacts> = Vec::with_capacity(found.len());
    let mut fresh: Vec<usize> = Vec::new();
    for (i, (item, path)) in found.iter().zip(paths).enumerate() {
        let start = text.len();
        match item {
            Found::Entry(entry) => {
                text.extend_from_slice(entry_json(entry).expect("checked when read"));
                files.push(decode_entry(it, entry, start, path)?);
            }
            Found::Fresh { json, .. } => {
                text.extend_from_slice(json.as_bytes());
                files.push(
                    decode_record(it, &text, start..text.len(), path).map_err(|e| e.to_string())?,
                );
                fresh.push(i);
            }
        }
        text.push(b'\n');
    }
    let shared: &Interner = it;
    fresh.par_iter().for_each(|&i| {
        if let Found::Fresh {
            json,
            key: Some(key),
            lite: is_lite,
        } = &found[i]
        {
            let entry = encode_entry(shared, json.as_bytes(), &files[i], files[i].line.start);
            if *is_lite {
                lite.put(key, &entry)
            } else {
                full.put(key, &entry)
            }
        }
    });
    Ok((FactSet { text, files }, stats))
}
