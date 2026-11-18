//! Typed L1 facts of one file. Rows serialize as positional JSON arrays,
//! defs and assigns as objects with their keys in code point order.

use crate::config::{Kind, Kinds};
use crate::value::{Keyword, Value};
use serde::Serialize;
use serde::ser::{SerializeSeq, Serializer};

/// Statement context flags. The Python side (`hoglint/core/symbols.py`) parses these constants
/// in a test.
pub mod flags {
    /// Body of `if TYPE_CHECKING:`.
    pub const TYPE_ONLY: u32 = 1;
    /// `else` or `elif` branch of such an `if`.
    pub const TC_ELSE: u32 = 2;
    pub const FUNC: u32 = 4;
    pub const CLASS: u32 = 8;
    /// Try body, handlers, else and finally.
    pub const TRY: u32 = 16;
    /// Any other `if` / `elif` / `else` body.
    pub const IF: u32 = 32;
    /// With, for and while bodies, their else, and match cases.
    pub const BLOCK: u32 = 64;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Status {
    Ok,
    DecodeError,
    SyntaxError,
    /// The shell could not read the file.
    ReadError,
}

/// Where a row sits: enclosing scope (def index, -1 = module), the def whose header holds it
/// (-1 = none), line, byte column and the statement flags there.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Site {
    pub scope: i64,
    pub header: i64,
    pub l: i64,
    pub c: i64,
    pub flags: u32,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Import {
    pub scope: i64,
    pub l: i64,
    pub c: i64,
    pub flags: u32,
    pub module: Option<String>,
    pub level: u32,
    /// The from-import name; None for `import a.b`.
    pub name: Option<String>,
    pub asname: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum DefKind {
    Def,
    Async,
    Class,
}

/// `[name, kind, annotation?, default?]`; trailing nulls are dropped.
#[derive(Debug, Clone, PartialEq)]
pub struct Param {
    pub name: String,
    /// `po`, `a`, `va`, `ko` or `kw`.
    pub kind: &'static str,
    pub annotation: Value,
    pub default: Value,
}

/// An attribute a method assigns on its first parameter (`self.name = value`): what an
/// instance attribute holds. `[line, name, value, annotation?]`; a null annotation is dropped.
#[derive(Debug, Clone, PartialEq)]
pub struct SelfAttr {
    pub l: i64,
    pub name: String,
    pub value: Value,
    pub annotation: Value,
}

/// A PEP 695 type parameter of a def or class (`def f[T: Bound]`, `class C[T: (A, B)]`):
/// `[name, bound?]`. The bound is the written bound or the tuple of constraints; a null bound is
/// dropped, and a `ParamSpec` or `TypeVarTuple` has none.
#[derive(Debug, Clone, PartialEq)]
pub struct TypeParam {
    pub name: String,
    pub bound: Value,
}

/// One def / async def / class. Its index in the record is its scope id.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Def {
    /// A method's assignments to attributes of its first parameter, in source order.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub attrs: Vec<SelfAttr>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub bases: Vec<Value>,
    pub c: i64,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub dec: Vec<Value>,
    /// Last line of the statement.
    pub e: i64,
    #[serde(skip_serializing_if = "is_zero")]
    pub f: u32,
    pub k: DefKind,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub kw: Vec<Keyword>,
    pub l: i64,
    pub n: String,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub params: Vec<Param>,
    #[serde(skip_serializing_if = "Value::is_blank")]
    pub ret: Value,
    /// Enclosing scope.
    pub s: i64,
    #[serde(skip_serializing_if = "is_false")]
    pub stub: bool,
    /// The PEP 695 type parameters, in order.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub tp: Vec<TypeParam>,
}

/// A module-scope or class-scope assignment.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Assign {
    /// AnnAssign only; present even when it encodes to null.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ann: Option<Value>,
    pub c: i64,
    #[serde(skip_serializing_if = "is_zero")]
    pub f: u32,
    pub l: i64,
    pub op: String,
    pub s: i64,
    pub t: Vec<Value>,
    #[serde(skip_serializing_if = "Value::is_null")]
    pub v: Value,
}

/// A use site of a tracked name. `origin` is the deriving scope of a local-derived name: set
/// on `derived` rows only.
#[derive(Debug, Clone, PartialEq)]
pub struct Ref {
    pub site: Site,
    pub root: String,
    pub chain: String,
    pub up: String,
    pub origin: Option<i64>,
}

/// A call to a watched callee, with shallow arguments.
#[derive(Debug, Clone, PartialEq)]
pub struct WatchedCall {
    pub site: Site,
    pub callee: String,
    pub args: Vec<Value>,
    pub kws: Vec<Keyword>,
}

