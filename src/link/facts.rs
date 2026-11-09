//! Schema-v3 record lines decoded into what the link reads. The line text stays, so callers can
//! hand any fact list to Python exactly as the extractor wrote it.

use super::intern::{Interner, Sid};
use rayon::prelude::*;
use serde_json::Value as J;
use serde_json::value::RawValue;
use std::collections::BTreeMap;
use std::ops::Range;

/// A value tree (schema.md section 4), reduced to the forms resolution reads.
#[derive(Debug, Clone)]
pub enum V {
    Null,
    /// A name or attribute chain rooted at a name: `a.b.c`.
    Chain(Sid),
    /// `{"s": text}`, with `sa` (the parsed text of a string annotation) when the key is there.
    Str(Sid, Option<Box<V>>),
    /// `{"call": callee, ...}`, also the over-budget form `{"call": callee, "o": "big"}`.
    Call(Box<V>),
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

/// One file's record. `keys` maps each top-level key to the byte range of its JSON text.
pub struct FileFacts {
    pub path: Box<str>,
    pub status: Box<str>,
    pub imports: Vec<Import>,
    pub defs: Vec<Def>,
    pub assigns: Vec<Assign>,
    pub refs: Vec<RefRow>,
    pub strs: Vec<StrRow>,
    pub calls: Vec<CallRow>,
    pub line: Range<usize>,
    pub keys: Vec<(Box<str>, Range<usize>)>,
}

impl FileFacts {
    pub fn ok(&self) -> bool {
        &*self.status == "ok"
    }
}

/// Every record of one snapshot and the text they came from.
pub struct FactSet {
    pub text: Vec<u8>,
    pub files: Vec<FileFacts>,
}

impl FactSet {
    /// The JSON text of one top-level key of a record, as the extractor wrote it.
    pub fn raw(&self, file: usize, key: &str) -> Option<&[u8]> {
        let facts = &self.files[file];
        let (_, range) = facts.keys.iter().find(|(name, _)| &**name == key)?;
        Some(&self.text[range.clone()])
    }

    pub fn line(&self, file: usize) -> &[u8] {
        &self.text[self.files[file].line.clone()]
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

pub fn value(it: &mut Interner, json: &J) -> V {
    match json {
        J::Null => V::Null,
        J::String(chain) => V::Chain(it.intern(chain)),
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
                return V::Call(Box::new(value(it, callee)));
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
    FileFacts {
        path: parsed.path.into(),
        status: parsed.status.into(),
        imports,
        defs,
        assigns,
        refs,
        strs,
        calls,
        line,
        keys: parsed.keys,
    }
}

/// One record from `text[range]`, named `path` (a cache entry holds an empty path).
pub fn decode_record(
    it: &mut Interner,
    text: &[u8],
    range: Range<usize>,
    path: &str,
) -> Result<FileFacts, DecodeError> {
    let parsed = parse_line(&text[range.clone()], range.start)?;
    let mut facts = convert(it, parsed, range);
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
    Ok(FactSet { text, files })
}
