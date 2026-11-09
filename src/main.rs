//! hoglint-facts: the shell around the `hoglint_facts` core. Reads files, runs `extract` on a
//! thread pool, keeps the content-hash cache and writes records.
//!
//! Usage:
//!   hoglint-facts --discover <repo_root> [--v1.1] > files.txt
//!   hoglint-facts --build-id
//!   hoglint-facts <repo_root> <file_list|-> [--jobs N] [--first-party a,b] [--watch-callees a,b]
//!      [--mode stream|cache] [--out PATH] [--cache DIR] [--timing] [--v1.1]
//!
//! `--v1.1` is accepted and changes nothing: the v1.1 caps are the only caps.
//! `--watch-callees` adds callee names (last dotted part) whose calls get a `calls` row.
//! `--timing` prints wall time, output size and per-stage CPU seconds to stderr.
//!
//! Modes: `stream` writes JSON lines to --out (default stdout); `cache` keeps one record per
//! source sha256 under --cache and only parses files whose content has no entry yet (records
//! still stream to --out). The cache directory name holds the schema version, the build id and
//! every option that changes records.
//!
//! A file that cannot be read gets a `read_error` record and the run goes on. Bad arguments,
//! an unwritable output and a failing git exit non-zero.

use hoglint_facts::{
    BUILD_ID, ExtractConfig, Facts, FileRecord, Status, extract, record_line, sha256_hex,
};
use rayon::prelude::*;
use std::collections::HashSet;
use std::fmt;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Instant;

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
            Some("cache") => OutputMode::Cache,
            Some(other) => return Err(usage(format!("unknown --mode {other}"))),
        };
        let out = args.value("--out")?.map(PathBuf::from);
        let cache = args.value("--cache")?.map(PathBuf::from);
        args.switch("--v1.1");
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
            config: ExtractConfig::new(first_party, watch_callees),
            mode,
            out,
            cache,
            timing,
            jobs,
        })
    }
}

/// The shell stages `--timing` reports, in CPU seconds summed over threads.
#[derive(Clone, Copy)]
enum Stage {
    Read,
    Extract,
    Serialize,
    CacheIo,
}

const STAGES: [&str; 4] = ["read", "extract", "serialize", "cache_io"];

struct Runner<'o> {
    opts: &'o Options,
    cfg: String,
    tmp_counter: AtomicU64,
    /// Per stage: calls and nanoseconds. Only `--timing` fills them.
    calls: [AtomicU64; 4],
    nanos: [AtomicU64; 4],
}

impl<'o> Runner<'o> {
    fn new(opts: &'o Options) -> Self {
        Runner {
            opts,
            cfg: opts.config.config_id(),
            tmp_counter: AtomicU64::new(0),
            calls: Default::default(),
            nanos: Default::default(),
        }
    }

    fn timed<T>(&self, stage: Stage, work: impl FnOnce() -> T) -> T {
        if !self.opts.timing {
            return work();
        }
        let started = Instant::now();
        let result = work();
        let nanos = started.elapsed().as_nanos() as u64;
        self.calls[stage as usize].fetch_add(1, Ordering::Relaxed);
        self.nanos[stage as usize].fetch_add(nanos, Ordering::Relaxed);
        result
    }

    fn report(&self, wall: f64, written: usize) {
        let seconds: Vec<String> = STAGES
            .iter()
            .zip(&self.nanos)
            .map(|(name, nanos)| {
                let total = nanos.load(Ordering::Relaxed) as f64 / 1e9;
                format!("{name}={total:.3}")
            })
            .collect();
        eprintln!(
            "files={} parsed={} wall={wall:.3}s out={written}B | cpu-sum {}",
            self.opts.files.len(),
            self.calls[Stage::Extract as usize].load(Ordering::Relaxed),
            seconds.join(" ")
        );
    }

    fn read(&self, rel: &str) -> Option<Vec<u8>> {
        match self.timed(Stage::Read, || std::fs::read(self.opts.root.join(rel))) {
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

    fn extract(&self, raw: &[u8], sha256: String) -> FileRecord {
        self.timed(Stage::Extract, || extract(raw, sha256, &self.opts.config))
    }

    fn encode(&self, record: &FileRecord, path: &str) -> Result<String, ShellError> {
        let line = self.timed(Stage::Serialize, || record_line(record, &self.cfg, path))?;
        Ok(line)
    }

    /// stream: parse every file.
    fn line(&self, rel: &str) -> Result<String, ShellError> {
        let record = match self.read(rel) {
            Some(raw) => self.extract(&raw, sha256_hex(&raw)),
            None => Self::unreadable(),
        };
        self.encode(&record, rel)
    }

    /// cache: reuse the entry for this content hash, else extract and store one.
    fn cached_line(&self, rel: &str, dir: &Path) -> Result<String, ShellError> {
        let Some(raw) = self.read(rel) else {
            return self.encode(&Self::unreadable(), rel);
        };
        let sha256 = sha256_hex(&raw);
        let entry = dir.join(format!("{sha256}.json"));
        if let Ok(text) = self.timed(Stage::CacheIo, || std::fs::read_to_string(&entry)) {
            return with_path(&text, rel);
        }
        let record = self.extract(&raw, sha256);
        let body = self.encode(&record, "")?;
        self.timed(Stage::CacheIo, || {
            self.store(dir, &record.sha256, &entry, &body)
        });
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

fn run_extract(opts: &Options) -> Result<(), ShellError> {
    if opts.jobs > 0 {
        rayon::ThreadPoolBuilder::new()
            .num_threads(opts.jobs)
            .build_global()
            .map_err(|error| usage(format!("--jobs {}: {error}", opts.jobs)))?;
    }
    let runner = Runner::new(opts);
    let started = Instant::now();
    let lines: Vec<String> = match opts.mode {
        OutputMode::Stream => opts
            .files
            .par_iter()
            .map(|rel| runner.line(rel))
            .collect::<Result<_, _>>()?,
        OutputMode::Cache => {
            let Some(base) = &opts.cache else {
                return Err(usage("--mode cache needs --cache DIR"));
            };
            let dir = base.join(opts.config.cache_namespace());
            std::fs::create_dir_all(&dir).map_err(io_error(format!("create {}", dir.display())))?;
            opts.files
                .par_iter()
                .map(|rel| runner.cached_line(rel, &dir))
                .collect::<Result<_, _>>()?
        }
    };
    let written = write_lines(&opts.out, &lines)?;
    if opts.timing {
        runner.report(started.elapsed().as_secs_f64(), written);
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
