//! hoglint-facts: the shell around the `hoglint_facts` core. Reads files, runs `extract` on a
//! thread pool, keeps the content-hash cache and writes records.
//!
//! Usage:
//!   hoglint-facts --facts-id | --native-id | --build-id
//!   hoglint-facts <repo_root> <file_list|-> [--jobs N] [--first-party a,b]
//!      [--watch-callees a,b] [--watch-prefixes a,b] [--watch-keys a,b] [--watch-hooks a,b]
//!      [--kinds k,k] [--lite-list PATH] [--lite-kinds k,k]
//!      [--mode stream|cache] [--out PATH] [--cache DIR] [--timing]
//!
//! `--watch-callees` / `--watch-prefixes`: a call whose callee's last dotted part equals / starts
//! with one of these gets a `calls` row. `--watch-keys`: a dict entry whose key is one of these
//! str constants gets a `watched` row. `--watch-hooks`: a call of one of these, also through an
//! import alias (`from m import a as b`, then `b(...)`), gets a `calls` row in which a call
//! argument keeps its callee. All empty by default.
//! `--kinds` names the fact lists records carry (default: all). Files listed in `--lite-list`
//! (a subset of the main list, same format) carry only `--lite-kinds` (default:
//! imports,defs,assigns). Both kind sets are part of the record `cfg`.
//! `--timing` prints wall time, output size and per-stage CPU seconds to stderr.
//!
//! Modes: `stream` writes JSON lines to --out (default stdout); `cache` keeps one record per
//! source sha256 under --cache and only parses files whose content has no entry yet (records
//! still stream to --out). The cache directory name holds the schema version, the extractor id
//! (FACTS_ID) and the record `cfg`. `--facts-id`, `--native-id` and `--build-id` print the ids of
//! the `[facts]`, `[native]` and `[engine]` sources (see build.rs).
//!
//! A file that cannot be read gets a `read_error` record and the run goes on. Bad arguments and
//! an unwritable output exit non-zero.

use hoglint_facts::{
    ENGINE_ID, ExtractConfig, FACTS_ID, Facts, FileRecord, Kinds, NATIVE_ID, Status, extract,
    record_line, sha256_hex,
};
use rayon::prelude::*;
use std::collections::HashSet;
use std::fmt;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Instant;

/// Cache entries store this path; a hit swaps in the real one.
const PATH_PLACEHOLDER: &str = "\"path\":\"\"";

const DEFAULT_LITE_KINDS: &str = "imports,defs,assigns";

#[derive(Debug)]
enum ShellError {
    Usage(String),
    Io { what: String, error: std::io::Error },
    Encode(serde_json::Error),
}

impl fmt::Display for ShellError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ShellError::Usage(message) => write!(f, "usage: {message}"),
            ShellError::Io { what, error } => write!(f, "{what}: {error}"),
            ShellError::Encode(error) => write!(f, "encode a record: {error}"),
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

/// One path per non-blank line; `-` reads stdin.
fn read_list(listing: &str) -> Result<Vec<String>, ShellError> {
    let text = if listing == "-" {
        std::io::read_to_string(std::io::stdin()).map_err(io_error("read the file list"))?
    } else {
        std::fs::read_to_string(listing).map_err(io_error(format!("read {listing}")))?
    };
    Ok(text
        .lines()
        .filter(|l| !l.trim().is_empty())
        .map(str::to_string)
        .collect())
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
    /// Paths that carry only the lite kinds, and the configuration they are extracted with.
    lite: Option<(HashSet<String>, ExtractConfig)>,
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
        Ok(split_list(&self.value(flag)?.unwrap_or_default()))
    }

    fn kinds(&mut self, flag: &str, default: &str) -> Result<Kinds, ShellError> {
        let text = self.value(flag)?.unwrap_or_else(|| default.to_string());
        Kinds::parse(&split_list(&text)).map_err(|error| usage(format!("{flag}: {error}")))
    }
}

fn split_list(text: &str) -> Vec<String> {
    text.split(',')
        .filter(|item| !item.is_empty())
        .map(str::to_string)
        .collect()
}

impl Options {
    fn parse(argv: Vec<String>) -> Result<Options, ShellError> {
        let mut args = Args { rest: argv };
        let jobs = args.number("--jobs")?.unwrap_or(0);
        let first_party = args.list("--first-party")?;
        let callees = args.list("--watch-callees")?;
        let prefixes = args.list("--watch-prefixes")?;
        let keys = args.list("--watch-keys")?;
        let hooks = args.list("--watch-hooks")?;
        let kinds = args.kinds("--kinds", "")?;
        let lite_list = args.value("--lite-list")?;
        let lite_kinds = args.kinds("--lite-kinds", DEFAULT_LITE_KINDS)?;
        let mode = match args.value("--mode")?.as_deref() {
            None | Some("stream") => OutputMode::Stream,
            Some("cache") => OutputMode::Cache,
            Some(other) => return Err(usage(format!("unknown --mode {other}"))),
        };
        let out = args.value("--out")?.map(PathBuf::from);
        let cache = args.value("--cache")?.map(PathBuf::from);
        let timing = args.switch("--timing");
        if let Some(flag) = args.rest.iter().find(|a| a.starts_with("--")) {
            return Err(usage(format!("unknown option {flag}")));
        }
        let [root, listing] = args.rest.as_slice() else {
            return Err(usage("hoglint-facts <repo_root> <file_list|-> [options]"));
        };
        let files = read_list(listing)?;
        let config = ExtractConfig::new(first_party)
            .watching(callees, prefixes, keys)
            .watching_hooks(hooks);
        let lite = match lite_list {
            None => None,
            Some(listing) => {
                let paths: HashSet<String> = read_list(&listing)?.into_iter().collect();
                let listed: HashSet<&str> = files.iter().map(String::as_str).collect();
                if let Some(missing) = paths.iter().find(|p| !listed.contains(p.as_str())) {
                    return Err(usage(format!(
                        "--lite-list path {missing} is not in the file list"
                    )));
                }
                Some((paths, config.clone().with_kinds(lite_kinds)))
            }
        };
        Ok(Options {
            root: PathBuf::from(root),
            files,
            config: config.with_kinds(kinds),
            lite,
            mode,
            out,
            cache,
            timing,
            jobs,
        })
    }
}

