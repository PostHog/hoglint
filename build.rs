//! Sets HOGLINT_FACTS_ID, HOGLINT_NATIVE_ID and HOGLINT_ENGINE_ID: the ids of the `[facts]`,
//! `[native]` and `[engine]` source sets that engine-sources.txt defines (see src/sources.rs).
//! They hash source text, never a built binary, so every platform agrees. The fact cache keys on
//! the facts id, so a link or Python change never re-extracts; a loaded module must report the
//! native id of its sources; an installed engine reports the engine id (`--build-id`).

#[path = "src/sources.rs"]
mod sources;

use sources::{ENGINE, FACTS, NATIVE, SourceList};
use std::path::Path;

/// Crate-relative `/` paths of every file, skipping directories named `target` or starting `.`.
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

fn main() {
    let root = std::path::PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").expect("manifest dir"));
    let listing = root.join("engine-sources.txt");
    println!("cargo:rerun-if-changed={}", listing.display());
    // A directory entry makes cargo rerun this script when a file is added or removed in it.
    for dir in ["src", "binding", "hoglint", "presets"] {
        println!("cargo:rerun-if-changed={}", root.join(dir).display());
    }
    let list =
        SourceList::parse(&std::fs::read_to_string(&listing).expect("read engine-sources.txt"))
            .expect("parse engine-sources.txt");
    let mut paths = Vec::new();
    walk(&root, &root, &mut paths);
    let ids = [
        (FACTS, "HOGLINT_FACTS_ID"),
        (NATIVE, "HOGLINT_NATIVE_ID"),
        (ENGINE, "HOGLINT_ENGINE_ID"),
    ];
    for (section, name) in ids {
        let files: Vec<(String, Vec<u8>)> = list
            .files(section, &paths)
            .into_iter()
            .map(|rel| {
                let path = root.join(&rel);
                println!("cargo:rerun-if-changed={}", path.display());
                let bytes = std::fs::read(&path).unwrap_or_default();
                (rel, bytes)
            })
            .collect();
        println!("cargo:rustc-env={name}={}", list.digest(section, &files));
    }
}
