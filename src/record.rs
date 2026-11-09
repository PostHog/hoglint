//! One L1 record per file: envelope plus the sorted fact lists (reference `FileRecord`).

use crate::json::{write_str, J};
use crate::value::{Encoder, MAX_HEADER_NODES};
use crate::walker::{DefNode, DefTable, Derived, LineIndex, Row, Walker};
use ruff_python_ast::{Expr, Parameters};
use sha2::{Digest, Sha256};
use ruff_python_ast::PythonVersion;
use ruff_python_parser::{Mode, ParseOptions, Parsed};
use std::time::{Duration, Instant};

/// The oracle is CPython 3.14 `ast`: syntax that ruff gates on a newer version (lazy imports,
/// unpacking in comprehensions) is a syntax error here too.
pub fn parse_py314(text: &str, mode: Mode) -> Parsed<ruff_python_ast::Mod> {
    ruff_python_parser::parse_unchecked(text, ParseOptions::from(mode).with_target_version(PythonVersion::PY314))
}

pub const SCHEMA_VERSION: i64 = 1;

/// Fact list names in record key order (the record is serialized with sorted keys).
pub const KINDS: [&str; 12] = [
    "assigns", "branches", "callees", "calls", "defs", "imports", "names", "refs", "strs", "tags", "uses", "watched",
];

pub struct FileOut {
    pub sha256: String,
    pub bytes: usize,
    pub status: &'static str,
    /// (kind, JSON array text) for each non-empty list, in KINDS order.
    pub lists: Vec<(&'static str, String)>,
}

#[derive(Default, Clone, Copy)]
pub struct Times {
    pub parse: Duration,
    pub extract: Duration,
    pub serialize: Duration,
}

pub fn sha256_hex(raw: &[u8]) -> String {
    let digest = Sha256::digest(raw);
    digest.iter().map(|b| format!("{b:02x}")).collect()
}

pub fn config_id(first_party: &[String]) -> String {
    sha256_hex(first_party.join(",").as_bytes())[..8].to_string()
}

impl FileOut {
    /// The record line. `path` None writes an empty path (cache entries are path-free).
    pub fn to_line(&self, cfg: &str, path: &str) -> String {
        let mut out = String::with_capacity(256 + self.lists.iter().map(|l| l.1.len() + 12).sum::<usize>());
        out.push('{');
        let list = |name: &str| self.lists.iter().find(|l| l.0 == name).map(|l| l.1.as_str());
        let mut first = true;
        let mut key = |out: &mut String, k: &str| {
            if !first {
                out.push(',');
            }
            first = false;
            write_str(out, k);
            out.push(':');
        };
        for name in ["assigns", "branches"] {
            if let Some(v) = list(name) {
                key(&mut out, name);
                out.push_str(v);
            }
        }
        key(&mut out, "bytes");
        out.push_str(&self.bytes.to_string());
        for name in ["callees", "calls"] {
            if let Some(v) = list(name) {
                key(&mut out, name);
                out.push_str(v);
            }
        }
        key(&mut out, "cfg");
        write_str(&mut out, cfg);
        for name in ["defs", "imports", "names"] {
            if let Some(v) = list(name) {
                key(&mut out, name);
                out.push_str(v);
            }
        }
        key(&mut out, "path");
        write_str(&mut out, path);
        if let Some(v) = list("refs") {
            key(&mut out, "refs");
            out.push_str(v);
        }
        key(&mut out, "sha256");
        write_str(&mut out, &self.sha256);
        key(&mut out, "status");
        write_str(&mut out, self.status);
        for name in ["strs", "tags", "uses"] {
            if let Some(v) = list(name) {
                key(&mut out, name);
                out.push_str(v);
            }
        }
        key(&mut out, "v");
        out.push_str(&SCHEMA_VERSION.to_string());
        if let Some(v) = list("watched") {
            key(&mut out, "watched");
            out.push_str(v);
        }
        out.push('}');
        out
    }
}

fn join_rows(mut rows: Vec<Row>) -> String {
    rows.sort_by(|a, b| (a.l, a.c, &a.json).cmp(&(b.l, b.c, &b.json)));
    join(rows.iter().map(|r| r.json.as_str()))
}

fn join<'x>(items: impl Iterator<Item = &'x str>) -> String {
    let mut out = String::from("[");
    for (i, item) in items.enumerate() {
        if i > 0 {
            out.push(',');
        }
        out.push_str(item);
    }
    out.push(']');
    out
}

