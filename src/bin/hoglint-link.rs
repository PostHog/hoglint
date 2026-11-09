//! hoglint-link: a debug shell around the native link. Reads a facts JSONL file (what
//! `hoglint-facts` writes) and a link config JSON, links, and writes the core edges and symbols
//! in the parity tool's normalized form.
//!
//! Usage: hoglint-link <facts.jsonl> <config.json> [--edges PATH] [--symbols PATH] [--timing]

use hoglint_facts::link::facts::decode_lines;
use hoglint_facts::link::intern::Interner;
use hoglint_facts::link::{Link, LinkConfig};
use std::io::Write;
use std::process::ExitCode;
use std::time::Instant;

fn value_of(args: &[String], flag: &str) -> Option<String> {
    args.iter()
        .position(|a| a == flag)
        .and_then(|at| args.get(at + 1))
        .cloned()
}

fn write_lines(path: &str, lines: impl Iterator<Item = serde_json::Value>) -> Result<(), String> {
    let file = std::fs::File::create(path).map_err(|e| format!("create {path}: {e}"))?;
    let mut out = std::io::BufWriter::new(file);
    for line in lines {
        writeln!(out, "{line}").map_err(|e| format!("write {path}: {e}"))?;
    }
    out.flush().map_err(|e| format!("write {path}: {e}"))
}

fn run(args: &[String]) -> Result<(), String> {
    let [facts_path, config_path, ..] = args else {
        return Err("usage: hoglint-link <facts.jsonl> <config.json> [--edges PATH] [--symbols PATH] [--timing]".into());
    };
    let started = Instant::now();
    let config: LinkConfig = serde_json::from_slice(
        &std::fs::read(config_path).map_err(|e| format!("read {config_path}: {e}"))?,
    )
    .map_err(|e| format!("config: {e}"))?;
    let text = std::fs::read(facts_path).map_err(|e| format!("read {facts_path}: {e}"))?;
    let read = started.elapsed().as_secs_f64();
    let mut it = Interner::new();
    let facts = decode_lines(&mut it, text).map_err(|e| e.to_string())?;
    let decoded = started.elapsed().as_secs_f64();
    let link = Link::build(&config, it, facts)?;
    let linked = started.elapsed().as_secs_f64();
    if let Some(path) = value_of(args, "--edges") {
        write_lines(&path, (0..link.edges.rows.len()).map(|i| link.edge_json(i)))?;
    }
    if let Some(path) = value_of(args, "--symbols") {
        let mut lines = vec![serde_json::json!({"first_party": link.first_party_heads()})];
        lines.push(serde_json::json!({"modules": link.modules_json()}));
        lines.extend(link.symbols_json());
        write_lines(&path, lines.into_iter())?;
    }
    if args.iter().any(|a| a == "--timing") {
        let stages: Vec<String> = link
            .timings
            .stages
            .iter()
            .map(|(n, s)| format!("{n}={s:.3}"))
            .collect();
        eprintln!(
            "read={read:.3} decode={:.3} link={:.3} [{}] edges={} strings={}",
            decoded - read,
            linked - decoded,
            stages.join(" "),
            link.edges.rows.len(),
            link.core.it.len()
        );
    }
    Ok(())
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match run(&args) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("hoglint-link: {error}");
            ExitCode::FAILURE
        }
    }
}
