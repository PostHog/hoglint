//! Schema-v3 record lines decoded into what the link reads. The line text stays, so callers can
//! hand any fact list to Python exactly as the extractor wrote it.

use super::intern::{Interner, Sid};
use rayon::prelude::*;
use serde_json::Value as J;
use serde_json::value::RawValue;
use std::collections::BTreeMap;
use std::ops::Range;

/// A value tree (`src/value.rs`), reduced to the forms resolution reads.
#[derive(Debug, Clone)]
pub enum V {
    Null,
    /// A name or attribute chain rooted at a name: `a.b.c`.
    Chain(Sid),
    /// `{"s": text}`, with `sa` (the parsed text of a string annotation) when the key is there.
    Str(Sid, Option<Box<V>>),
    /// `{"call": callee, ...}`, also the over-budget form `{"call": callee, "o": "big"}`; true
    /// when the keywords pass `frozen=True` (a frozen dataclass decorator, for example); the
    /// argument a reader needs: the one positional argument of a copy or a builtin constructor
    /// (`sorted(_LAZY)`), or what a `TypeVar` call declares (`type_argument`).
    Call(Box<V>, bool, Option<Box<V>>),
    Attr(Box<V>, Sid),
    Sub(Box<V>, Box<V>),
    Tuple(Vec<V>),
    List(Vec<V>),
    Set(Vec<V>),
    Dict(Vec<(Option<V>, V)>),
    Star(Box<V>),
    /// `{"bin": op, "l": .., "r": ..}`; the flag is whether `op` is `|`.
    Bin(bool, Box<V>, Box<V>),
    Opaque(Sid),
    /// A number or a bool, written as itself.
    Scalar,
    /// `{"un": op, "v": ..}`: a unary operation.
    Unary(Box<V>),
    /// `{"bool": op, "vs": [..]}`: a boolean operation.
    BoolOp(Vec<V>),
    /// `{"cmp": .., "cs": [..], "ops": [..]}`: a comparison, without its operators.
    Compare(Box<V>, Vec<V>),
    Other,
}

