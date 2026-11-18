//! The record line: compact JSON with keys in code point order.

use crate::config::SCHEMA_VERSION;
use crate::facts::{
    Assign, Branch, Count, Def, DottedStr, FileRecord, Import, Ref, Status, Tag, TextKey, UseKey,
    WatchedCall, WatchedKey,
};
use serde::Serialize;

/// Field order is the key order of the line.
#[derive(Serialize)]
struct Line<'r> {
    #[serde(skip_serializing_if = "<[_]>::is_empty")]
    assigns: &'r [Assign],
    #[serde(skip_serializing_if = "<[_]>::is_empty")]
    branches: &'r [Branch],
    bytes: usize,
    #[serde(skip_serializing_if = "<[_]>::is_empty")]
    callees: &'r [Count<TextKey>],
    #[serde(skip_serializing_if = "<[_]>::is_empty")]
    calls: &'r [WatchedCall],
    cfg: &'r str,
    #[serde(skip_serializing_if = "<[_]>::is_empty")]
    defs: &'r [Def],
    #[serde(skip_serializing_if = "<[_]>::is_empty")]
    derived: &'r [Ref],
    #[serde(skip_serializing_if = "<[_]>::is_empty")]
    escapes: &'r [Tag],
    #[serde(skip_serializing_if = "<[_]>::is_empty")]
    imports: &'r [Import],
    #[serde(skip_serializing_if = "<[_]>::is_empty")]
    listed: &'r [Count<TextKey>],
    #[serde(skip_serializing_if = "<[_]>::is_empty")]
    names: &'r [Count<TextKey>],
    path: &'r str,
    #[serde(skip_serializing_if = "<[_]>::is_empty")]
    refs: &'r [Ref],
    sha256: &'r str,
    status: Status,
    #[serde(skip_serializing_if = "<[_]>::is_empty")]
    strs: &'r [DottedStr],
    #[serde(skip_serializing_if = "<[_]>::is_empty")]
    tags: &'r [Tag],
    #[serde(skip_serializing_if = "<[_]>::is_empty")]
    uses: &'r [Count<UseKey>],
    v: i64,
    #[serde(skip_serializing_if = "<[_]>::is_empty")]
    watched: &'r [WatchedKey],
}

/// One JSON line for `record` at `path`, under the extractor configuration id `cfg`.
pub fn record_line(record: &FileRecord, cfg: &str, path: &str) -> serde_json::Result<String> {
    let facts = &record.facts;
    let line = Line {
        assigns: &facts.assigns,
        branches: &facts.branches,
        bytes: record.bytes,
        callees: &facts.callees,
        calls: &facts.calls,
        cfg,
        defs: &facts.defs,
        derived: &facts.derived,
        escapes: &facts.escapes,
        imports: &facts.imports,
        listed: &facts.listed,
        names: &facts.names,
        path,
        refs: &facts.refs,
        sha256: &record.sha256,
        status: record.status,
        strs: &facts.strs,
        tags: &facts.tags,
        uses: &facts.uses,
        v: SCHEMA_VERSION,
        watched: &facts.watched,
    };
    serde_json::to_string(&line)
}
