//! L1 fact extractor (schema v3, see schema.md) on the ruff parser: the pure core.
//!
//! `extract` turns one file's bytes into a typed `FileRecord`; `record_line` encodes it.
//! Nothing here reads files, the clock or global state. The `hoglint-facts` binary is the
//! shell: arguments, file reading, the content-hash cache, output and the thread pool.
//! Output matches reference_l1.py byte for byte, line for line.

mod defs;
mod derive;
mod encode;
mod extract;
mod facts;
mod path;
mod source;
mod strings;
mod value;
mod walker;

pub mod config;
pub mod link;

pub use config::{BUILD_ID, ExtractConfig, Kind, Kinds, SCHEMA_VERSION, sha256_hex};
pub use encode::record_line;
pub use extract::extract;
pub use facts::{
    Assign, Branch, Count, Def, DefKind, DottedStr, Facts, FileRecord, Import, Param, Ref, Site,
    Status, Tag, TextKey, UseKey, WatchedCall, WatchedKey, flags,
};
pub use value::{Keyword, Value};

#[cfg(test)]
mod tests;
