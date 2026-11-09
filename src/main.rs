//! hoglint-facts: the shell around the `hoglint_facts` core. Reads files, runs `extract` on a
//! thread pool, keeps the content-hash cache and writes records.
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
//!
//! A file that cannot be read gets a `read_error` record and the run goes on. Bad arguments,
//! an unwritable output and a failing git exit non-zero.

use hoglint_facts::{
    BUILD_ID, ExtractConfig, Facts, FileRecord, Limits, Status, extract, record_line, sha256_hex,
};
use rayon::prelude::*;
use std::collections::{HashMap, HashSet};
use std::fmt;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode};
use std::sync::Mutex;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Instant;

/// Fact list names of a record, for the `kinds` mode.
const FACT_KINDS: [&str; 13] = [
    "assigns",
    "branches",
    "callees",
    "calls",
    "defs",
    "imports",
    "names",
    "refs",
    "site_flags",
    "strs",
    "tags",
    "uses",
    "watched",
];

/// Cache entries store this path; a hit swaps in the real one.
const PATH_PLACEHOLDER: &str = "\"path\":\"\"";

#[derive(Debug)]
enum ShellError {
    Usage(String),
    Io { what: String, error: std::io::Error },
    Encode(serde_json::Error),
    Git(String),
}

impl fmt::Display for ShellError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ShellError::Usage(message) => write!(f, "usage: {message}"),
            ShellError::Io { what, error } => write!(f, "{what}: {error}"),
            ShellError::Encode(error) => write!(f, "encode a record: {error}"),
            ShellError::Git(message) => write!(f, "git ls-files: {message}"),
        }
    }
}

impl From<serde_json::Error> for ShellError {
    fn from(error: serde_json::Error) -> Self {
        ShellError::Encode(error)
    }
}

fn io_error(what: impl Into<String>) -> impl FnOnce(std::io::Error) -> ShellError {
    let what = what.into();
    move |error| ShellError::Io { what, error }
}

fn usage(message: impl Into<String>) -> ShellError {
    ShellError::Usage(message.into())
}

struct Discovery;

impl Discovery {
    fn is_excluded(rel: &str) -> bool {
        let parts: Vec<&str> = rel.split('/').collect();
        if parts.iter().any(|p| p.starts_with('.')) {
            return true;
        }
        parts[..parts.len() - 1]
            .iter()
            .any(|p| *p == "docs" || p.ends_with("__pycache__") || p.ends_with("egg-info"))
    }

