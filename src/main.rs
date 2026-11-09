//! L1 fact extractor (schema v1) on the ruff parser. Output matches reference_l1.py line for line.
//!
//! Usage:
//!   hoglint-facts --discover <repo_root> [--v1.1] > files.txt
//!   hoglint-facts --build-id
//!   hoglint-facts <repo_root> <file_list|-> [--jobs N] [--first-party a,b] [--watch-callees a,b]
//!      [--mode stream|kinds|cache] [--out PATH] [--cache DIR] [--timing] [--v1.1]
//!      [--max-nodes N] [--max-symbolic N]
//!
//! `--v1.1` raises the value-tree cap to 1,000,000 and the symbolic-string cap to 1,000.
//! `--watch-callees` adds callee names (last dotted part) whose calls get a `calls` row.
//!
//! Modes: `stream` writes JSON lines to --out (default stdout); `kinds` writes one file per fact
//! kind into the --out directory; `cache` keeps one record per source sha256 under --cache and
//! only parses files whose content has no entry yet (records still stream to --out). The cache
//! directory name holds the schema version, the build id and every option that changes records.

mod json;
mod record;
mod value;
mod walker;

use rayon::prelude::*;
use record::{config_id, extract, sha256_hex, FileOut, Times, BUILD_ID, KINDS};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::Mutex;
use std::time::Instant;

/// Cache entries store this path; a hit swaps in the real one.
const PATH_PLACEHOLDER: &str = "\"path\":\"\"";
static TMP_COUNTER: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

struct Discovery;

impl Discovery {
    fn is_excluded(rel: &str) -> bool {
        let parts: Vec<&str> = rel.split('/').collect();
        if parts.iter().any(|p| p.starts_with('.')) {
            return true;
        }
        for p in &parts[..parts.len() - 1] {
            if *p == "docs" || p.ends_with("__pycache__") || p.ends_with("egg-info") {
                return true;
            }
        }
        false
    }

    /// Git-visible sources minus hidden paths, docs, caches and egg-info. v1.1 adds `.pyi`.
    /// Project excludes are applied by the caller (hoglint.core.discovery).
    fn files(root: &Path, v11: bool) -> Vec<String> {
        let patterns: &[&str] = if v11 { &["*.py", "*.pyi"] } else { &["*.py"] };
        let out = Command::new("git")
            .args(["ls-files", "-z", "-co", "--exclude-standard", "--"])
            .args(patterns)
            .current_dir(root)
            .output()
            .expect("git ls-files");
        let text = String::from_utf8(out.stdout).expect("utf-8 paths");
        let mut seen = std::collections::HashSet::new();
        let mut files: Vec<String> = Vec::new();
        for rel in text.split('\0') {
            if rel.is_empty() || seen.contains(rel) || Self::is_excluded(rel) {
                continue;
            }
            seen.insert(rel.to_string());
            if root.join(rel).is_file() {
                files.push(rel.to_string());
            }
        }
        files.sort();
        files
    }
}

struct Options {
    root: PathBuf,
    files: Vec<String>,
    first_party: Vec<String>,
    extra_callees: Vec<String>,
    mode: String,
    out: Option<PathBuf>,
    cache: Option<PathBuf>,
    timing: bool,
}

impl Options {
    fn parse(mut argv: Vec<String>) -> Options {
        let mut take = |flag: &str| -> Option<String> {
            let at = argv.iter().position(|a| a == flag)?;
            let value = argv.get(at + 1).cloned();
            argv.drain(at..(at + 2).min(argv.len()));
            value
        };
        let jobs = take("--jobs");
        let first_party = take("--first-party");
        let watch_callees = take("--watch-callees");
        let mode = take("--mode").unwrap_or_else(|| "stream".to_string());
        let out = take("--out").map(PathBuf::from);
        let cache = take("--cache").map(PathBuf::from);
        let max_nodes = take("--max-nodes");
        let max_symbolic = take("--max-symbolic");
        let v11 = argv.iter().any(|a| a == "--v1.1");
        argv.retain(|a| a != "--v1.1");
        let (default_nodes, default_symbolic) = if v11 { ("1000000", "1000") } else { ("400", "100") };
        value::set_limits(value::Limits {
            max_nodes: max_nodes.as_deref().unwrap_or(default_nodes).parse().expect("--max-nodes N"),
            max_symbolic: max_symbolic.as_deref().unwrap_or(default_symbolic).parse().expect("--max-symbolic N"),
        });
        let timing = argv.iter().any(|a| a == "--timing");
        argv.retain(|a| a != "--timing");
        if let Some(j) = jobs {
            let n: usize = j.parse().expect("--jobs N");
            if n > 0 {
                rayon::ThreadPoolBuilder::new().num_threads(n).build_global().unwrap();
            }
        }
        let first_party = sorted_list(first_party);
        let extra_callees = sorted_list(watch_callees);
        walker::set_extra_watched(extra_callees.clone());
        let root = PathBuf::from(&argv[0]);
        let listing = &argv[1];
        let text = if listing == "-" {
            std::io::read_to_string(std::io::stdin()).expect("stdin")
        } else {
            std::fs::read_to_string(listing).expect("file list")
        };
        let files = text.lines().filter(|l| !l.trim().is_empty()).map(str::to_string).collect();
        Options { root, files, first_party, extra_callees, mode, out, cache, timing }
    }
}

