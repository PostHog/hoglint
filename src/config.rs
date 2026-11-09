//! Extraction options. `extract` takes one `ExtractConfig`; the core reads no global state.

use sha2::{Digest, Sha256};

pub const SCHEMA_VERSION: i64 = 2;

/// Hash of the extractor sources, from build.rs. Part of the cache key.
pub const BUILD_ID: &str = env!("HOGLINT_BUILD_ID");

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExtractConfig {
    /// Top-level module names whose imports are first-party. Sorted, no duplicates.
    pub first_party: Vec<String>,
    /// Extra callee names (last dotted part) whose calls get a `calls` row. Sorted, no duplicates.
    pub watch_callees: Vec<String>,
}

impl ExtractConfig {
    pub fn new(first_party: Vec<String>, watch_callees: Vec<String>) -> Self {
        ExtractConfig {
            first_party: sorted_unique(first_party),
            watch_callees: sorted_unique(watch_callees),
        }
    }

    pub fn is_first_party(&self, module: &str) -> bool {
        let head = module.split('.').next().unwrap_or("");
        self.first_party.iter().any(|root| root == head)
    }

    /// Short hash of the options that change record content: the record's `cfg`. Without
    /// extra callees it keeps the first-party-only value, so older records keep their `cfg`.
    pub fn config_id(&self) -> String {
        let mut key = self.first_party.join(",");
        if !self.watch_callees.is_empty() {
            key.push('|');
            key.push_str(&self.watch_callees.join(","));
        }
        sha256_hex(key.as_bytes())[..8].to_string()
    }

    /// Cache directory name: schema version, build id and every option that changes records.
    pub fn cache_namespace(&self) -> String {
        format!("v{SCHEMA_VERSION}-b{BUILD_ID}-{}", self.config_id())
    }
}

fn sorted_unique(mut items: Vec<String>) -> Vec<String> {
    items.retain(|item| !item.is_empty());
    items.sort();
    items.dedup();
    items
}

pub fn sha256_hex(raw: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let digest = Sha256::digest(raw);
    let mut out = String::with_capacity(digest.len() * 2);
    for byte in digest {
        out.push(HEX[usize::from(byte >> 4)] as char);
        out.push(HEX[usize::from(byte & 0x0f)] as char);
    }
    out
}