    /// Git-visible sources minus hidden paths, docs, caches and egg-info. v1.1 adds `.pyi`.
    /// Project excludes are applied by the caller (hoglint.core.discovery).
    fn files(root: &Path, v11: bool) -> Result<Vec<String>, ShellError> {
        let patterns: &[&str] = if v11 { &["*.py", "*.pyi"] } else { &["*.py"] };
        let out = Command::new("git")
            .args(["ls-files", "-z", "-co", "--exclude-standard", "--"])
            .args(patterns)
            .current_dir(root)
            .output()
            .map_err(|error| ShellError::Git(error.to_string()))?;
        if !out.status.success() {
            let stderr = String::from_utf8_lossy(&out.stderr);
            return Err(ShellError::Git(stderr.trim().to_string()));
        }
        let text = String::from_utf8(out.stdout)
            .map_err(|_| ShellError::Git("a path is not UTF-8".to_string()))?;
        let mut seen = HashSet::new();
        let mut files: Vec<String> = Vec::new();
        for rel in text.split('\0') {
            if rel.is_empty() || Self::is_excluded(rel) || !seen.insert(rel) {
                continue;
            }
            if root.join(rel).is_file() {
                files.push(rel.to_string());
            }
        }
        files.sort();
        Ok(files)
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum OutputMode {
    Stream,
    Kinds,
    Cache,
}

struct Options {
    root: PathBuf,
    files: Vec<String>,
    config: ExtractConfig,
    mode: OutputMode,
    out: Option<PathBuf>,
    cache: Option<PathBuf>,
    timing: bool,
    jobs: usize,
}

/// Command-line arguments, consumed flag by flag; what is left are the positionals.
struct Args {
    rest: Vec<String>,
}

impl Args {
    fn value(&mut self, flag: &str) -> Result<Option<String>, ShellError> {
        let Some(at) = self.rest.iter().position(|a| a == flag) else {
            return Ok(None);
        };
        if at + 1 >= self.rest.len() {
            return Err(usage(format!("{flag} needs a value")));
        }
        let value = self.rest.remove(at + 1);
        self.rest.remove(at);
        Ok(Some(value))
    }

    fn switch(&mut self, flag: &str) -> bool {
        let before = self.rest.len();
        self.rest.retain(|a| a != flag);
        self.rest.len() != before
    }

    fn number(&mut self, flag: &str) -> Result<Option<usize>, ShellError> {
        let Some(text) = self.value(flag)? else {
            return Ok(None);
        };
        let number = text
            .parse()
            .map_err(|_| usage(format!("{flag} needs a number, got {text:?}")))?;
        Ok(Some(number))
    }

    fn list(&mut self, flag: &str) -> Result<Vec<String>, ShellError> {
        let text = self.value(flag)?.unwrap_or_default();
        Ok(text.split(',').map(str::to_string).collect())
    }
}

impl Options {
    fn parse(argv: Vec<String>) -> Result<Options, ShellError> {
        let mut args = Args { rest: argv };
        let jobs = args.number("--jobs")?.unwrap_or(0);
        let first_party = args.list("--first-party")?;
        let watch_callees = args.list("--watch-callees")?;
        let mode = match args.value("--mode")?.as_deref() {
            None | Some("stream") => OutputMode::Stream,
            Some("kinds") => OutputMode::Kinds,
            Some("cache") => OutputMode::Cache,
            Some(other) => return Err(usage(format!("unknown --mode {other}"))),
        };
        let out = args.value("--out")?.map(PathBuf::from);
        let cache = args.value("--cache")?.map(PathBuf::from);
        let base = if args.switch("--v1.1") {
            Limits::V1_1
        } else {
            Limits::V1
        };
        let limits = Limits {
            max_nodes: args.number("--max-nodes")?.unwrap_or(base.max_nodes),
            max_symbolic: args.number("--max-symbolic")?.unwrap_or(base.max_symbolic),
        };
        let timing = args.switch("--timing");
        let [root, listing] = args.rest.as_slice() else {
            return Err(usage("hoglint-facts <repo_root> <file_list|-> [options]"));
        };
        let text = if listing == "-" {
            std::io::read_to_string(std::io::stdin()).map_err(io_error("read the file list"))?
        } else {
            std::fs::read_to_string(listing).map_err(io_error(format!("read {listing}")))?
        };
        let files = text
            .lines()
            .filter(|l| !l.trim().is_empty())
            .map(str::to_string)
            .collect();
        Ok(Options {
            root: PathBuf::from(root),
            files,
            config: ExtractConfig::new(first_party, watch_callees, limits),
            mode,
            out,
            cache,
            timing,
            jobs,
        })
    }
}

/// CPU seconds summed over threads.
#[derive(Default)]
struct Totals {
    read: f64,
    extract: f64,
    serialize: f64,
    cache_io: f64,
    parsed_files: usize,
}

struct Runner<'o> {
    opts: &'o Options,
    cfg: String,
    totals: Mutex<Totals>,
    tmp_counter: AtomicU64,
}

impl<'o> Runner<'o> {
    fn new(opts: &'o Options) -> Self {
        Runner {
            opts,
            cfg: opts.config.config_id(),
            totals: Mutex::new(Totals::default()),
            tmp_counter: AtomicU64::new(0),
        }
    }

    fn add(&self, update: impl FnOnce(&mut Totals)) {
        if let Ok(mut totals) = self.totals.lock() {
            update(&mut totals);
        }
    }

    fn read(&self, rel: &str) -> Option<Vec<u8>> {
        let started = Instant::now();
        let raw = std::fs::read(self.opts.root.join(rel));
        let seconds = started.elapsed().as_secs_f64();
        self.add(|t| t.read += seconds);
        match raw {
            Ok(raw) => Some(raw),
            Err(error) => {
                eprintln!("hoglint-facts: read {rel}: {error}");
                None
            }
        }
    }

    fn unreadable() -> FileRecord {
        FileRecord {
            sha256: String::new(),
            bytes: 0,
            status: Status::ReadError,
            facts: Facts::default(),
        }
    }