fn sorted_list(list: Option<String>) -> Vec<String> {
    let mut items: Vec<String> = list.unwrap_or_default().split(',').filter(|s| !s.is_empty()).map(str::to_string).collect();
    items.sort();
    items.dedup();
    items
}

#[derive(Default)]
struct Totals {
    read: f64,
    parse: f64,
    extract: f64,
    serialize: f64,
    cache_io: f64,
    parsed_files: usize,
}

struct Runner<'o> {
    opts: &'o Options,
    cfg: String,
    totals: Mutex<Totals>,
}

impl Runner<'_> {
    fn read(&self, rel: &str) -> Vec<u8> {
        std::fs::read(self.opts.root.join(rel)).unwrap_or_else(|e| panic!("read {rel}: {e}"))
    }

    fn fresh(&self, raw: &[u8]) -> (FileOut, Times) {
        let mut times = Times::default();
        let out = extract(raw, &self.opts.first_party, &mut times);
        (out, times)
    }

    fn add(&self, read: f64, times: Times, cache_io: f64, parsed: bool) {
        let mut t = self.totals.lock().unwrap();
        t.read += read;
        t.parse += times.parse.as_secs_f64();
        t.extract += times.extract.as_secs_f64();
        t.serialize += times.serialize.as_secs_f64();
        t.cache_io += cache_io;
        t.parsed_files += parsed as usize;
    }

    /// stream and kinds: parse every file.
    fn record(&self, rel: &str) -> FileOut {
        let t0 = Instant::now();
        let raw = self.read(rel);
        let read = t0.elapsed().as_secs_f64();
        let (out, times) = self.fresh(&raw);
        self.add(read, times, 0.0, true);
        out
    }

    /// cache: reuse the entry for this content hash, else extract and store one.
    fn cached_line(&self, rel: &str, dir: &Path) -> String {
        let t0 = Instant::now();
        let raw = self.read(rel);
        let read = t0.elapsed().as_secs_f64();
        let entry = dir.join(format!("{}.json", sha256_hex(&raw)));
        let t1 = Instant::now();
        if let Ok(text) = std::fs::read_to_string(&entry) {
            let line = with_path(&text, rel);
            self.add(read, Times::default(), t1.elapsed().as_secs_f64(), false);
            return line;
        }
        let (out, times) = self.fresh(&raw);
        let t2 = Instant::now();
        let body = out.to_line(&self.cfg, "");
        // Files with equal content race for one entry; each writer gets its own temp name.
        let n = TMP_COUNTER.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        let tmp = dir.join(format!("{}.tmp{}-{n}", out.sha256, std::process::id()));
        std::fs::write(&tmp, &body).expect("cache write");
        std::fs::rename(&tmp, &entry).expect("cache rename");
        self.add(read, times, t2.elapsed().as_secs_f64(), true);
        with_path(&body, rel)
    }
}

fn with_path(body: &str, rel: &str) -> String {
    let mut quoted = String::new();
    json::write_str(&mut quoted, rel);
    body.replacen(PATH_PLACEHOLDER, &format!("\"path\":{quoted}"), 1)
}

fn writer(out: &Option<PathBuf>) -> Box<dyn Write> {
    match out {
        Some(p) => Box::new(std::io::BufWriter::new(std::fs::File::create(p).expect("create --out"))),
        None => Box::new(std::io::BufWriter::new(std::io::stdout().lock())),
    }
}

