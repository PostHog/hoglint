//! Link flags and the engine's source digest.
//!
//! A plain `cargo build` of the extension needs the link flags maturin passes
//! (`-undefined dynamic_lookup` on macOS): the interpreter provides the Python symbols.
//!
//! `HOGLINT_NATIVE_SOURCES` is a digest of every source the native engine is built from: the
//! core crate (extractor and link), this binding, the manifests and the lockfile. It hashes
//! text, never the built library, so Linux and macOS builds of one commit get the same value
//! and a run summary from one can serve the other.

use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};

fn walk(dir: &Path, out: &mut Vec<PathBuf>) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            walk(&path, out);
        } else if path.extension().is_some_and(|ext| ext == "rs") {
            out.push(path);
        }
    }
}

fn sources(crate_dir: &Path) -> Vec<PathBuf> {
    let core = crate_dir
        .parent()
        .expect("the binding sits inside the core crate");
    let mut files = Vec::new();
    walk(&core.join("src"), &mut files);
    walk(&crate_dir.join("src"), &mut files);
    for name in [
        "Cargo.toml",
        "Cargo.lock",
        "build.rs",
        "rust-toolchain.toml",
    ] {
        files.push(core.join(name));
    }
    for name in [
        "Cargo.toml",
        "build.rs",
        "pyproject.toml",
        "hoglint_native.pyi",
    ] {
        files.push(crate_dir.join(name));
    }
    files
}

fn main() {
    pyo3_build_config::add_extension_module_link_args();
    let crate_dir = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").expect("manifest dir"));
    let core = crate_dir.parent().expect("core crate").to_path_buf();
    println!("cargo:rerun-if-changed={}", core.join("src").display());
    println!("cargo:rerun-if-changed={}", crate_dir.join("src").display());
    let mut named: Vec<(String, PathBuf)> = sources(&crate_dir)
        .into_iter()
        .map(|path| {
            let rel = path.strip_prefix(&core).expect("under the core crate");
            let rel = rel
                .components()
                .map(|c| c.as_os_str().to_string_lossy())
                .collect::<Vec<_>>()
                .join("/");
            (rel, path)
        })
        .collect();
    named.sort();
    let mut hasher = Sha256::new();
    for (rel, path) in &named {
        println!("cargo:rerun-if-changed={}", path.display());
        hasher.update(rel.as_bytes());
        hasher.update([0]);
        hasher.update(std::fs::read(path).unwrap_or_default());
        hasher.update([0]);
    }
    let digest: String = hasher
        .finalize()
        .iter()
        .take(8)
        .map(|b| format!("{b:02x}"))
        .collect();
    println!("cargo:rustc-env=HOGLINT_NATIVE_SOURCES={digest}");
}