/// `[*key, count, first line, first col]` sorted by first position, then row text.
fn aggregate_rows(table: &std::collections::HashMap<String, (i64, i64, i64)>) -> Vec<Row> {
    table
        .iter()
        .map(|(prefix, (n, l, c))| Row { l: *l, c: *c, json: format!("{prefix},{n},{l},{c}]") })
        .collect()
}

fn params(enc: &mut Encoder, p: &Parameters, src: &str) -> Vec<J> {
    let mut out: Vec<Vec<J>> = Vec::new();
    for param in p.posonlyargs.iter() {
        out.push(vec![
            J::s(param.parameter.name.as_str()),
            J::s("po"),
            enc.value(param.parameter.annotation.as_deref(), true, MAX_HEADER_NODES, src),
            param.default.as_deref().map(|d| enc.shallow(d, src)).unwrap_or(J::Null),
        ]);
    }
    for param in p.args.iter() {
        out.push(vec![
            J::s(param.parameter.name.as_str()),
            J::s("a"),
            enc.value(param.parameter.annotation.as_deref(), true, MAX_HEADER_NODES, src),
            param.default.as_deref().map(|d| enc.shallow(d, src)).unwrap_or(J::Null),
        ]);
    }
    if let Some(v) = &p.vararg {
        out.push(vec![J::s(v.name.as_str()), J::s("va"), enc.value(v.annotation.as_deref(), true, MAX_HEADER_NODES, src)]);
    }
    for param in p.kwonlyargs.iter() {
        out.push(vec![
            J::s(param.parameter.name.as_str()),
            J::s("ko"),
            enc.value(param.parameter.annotation.as_deref(), true, MAX_HEADER_NODES, src),
            param.default.as_deref().map(|d| enc.shallow(d, src)).unwrap_or(J::Null),
        ]);
    }
    if let Some(k) = &p.kwarg {
        out.push(vec![J::s(k.name.as_str()), J::s("kw"), enc.value(k.annotation.as_deref(), true, MAX_HEADER_NODES, src)]);
    }
    out.into_iter()
        .map(|mut row| {
            while row.last().is_some_and(|j| j.is_null()) {
                row.pop();
            }
            J::Arr(row)
        })
        .collect()
}

/// Optional def keys are left out when empty, null, false or 0.
fn is_blank(v: &J) -> bool {
    match v {
        J::Null | J::Bool(false) | J::Int(0) => true,
        J::Arr(items) => items.is_empty(),
        _ => false,
    }
}

