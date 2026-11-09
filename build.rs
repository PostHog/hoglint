//! Two ids, both hashes of source text, never of a built binary, so every platform agrees:
//!
//! - HOGLINT_BUILD_ID: the engine id. It hashes every file `engine-sources.txt` lists (Rust
//!   extractor and link, the binding, manifests, the lockfile, the Python engine, presets), as
//!   that file's header describes; hoglint/ledger/envelope.py makes the same id from the same
//!   file. Run summaries key on it.
//! - HOGLINT_FACTS_ID: the extractor id. It hashes the extractor sources (top-level `src/*.rs`)
//!   and the crate files. The fact cache keys on it: facts depend on the extractor only, so a
//!   change to the link or the Python engine keeps every cached fact.

use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};

const ENGINE_SOURCES: &str = "engine-sources.txt";
const PROTOCOL_LINE: &str = "protocol ";
const ENGINE_DIGEST_BYTES: usize = 8;
const FACTS_ID_BYTES: usize = 6;

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// fnmatch for one path segment: `*`, `?`, `[set]` and `[!set]`.
fn segment_match(pattern: &[char], text: &[char]) -> bool {
    match pattern.first() {
        None => text.is_empty(),
        Some('*') => (0..=text.len()).any(|skip| segment_match(&pattern[1..], &text[skip..])),
        Some('?') => !text.is_empty() && segment_match(&pattern[1..], &text[1..]),
        Some('[') => {
            let Some(end) = pattern
                .iter()
                .skip(2)
                .position(|&c| c == ']')
                .map(|at| at + 2)
            else {
                return text.first() == Some(&'[') && segment_match(&pattern[1..], &text[1..]);
            };
            let Some(&c) = text.first() else {
                return false;
            };
            let (negate, set) = match pattern[1] {
                '!' => (true, &pattern[2..end]),
                _ => (false, &pattern[1..end]),
            };
            let mut found = false;
            let mut i = 0;
            while i < set.len() {
                if i + 2 < set.len() && set[i + 1] == '-' {
                    found |= set[i] <= c && c <= set[i + 2];
                    i += 3;
                } else {
                    found |= set[i] == c;
                    i += 1;
                }
            }
            found != negate && segment_match(&pattern[end + 1..], &text[1..])
        }
        Some(&p) => text.first() == Some(&p) && segment_match(&pattern[1..], &text[1..]),
    }
}

/// `**` matches any number of segments, none included; other segments match with fnmatch.
fn glob_match(pattern: &[Vec<char>], path: &[Vec<char>]) -> bool {
    let Some(first) = pattern.first() else {
        return path.is_empty();
    };
    if first.as_slice() == ['*', '*'] {
        return (0..=path.len()).any(|skip| glob_match(&pattern[1..], &path[skip..]));
    }
    !path.is_empty() && segment_match(first, &path[0]) && glob_match(&pattern[1..], &path[1..])
}

fn walk(root: &Path, dir: &Path, out: &mut Vec<String>) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        let name = entry.file_name().to_string_lossy().to_string();
        if path.is_dir() {
            if name != "target" && !name.starts_with('.') {
                walk(root, &path, out);
            }
            continue;
        }
        let rel = path.strip_prefix(root).expect("under the crate");
        out.push(
            rel.components()
                .map(|c| c.as_os_str().to_string_lossy())
                .collect::<Vec<_>>()
                .join("/"),
        );
    }
}

fn engine_id(root: &Path) -> String {
    let listing =
        std::fs::read_to_string(root.join(ENGINE_SOURCES)).expect("read engine-sources.txt");
    let mut protocol = String::new();
    let mut patterns: Vec<Vec<Vec<char>>> = Vec::new();
    for raw in listing.lines() {
        let line = raw.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if let Some(version) = line.strip_prefix(PROTOCOL_LINE) {
            protocol = version.trim().to_string();
            continue;
        }
        patterns.push(line.split('/').map(|part| part.chars().collect()).collect());
    }
    let mut files = Vec::new();
    walk(root, root, &mut files);
    files.retain(|rel| {
        let parts: Vec<Vec<char>> = rel.split('/').map(|part| part.chars().collect()).collect();
        patterns.iter().any(|pattern| glob_match(pattern, &parts))
    });
    files.sort();
    let mut hasher = Sha256::new();
    hasher.update(format!("{PROTOCOL_LINE}{protocol}").as_bytes());
    hasher.update([0]);
    for rel in &files {
        let path = root.join(rel);
        println!("cargo:rerun-if-changed={}", path.display());
        hasher.update(rel.as_bytes());
        hasher.update([0]);
        hasher.update(std::fs::read(&path).unwrap_or_default());
        hasher.update([0]);
    }
    hex(&hasher.finalize()[..ENGINE_DIGEST_BYTES])
}

fn facts_inputs(root: &Path) -> Vec<PathBuf> {
    let mut files: Vec<PathBuf> = std::fs::read_dir(root.join("src"))
        .expect("read src")
        .map(|entry| entry.expect("src entry").path())
        .filter(|path| path.extension().is_some_and(|ext| ext == "rs"))
        .collect();
    files.sort();
    for name in [
        "Cargo.toml",
        "Cargo.lock",
        "build.rs",
        "rust-toolchain.toml",
    ] {
        files.push(root.join(name));
    }
    files
}

fn facts_id(root: &Path) -> String {
    let mut hasher = Sha256::new();
    for path in facts_inputs(root) {
        let rel = path.strip_prefix(root).expect("input under the crate");
        hasher.update(rel.to_string_lossy().as_bytes());
        hasher.update([0]);
        hasher.update(std::fs::read(&path).unwrap_or_default());
        hasher.update([0]);
    }
    hex(&hasher.finalize()[..FACTS_ID_BYTES])
}

fn main() {
    let root = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").expect("manifest dir"));
    // A directory entry makes cargo rerun this script when a file is added or removed in it.
    for dir in ["src", "binding", "hoglint", "presets"] {
        println!("cargo:rerun-if-changed={}", root.join(dir).display());
    }
    println!(
        "cargo:rerun-if-changed={}",
        root.join(ENGINE_SOURCES).display()
    );
    println!("cargo:rustc-env=HOGLINT_BUILD_ID={}", engine_id(&root));
    println!("cargo:rustc-env=HOGLINT_FACTS_ID={}", facts_id(&root));
}