    fn extract(&self, raw: &[u8]) -> FileRecord {
        let started = Instant::now();
        let record = extract(raw, &self.opts.config);
        let seconds = started.elapsed().as_secs_f64();
        self.add(|t| {
            t.extract += seconds;
            t.parsed_files += 1;
        });
        record
    }

    fn encode(&self, record: &FileRecord, path: &str) -> Result<String, ShellError> {
        let started = Instant::now();
        let line = record_line(record, &self.cfg, path)?;
        let seconds = started.elapsed().as_secs_f64();
        self.add(|t| t.serialize += seconds);
        Ok(line)
    }

    /// stream and kinds: parse every file.
    fn record(&self, rel: &str) -> FileRecord {
        match self.read(rel) {
            Some(raw) => self.extract(&raw),
            None => Self::unreadable(),
        }
    }

    /// cache: reuse the entry for this content hash, else extract and store one.
    fn cached_line(&self, rel: &str, dir: &Path) -> Result<String, ShellError> {
        let Some(raw) = self.read(rel) else {
            return self.encode(&Self::unreadable(), rel);
        };
        let sha256 = sha256_hex(&raw);
        let entry = dir.join(format!("{sha256}.json"));
        let started = Instant::now();
        if let Ok(text) = std::fs::read_to_string(&entry) {
            let seconds = started.elapsed().as_secs_f64();
            self.add(|t| t.cache_io += seconds);
            return with_path(&text, rel);
        }
        let record = self.extract(&raw);
        let body = self.encode(&record, "")?;
        let started = Instant::now();
        self.store(dir, &sha256, &entry, &body);
        let seconds = started.elapsed().as_secs_f64();
        self.add(|t| t.cache_io += seconds);
        with_path(&body, rel)
    }