impl V {
    pub fn is_null(&self) -> bool {
        matches!(self, V::Null)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DefKind {
    Def,
    Async,
    Class,
}

impl DefKind {
    pub fn name(self) -> &'static str {
        match self {
            DefKind::Def => "def",
            DefKind::Async => "async",
            DefKind::Class => "class",
        }
    }
}

pub struct Import {
    pub scope: i32,
    pub line: u32,
    pub col: u32,
    pub flags: u32,
    pub target: Option<Sid>,
    pub level: u32,
    pub name: Option<Sid>,
    pub asname: Option<Sid>,
}

pub struct Param {
    pub name: Sid,
    /// The annotation; None when absent or null.
    pub annotation: Option<V>,
    /// A positional parameter (`po` or `a`), not `*args`, keyword-only or `**kwargs`.
    pub positional: bool,
}

/// An attribute a method assigns on its first parameter: `Def::attrs`.
pub struct SelfAttr {
    pub line: u32,
    pub name: Sid,
    pub value: V,
    pub ann: Option<V>,
}

pub struct Def {
    pub s: i32,
    pub l: u32,
    pub c: u32,
    pub f: u32,
    pub kind: DefKind,
    pub n: Sid,
    pub bases: Vec<V>,
    pub dec: Vec<V>,
    pub params: Vec<Param>,
    pub ret: Option<V>,
    pub stub: bool,
    /// A method's assignments to attributes of its first parameter, in source order.
    pub attrs: Vec<SelfAttr>,
    /// The PEP 695 type parameters, each with its bound or its tuple of constraints.
    pub type_params: Vec<(Sid, Option<V>)>,
}

impl Def {
    pub fn is_class(&self) -> bool {
        self.kind == DefKind::Class
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Op {
    Assign,
    Annotated,
    TypeAlias,
    Other,
    /// `+=`, the one augmented assignment a reader of `__all__` follows.
    AddAssign,
}

pub struct Assign {
    pub s: i32,
    pub l: u32,
    pub c: u32,
    pub f: u32,
    pub op: Op,
    pub targets: Vec<V>,
    /// The `v` key; the extractor leaves it out when the value is null.
    pub value: Option<V>,
    /// The `ann` key of an annotated assignment; Some(V::Null) when it encodes to null.
    pub ann: Option<V>,
}

pub struct RefRow {
    pub scope: i32,
    pub header: i32,
    pub line: u32,
    pub col: u32,
    pub flags: u32,
    pub name: Sid,
    pub chain: Box<str>,
    pub up: Box<str>,
}

pub struct StrRow {
    pub scope: i32,
    pub line: u32,
    pub col: u32,
    pub flags: u32,
    pub value: Option<Box<str>>,
    pub up: Box<str>,
    pub parts: Option<Vec<Option<Box<str>>>>,
}

/// The module argument of a loader call, as the dynamic-import producer reads it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Argument {
    /// No argument, or a null one.
    Missing,
    /// A plain string constant: `{"s": text}` and nothing else.
    Literal(Box<str>),
    /// A name or attribute chain.
    Name(Box<str>),
    /// An object with an `o` key.
    Opaque(Box<str>),
    Expr,
}

pub struct CallRow {
    pub scope: i32,
    pub line: u32,
    pub col: u32,
    pub flags: u32,
    pub callee: Box<str>,
    pub argument: Argument,
}

/// A `uses` row: a name used through attributes in one scope, how often, and where first.
pub struct UseRow {
    pub scope: i32,
    pub header: i32,
    pub root: Sid,
    pub attrs: Sid,
    pub count: u32,
    pub line: u32,
    pub col: u32,
}

/// A `callees` or `names` row: a text in one scope, how often, and where first.
pub struct CountRow {
    pub scope: i32,
    pub header: i32,
    pub text: Sid,
    pub count: u32,
    pub line: u32,
    pub col: u32,
}

/// A `tags` row.
pub struct TagRow {
    pub scope: i32,
    pub header: i32,
    pub line: u32,
    pub col: u32,
    pub flags: u32,
    pub name: Sid,
}

/// One file's record. `keys` maps each top-level key to the byte range of its JSON text. The
/// link reads imports, defs, assigns, refs, strs and calls; packs' candidate queries read uses,
/// callees, names and tags, so no pack decodes whole records to select candidates.
pub struct FileFacts {
    pub path: Box<str>,
    pub status: Box<str>,
    pub imports: Vec<Import>,
    pub defs: Vec<Def>,
    pub assigns: Vec<Assign>,
    pub refs: Vec<RefRow>,
    pub strs: Vec<StrRow>,
    pub calls: Vec<CallRow>,
    pub uses: Vec<UseRow>,
    pub callees: Vec<CountRow>,
    pub names: Vec<CountRow>,
    pub tags: Vec<TagRow>,
    /// The import-bound names every load of which sits in a list, tuple, set or dict literal.
    pub listed: Vec<Sid>,
    pub line: Range<usize>,
    pub keys: Vec<(Box<str>, Range<usize>)>,
}

impl FileFacts {
    pub fn ok(&self) -> bool {
        &*self.status == "ok"
    }
}

/// Reads one record's JSON text, by the record's place in fact order, when something asks: the
/// link itself reads it only for a registry's declaration (`Producers::declaration_rows`), and no
/// reader holds the whole snapshot's text.
pub trait RecordText: Send + Sync {
    fn record(&self, file: usize) -> Result<Vec<u8>, String>;