/// How one file is extracted: the configuration, its `cfg` id and, in cache mode, its cache
/// directory.
struct Profile<'o> {
    config: &'o ExtractConfig,
    cfg: String,
    dir: Option<PathBuf>,
}

impl<'o> Profile<'o> {
    fn new(config: &'o ExtractConfig, cache: Option<&Path>) -> Result<Self, ShellError> {
        let dir = cache.map(|base| base.join(config.cache_namespace()));
        if let Some(dir) = &dir {
            std::fs::create_dir_all(dir).map_err(io_error(format!("create {}", dir.display())))?;
        }
        Ok(Profile {
            config,
            cfg: config.config_id(),
            dir,
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
    full: Profile<'o>,
    lite: Option<Profile<'o>>,
    tmp_counter: AtomicU64,
    /// Per stage: calls and nanoseconds. Only `--timing` fills them.
    calls: [AtomicU64; 4],
    nanos: [AtomicU64; 4],
}

impl<'o> Runner<'o> {
    fn new(opts: &'o Options) -> Result<Self, ShellError> {
        let cache = match opts.mode {
            OutputMode::Stream => None,
            OutputMode::Cache => match &opts.cache {
                Some(base) => Some(base.as_path()),
                None => return Err(usage("--mode cache needs --cache DIR")),
            },
        };
        let lite = match &opts.lite {
            Some((_, config)) => Some(Profile::new(config, cache)?),
            None => None,
        };
        Ok(Runner {
            opts,
            full: Profile::new(&opts.config, cache)?,
            lite,
            tmp_counter: AtomicU64::new(0),
            calls: Default::default(),
            nanos: Default::default(),
        })
    }

    fn profile(&self, rel: &str) -> &Profile<'o> {
        match (&self.opts.lite, &self.lite) {
            (Some((paths, _)), Some(lite)) if paths.contains(rel) => lite,
            _ => &self.full,
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

    fn extract(&self, raw: &[u8], sha256: String, profile: &Profile) -> FileRecord {
        self.timed(Stage::Extract, || extract(raw, sha256, profile.config))
    }

    fn encode(&self, record: &FileRecord, cfg: &str, path: &str) -> Result<String, ShellError> {
        let line = self.timed(Stage::Serialize, || record_line(record, cfg, path))?;
        Ok(line)
    }

    /// The record line of one file: from the cache when its profile has a cache directory.
    fn line(&self, rel: &str) -> Result<String, ShellError> {
        let profile = self.profile(rel);
        let Some(raw) = self.read(rel) else {
            return self.encode(&Self::unreadable(), &profile.cfg, rel);
        };
        let sha256 = sha256_hex(&raw);
        match &profile.dir {
            None => {
                let record = self.extract(&raw, sha256, profile);
                self.encode(&record, &profile.cfg, rel)
            }
            Some(dir) => self.cached_line(rel, &raw, sha256, profile, dir),
        }
    }

    /// Reuses the entry for this content hash, else extracts and stores one.
    fn cached_line(
        &self,
        rel: &str,
        raw: &[u8],
        sha256: String,
        profile: &Profile,
        dir: &Path,
    ) -> Result<String, ShellError> {
        let entry = dir.join(format!("{sha256}.json"));
        if let Ok(text) = self.timed(Stage::CacheIo, || std::fs::read_to_string(&entry)) {
            return with_path(&text, rel);
        }
        let record = self.extract(raw, sha256, profile);
        let body = self.encode(&record, &profile.cfg, "")?;
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
    let runner = Runner::new(opts)?;
    let started = Instant::now();
    let lines: Vec<String> = opts
        .files
        .par_iter()
        .map(|rel| runner.line(rel))
        .collect::<Result<_, _>>()?;
    let written = write_lines(&opts.out, &lines)?;
    if opts.timing {
        runner.report(started.elapsed().as_secs_f64(), written);
    }
    Ok(())
}

fn run(argv: Vec<String>) -> Result<(), ShellError> {
    if argv.first().map(String::as_str) == Some("--facts-id") {
        println!("{FACTS_ID}");
        return Ok(());
    }
    if argv.first().map(String::as_str) == Some("--build-id") {
        println!("{ENGINE_ID}");
        return Ok(());
    }
    if argv.first().map(String::as_str) == Some("--native-id") {
        println!("{NATIVE_ID}");
        return Ok(());
    }
    run_extract(&Options::parse(argv)?)
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