fn def_record(defs: &DefTable, flags: &[u32], idx: usize, src: &str) -> String {
    let entry = &defs.entries[idx];
    let mut enc = Encoder::new();
    let mut pairs: Vec<(&'static str, J)> = vec![
        ("s", J::Int(entry.parent)),
        ("l", J::Int(entry.l)),
        ("c", J::Int(entry.c)),
        ("e", J::Int(entry.e)),
    ];
    let mut optional: Vec<(&'static str, J)> = vec![("f", J::Int(flags[idx] as i64))];
    match entry.node {
        DefNode::Func(f) => {
            pairs.push(("n", J::s(f.name.as_str())));
            pairs.push(("k", J::s(if f.is_async { "async" } else { "def" })));
            optional.push(("dec", J::Arr(f.decorator_list.iter().map(|d| enc.shallow_call(&d.expression, src)).collect())));
            optional.push(("stub", J::Bool(crate::walker::is_stub_body(&f.body))));
            optional.push(("params", J::Arr(params(&mut enc, &f.parameters, src))));
            optional.push(("ret", enc.value(f.returns.as_deref(), true, MAX_HEADER_NODES, src)));
        }
        DefNode::Class(c) => {
            pairs.push(("n", J::s(c.name.as_str())));
            pairs.push(("k", J::s("class")));
            optional.push(("dec", J::Arr(c.decorator_list.iter().map(|d| enc.shallow_call(&d.expression, src)).collect())));
            optional.push(("stub", J::Bool(crate::walker::is_stub_body(&c.body))));
            let (bases, kws): (Vec<&Expr>, Vec<_>) = match &c.arguments {
                Some(a) => (a.args.iter().collect(), a.keywords.iter().collect()),
                None => (Vec::new(), Vec::new()),
            };
            optional.push(("bases", J::Arr(bases.iter().map(|b| enc.value(Some(b), false, MAX_HEADER_NODES, src)).collect())));
            let kw = kws
                .iter()
                .map(|k| {
                    J::Arr(vec![
                        J::opt_s(k.arg.as_ref().map(|a| a.as_str())),
                        enc.value(Some(&k.value), false, MAX_HEADER_NODES, src),
                    ])
                })
                .collect();
            optional.push(("kw", J::Arr(kw)));
        }
    }
    pairs.extend(optional.into_iter().filter(|(_, v)| !is_blank(v)));
    J::obj(pairs).to_json()
}

fn failed(sha256: String, bytes: usize, status: &'static str) -> FileOut {
    FileOut { sha256, bytes, status, lists: Vec::new() }
}

/// Builds the record for one file's bytes.
pub fn extract(raw: &[u8], first_party: &[String], times: &mut Times) -> FileOut {
    let sha256 = sha256_hex(raw);
    let bytes = raw.len();
    let Ok(text) = std::str::from_utf8(raw) else {
        return failed(sha256, bytes, "decode_error");
    };
    let text = text.strip_prefix('\u{feff}').unwrap_or(text);
    if text.contains('\0') {
        return failed(sha256, bytes, "syntax_error");
    }
    // CPython translates \r\n and lone \r to \n before tokenizing; columns stay the same.
    let normalized;
    let text = if text.contains('\r') {
        normalized = text.replace("\r\n", "\n").replace('\r', "\n");
        normalized.as_str()
    } else {
        text
    };
    let t0 = Instant::now();
    let parsed = parse_py314(text, Mode::Module).try_into_module().expect("module mode");
    times.parse += t0.elapsed();
    if parsed.has_syntax_errors() {
        return failed(sha256, bytes, "syntax_error");
    }
    let t1 = Instant::now();
    let suite = parsed.suite();
    let lines = LineIndex::new(text);
    let (defs, bound) = DefTable::build(suite, text, &lines, first_party);
    let mut derived = Derived::new();
    let out = loop {
        let mut w = Walker::new(text, &lines, &defs, &bound, &derived);
        w.run(suite);
        let mut grown = derived.clone();
        for (scope, names) in &w.new_derived {
            grown.entry(*scope).or_default().extend(names.iter().cloned());
        }
        if grown == derived {
            break build_lists(w, &defs, text, sha256, bytes, times, t1);
        }
        drop(w);
        derived = grown;
    };
    out
}

fn build_lists(w: Walker, defs: &DefTable, src: &str, sha256: String, bytes: usize, times: &mut Times, t1: Instant) -> FileOut {
    let def_rows: Vec<String> = (0..defs.entries.len()).map(|i| def_record(defs, &w.def_flags, i, src)).collect();
    times.extract += t1.elapsed();
    let t2 = Instant::now();
    let lists_raw: Vec<(&'static str, Vec<Row>)> = vec![
        ("assigns", w.assigns),
        ("branches", w.branches),
        ("callees", aggregate_rows(&w.callees)),
        ("calls", w.calls),
        ("imports", w.imports),
        ("names", aggregate_rows(&w.names)),
        ("refs", w.refs),
        ("strs", w.strs),
        ("tags", w.tags),
        ("uses", aggregate_rows(&w.uses)),
        ("watched", w.watched),
    ];
    let mut lists: Vec<(&'static str, String)> = Vec::new();
    for (name, rows) in lists_raw {
        if !rows.is_empty() {
            lists.push((name, join_rows(rows)));
        }
    }
    if !def_rows.is_empty() {
        lists.push(("defs", join(def_rows.iter().map(|s| s.as_str()))));
    }
    lists.sort_by_key(|l| l.0);
    times.serialize += t2.elapsed();
    FileOut { sha256, bytes, status: "ok", lists }
}