fn write_lines(out: &Option<PathBuf>, lines: &[String]) -> usize {
    let mut w = writer(out);
    let mut total = 0;
    for line in lines {
        w.write_all(line.as_bytes()).unwrap();
        w.write_all(b"\n").unwrap();
        total += line.len() + 1;
    }
    w.flush().unwrap();
    total
}

/// The py-l2 split store: `envelope.json` = {path: envelope}, `<kind>.json` = {path: rows}.
/// A later duplicate path wins, as in split_store.py.
fn write_kinds(dir: &Path, files: &[String], records: &[FileOut], cfg: &str) -> usize {
    std::fs::create_dir_all(dir).expect("kinds dir");
    let mut last: std::collections::HashMap<&str, usize> = std::collections::HashMap::new();
    for (i, rel) in files.iter().enumerate() {
        last.insert(rel.as_str(), i);
    }
    let keep: Vec<usize> = (0..files.len()).filter(|i| last[files[*i].as_str()] == *i).collect();
    let mut total = 0;
    let mut write_doc = |name: &str, entries: &mut dyn Iterator<Item = (usize, String)>| {
        let mut doc = String::from("{");
        let mut first = true;
        for (i, value) in entries {
            if !first {
                doc.push(',');
            }
            first = false;
            json::write_str(&mut doc, &files[i]);
            doc.push(':');
            doc.push_str(&value);
        }
        doc.push('}');
        std::fs::write(dir.join(name), &doc).expect("kinds write");
        total += doc.len();
    };
    let mut envelopes = keep.iter().map(|&i| {
        let rec = &records[i];
        let empty = FileOut { sha256: rec.sha256.clone(), bytes: rec.bytes, status: rec.status, lists: Vec::new() };
        (i, empty.to_line(cfg, &files[i]))
    });
    write_doc("envelope.json", &mut envelopes);
    for kind in KINDS {
        let mut rows = keep
            .iter()
            .filter_map(|&i| records[i].lists.iter().find(|l| l.0 == kind).map(|l| (i, l.1.clone())));
        write_doc(&format!("{kind}.json"), &mut rows);
    }
    total
}

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    if argv.first().map(String::as_str) == Some("--build-id") {
        println!("{BUILD_ID}");
        return;
    }
    if argv.first().map(String::as_str) == Some("--discover") {
        let files = Discovery::files(Path::new(&argv[1]), argv.iter().any(|a| a == "--v1.1"));
        let mut out = std::io::BufWriter::new(std::io::stdout().lock());
        for f in files {
            writeln!(out, "{f}").unwrap();
        }
        return;
    }
    let opts = Options::parse(argv);
    let runner = Runner { opts: &opts, cfg: config_id(&opts.first_party, &opts.extra_callees), totals: Mutex::new(Totals::default()) };
    let started = Instant::now();
    let written = match opts.mode.as_str() {
        "stream" => {
            let lines: Vec<String> = opts.files.par_iter().map(|rel| runner.record(rel).to_line(&runner.cfg, rel)).collect();
            write_lines(&opts.out, &lines)
        }
        "kinds" => {
            let records: Vec<FileOut> = opts.files.par_iter().map(|rel| runner.record(rel)).collect();
            let dir = opts.out.clone().expect("--mode kinds needs --out DIR");
            write_kinds(&dir, &opts.files, &records, &runner.cfg)
        }
        "cache" => {
            let base = opts.cache.clone().expect("--mode cache needs --cache DIR");
            let dir = base.join(format!(
                "v{}-b{}-{}-n{}-s{}",
                record::SCHEMA_VERSION,
                BUILD_ID,
                runner.cfg,
                value::max_nodes(),
                value::max_symbolic()
            ));
            std::fs::create_dir_all(&dir).expect("cache dir");
            let lines: Vec<String> = opts.files.par_iter().map(|rel| runner.cached_line(rel, &dir)).collect();
            write_lines(&opts.out, &lines)
        }
        other => panic!("unknown --mode {other}"),
    };
    if opts.timing {
        let t = runner.totals.lock().unwrap();
        eprintln!(
            "mode={} files={} parsed={} wall={:.3}s out={}B | cpu-sum read={:.3} parse={:.3} extract={:.3} serialize={:.3} cache_io={:.3}",
            opts.mode,
            opts.files.len(),
            t.parsed_files,
            started.elapsed().as_secs_f64(),
            written,
            t.read,
            t.parse,
            t.extract,
            t.serialize,
            t.cache_io
        );
    }
}