/// A dotted-path string literal. `parts` is set for an implicit concatenation only.
#[derive(Debug, Clone, PartialEq)]
pub struct DottedStr {
    pub site: Site,
    pub value: Option<String>,
    pub chain: String,
    pub up: String,
    pub parts: Option<Vec<Option<String>>>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Tag {
    pub site: Site,
    pub name: String,
}

/// A dict entry with a watched key. The position is the value's; chain and up are the dict's.
#[derive(Debug, Clone, PartialEq)]
pub struct WatchedKey {
    pub site: Site,
    pub key: String,
    pub value: Value,
    pub chain: String,
    pub up: String,
}

/// An `if` whose test is a comparison with a Name on the left.
#[derive(Debug, Clone, PartialEq)]
pub struct Branch {
    pub site: Site,
    pub body_end: i64,
    pub test: Value,
}

/// Occurrences of one key in a scope: how many, and the first position.
#[derive(Debug, Clone, PartialEq)]
pub struct Count<K> {
    pub key: K,
    pub count: i64,
    pub l: i64,
    pub c: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct UseKey {
    pub scope: i64,
    pub header: i64,
    pub root: String,
    /// Leading `.a.b` attributes of the chain.
    pub attrs: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct TextKey {
    pub scope: i64,
    pub header: i64,
    pub text: String,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Facts {
    pub assigns: Vec<Assign>,
    pub branches: Vec<Branch>,
    pub callees: Vec<Count<TextKey>>,
    pub calls: Vec<WatchedCall>,
    pub defs: Vec<Def>,
    pub derived: Vec<Ref>,
    /// The names the item reading cannot read at a site: a module-level or class-body use it does
    /// not understand (handed to a call, an attribute store, another method), a store of a name a
    /// function declares `global` by no assignment, or `*` for `globals()` and
    /// `sys.modules[__name__]`, which reach every name.
    pub escapes: Vec<Tag>,
    pub imports: Vec<Import>,
    /// The import-bound names every load of which in the file sits in a list, tuple, set or dict
    /// literal: one row per name (scope and header -1), with its count of loads.
    pub listed: Vec<Count<TextKey>>,
    pub names: Vec<Count<TextKey>>,
    pub refs: Vec<Ref>,
    pub strs: Vec<DottedStr>,
    pub tags: Vec<Tag>,
    pub uses: Vec<Count<UseKey>>,
    pub watched: Vec<WatchedKey>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct FileRecord {
    pub sha256: String,
    pub bytes: usize,
    pub status: Status,
    pub facts: Facts,
}

fn is_zero(value: &u32) -> bool {
    *value == 0
}

fn is_false(value: &bool) -> bool {
    !*value
}

/// Rows sort by position, then by their compact JSON text.
pub trait Positioned {
    fn position(&self) -> (i64, i64);
}

pub fn sort_rows<T: Positioned + Serialize>(rows: &mut [T]) {
    rows.sort_by(|a, b| {
        a.position()
            .cmp(&b.position())
            .then_with(|| row_text(a).cmp(&row_text(b)))
    });
}

fn row_text<T: Serialize>(row: &T) -> String {
    serde_json::to_string(row).unwrap_or_default()
}

impl Facts {
    /// Drops the lists of kinds the configuration does not ask for.
    pub fn keep(&mut self, kinds: Kinds) {
        fn clear<T>(list: &mut Vec<T>, kinds: Kinds, kind: Kind) {
            if !kinds.has(kind) {
                list.clear();
            }
        }
        clear(&mut self.assigns, kinds, Kind::Assigns);
        clear(&mut self.branches, kinds, Kind::Branches);
        clear(&mut self.callees, kinds, Kind::Callees);
        clear(&mut self.calls, kinds, Kind::Calls);
        clear(&mut self.defs, kinds, Kind::Defs);
        clear(&mut self.derived, kinds, Kind::Derived);
        clear(&mut self.escapes, kinds, Kind::Escapes);
        clear(&mut self.imports, kinds, Kind::Imports);
        clear(&mut self.listed, kinds, Kind::Listed);
        clear(&mut self.names, kinds, Kind::Names);
        clear(&mut self.refs, kinds, Kind::Refs);
        clear(&mut self.strs, kinds, Kind::Strs);
        clear(&mut self.tags, kinds, Kind::Tags);
        clear(&mut self.uses, kinds, Kind::Uses);
        clear(&mut self.watched, kinds, Kind::Watched);
    }

    /// Puts every list in record order. Defs keep their scope-id order.
    pub fn sort(&mut self) {
        sort_rows(&mut self.assigns);
        sort_rows(&mut self.branches);
        sort_rows(&mut self.callees);
        sort_rows(&mut self.calls);
        sort_rows(&mut self.derived);
        sort_rows(&mut self.escapes);
        sort_rows(&mut self.imports);
        sort_rows(&mut self.listed);
        sort_rows(&mut self.names);
        sort_rows(&mut self.refs);
        sort_rows(&mut self.strs);
        sort_rows(&mut self.tags);
        sort_rows(&mut self.uses);
        sort_rows(&mut self.watched);
    }
}

macro_rules! positioned_by_site {
    ($($row:ty),*) => {
        $(impl Positioned for $row {
            fn position(&self) -> (i64, i64) {
                (self.site.l, self.site.c)
            }
        })*
    };
}

positioned_by_site!(Ref, WatchedCall, DottedStr, Tag, WatchedKey, Branch);

macro_rules! positioned_by_lc {
    ($($row:ty),*) => {
        $(impl Positioned for $row {
            fn position(&self) -> (i64, i64) {
                (self.l, self.c)
            }
        })*
    };
}

positioned_by_lc!(Import, Assign, Count<TextKey>, Count<UseKey>);

impl Serialize for Import {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        let row = (
            self.scope,
            self.l,
            self.c,
            self.flags,
            &self.module,
            self.level,
            &self.name,
            &self.asname,
        );
        row.serialize(s)
    }
}

impl Serialize for Param {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        let mut seq = s.serialize_seq(None)?;
        seq.serialize_element(&self.name)?;
        seq.serialize_element(self.kind)?;
        if !self.annotation.is_null() || !self.default.is_null() {
            seq.serialize_element(&self.annotation)?;
        }
        if !self.default.is_null() {
            seq.serialize_element(&self.default)?;
        }
        seq.end()
    }
}

impl Serialize for SelfAttr {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        let mut seq = s.serialize_seq(None)?;
        seq.serialize_element(&self.l)?;
        seq.serialize_element(&self.name)?;
        seq.serialize_element(&self.value)?;
        if !self.annotation.is_null() {
            seq.serialize_element(&self.annotation)?;
        }
        seq.end()
    }
}

impl Serialize for TypeParam {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        let mut seq = s.serialize_seq(None)?;
        seq.serialize_element(&self.name)?;
        if !self.bound.is_null() {
            seq.serialize_element(&self.bound)?;
        }
        seq.end()
    }
}

impl Site {
    /// Starts a row with the site columns `[s, h, l, c, f]`.
    fn head<S: Serializer>(&self, s: S) -> Result<S::SerializeSeq, S::Error> {
        let mut row = s.serialize_seq(None)?;
        row.serialize_element(&self.scope)?;
        row.serialize_element(&self.header)?;
        row.serialize_element(&self.l)?;
        row.serialize_element(&self.c)?;
        row.serialize_element(&self.flags)?;
        Ok(row)
    }
}

impl Serialize for Ref {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        let mut row = self.site.head(s)?;
        row.serialize_element(&self.root)?;
        row.serialize_element(&self.chain)?;
        row.serialize_element(&self.up)?;
        if let Some(origin) = self.origin {
            row.serialize_element(&origin)?;
        }
        row.end()
    }
}