    /// Bytes `range` of one record's JSON text. A source that can read part of a record in
    /// place overrides it.
    fn slice(&self, file: usize, range: Range<usize>) -> Result<Vec<u8>, String> {
        let text = self.record(file)?;
        text.get(range)
            .map(<[u8]>::to_vec)
            .ok_or_else(|| "a record is shorter than its keys say".to_string())
    }
}

/// The JSON text of every record held in memory, one per line: a JSONL input.
struct Lines {
    text: Vec<u8>,
    lines: Vec<Range<usize>>,
}

impl RecordText for Lines {
    fn record(&self, file: usize) -> Result<Vec<u8>, String> {
        Ok(self.text[self.lines[file].clone()].to_vec())
    }
}

/// The facts of a snapshot. Records keep the ranges of their keys in their own JSON text,
/// offset by `FileFacts::line.start`.
pub struct FactSet {
    source: Box<dyn RecordText>,
    pub files: Vec<FileFacts>,
    /// Set once the rows are dropped (`release_rows`).
    pub released: bool,
    /// The kinds whose rows were dropped early (`release_kinds`).
    pub dropped: Vec<&'static str>,
}

/// The kinds of rows the link keeps that only the core producers and pack queries read:
/// resolution reads imports, defs and assigns, so those stay until `release_rows`.
/// The callees whose one positional argument a call value keeps: the copies of a sequence a
/// module's `__all__` can be made with (`sorted(_NAMES)`), the only reader of that argument.
pub const COPY_CALLEES: [&str; 4] = ["sorted", "list", "tuple", "set"];
/// The builtin constructors whose one argument a call keeps when it has no keyword, so a reader
/// can tell a constant built by one (`frozenset({...})`).
pub const CONSTRUCTORS: [&str; 10] = [
    "frozenset",
    "set",
    "tuple",
    "list",
    "dict",
    "str",
    "int",
    "float",
    "bool",
    "bytes",
];

pub const QUERY_KINDS: [&str; 7] = ["refs", "strs", "calls", "uses", "callees", "names", "tags"];

impl FactSet {
    pub fn new(text: Vec<u8>, files: Vec<FileFacts>) -> FactSet {
        let lines = files.iter().map(|f| f.line.clone()).collect();
        FactSet {
            source: Box::new(Lines { text, lines }),
            files,
            released: false,
            dropped: Vec::new(),
        }
    }

    /// A fact set whose records' JSON text `source` reads one record at a time.
    pub fn deferred(files: Vec<FileFacts>, source: Box<dyn RecordText>) -> FactSet {
        FactSet {
            source,
            files,
            released: false,
            dropped: Vec::new(),
        }
    }

    /// Drops the rows of the query kinds (`QUERY_KINDS`) not in `kept`: after the core
    /// producers, only packs read them, and a pack reads only the kinds it declares.
    pub fn release_kinds(&mut self, kept: &[String]) {
        for kind in QUERY_KINDS {
            if kept.iter().any(|name| name == kind) || self.dropped.contains(&kind) {
                continue;
            }
            for file in &mut self.files {
                match kind {
                    "refs" => file.refs = Vec::new(),
                    "strs" => file.strs = Vec::new(),
                    "calls" => file.calls = Vec::new(),
                    "uses" => file.uses = Vec::new(),
                    "callees" => file.callees = Vec::new(),
                    "names" => file.names = Vec::new(),
                    _ => file.tags = Vec::new(),
                }
            }
            self.dropped.push(kind);
        }
    }

    /// Drops every record's rows. Paths, status, key ranges and the JSON text stay, so `raw`
    /// and `has_rows` still answer; whatever reads rows must check `released` first.
    pub fn release_rows(&mut self) {
        for file in &mut self.files {
            file.imports = Vec::new();
            file.defs = Vec::new();
            file.assigns = Vec::new();
            file.refs = Vec::new();
            file.strs = Vec::new();
            file.calls = Vec::new();
            file.uses = Vec::new();
            file.callees = Vec::new();
            file.names = Vec::new();
            file.tags = Vec::new();
        }
        self.released = true;
    }

    /// The JSON text of one record.
    pub fn record(&self, file: usize) -> Result<Vec<u8>, String> {
        self.source.record(file)
    }

    /// The JSON text of one top-level key of a record, as the extractor wrote it.
    pub fn raw(&self, file: usize, key: &str) -> Result<Option<Vec<u8>>, String> {
        let facts = &self.files[file];
        let Some((_, range)) = facts.keys.iter().find(|(name, _)| &**name == key) else {
            return Ok(None);
        };
        let start = facts.line.start;
        self.source
            .slice(file, range.start - start..range.end - start)
            .map(Some)
            .map_err(|e| format!("{}: {e}", facts.path))
    }

