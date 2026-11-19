//! Extraction options. `extract` takes one `ExtractConfig`; the core reads no global state.

use sha2::{Digest, Sha256};

pub const SCHEMA_VERSION: i64 = 3;

/// The id of the `[facts]` sources (the extractor), from build.rs. The fact cache keys on it.
pub const FACTS_ID: &str = env!("HOGLINT_FACTS_ID");
/// The id of the `[native]` sources (extractor, link, binding), from build.rs.
pub const NATIVE_ID: &str = env!("HOGLINT_NATIVE_ID");
/// The id of the `[engine]` sources (everything, Python included), from build.rs.
pub const ENGINE_ID: &str = env!("HOGLINT_ENGINE_ID");

/// One fact list of a record (`src/facts.rs`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Assigns,
    Branches,
    Callees,
    Calls,
    Computed,
    Defs,
    Derived,
    Escapes,
    Imports,
    Listed,
    Names,
    Refs,
    Strs,
    Tags,
    Uses,
    Watched,
}

impl Kind {
    /// In name order, which is also the order `cfg` lists them in.
    pub const ALL: [Kind; 16] = [
        Kind::Assigns,
        Kind::Branches,
        Kind::Callees,
        Kind::Calls,
        Kind::Computed,
        Kind::Defs,
        Kind::Derived,
        Kind::Escapes,
        Kind::Imports,
        Kind::Listed,
        Kind::Names,
        Kind::Refs,
        Kind::Strs,
        Kind::Tags,
        Kind::Uses,
        Kind::Watched,
    ];

    pub fn name(self) -> &'static str {
        match self {
            Kind::Assigns => "assigns",
            Kind::Branches => "branches",
            Kind::Callees => "callees",
            Kind::Calls => "calls",
            Kind::Computed => "computed",
            Kind::Defs => "defs",
            Kind::Derived => "derived",
            Kind::Escapes => "escapes",
            Kind::Imports => "imports",
            Kind::Listed => "listed",
            Kind::Names => "names",
            Kind::Refs => "refs",
            Kind::Strs => "strs",
            Kind::Tags => "tags",
            Kind::Uses => "uses",
            Kind::Watched => "watched",
        }
    }

    fn bit(self) -> u32 {
        1 << (self as u32)
    }
}

/// A set of fact kinds: the lists a record carries.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Kinds(u32);

impl Kinds {
    pub const ALL: Kinds = Kinds((1 << Kind::ALL.len()) - 1);

    pub fn of(kinds: &[Kind]) -> Kinds {
        Kinds(kinds.iter().fold(0, |bits, kind| bits | kind.bit()))
    }

    /// Kind names as given on the command line; an empty list means every kind.
    pub fn parse(names: &[String]) -> Result<Kinds, String> {
        if names.is_empty() {
            return Ok(Kinds::ALL);
        }
        let mut kinds = Vec::new();
        for name in names {
            let Some(kind) = Kind::ALL.into_iter().find(|kind| kind.name() == name) else {
                return Err(format!("unknown fact kind {name:?}"));
            };
            kinds.push(kind);
        }
        Ok(Kinds::of(&kinds))
    }

    pub fn has(self, kind: Kind) -> bool {
        self.0 & kind.bit() != 0
    }

    pub fn any(self, kinds: &[Kind]) -> bool {
        self.0 & Kinds::of(kinds).0 != 0
    }

    fn names(self) -> Vec<&'static str> {
        Kind::ALL
            .into_iter()
            .filter(|kind| self.has(*kind))
            .map(Kind::name)
            .collect()
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExtractConfig {
    /// Top-level module names whose imports are first-party. Sorted, no duplicates.
    pub first_party: Vec<String>,
    /// A call whose callee's last dotted part equals one of these gets a `calls` row.
    pub watch_callees: Vec<String>,
    /// A call whose callee's last dotted part starts with one of these gets a `calls` row.
    pub watch_prefixes: Vec<String>,
    /// A dict entry whose key is one of these str constants gets a `watched` row.
    pub watch_keys: Vec<String>,
    /// Hook names: a call whose callee's last dotted part is one, or that calls a name a file
    /// imports one as (`from m import NAME as ALIAS`), gets a `calls` row, in which a call
    /// argument keeps its callee (`{"call": chain, "o": "Call"}`).
    pub watch_hooks: Vec<String>,
    /// Full names of callees a call reaches through any import alias and still gets a `calls`
    /// row (`from importlib import import_module as load`): the loaders.
    pub watch_aliased: Vec<String>,
    pub kinds: Kinds,
}

impl ExtractConfig {
    /// Every fact kind, nothing watched.
    pub fn new(first_party: Vec<String>) -> Self {
        ExtractConfig {
            first_party: sorted_unique(first_party),
            watch_callees: Vec::new(),
            watch_prefixes: Vec::new(),
            watch_keys: Vec::new(),
            watch_hooks: Vec::new(),
            watch_aliased: Vec::new(),
            kinds: Kinds::ALL,
        }
    }

    pub fn watching(
        mut self,
        callees: Vec<String>,
        prefixes: Vec<String>,
        keys: Vec<String>,
    ) -> Self {
        self.watch_callees = sorted_unique(callees);
        self.watch_prefixes = sorted_unique(prefixes);
        self.watch_keys = sorted_unique(keys);
        self
    }

    pub fn watching_hooks(mut self, names: Vec<String>) -> Self {
        self.watch_hooks = sorted_unique(names);
        self
    }

    pub fn watching_aliased(mut self, names: Vec<String>) -> Self {
        self.watch_aliased = sorted_unique(names);
        self
    }

    /// Whether `name` is a hook name (`watch_hooks`).
    pub fn is_hook(&self, name: &str) -> bool {
        self.watch_hooks.iter().any(|hook| hook == name)
    }

    pub fn with_kinds(mut self, kinds: Kinds) -> Self {
        self.kinds = kinds;
        self
    }

    pub fn is_first_party(&self, module: &str) -> bool {
        let head = module.split('.').next().unwrap_or("");
        self.first_party.iter().any(|root| root == head)
    }

    /// Whether a call whose callee ends in `last` gets a `calls` row.
    pub fn is_watched_callee(&self, last: &str) -> bool {
        self.watch_callees.iter().any(|name| name == last)
            || self
                .watch_prefixes
                .iter()
                .any(|prefix| last.starts_with(prefix.as_str()))
    }

    /// Short hash of every option that changes record content: the record's `cfg`.
    pub fn config_id(&self) -> String {
        let mut parts = vec![
            self.first_party.join(","),
            self.watch_callees.join(","),
            self.watch_prefixes.join(","),
            self.watch_keys.join(","),
            self.kinds.names().join(","),
        ];
        // Only when set, so a config without it keeps the id it had.
        if !self.watch_hooks.is_empty() {
            parts.push(format!("hooks:{}", self.watch_hooks.join(",")));
        }
        if !self.watch_aliased.is_empty() {
            parts.push(format!("aliased:{}", self.watch_aliased.join(",")));
        }
        sha256_hex(parts.join("|").as_bytes())[..8].to_string()
    }

    /// Cache directory name: schema version, extractor id and every option that changes records.
    pub fn cache_namespace(&self) -> String {
        format!("v{SCHEMA_VERSION}-b{FACTS_ID}-{}", self.config_id())
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