    /// Files with equal content race for one entry, so each writer renames its own temp file.
    /// A failed write only costs a later re-parse.
    fn store(&self, dir: &Path, sha256: &str, entry: &Path, body: &str) {
        let n = self.tmp_counter.fetch_add(1, Ordering::Relaxed);
        let tmp = dir.join(format!("{sha256}.tmp{}-{n}", std::process::id()));
        let written = std::fs::write(&tmp, body).and_then(|()| std::fs::rename(&tmp, entry));
        if let Err(error) = written {
            eprintln!("hoglint-facts: cache entry {}: {error}", entry.display());
            let _ = std::fs::remove_file(&tmp);
        }
    }
}

fn with_path(body: &str, rel: &str) -> Result<String, ShellError> {
    let quoted = serde_json::to_string(rel)?;
    Ok(body.replacen(PATH_PLACEHOLDER, &format!("\"path\":{quoted}"), 1))
}

fn writer(out: &Option<PathBuf>) -> Result<Box<dyn Write>, ShellError> {
    Ok(match out {
        Some(p) => {
            let file =
                std::fs::File::create(p).map_err(io_error(format!("create {}", p.display())))?;
            Box::new(std::io::BufWriter::new(file))
        }
        None => Box::new(std::io::BufWriter::new(std::io::stdout().lock())),
    })
}

fn write_lines(out: &Option<PathBuf>, lines: &[String]) -> Result<usize, ShellError> {
    let mut w = writer(out)?;
    let mut total = 0;
    for line in lines {
        w.write_all(line.as_bytes())
            .and_then(|()| w.write_all(b"\n"))
            .map_err(io_error("write records"))?;
        total += line.len() + 1;
    }
    w.flush().map_err(io_error("write records"))?;
    Ok(total)
}

/// The py-l2 split store: `envelope.json` = {path: envelope}, `<kind>.json` = {path: rows}.
/// A later duplicate path wins, as in split_store.py.
fn write_kinds(
    dir: &Path,
    files: &[String],
    records: &[FileRecord],
    cfg: &str,
) -> Result<usize, ShellError> {
    std::fs::create_dir_all(dir).map_err(io_error(format!("create {}", dir.display())))?;
    let last: HashMap<&str, usize> = files
        .iter()
        .enumerate()
        .map(|(i, rel)| (rel.as_str(), i))
        .collect();
    let keep: Vec<usize> = (0..files.len())
        .filter(|i| last[files[*i].as_str()] == *i)
        .collect();
    let mut envelopes = Vec::new();
    let mut lists: Vec<serde_json::Map<String, serde_json::Value>> = Vec::new();
    for &i in &keep {
        let record = &records[i];
        let envelope = FileRecord {
            sha256: record.sha256.clone(),
            bytes: record.bytes,
            status: record.status,
            facts: Facts::default(),
        };
        envelopes.push(record_line(&envelope, cfg, &files[i])?);
        lists.push(serde_json::from_str(&record_line(record, cfg, &files[i])?)?);
    }
    let mut total = 0;
    let mut write_doc = |name: &str, entries: Vec<(usize, String)>| -> Result<(), ShellError> {
        let mut doc = String::from("{");
        for (n, (i, value)) in entries.iter().enumerate() {
            if n > 0 {
                doc.push(',');
            }
            doc.push_str(&serde_json::to_string(&files[*i])?);
            doc.push(':');
            doc.push_str(value);
        }
        doc.push('}');
        std::fs::write(dir.join(name), &doc).map_err(io_error(format!("write {name}")))?;
        total += doc.len();
        Ok(())
    };
    write_doc(
        "envelope.json",
        keep.iter().copied().zip(envelopes).collect(),
    )?;
    for kind in FACT_KINDS {
        let mut rows = Vec::new();
        for (&i, map) in keep.iter().zip(&lists) {
            if let Some(value) = map.get(kind) {
                rows.push((i, serde_json::to_string(value)?));
            }
        }
        write_doc(&format!("{kind}.json"), rows)?;
    }
    Ok(total)
}

fn run_extract(opts: &Options) -> Result<(), ShellError> {
    if opts.jobs > 0 {
        rayon::ThreadPoolBuilder::new()
            .num_threads(opts.jobs)
            .build_global()
            .map_err(|error| usage(format!("--jobs {}: {error}", opts.jobs)))?;
    }
    let runner = Runner::new(opts);
    let started = Instant::now();
    let written = match opts.mode {
        OutputMode::Stream => {
            let lines: Vec<String> = opts
                .files
                .par_iter()
                .map(|rel| runner.encode(&runner.record(rel), rel))
                .collect::<Result<_, _>>()?;
            write_lines(&opts.out, &lines)?
        }
        OutputMode::Kinds => {
            let Some(dir) = &opts.out else {
                return Err(usage("--mode kinds needs --out DIR"));
            };
            let records: Vec<FileRecord> = opts
                .files
                .par_iter()
                .map(|rel| runner.record(rel))
                .collect();
            write_kinds(dir, &opts.files, &records, &runner.cfg)?
        }
        OutputMode::Cache => {
            let Some(base) = &opts.cache else {
                return Err(usage("--mode cache needs --cache DIR"));
            };
            let dir = base.join(opts.config.cache_namespace());
            std::fs::create_dir_all(&dir).map_err(io_error(format!("create {}", dir.display())))?;
            let lines: Vec<String> = opts
                .files
                .par_iter()
                .map(|rel| runner.cached_line(rel, &dir))
                .collect::<Result<_, _>>()?;
            write_lines(&opts.out, &lines)?
        }
    };
    if opts.timing
        && let Ok(t) = runner.totals.lock()
    {
        eprintln!(
            "files={} parsed={} wall={:.3}s out={}B | cpu-sum read={:.3} extract={:.3} serialize={:.3} cache_io={:.3}",
            opts.files.len(),
            t.parsed_files,
            started.elapsed().as_secs_f64(),
            written,
            t.read,
            t.extract,
            t.serialize,
            t.cache_io
        );
    }
    Ok(())
}

fn run(argv: Vec<String>) -> Result<(), ShellError> {
    match argv.first().map(String::as_str) {
        Some("--build-id") => {
            println!("{BUILD_ID}");
            Ok(())
        }
        Some("--discover") => {
            let Some(root) = argv.get(1) else {
                return Err(usage("hoglint-facts --discover <repo_root> [--v1.1]"));
            };
            let files = Discovery::files(Path::new(root), argv.iter().any(|a| a == "--v1.1"))?;
            let mut out = std::io::BufWriter::new(std::io::stdout().lock());
            for f in files {
                writeln!(out, "{f}").map_err(io_error("write the file list"))?;
            }
            out.flush().map_err(io_error("write the file list"))
        }
        _ => run_extract(&Options::parse(argv)?),
    }
}

fn main() -> ExitCode {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    match run(argv) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("hoglint-facts: {error}");
            ExitCode::FAILURE
        }
    }
}
