//! Extraction options. `extract` takes one `ExtractConfig`; the core reads no global state.

use sha2::{Digest, Sha256};

pub const SCHEMA_VERSION: i64 = 2;

/// Hash of the extractor sources, from build.rs. Part of the cache key.
pub const BUILD_ID: &str = env!("HOGLINT_BUILD_ID");

/// Element caps of value trees and symbolic strings. Schema v1.1 raises both.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Limits {
    /// Element budget for assignment values and branch tests (header slots use their own cap).
    pub max_nodes: usize,
    /// Longest literal, in code points, that still counts as symbolic for `strs` and `names`.
    pub max_symbolic: usize,
}

impl Limits {
    pub const V1: Limits = Limits {
        max_nodes: 400,
        max_symbolic: 100,
    };
    pub const V1_1: Limits = Limits {
        max_nodes: 1_000_000,
        max_symbolic: 1_000,
    };
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExtractConfig {
    /// Top-level module names whose imports are first-party. Sorted, no duplicates.
    pub first_party: Vec<String>,
    /// Extra callee names (last dotted part) whose calls get a `calls` row. Sorted, no duplicates.
    pub watch_callees: Vec<String>,
    pub limits: Limits,
}

impl ExtractConfig {
    pub fn new(first_party: Vec<String>, watch_callees: Vec<String>, limits: Limits) -> Self {
        ExtractConfig {
            first_party: sorted_unique(first_party),
            watch_callees: sorted_unique(watch_callees),
            limits,
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
        format!(
            "v{SCHEMA_VERSION}-b{BUILD_ID}-{}-n{}-s{}",
            self.config_id(),
            self.limits.max_nodes,
            self.limits.max_symbolic
        )
    }
}

fn sorted_unique(mut items: Vec<String>) -> Vec<String> {
    items.retain(|item| !item.is_empty());
    items.sort();
    items.dedup();
    items
}

pub fn sha256_hex(raw: &[u8]) -> String {
    Sha256::digest(raw)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}