impl Serialize for WatchedCall {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        let mut row = self.site.head(s)?;
        row.serialize_element(&self.callee)?;
        row.serialize_element(&self.args)?;
        row.serialize_element(&self.kws)?;
        row.end()
    }
}

impl Serialize for DottedStr {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        let mut row = self.site.head(s)?;
        row.serialize_element(&self.value)?;
        row.serialize_element(&self.chain)?;
        row.serialize_element(&self.up)?;
        if let Some(parts) = &self.parts {
            row.serialize_element(parts)?;
        }
        row.end()
    }
}

impl Serialize for Tag {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        let mut row = self.site.head(s)?;
        row.serialize_element(&self.name)?;
        row.end()
    }
}

impl Serialize for WatchedKey {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        let mut row = self.site.head(s)?;
        row.serialize_element(&self.key)?;
        row.serialize_element(&self.value)?;
        row.serialize_element(&self.chain)?;
        row.serialize_element(&self.up)?;
        row.end()
    }
}

impl Serialize for Branch {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        let mut row = self.site.head(s)?;
        row.serialize_element(&self.body_end)?;
        row.serialize_element(&self.test)?;
        row.end()
    }
}

impl Serialize for Count<UseKey> {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        let key = &self.key;
        let row = (
            key.scope, key.header, &key.root, &key.attrs, self.count, self.l, self.c,
        );
        row.serialize(s)
    }
}

impl Serialize for Count<TextKey> {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        let key = &self.key;
        (key.scope, key.header, &key.text, self.count, self.l, self.c).serialize(s)
    }
}
