//! Sets HOGLINT_BUILD_ID: a hash of the extractor sources, manifest and lockfile. The fact
//! cache keys on it, so a changed extractor never reuses records from an older build.

use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};

fn inputs(root: &Path) -> Vec<PathBuf> {
    let mut files: Vec<PathBuf> = std::fs::read_dir(root.join("src"))
        .expect("read src")
        .map(|entry| entry.expect("src entry").path())
        .filter(|path| path.extension().is_some_and(|ext| ext == "rs"))
        .collect();
    files.sort();
    for name in ["Cargo.toml", "Cargo.lock", "build.rs", "rust-toolchain.toml"] {
        files.push(root.join(name));
    }
    files
}

fn main() {
    let root = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").expect("manifest dir"));
    let mut hasher = Sha256::new();
    for path in inputs(&root) {
        println!("cargo:rerun-if-changed={}", path.display());
        let rel = path.strip_prefix(&root).expect("input under the crate");
        hasher.update(rel.to_string_lossy().as_bytes());
        hasher.update([0]);
        hasher.update(std::fs::read(&path).unwrap_or_default());
        hasher.update([0]);
    }
    let digest = hasher.finalize();
    let id: String = digest.iter().take(6).map(|b| format!("{b:02x}")).collect();
    println!("cargo:rustc-env=HOGLINT_BUILD_ID={id}");
}