    /// Whether a record's list under `key` has rows: its JSON text is longer than `[]`.
    pub fn has_rows(&self, file: usize, key: &str) -> bool {
        self.files[file]
            .keys
            .iter()
            .any(|(name, range)| &**name == key && range.len() > 2)
    }
}

#[derive(Debug)]
pub struct DecodeError(pub String);

impl std::fmt::Display for DecodeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

fn int(value: &J) -> i64 {
    value.as_i64().unwrap_or(0)
}

fn text(value: &J) -> &str {
    value.as_str().unwrap_or("")
}

fn opt_sid(it: &mut Interner, value: &J) -> Option<Sid> {
    value.as_str().map(|text| it.intern(text))
}

fn row(value: &J, index: usize) -> &J {
    value
        .as_array()
        .and_then(|items| items.get(index))
        .unwrap_or(&J::Null)
}

/// Whether call keywords (`[[name, value], ...]`) pass `frozen=True`.
fn frozen_keyword(keywords: Option<&J>) -> bool {
    let Some(J::Array(pairs)) = keywords else {
        return false;
    };
    pairs.iter().any(|pair| match pair {
        J::Array(items) => items.len() == 2 && items[0] == "frozen" && items[1] == J::Bool(true),
        _ => false,
    })
}

/// The argument a call that declares a type keeps, by the callee's last name: a `TypeVar`'s
/// `bound`, else its constraints as a tuple. Whether the callee is `typing`'s is the reader's to
/// tell.
fn type_argument(
    it: &mut Interner,
    callee: &str,
    args: Option<&J>,
    kws: Option<&J>,
) -> Option<Box<V>> {
    let args = args.and_then(J::as_array).map_or(&[][..], Vec::as_slice);
    let kept = match callee.rsplit('.').next()? {
        "TypeVar" => {
            let bound = kws
                .and_then(J::as_array)
                .into_iter()
                .flatten()
                .find(|pair| row(pair, 0).as_str() == Some("bound"));
            match bound {
                Some(pair) => value(it, row(pair, 1)),
                None if args.len() > 1 => {
                    V::Tuple(args[1..].iter().map(|a| value(it, a)).collect())
                }
                None => return None,
            }
        }
        _ => return None,
    };
    Some(Box::new(kept))
}

pub fn value(it: &mut Interner, json: &J) -> V {
    match json {
        J::Null => V::Null,
        J::String(chain) => V::Chain(it.intern(chain)),
        J::Bool(_) | J::Number(_) => V::Scalar,
        J::Object(map) => {
            if let Some(inner) = map.get("sub") {
                let index = map.get("i").map_or(V::Null, |i| value(it, i));
                return V::Sub(Box::new(value(it, inner)), Box::new(index));
            }
            if let Some(s) = map.get("s") {
                let parsed = map.get("sa").map(|sa| Box::new(value(it, sa)));
                return V::Str(it.intern(text(s)), parsed);
            }
            if let Some(callee) = map.get("call") {
                let copy =
                    matches!(callee, J::String(name) if COPY_CALLEES.contains(&name.as_str()));
                let constructor = matches!(callee, J::String(name) if CONSTRUCTORS.contains(&name.as_str()))
                    && map.get("kw").is_none();
                let sole = match map.get("args") {
                    Some(J::Array(args)) if (copy || constructor) && args.len() == 1 => {
                        Some(Box::new(value(it, &args[0])))
                    }
                    _ => match callee {
                        J::String(name) => type_argument(it, name, map.get("args"), map.get("kw")),
                        _ => None,
                    },
                };
                return V::Call(
                    Box::new(value(it, callee)),
                    frozen_keyword(map.get("kw")),
                    sole,
                );
            }
            if let (Some(a), Some(v)) = (map.get("a"), map.get("v")) {
                let attr = it.intern(text(a));
                return V::Attr(Box::new(value(it, v)), attr);
            }
            if let Some(J::Array(items)) = map.get("tuple") {
                return V::Tuple(items.iter().map(|x| value(it, x)).collect());
            }
            if let Some(J::Array(items)) = map.get("list") {
                return V::List(items.iter().map(|x| value(it, x)).collect());
            }
            if let Some(J::Array(items)) = map.get("set") {
                return V::Set(items.iter().map(|x| value(it, x)).collect());
            }
            if let Some(J::Array(items)) = map.get("dict") {
                let pairs = items
                    .iter()
                    .map(|pair| {
                        let key = row(pair, 0);
                        let key = if key.is_null() {
                            None
                        } else {
                            Some(value(it, key))
                        };
                        (key, value(it, row(pair, 1)))
                    })
                    .collect();
                return V::Dict(pairs);
            }
            if let Some(inner) = map.get("star") {
                return V::Star(Box::new(value(it, inner)));
            }
            if map.contains_key("un") {
                let inner = map.get("v").map_or(V::Null, |x| value(it, x));
                return V::Unary(Box::new(inner));
            }
            if let Some(J::Array(items)) = map.get("vs") {
                return V::BoolOp(items.iter().map(|x| value(it, x)).collect());
            }
            if let Some(first) = map.get("cmp") {
                let rest = match map.get("cs") {
                    Some(J::Array(items)) => items.iter().map(|x| value(it, x)).collect(),
                    _ => Vec::new(),
                };
                return V::Compare(Box::new(value(it, first)), rest);
            }
            if let Some(op) = map.get("bin") {
                let l = map.get("l").map_or(V::Null, |x| value(it, x));
                let r = map.get("r").map_or(V::Null, |x| value(it, x));
                return V::Bin(op.as_str() == Some("|"), Box::new(l), Box::new(r));
            }
            if let Some(what) = map.get("o") {
                return V::Opaque(it.intern(text(what)));
            }
            V::Other
        }
        _ => V::Other,
    }
}

fn argument_of(json: &J) -> Argument {
    match json {
        J::Null => Argument::Missing,
        J::String(chain) => Argument::Name(chain.as_str().into()),
        J::Object(map) => {
            if map.len() == 1
                && let Some(J::String(s)) = map.get("s")
            {
                return Argument::Literal(s.as_str().into());
            }
            match map.get("o") {
                Some(what) => Argument::Opaque(text(what).into()),
                None => Argument::Expr,
            }
        }
        _ => Argument::Expr,
    }
}

/// The first positional argument, else the `name` keyword.
fn module_argument(args: &J, kws: &J) -> Argument {
    if let Some(first) = args.as_array().and_then(|items| items.first()) {
        return argument_of(first);
    }
    for pair in kws.as_array().into_iter().flatten() {
        if row(pair, 0).as_str() == Some("name") {
            return argument_of(row(pair, 1));
        }
    }
    Argument::Missing
}

/// The parsed JSON of the kinds the link reads, before interning.
struct Parsed {
    status: String,
    path: String,
    keys: Vec<(Box<str>, Range<usize>)>,
    imports: J,
    defs: J,
    assigns: J,
    refs: J,
    strs: J,
    calls: J,
    uses: J,
    callees: J,
    names: J,
    tags: J,
    listed: J,
}

fn parse_line(line: &[u8], base: usize) -> Result<Parsed, DecodeError> {
    let map: BTreeMap<&str, &RawValue> =
        serde_json::from_slice(line).map_err(|e| DecodeError(format!("record: {e}")))?;
    let start = line.as_ptr() as usize;
    let mut keys = Vec::with_capacity(map.len());
    let mut parsed = Parsed {
        status: String::new(),
        path: String::new(),
        keys: Vec::new(),
        imports: J::Null,
        defs: J::Null,
        assigns: J::Null,
        refs: J::Null,
        strs: J::Null,
        calls: J::Null,
        uses: J::Null,
        callees: J::Null,
        names: J::Null,
        tags: J::Null,
        listed: J::Null,
    };
    for (key, raw) in &map {
        let at = raw.get().as_ptr() as usize - start;
        keys.push(((*key).into(), base + at..base + at + raw.get().len()));
        let decode = |raw: &RawValue| -> Result<J, DecodeError> {
            serde_json::from_str(raw.get()).map_err(|e| DecodeError(format!("{key}: {e}")))
        };
        match *key {
            "status" => parsed.status = decode(raw)?.as_str().unwrap_or("").to_string(),
            "path" => parsed.path = decode(raw)?.as_str().unwrap_or("").to_string(),
            "imports" => parsed.imports = decode(raw)?,
            "defs" => parsed.defs = decode(raw)?,
            "assigns" => parsed.assigns = decode(raw)?,
            "refs" => parsed.refs = decode(raw)?,
            "strs" => parsed.strs = decode(raw)?,
            "calls" => parsed.calls = decode(raw)?,
            "uses" => parsed.uses = decode(raw)?,
            "callees" => parsed.callees = decode(raw)?,
            "names" => parsed.names = decode(raw)?,
            "tags" => parsed.tags = decode(raw)?,
            "listed" => parsed.listed = decode(raw)?,
            _ => {}
        }
    }
    parsed.keys = keys;
    Ok(parsed)
}

fn rows(value: &J) -> &[J] {
    value.as_array().map(Vec::as_slice).unwrap_or(&[])
}

fn convert(it: &mut Interner, parsed: Parsed, line: Range<usize>) -> FileFacts {
    let imports = rows(&parsed.imports)
        .iter()
        .map(|r| Import {
            scope: int(row(r, 0)) as i32,
            line: int(row(r, 1)) as u32,
            col: int(row(r, 2)) as u32,
            flags: int(row(r, 3)) as u32,
            target: opt_sid(it, row(r, 4)),
            level: int(row(r, 5)) as u32,
            name: opt_sid(it, row(r, 6)),
            asname: opt_sid(it, row(r, 7)),
        })
        .collect();
    let defs = rows(&parsed.defs)
        .iter()
        .map(|d| {
            let values = |it: &mut Interner, key: &str| -> Vec<V> {
                rows(&d[key]).iter().map(|x| value(it, x)).collect()
            };
            let bases = values(it, "bases");
            let dec = values(it, "dec");
            let params = rows(&d["params"])
                .iter()
                .map(|p| {
                    let annotation = row(p, 2);
                    Param {
                        name: it.intern(text(row(p, 0))),
                        positional: matches!(text(row(p, 1)), "po" | "a"),
                        annotation: if annotation.is_null() {
                            None
                        } else {
                            Some(value(it, annotation))
                        },
                    }
                })
                .collect();
            let ret = match &d["ret"] {
                J::Null => None,
                other => Some(value(it, other)),
            };
            let attrs = rows(&d["attrs"])
                .iter()
                .map(|a| {
                    let ann = row(a, 3);
                    SelfAttr {
                        line: int(row(a, 0)) as u32,
                        name: it.intern(text(row(a, 1))),
                        value: value(it, row(a, 2)),
                        ann: if ann.is_null() {
                            None
                        } else {
                            Some(value(it, ann))
                        },
                    }
                })
                .collect();
            let type_params = rows(&d["tp"])
                .iter()
                .map(|t| {
                    let bound = row(t, 1);
                    let bound = if bound.is_null() {
                        None
                    } else {
                        Some(value(it, bound))
                    };
                    (it.intern(text(row(t, 0))), bound)
                })
                .collect();
            let kind = match text(&d["k"]) {
                "class" => DefKind::Class,
                "async" => DefKind::Async,
                _ => DefKind::Def,
            };
            Def {
                s: int(&d["s"]) as i32,
                l: int(&d["l"]) as u32,
                c: int(&d["c"]) as u32,
                f: int(&d["f"]) as u32,
                kind,
                n: it.intern(text(&d["n"])),
                bases,
                dec,
                params,
                ret,
                stub: d["stub"].as_bool().unwrap_or(false),
                attrs,
                type_params,
            }
        })
        .collect();
    let assigns = rows(&parsed.assigns)
        .iter()
        .map(|a| {
            let op = match text(&a["op"]) {
                "=" => Op::Assign,
                ":" => Op::Annotated,
                "type" => Op::TypeAlias,
                "+=" => Op::AddAssign,
                _ => Op::Other,
            };
            let object = a.as_object();
            let present = |key: &str| object.is_some_and(|m| m.contains_key(key));
            Assign {
                s: int(&a["s"]) as i32,
                l: int(&a["l"]) as u32,
                c: int(&a["c"]) as u32,
                f: int(&a["f"]) as u32,
                op,
                targets: rows(&a["t"]).iter().map(|x| value(it, x)).collect(),
                value: if present("v") {
                    Some(value(it, &a["v"]))
                } else {
                    None
                },
                ann: if present("ann") {
                    Some(value(it, &a["ann"]))
                } else {
                    None
                },
            }
        })
        .collect();
    let refs = rows(&parsed.refs)
        .iter()
        .map(|r| RefRow {
            scope: int(row(r, 0)) as i32,
            header: int(row(r, 1)) as i32,
            line: int(row(r, 2)) as u32,
            col: int(row(r, 3)) as u32,
            flags: int(row(r, 4)) as u32,
            name: it.intern(text(row(r, 5))),
            chain: text(row(r, 6)).into(),
            up: text(row(r, 7)).into(),
        })
        .collect();
    let strs = rows(&parsed.strs)
        .iter()
        .map(|r| {
            let parts = match row(r, 8) {
                J::Array(items) => Some(items.iter().map(|p| p.as_str().map(Into::into)).collect()),
                _ => None,
            };
            StrRow {
                scope: int(row(r, 0)) as i32,
                line: int(row(r, 2)) as u32,
                col: int(row(r, 3)) as u32,
                flags: int(row(r, 4)) as u32,
                value: row(r, 5).as_str().map(Into::into),
                up: text(row(r, 7)).into(),
                parts,
            }
        })
        .collect();
    let calls = rows(&parsed.calls)
        .iter()
        .map(|r| CallRow {
            scope: int(row(r, 0)) as i32,
            line: int(row(r, 2)) as u32,
            col: int(row(r, 3)) as u32,
            flags: int(row(r, 4)) as u32,
            callee: text(row(r, 5)).into(),
            argument: module_argument(row(r, 6), row(r, 7)),
        })
        .collect();
    let uses = rows(&parsed.uses)
        .iter()
        .map(|r| UseRow {
            scope: int(row(r, 0)) as i32,
            header: int(row(r, 1)) as i32,
            root: it.intern(text(row(r, 2))),
            attrs: it.intern(text(row(r, 3))),
            count: int(row(r, 4)) as u32,
            line: int(row(r, 5)) as u32,
            col: int(row(r, 6)) as u32,
        })
        .collect();
    let counts = |it: &mut Interner, value: &J| -> Vec<CountRow> {
        rows(value)
            .iter()
            .map(|r| CountRow {
                scope: int(row(r, 0)) as i32,
                header: int(row(r, 1)) as i32,
                text: it.intern(text(row(r, 2))),
                count: int(row(r, 3)) as u32,
                line: int(row(r, 4)) as u32,
                col: int(row(r, 5)) as u32,
            })
            .collect()
    };
    let callees = counts(it, &parsed.callees);
    let names = counts(it, &parsed.names);
    let tags = rows(&parsed.tags)
        .iter()
        .map(|r| TagRow {
            scope: int(row(r, 0)) as i32,
            header: int(row(r, 1)) as i32,
            line: int(row(r, 2)) as u32,
            col: int(row(r, 3)) as u32,
            flags: int(row(r, 4)) as u32,
            name: it.intern(text(row(r, 5))),
        })
        .collect();
    let listed = rows(&parsed.listed)
        .iter()
        .map(|r| it.intern(text(row(r, 2))))
        .collect();
    FileFacts {
        path: parsed.path.into(),
        status: parsed.status.into(),
        imports,
        defs,
        assigns,
        refs,
        strs,
        calls,
        uses,
        callees,
        names,
        tags,
        listed,
        line,
        keys: parsed.keys,
    }
}

/// One record from its JSON text `json`, which starts at `start` in the fact set's text, named
/// `path` (a cache entry holds an empty path).
pub fn decode_record(
    it: &mut Interner,
    json: &[u8],
    start: usize,
    path: &str,
) -> Result<FileFacts, DecodeError> {
    let parsed = parse_line(json, start)?;
    let mut facts = convert(it, parsed, start..start + json.len());
    facts.path = path.into();
    Ok(facts)
}

/// Lines per parallel batch: bounds the memory of parsed JSON trees held at once.
const BATCH: usize = 2048;

/// Decodes JSON lines (one record each). `text` is kept; records keep ranges into it.
pub fn decode_lines(it: &mut Interner, text: Vec<u8>) -> Result<FactSet, DecodeError> {
    let mut ranges: Vec<Range<usize>> = Vec::new();
    let mut start = 0;
    for (at, byte) in text.iter().enumerate() {
        if *byte == b'\n' {
            if at > start {
                ranges.push(start..at);
            }
            start = at + 1;
        }
    }
    if start < text.len() {
        ranges.push(start..text.len());
    }
    let mut files = Vec::with_capacity(ranges.len());
    for batch in ranges.chunks(BATCH) {
        let parsed: Vec<Result<Parsed, DecodeError>> = batch
            .par_iter()
            .map(|range| parse_line(&text[range.clone()], range.start))
            .collect();
        for (parsed, range) in parsed.into_iter().zip(batch) {
            files.push(convert(it, parsed?, range.clone()));
        }
    }
    Ok(FactSet::new(text, files))
}
