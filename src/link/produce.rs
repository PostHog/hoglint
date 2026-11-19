//! The core producers: import, reference, declaration, dotted-path, dynamic-import and unparsed
//! edges. hoglint/core/catalog.py names the edge kinds and fact kinds of each.

use super::LinkConfig;
use super::facts::{
    Argument, Assign, COPY_CALLEES, CallRow, ComputedRow, Def, FactSet, FileFacts, Import,
    LoadForm, Op, V, value as value_tree,
};
use super::intern::{EMPTY, Interner, Sid};
use super::resolve::{
    Binding, CLASS, Core, Ent, FUNC, HitId, HitKind, OrderedMap, import_surface, module_assigns,
};
use super::rules::glob::{ExportPaths, Glob};
use super::scope::{Lookup, LookupNames, Res, Resolved, Scope, UNRESOLVED, ctx};
use super::surface::{
    ALL_CLAUSE, HandOut, Handed, HandedOut, LAZY_CLAUSE, annotated_constant, star_names,
};
use super::trace::{Kind, Memo};
use crate::path::listed;
use rustc_hash::{FxHashMap, FxHashSet};
use serde::Deserialize;
use serde_json::Value as J;
use serde_json::value::RawValue;
use std::borrow::Cow;
use std::rc::Rc;
use std::sync::Arc;

/// The hooks a record's construction invokes with the value it stores (`bare_record`).
const RECORD_HOOKS: [&str; 5] = [
    "__setattr__",
    "__getattribute__",
    "__getattr__",
    "__set_name__",
    "__set__",
];

/// The library bases whose metaclass has no `__set__` (`plain_metaclass`).
const PLAIN_BASES: [&str; 4] = ["builtins.object", "object", "abc.ABC", "typing.Generic"];

/// How deep `plain_metaclass` follows first-party bases.
const MAX_BASE_DEPTH: usize = 32;

pub const KIND_NAMES: [&str; 10] = [
    "import",
    "reference",
    "declares",
    "decorate",
    "inherit",
    "exposes",
    "dotted-path",
    "dynamic-import",
    "unparsed",
    "rebind",
];
pub const IMPORT: u8 = 0;
pub const REFERENCE: u8 = 1;
pub const DECLARES: u8 = 2;
pub const DECORATE: u8 = 3;
pub const INHERIT: u8 = 4;
pub const EXPOSES: u8 = 5;
pub const DOTTED: u8 = 6;
pub const DYNAMIC: u8 = 7;
pub const UNPARSED: u8 = 8;
pub const REBIND: u8 = 9;

/// The standard library's ways to load a module by a name or a file path other than a loader
/// (`importlib.import_module`, `__import__`): the link does not read their arguments.
const UNREAD_LOADERS: [&str; 6] = [
    "importlib.__import__",
    "importlib.util.find_spec",
    "importlib.util.spec_from_file_location",
    "importlib.util.spec_from_loader",
    "runpy.run_module",
    "runpy.run_path",
];

/// One core edge. Strings are interned; `via` and `attrs` are ranges into the store's arenas.
#[derive(Clone, Copy)]
pub struct EdgeRow {
    pub kind: u8,
    pub res: Res,
    pub ctx: u8,
    pub scope: i32,
    pub file: u32,
    /// Index of the file's scope in `Link::scopes`.
    pub owner: u32,
    pub line: u32,
    pub col: u32,
    pub surface: Sid,
    pub target: Option<Ent>,
    pub use_: Sid,
    pub via: (u32, u32),
    pub attrs: (u32, u32),
}

/// Fields a pack set on a core edge it did not make (D22): the edge stays one edge, and every
/// reader sees it with these fields. `attrs` come after the edge's own.
#[derive(Clone, Default)]
pub struct Refinement {
    pub shape: Sid,
    pub shape_detail: Sid,
    pub attrs: Vec<(Sid, Sid)>,
}

/// The record of a pack edge that names no record of the snapshot.
pub const NO_FILE: u32 = u32::MAX;

/// One edge a pack made (D22): it lives in the link next to the core edges, after them, so
/// every reader of edges reads one table. Its site is whatever the pack wrote.
#[derive(Clone, Copy)]
pub struct PackRow {
    pub kind: Sid,
    pub producer: Sid,
    pub path: Sid,
    /// The record whose path is `path`, or NO_FILE.
    pub file: u32,
    pub module: Sid,
    pub scope: Sid,
    pub line: u32,
    pub col: u32,
    pub surface: Sid,
    pub targets: (u32, u32),
    pub res: Res,
    pub ctx: u8,
    pub via: (u32, u32),
    pub use_: Sid,
    pub shape: Sid,
    pub shape_detail: Sid,
    pub attrs: (u32, u32),
}

/// Every edge of a snapshot: core rows (ids 0..rows.len()), then pack rows.
#[derive(Default)]
pub struct EdgeStore {
    pub rows: Vec<EdgeRow>,
    pub pack: Vec<PackRow>,
    pub via: Vec<Ent>,
    pub attrs: Vec<(Sid, Sid)>,
    /// Targets of pack rows.
    pub targets: Vec<Ent>,
    /// Refinements by edge id. `packs/refined.rs` refuses a batch that sets a field apart.
    pub refined: FxHashMap<u32, Refinement>,
    /// The interned name and producer of each core kind code (`KIND_NAMES`).
    pub kind_names: Vec<Sid>,
    pub producers: Vec<Sid>,
}

fn range<T>(items: &[T], at: (u32, u32)) -> &[T] {
    &items[at.0 as usize..(at.0 + at.1) as usize]
}

impl EdgeStore {
    pub fn len(&self) -> usize {
        self.rows.len() + self.pack.len()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// The pack row of edge `id`, or None for a core edge.
    pub fn pack_row(&self, id: u32) -> Option<&PackRow> {
        (id as usize)
            .checked_sub(self.rows.len())
            .map(|at| &self.pack[at])
    }

    pub fn via_of(&self, row: &EdgeRow) -> &[Ent] {
        range(&self.via, row.via)
    }

    /// The attrs core gave a core edge, without what packs added (`attrs_with`).
    pub fn attrs_of(&self, row: &EdgeRow) -> &[(Sid, Sid)] {
        range(&self.attrs, row.attrs)
    }

    /// The attrs edge `id` was made with.
    pub fn own_attrs(&self, id: u32) -> &[(Sid, Sid)] {
        match self.pack_row(id) {
            Some(pack) => range(&self.attrs, pack.attrs),
            None => self.attrs_of(&self.rows[id as usize]),
        }
    }

    /// Every attr of edge `id`: its own, then those refinements added.
    pub fn attrs_with(&self, id: u32) -> impl Iterator<Item = &(Sid, Sid)> {
        let added = self
            .refined
            .get(&id)
            .map_or(&[][..], |r| r.attrs.as_slice());
        self.own_attrs(id).iter().chain(added)
    }

    /// (shape, shape detail) of edge `id`, refinements included.
    pub fn shape_of(&self, id: u32) -> (Sid, Sid) {
        let own = self
            .pack_row(id)
            .map_or((EMPTY, EMPTY), |pack| (pack.shape, pack.shape_detail));
        self.refined
            .get(&id)
            .map_or(own, |r| (r.shape, r.shape_detail))
    }

    pub fn kind_of(&self, id: u32) -> Sid {
        match self.pack_row(id) {
            Some(pack) => pack.kind,
            None => self.kind_names[self.rows[id as usize].kind as usize],
        }
    }

    pub fn producer_of(&self, id: u32) -> Sid {
        match self.pack_row(id) {
            Some(pack) => pack.producer,
            None => self.producers[self.rows[id as usize].kind as usize],
        }
    }

    pub fn surface_of(&self, id: u32) -> Sid {
        self.pack_row(id)
            .map_or_else(|| self.rows[id as usize].surface, |pack| pack.surface)
    }

    pub fn res_of(&self, id: u32) -> Res {
        self.pack_row(id)
            .map_or_else(|| self.rows[id as usize].res, |pack| pack.res)
    }

    pub fn ctx_of(&self, id: u32) -> u8 {
        self.pack_row(id)
            .map_or_else(|| self.rows[id as usize].ctx, |pack| pack.ctx)
    }

    /// The context bits a rule filters edge `id` on: an edge with none counts as prod, as
    /// `contexts_of` reads it in Python. Core edges always have a base context.
    pub fn seen_ctx(&self, id: u32) -> u8 {
        match self.ctx_of(id) {
            0 => super::scope::ctx::PROD,
            bits => bits,
        }
    }

    /// Whether edge `id`'s contexts meet `include` (any, when it is empty) and avoid `exclude`.
    pub fn context_ok(&self, id: u32, include: u8, exclude: u8) -> bool {
        let ctx = self.seen_ctx(id);
        (include == 0 || ctx & include != 0) && ctx & exclude == 0
    }

    pub fn use_of(&self, id: u32) -> Sid {
        self.pack_row(id)
            .map_or_else(|| self.rows[id as usize].use_, |pack| pack.use_)
    }

    pub fn via_of_id(&self, id: u32) -> &[Ent] {
        match self.pack_row(id) {
            Some(pack) => range(&self.via, pack.via),
            None => self.via_of(&self.rows[id as usize]),
        }
    }

    /// Every target of edge `id`.
    pub fn targets_of(&self, id: u32) -> &[Ent] {
        match self.pack_row(id) {
            Some(pack) => range(&self.targets, pack.targets),
            None => self.rows[id as usize].target.as_slice(),
        }
    }

    /// The target of an edge with exactly one, as `Edge.target` reads it.
    pub fn target_of(&self, id: u32) -> Option<Ent> {
        match self.targets_of(id) {
            [one] => Some(*one),
            _ => None,
        }
    }

    /// Sets the fields a refinement names (`shape`, `shape_detail` when Some) and appends its
    /// attrs, for edge `id`.
    pub fn refine(
        &mut self,
        id: u32,
        shape: Option<Sid>,
        shape_detail: Option<Sid>,
        attrs: &[(Sid, Sid)],
    ) {
        let own_shape = self.shape_of(id);
        let own: Vec<(Sid, Sid)> = self.own_attrs(id).to_vec();
        let found = self.refined.entry(id).or_insert_with(|| Refinement {
            shape: own_shape.0,
            shape_detail: own_shape.1,
            attrs: Vec::new(),
        });
        if let Some(shape) = shape {
            found.shape = shape;
        }
        if let Some(detail) = shape_detail {
            found.shape_detail = detail;
        }
        for pair in attrs {
            if !own.contains(pair) && !found.attrs.contains(pair) {
                found.attrs.push(*pair);
            }
        }
    }
}

/// `graph.symbols`: "module.qualname" -> (def kind, file). A later def of the same name keeps
/// the first one's place and replaces its facts, as a Python dict does.
#[derive(Default)]
pub struct Symbols {
    pub order: Vec<(Sid, u8, u32)>,
    pub at: rustc_hash::FxHashMap<Sid, usize>,
    /// The facts packs wrote, as (key, value) pairs per symbol: they replace the symbol's facts
    /// whole, as `NativeSymbols.update` does.
    pub added: rustc_hash::FxHashMap<Sid, Vec<(Sid, Sid)>>,
}

impl Symbols {
    /// Whether `graph.symbols` holds the name.
    pub fn has(&self, name: Sid) -> bool {
        self.at.contains_key(&name) || self.added.contains_key(&name)
    }

    fn set(&mut self, name: Sid, kind: u8, file: u32) {
        match self.at.get(&name) {
            Some(&i) => self.order[i] = (name, kind, file),
            None => {
                self.at.insert(name, self.order.len());
                self.order.push((name, kind, file));
            }
        }
    }
}

/// Settings the producers read, interned once.
pub struct Producing {
    pub names: LookupNames,
    pub module_scope: Sid,
    pub loaders: Vec<Sid>,
    pub loader_last_names: Vec<String>,
    pub dataclass_decorators: Vec<Sid>,
    pub dataclass_bases: Vec<Sid>,
    pub patch_callees: Vec<String>,
    pub path_callees: Vec<String>,
    /// Modules (dotted names or `*` globs) whose strings are paths wherever they stand.
    pub path_modules: Vec<String>,
    /// Every way an edge's target can spell a `[discovery] reference_names` entry: each split of
    /// the dotted name into a module and the symbol inside it (`named_references`).
    pub reference_names: FxHashSet<Ent>,
    /// The files of exports whose signatures are part of their contract.
    pub contract_surfaces: Vec<ExportPaths>,
    /// The files of every export.
    pub export_surfaces: Vec<ExportPaths>,
    /// Each registry's files and the attribute its loader reads there, in config order.
    pub registries: Vec<(Glob, Sid)>,
    k: Keys,
}

/// Attr keys and fixed values.
struct Keys {
    binds: Sid,
    lazy: Sid,
    module: Sid,
    chain: Sid,
    up: Sid,
    header: Sid,
    decl: Sid,
    body: Sid,
    decorated: Sid,
    called: Sid,
    classes: Sid,
    unknown_base: Sid,
    frozen: Sid,
    class: Sid,
    index: Sid,
    callable: Sid,
    slot: Sid,
    position: Sid,
    literal: Sid,
    role: Sid,
    member: Sid,
    loader: Sid,
    argument: Sid,
    status: Sid,
    zero: Sid,
    one: Sid,
    star: Sid,
    /// The attr name of an import edge for one name a star import binds.
    star_attr: Sid,
    use_call: Sid,
    use_decorator: Sid,
    use_base: Sid,
    use_annotation: Sid,
    whole: Sid,
    nested: Sid,
    part: Sid,
    joined: Sid,
    patch_target: Sid,
    literal_role: Sid,
    literal_name: Sid,
    annotated_name: Sid,
    init: Sid,
    annotation: Sid,
    missing: Sid,
    unread: Sid,
    handed: Sid,
    ret: Sid,
    alias: Sid,
    too_deep: Sid,
    expanded: Sid,
    subscripted: Sid,
    argument_of: Sid,
    dunder_all: Sid,
    names: Sid,
    signature: Sid,
    staticmethod: Sid,
    fields: Sid,
    class_var: Sid,
    constant: Sid,
    /// The attr of an import or reference edge whose name sits in a list, tuple, set or dict
    /// literal: for an import, every load of the name it binds does.
    listed: Sid,
    /// The callees that declare a type variable by a call.
    type_vars: [Sid; 2],
}

impl Producing {
    /// The attribute the first registry whose files match `path` reads there.
    fn registry_attribute(&self, path: &str) -> Option<Sid> {
        self.registries
            .iter()
            .find(|(glob, _)| glob.is_match(path))
            .map(|(_, attribute)| *attribute)
    }

    /// (export, root) of every contract surface that covers a path.
    fn surface_roots(&self, path: &str) -> Vec<(usize, String)> {
        self.contract_surfaces
            .iter()
            .enumerate()
            .filter_map(|(at, surface)| surface.covered_root(path).map(|root| (at, root)))
            .collect()
    }

    pub fn new(
        it: &mut Interner,
        config: &LinkConfig,
        contract_surfaces: Vec<ExportPaths>,
        export_surfaces: Vec<ExportPaths>,
    ) -> Producing {
        let mut last_names: Vec<String> = config
            .dynamic_loaders
            .iter()
            .map(|loader| loader.rsplit('.').next().unwrap_or("").to_string())
            .collect();
        last_names.sort();
        last_names.dedup();
        let k = Keys {
            binds: it.intern("binds"),
            listed: it.intern("listed"),
            lazy: it.intern("lazy"),
            module: it.intern("module"),
            chain: it.intern("chain"),
            up: it.intern("up"),
            header: it.intern("header"),
            decl: it.intern("decl"),
            body: it.intern("body"),
            decorated: it.intern("decorated"),
            called: it.intern("called"),
            classes: it.intern("classes"),
            unknown_base: it.intern("?"),
            frozen: it.intern("frozen"),
            class: it.intern("class"),
            index: it.intern("index"),
            callable: it.intern("callable"),
            slot: it.intern("slot"),
            position: it.intern("position"),
            literal: it.intern("literal"),
            role: it.intern("role"),
            member: it.intern("member"),
            loader: it.intern("loader"),
            argument: it.intern("argument"),
            status: it.intern("status"),
            zero: it.intern("0"),
            one: it.intern("1"),
            star: it.intern("*"),
            star_attr: it.intern("star"),
            use_call: it.intern("call"),
            use_decorator: it.intern("decorator"),
            use_base: it.intern("base"),
            use_annotation: it.intern("annotation"),
            whole: it.intern("whole"),
            nested: it.intern("nested"),
            part: it.intern("part"),
            joined: it.intern("joined"),
            patch_target: it.intern("patch-target"),
            literal_role: it.intern("literal"),
            literal_name: it.intern("Literal"),
            annotated_name: it.intern("Annotated"),
            init: it.intern("__init__"),
            annotation: it.intern("annotation"),
            missing: it.intern("missing"),
            unread: it.intern("unread"),
            handed: it.intern("handed"),
            ret: it.intern("return"),
            alias: it.intern("alias"),
            too_deep: it.intern("too-deep"),
            expanded: it.intern("expanded"),
            subscripted: it.intern("subscripted"),
            argument_of: it.intern("argument_of"),
            dunder_all: it.intern("__all__"),
            names: it.intern("names"),
            signature: it.intern("signature"),
            staticmethod: it.intern("builtins.staticmethod"),
            fields: it.intern("fields"),
            class_var: it.intern("ClassVar"),
            constant: it.intern("constant"),
            type_vars: [
                it.intern("typing.TypeVar"),
                it.intern("typing_extensions.TypeVar"),
            ],
        };
        Producing {
            names: LookupNames {
                builtins: config.builtins.iter().map(|name| it.intern(name)).collect(),
                builtins_module: it.intern("builtins"),
            },
            module_scope: it.intern("<module>"),
            loaders: config
                .dynamic_loaders
                .iter()
                .map(|loader| it.intern(loader))
                .collect(),
            loader_last_names: last_names,
            dataclass_decorators: config
                .dataclass_decorators
                .iter()
                .map(|name| it.intern(name))
                .collect(),
            dataclass_bases: config
                .dataclass_bases
                .iter()
                .map(|name| it.intern(name))
                .collect(),
            patch_callees: config.patch_callees.clone(),
            path_callees: config.path_callees.clone(),
            path_modules: config.path_modules.clone(),
            reference_names: config
                .reference_names
                .iter()
                .flat_map(|name| spellings(it, name))
                .collect(),
            contract_surfaces,
            export_surfaces,
            // A glob that does not compile fails the rules' plan, which compiles the same one.
            registries: config
                .registries
                .iter()
                .filter_map(|registry| {
                    let glob = Glob::new(&registry.module).ok()?;
                    Some((glob, it.intern(&registry.attribute)))
                })
                .collect(),
            k,
        }
    }
}

/// Every entity whose text is `dotted`: the module itself, and each split into a module and the
/// symbol inside it (`a.b.c` is the module `a.b.c`, `c` in `a.b` and `b.c` in `a`), so a target
/// is compared with a listed name with no string built per use.
fn spellings(it: &mut Interner, dotted: &str) -> Vec<Ent> {
    let mut out = vec![Ent {
        m: it.intern(dotted),
        s: EMPTY,
    }];
    for (at, _) in dotted.match_indices('.') {
        out.push(Ent {
            m: it.intern(&dotted[..at]),
            s: it.intern(&dotted[at + 1..]),
        });
    }
    out
}

const WRAPPER_STEPS: [&str; 7] = [
    "await",
    "ifexp:body",
    "ifexp:else",
    "star",
    "bool:and",
    "bool:or",
    "walrus:",
];
const TARGET_STEPS: [&str; 6] = [
    "assign-target",
    "annassign-target",
    "augassign-target",
    "for-target",
    "comp:target",
    "walrus-target",
];
const UP_POSITIONS: [(&str, &str); 44] = [
    ("arg:", "argument"),
    ("kw:", "argument"),
    ("returns", "annotation"),
    ("return", "return"),
    ("yield", "yield"),
    ("assign:", "store"),
    ("annassign:", "store"),
    ("augassign:", "store"),
    ("for-iter", "iterate"),
    ("comp:iter", "iterate"),
    ("base:", "base"),
    ("classkw:", "class-keyword"),
    ("decorator", "decorator"),
    ("param-ann:", "annotation"),
    ("annotation", "annotation"),
    ("typealias:", "annotation"),
    ("typeparam", "annotation"),
    ("param-default:", "default"),
    ("lambda:default", "default"),
    ("except", "except"),
    ("raise", "raise"),
    ("cmp:", "compare"),
    ("elt:", "element"),
    ("dict:", "element"),
    ("comp:elt", "element"),
    ("comp:key", "element"),
    ("comp:val", "element"),
    ("slice", "index"),
    ("expr", "statement"),
    ("with-target", "target"),
    ("with", "context-manager"),
    ("case-guard", "test"),
    ("case", "match"),
    ("if-test", "test"),
    ("while-test", "test"),
    ("assert", "test"),
    ("ifexp:test", "test"),
    ("unary:not", "test"),
    ("bin:", "operand"),
    ("unary:", "operand"),
    ("fstr", "format"),
    ("tstr", "format"),
    ("del", "delete"),
    ("lambda", "lambda"),
];

/// The neutral use position of the escape path above a name.
pub fn up_position(up: &str) -> &'static str {
    let mut steps = up.split(' ').peekable();
    while steps
        .peek()
        .is_some_and(|step| WRAPPER_STEPS.iter().any(|w| step.starts_with(w)))
    {
        steps.next();
    }
    let Some(first) = steps.next().filter(|step| !step.is_empty()) else {
        return "other";
    };
    if TARGET_STEPS.iter().any(|t| first.starts_with(t)) {
        return "target";
    }
    UP_POSITIONS
        .iter()
        .find(|(prefix, _)| first.starts_with(prefix))
        .map_or("other", |(_, position)| position)
}

/// Python's `\w`: a letter, digit or underscore.
fn is_word(c: char) -> bool {
    c == '_' || c.is_alphanumeric()
}

/// A chain's tokens as Python's `\.\w+|\(\)|\[\]` finds them: `.name`, `()` or `[]`.
pub fn chain_tokens(chain: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut rest = chain;
    while !rest.is_empty() {
        if let Some(after) = rest.strip_prefix('.') {
            let len: usize = after
                .chars()
                .take_while(|&c| is_word(c))
                .map(char::len_utf8)
                .sum();
            if len > 0 {
                out.push(&rest[..1 + len]);
                rest = &after[len..];
                continue;
            }
        } else if rest.starts_with("()") || rest.starts_with("[]") {
            out.push(&rest[..2]);
            rest = &rest[2..];
            continue;
        }
        let skip = rest.chars().next().map_or(1, char::len_utf8);
        rest = &rest[skip..];
    }
    out
}

/// Whether a text matches a glob whose only wildcard is `*`, any run of characters (as
/// `fnmatch.fnmatchcase` reads such a pattern): `proj.settings.*`.
fn glob_matches(pattern: &str, text: &str) -> bool {
    let mut parts = pattern.split('*');
    let first = parts.next().unwrap_or("");
    let Some(mut rest) = text.strip_prefix(first) else {
        return false;
    };
    let middle: Vec<&str> = parts.collect();
    let Some((last, inner)) = middle.split_last() else {
        return rest.is_empty();
    };
    for part in inner {
        match rest.find(part) {
            Some(at) => rest = &rest[at + part.len()..],
            None => return false,
        }
    }
    rest.len() >= last.len() && rest.ends_with(last)
}

/// Whether a callee, as the code writes it, is one `names` lists: by its last name, or, for an
/// entry with a dot, by its last dotted parts (`patch.dict`, `subprocess.run`).
fn names_callee(callee: &str, names: &[String]) -> bool {
    let last = callee.rsplit('.').next().unwrap_or("");
    names.iter().any(|name| {
        if name.contains('.') {
            callee == name
                || callee
                    .strip_suffix(name.as_str())
                    .is_some_and(|head| head.ends_with('.'))
        } else {
            name == last
        }
    })
}

/// Where a string stands in a call that takes a dotted path.
#[derive(Clone, Copy, PartialEq, Eq)]
enum PathArgument {
    /// No such place.
    None,
    /// The first argument of a path callee: a sink.
    Sink,
    /// The first argument of a loader, or an element of a list or tuple that is a path callee's
    /// first argument (an argument vector, as in `python -m <module>`): a path place.
    Place,
}

fn path_argument(up: &str, settings: &Producing) -> PathArgument {
    let mut steps = up.split(' ');
    let mut first = steps.next().unwrap_or("");
    let element = first == "elt:list" || first == "elt:tuple";
    if element {
        first = steps.next().unwrap_or("");
    }
    let (kind, rest) = first.split_once(':').unwrap_or((first, ""));
    let (position, callee) = rest.split_once(':').unwrap_or((rest, ""));
    if kind != "arg" || position != "0" {
        return PathArgument::None;
    }
    if names_callee(callee, &settings.path_callees) {
        return if element {
            PathArgument::Place
        } else {
            PathArgument::Sink
        };
    }
    if !element && names_callee(callee, &settings.loader_last_names) {
        return PathArgument::Place;
    }
    PathArgument::None
}

/// Whether a module-level string is an item of the literal one of `lists` is bound to
/// (`elt:list assign:NAME`, a tuple or an annotated binding too).
fn in_element_list(up: &str, lists: &FxHashSet<String>) -> bool {
    let Some(rest) = up
        .strip_prefix("elt:list ")
        .or_else(|| up.strip_prefix("elt:tuple "))
    else {
        return false;
    };
    let name = rest
        .strip_prefix("assign:")
        .or_else(|| rest.strip_prefix("annassign:"));
    name.is_some_and(|name| lists.contains(name))
}

/// A value of the module's lazy (PEP 562) map: the entry `dict:val:<name>` of a module-level
/// assignment, whose text is the module the module loads `<name>` from on first read.
fn in_lazy_map(up: &str, text: &str, lazy: &[(String, String)]) -> bool {
    let mut steps = up.split(' ');
    let (Some(entry), Some(statement), None) = (steps.next(), steps.next(), steps.next()) else {
        return false;
    };
    let Some(name) = entry.strip_prefix("dict:val:") else {
        return false;
    };
    let assigned = statement.starts_with("assign:") || statement.starts_with("annassign:");
    assigned
        && lazy
            .iter()
            .any(|(lazy, module)| lazy == name && module == text)
}

fn patch_role(up: &str, patch_callees: &[String]) -> bool {
    let first = up.split(' ').next().unwrap_or("");
    let (kind, rest) = first.split_once(':').unwrap_or((first, ""));
    let last = |callee: &str| callee.rsplit('.').next().unwrap_or("").to_string();
    match kind {
        "arg" => {
            let (position, callee) = rest.split_once(':').unwrap_or((rest, ""));
            position == "0" && patch_callees.contains(&last(callee))
        }
        "kw" => {
            let (keyword, callee) = rest.split_once(':').unwrap_or((rest, ""));
            keyword == "target" && patch_callees.contains(&last(callee))
        }
        _ => false,
    }
}

/// Where an `exposes` edge is written.
#[derive(Clone, Copy)]
struct SlotSite {
    owner: usize,
    scope: i32,
    line: u32,
    col: u32,
    ctx: u8,
}

/// Where the names of a slot's annotation are looked up: the def's file, scope and line.
#[derive(Clone, Copy, PartialEq, Eq, Hash)]
pub struct SlotLookup {
    owner: usize,
    scope: i32,
    line: u32,
}

/// Where a handed-out name leads: a def (the file's scope and the def's index), or nowhere the
/// engine can follow.
enum Followed {
    Def(usize, u32),
    Unfollowed,
}

/// How many type aliases deep one slot's names are followed.
const MAX_ALIAS_DEPTH: usize = 32;

/// A dotted name in a type expression: whether it is the whole annotation, and where it is looked
/// up (an alias's names are looked up where the alias is assigned).
#[derive(Clone, Copy)]
struct Mention {
    value: Sid,
    whole: bool,
    lookup: SlotLookup,
}

/// A name a type alias expands to: whether it sits at the top of the expansion (the whole
/// annotation, when the alias is), where it is looked up, whether the expansion stopped at it
/// because the alias it names is too deep to follow, whether it is given arguments, and the
/// generic it is an argument of, as written (EMPTY for none). An item with `unread` stands for a
/// part of the alias's value hoglint cannot read as types, and names nothing.
#[derive(Clone, Copy, PartialEq, Eq, Hash)]
pub struct Expanded {
    value: Sid,
    top: bool,
    lookup: SlotLookup,
    too_deep: bool,
    subscripted: bool,
    argument_of: Sid,
    unread: bool,
}

/// Whether a name is one `wanted` holds, by its spelling or by its target.
fn named_in(wanted: &[Sid], surface: Sid, target: Option<Sid>) -> bool {
    wanted.contains(&surface) || target.is_some_and(|name| wanted.contains(&name))
}

/// `alias_value`: whether an assigned value reads as a type alias: a name, an attribute, a
/// subscript or a union (`a | b`).
fn alias_value(value: &V) -> bool {
    matches!(value, V::Chain(_) | V::Attr(..) | V::Sub(..) | V::Bin(..))
}

/// The union of type expressions, as `a | b | c` reads; a part no type expression reads when
/// there are none.
fn union(items: &[V]) -> V {
    items
        .iter()
        .cloned()
        .reduce(|left, right| V::Bin(true, Box::new(left), Box::new(right)))
        .unwrap_or(V::Other)
}

/// A type expression with each bare name a PEP 695 type parameter binds replaced by its bound,
/// also inside a string annotation. A tuple of constraints reads as their union.
fn substitute(value: &V, bounds: &[(Sid, V)]) -> V {
    let each = |items: &[V]| items.iter().map(|item| substitute(item, bounds)).collect();
    match value {
        V::Chain(name) => match bounds.iter().find(|(param, _)| param == name) {
            Some((_, V::Tuple(items))) => union(items),
            Some((_, bound)) => bound.clone(),
            None => value.clone(),
        },
        V::Str(text, Some(parsed)) => V::Str(*text, Some(Box::new(substitute(parsed, bounds)))),
        V::Sub(inner, index) => V::Sub(
            Box::new(substitute(inner, bounds)),
            Box::new(substitute(index, bounds)),
        ),
        V::Bin(pipe, l, r) => V::Bin(
            *pipe,
            Box::new(substitute(l, bounds)),
            Box::new(substitute(r, bounds)),
        ),
        V::Tuple(items) => V::Tuple(each(items)),
        V::List(items) => V::List(each(items)),
        V::Star(inner) => V::Star(Box::new(substitute(inner, bounds))),
        _ => value.clone(),
    }
}

/// An annotation as its PEP 695 type parameters' bounds make it (`substitute`); as written when
/// no parameter has a bound.
fn bounded<'v>(annotation: &'v V, bounds: &[(Sid, V)]) -> Cow<'v, V> {
    if bounds.is_empty() {
        Cow::Borrowed(annotation)
    } else {
        Cow::Owned(substitute(annotation, bounds))
    }
}

/// Whether a class base is a call, also under a subscript: `make()`, `make()[T]`.
fn called_base(base: &V) -> bool {
    match base {
        V::Sub(inner, _) => called_base(inner),
        V::Call(..) => true,
        _ => false,
    }
}

/// A name in a type expression.
struct Named {
    name: Sid,
    /// Whether it is the whole annotation.
    whole: bool,
    /// Whether it is given arguments (`dict[str, str]`, not `dict`).
    subscripted: bool,
    /// The generic it is an argument of, as written (EMPTY for none).
    argument_of: Sid,
}

/// A type alias, as (module, name).
type AliasKey = (Sid, Sid);

/// A type alias a mention names: its key, the (file, assign row) of its value, and where its
/// names are looked up.
#[derive(Clone, Copy)]
struct Alias {
    key: AliasKey,
    row: (u32, u32),
    lookup: SlotLookup,
}

/// One alias's expansion while it is worked out: the aliases above the current one (`stack`),
/// the (alias, top) pairs followed, and the names written, each once, in order.
struct Expansion {
    stack: Vec<AliasKey>,
    followed: FxHashSet<(AliasKey, bool)>,
    seen: FxHashSet<Expanded>,
    out: Vec<Expanded>,
}

impl Expansion {
    fn write(&mut self, item: Expanded) {
        if self.seen.insert(item) {
            self.out.push(item);
        }
    }
}

/// What every `exposes` row of one callable shares. `handed` is the clause a handed-out callable
/// comes by, EMPTY for the module's own.
#[derive(Clone, Copy)]
struct SlotRows {
    site: SlotSite,
    callable: Sid,
    handed: Sid,
}

/// Annotated single-name assignments by the def they sit in, as indexes into `assigns`: the
/// candidate dataclass fields.
fn fields_by_class(it: &Interner, assigns: &[Assign]) -> FxHashMap<i32, Vec<u32>> {
    let mut fields: FxHashMap<i32, Vec<u32>> = FxHashMap::default();
    for (at, row) in assigns.iter().enumerate() {
        if row.op == Op::Annotated
            && row.targets.len() == 1
            && let V::Chain(name) = &row.targets[0]
            && !it.s(*name).contains('.')
        {
            fields.entry(row.s).or_default().push(at as u32);
        }
    }
    fields
}

/// What tells a slot's rows of one name apart, as the attrs are
/// made of it.
#[derive(Clone, Copy, PartialEq, Eq, Hash)]
struct Marks {
    /// The `alias` attr: expanded, too deep, or EMPTY for none.
    alias: Sid,
    /// Whether the name is given arguments.
    subscripted: bool,
    /// The generic it is an argument of, as it resolves.
    argument_of: Option<Sid>,
}

impl Marks {
    /// The attrs, in the order every row writes them.
    fn attrs(self, k: &Keys) -> Vec<(Sid, Sid)> {
        let mut out = Vec::new();
        if self.alias != EMPTY {
            out.push((k.alias, self.alias));
        }
        if self.subscripted {
            out.push((k.subscripted, k.one));
        }
        if let Some(surface) = self.argument_of {
            out.push((k.argument_of, surface));
        }
        out
    }
}

/// An alias expansion row of a slot, as written once: (surface, whole position, marks).
type AliasRow = (Sid, bool, Marks);

/// What a class-body row of a class is a slot of.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Member {
    /// A field of a dataclass-like class's `__init__`.
    Field,
    /// An attribute of a class that is no dataclass.
    Attribute,
}

impl Member {
    fn prefix(self) -> &'static str {
        match self {
            Member::Field => "field",
            Member::Attribute => "attr",
        }
    }
}

/// A field of a dataclass's generated `__init__`: its assign row (file and index), where its
/// names are looked up, and whether the class itself declares it (else a base does).
#[derive(Clone, Copy)]
struct FieldRow {
    file: u32,
    at: u32,
    lookup: SlotLookup,
    own: bool,
}

/// How a value's calls are read (`Producers::call_items`).
#[derive(Clone, Copy, PartialEq, Eq)]
enum Reading {
    /// As the items of a list core registers.
    List,
    /// As a declaration a registry's loader reads: a call also holds what its arguments name.
    Declaration,
}

/// Where a value is read: its file's scope, its module, the scope its names are looked up in
/// (-1 for the module's, else a class body's), the line they resolve before, the name whose
/// binding is read (EMPTY for none), which a value that reads it again names (`W = W + [B]`),
/// and whether the row is the name's first binding in that scope.
#[derive(Clone, Copy)]
struct Place {
    owner: usize,
    module: Sid,
    scope: i32,
    line: u32,
    binding: Sid,
    first: bool,
}

/// The nodes only an assigned value's JSON text spells out (`value::Encoder::assigned`).
const SPELLED_NODES: [&str; 4] = ["IfExp", "ListComp", "SetComp", "GeneratorExp"];

/// The opaque constants a method can be called on (`"""...""".strip()`).
const CONSTANT_NODES: [&str; 3] = ["longstr", "badstr", "JoinedStr"];

/// How deep `registrable` follows names that hold names before it calls an argument an item.
const MAX_REGISTRABLE_DEPTH: usize = 16;

/// A file's assignment rows as the extractor wrote them: the record's JSON text split into rows
/// on first use, and a row's value parsed when it is asked for. Most rows of a file are never
/// read, so no value tree is built for them.
struct AssignText {
    facts: Arc<FactSet>,
    file: usize,
    rows: Option<Option<Vec<Box<RawValue>>>>,
    values: FxHashMap<usize, Option<J>>,
}

/// The value of one assignment row; the other keys are skipped.
#[derive(Deserialize)]
struct AssignValue {
    v: Option<J>,
}

impl AssignText {
    fn new(facts: Arc<FactSet>, file: usize) -> AssignText {
        AssignText {
            facts,
            file,
            rows: None,
            values: FxHashMap::default(),
        }
    }

    /// The value of assignment row `at`; None when the text cannot be read.
    fn value(&mut self, at: usize) -> Option<&J> {
        if !self.values.contains_key(&at) {
            let (facts, file) = (&self.facts, self.file);
            let rows = self
                .rows
                .get_or_insert_with(|| match facts.raw(file, "assigns") {
                    Ok(Some(text)) => serde_json::from_slice::<Vec<Box<RawValue>>>(&text).ok(),
                    _ => None,
                });
            let value = (rows.as_ref())
                .and_then(|rows| rows.get(at))
                .and_then(|row| serde_json::from_str::<AssignValue>(row.get()).ok())
                .and_then(|row| row.v);
            self.values.insert(at, value);
        }
        self.values[&at].as_ref()
    }
}

/// How an assignment row touches a name it names (`Producers::touches`).
#[derive(Clone, Copy, PartialEq, Eq)]
enum Touch {
    /// `=` or `:` binds the name to the value.
    Bind,
    /// The value adds to the name's items in place: `+=`, `name[key] = value`, or a method that
    /// changes it at module level (`name.append(item)`: the call's arguments).
    Add,
    /// The element at this index of a tuple or list that a target of the same length unpacks.
    Element(usize),
    /// The elements from the first index up to the second that a starred target takes.
    Elements(usize, usize),
    /// Anything else: another augmented assignment, an unpacking the link cannot pair. What the
    /// name holds cannot be told.
    Other,
    /// An escape (`FileFacts::escapes`): code the reading does not understand may change the
    /// value in place, so a value that can grow, or holds items already, cannot be told.
    Escaped,
}

/// The row an escape (`FileFacts::escapes`) touches a name with: no value, so it adds no item.
static ESCAPED: Assign = Assign {
    s: -1,
    l: 0,
    c: 0,
    f: 0,
    op: Op::Other,
    targets: Vec::new(),
    value: None,
    ann: None,
};

/// The row a binding the reading cannot read touches a name with (`=name`, an import after a
/// binding): no value; `unread_cause` tells it apart from an escape.
static REBOUND: Assign = Assign {
    s: -1,
    l: 0,
    c: 0,
    f: 0,
    op: Op::Other,
    targets: Vec::new(),
    value: None,
    ann: None,
};

/// One assignment row that touches a name, and how.
#[derive(Clone, Copy)]
struct Touching<'r> {
    at: usize,
    row: &'r Assign,
    touch: Touch,
}

/// A holder of a name's value (`Producers::aliases`): a change of the holder taints the held
/// name when the holder may be the value itself (`same`); escaping or a rebinding the reading
/// cannot read always does; `record` marks a record (D126), which a second binding taints too.
#[derive(Clone, Copy)]
struct Link {
    holder: Sid,
    held: Sid,
    same: bool,
    record: bool,
}

/// The rows that touch each name a file binds, by the key `sequences` stores the name under,
/// with the bare name.
type Touches<'r> = FxHashMap<Sid, (Sid, Vec<Touching<'r>>)>;

/// The arguments of a call as the extractor wrote it: positional (`*x` too), then each keyword's
/// value (`**x` too).
fn call_arguments(call: &serde_json::Map<String, J>) -> impl Iterator<Item = &J> {
    let args = call.get("args").and_then(J::as_array).into_iter().flatten();
    let keywords = call.get("kw").and_then(J::as_array).into_iter().flatten();
    args.chain(keywords.filter_map(|pair| pair.as_array()?.get(1)))
}

/// The names an unpacking target binds (`a, *b = ...`).
fn unpacked_targets(target: &V, out: &mut Vec<Sid>) {
    match target {
        V::Chain(name) => out.push(*name),
        V::Tuple(items) | V::List(items) => {
            for item in items {
                unpacked_targets(item, out);
            }
        }
        V::Star(inner) => unpacked_targets(inner, out),
        _ => {}
    }
}

/// Why a name's rows do not read exactly, when a binding says it: bound more than once
/// (`bound`), or bound again by a form the reading cannot read (`rebound`: `=name`, an import
/// after a binding); None for any other cause (an escape, an alias, `*`, a block).
fn unread_cause(rows: &[Touching]) -> Option<&'static str> {
    let bindings = (rows.iter())
        .filter(|t| {
            matches!(
                t.touch,
                Touch::Bind | Touch::Element(_) | Touch::Elements(..)
            )
        })
        .count();
    if bindings > 1 {
        return Some("bound");
    }
    let rebound = |t: &&Touching| t.touch == Touch::Other && std::ptr::eq(t.row, &REBOUND);
    rows.iter().any(|t| rebound(&t)).then_some("rebound")
}

/// Builds the core edges of every analyzed file, producer by producer.
pub struct Producers<'p> {
    pub core: &'p mut Core,
    pub scopes: &'p mut [Scope],
    pub settings: &'p Producing,
    /// `config.first_party` or, when empty, every repo top-level module.
    pub first_party: &'p FxHashSet<Sid>,
    pub edges: EdgeStore,
    pub symbols: Symbols,
    /// Per module: name -> (file, assign row) of the value each module-level name is assigned,
    /// read from the file the module table reads the name from.
    pub aliases: FxHashMap<Sid, FxHashMap<Sid, (u32, u32)>>,
    /// The scope of each analyzed file, by file (`Link.scope_of_file`).
    pub scope_of_file: &'p FxHashMap<u32, u32>,
    /// What each type alias expands to, by alias and the place its names are looked up.
    pub expansions: FxHashMap<(AliasKey, SlotLookup), Rc<[Expanded]>>,
    /// The candidate dataclass fields of each file, by file (`fields_by_class`).
    pub fields: FxHashMap<u32, FxHashMap<i32, Vec<u32>>>,
    /// The contract roots of each file, by its scope (`contract_roots`).
    pub roots: FxHashMap<usize, Vec<(usize, String)>>,
    /// What each export module hands out by import (`record_hand_out`).
    pub hand_outs: FxHashMap<Sid, HandOut>,
    /// Whether each class has a dataclass base, by (owner, def index) (`has_dataclass_base`).
    pub dataclass_bases: FxHashMap<(usize, u32), bool>,
    /// What each module-level sequence of names holds (`sequences`).
    pub sequences: Sequences,
    /// The classes, by (module, qualified name), whose attributes a reference edge reads as
    /// plain attributes (`Class.ATTR`, `Class().ATTR`, `Outer.Inner.ATTR`): `sequences` reads
    /// their class-body bindings too (`read_classes`).
    pub read_classes: FxHashSet<(Sid, Sid)>,
    /// What each name a declaration names holds, read as a declaration where it is bound, by
    /// (module, key); merged into `sequences` once every file is read (`bound_name`).
    pub declared: Sequences,
    /// Whether each name a library call is handed can be registered (`registrable`).
    pub registrable: FxHashMap<Ent, bool>,
    /// (module, module it reads by a computed name: `getattr(alias, NAME)`, `vars(alias)`)
    /// (`alias_references`).
    pub computed_reads: FxHashSet<(Sid, Sid)>,
    /// What each class attribute a value reads past a class holds (`Base.WORKFLOWS`), read as a
    /// list's items, by (module, key); merged into `sequences` with `declared` (`bound_name`).
    pub attributes: Sequences,
    /// Every name a module or a class body binds by assignment, constants too, by (module, key).
    pub bound: FxHashSet<(Sid, Sid)>,
    /// The defs and classes the body of each class in `read_classes` defines, as
    /// (module, `Class.NAME`): a property read off an instance is one of them.
    pub defined: FxHashSet<(Sid, Sid)>,
    /// The module-level names bound to a list or tuple of string literals that the reading reads
    /// exactly (`holds_paths`), by (module, name).
    pub path_lists: FxHashSet<(Sid, Sid)>,
    /// Each place a module names a module by a value the link cannot read exactly, by module, in
    /// words (`computed_loads`).
    pub computed_loads: Vec<(Sid, String)>,
    /// Why a name the reading cannot read is unread, when a binding says it, by (module, key):
    /// "bound" (bound more than once) or "rebound" (bound again by a form it cannot read).
    pub unread_causes: FxHashMap<(Sid, Sid), &'static str>,
    /// The names a module binds by an import only and binds again by a form the reading cannot
    /// read (`rebound_imports`), by (module, name).
    pub rebound_imports: FxHashSet<(Sid, Sid)>,
    /// The names stored into each module from any file and scope (`stores`: `m.name = v`,
    /// `setattr(m, "name", v)`, `m.__dict__["name"] = v`), by module; `*` for a computed name.
    /// Each is a binding of that name in the module.
    pub foreign: FxHashMap<Sid, FxHashSet<Sid>>,
    /// The attributes stored on each class the graph defines, from any file and scope
    /// (`stores`), by class; `*` for a computed name.
    pub class_stores: FxHashMap<Ent, FxHashSet<Sid>>,
    /// Each file's values that may be a module, a class or a function, used where no entry
    /// accounts for them (`values`), by owner.
    pub values: FxHashMap<usize, Vec<Held>>,
    /// The classes some file uses where no entry accounts for them (`values`).
    pub touched_classes: FxHashSet<Ent>,
    /// The first-party modules some file uses where no entry accounts for them (`values`).
    pub touched_modules: FxHashSet<Sid>,
    /// Classes handed to a record constructor: (class, owner, scope, line, callee). Accounted
    /// only when the callee builds a record (`touched`).
    pub handed: Vec<(Ent, usize, i32, u32, String)>,
    /// The classes `touched` is deciding, against a loop through record callees.
    pub touching: FxHashSet<Ent>,
}

/// A value a `!` row names, as the reading file resolves it (`Producers::values`).
#[derive(Clone, Copy, Debug)]
pub enum Held {
    /// A first-party module.
    Module(Sid),
    /// A class the graph defines; reached through a dunder attribute (its module then too).
    Class(Ent, bool),
    /// A function the graph defines; reached through a dunder attribute.
    Def(Ent, bool),
    /// A first-party name of another kind (`plain_variable`).
    Variable(Ent),
    /// An attribute of a class.
    Member,
    /// Anything else: any module.
    Any,
}

/// What each module-level sequence of names holds, by (module, name): per item, the entity its
/// name resolves to, or None for an item that cannot be read. Rules read it after the fact rows
/// are released (`Link::sequences`), to need the modules that define a registered list's items.
pub type Sequences = FxHashMap<(Sid, Sid), Vec<Option<Ent>>>;

impl Producers<'_> {
    #[allow(clippy::too_many_arguments)]
    fn push(
        &mut self,
        kind: u8,
        owner: usize,
        scope: i32,
        line: u32,
        col: u32,
        ctx: u8,
        surface: Sid,
        resolved: Resolved,
        use_: Sid,
        attrs: &[(Sid, Sid)],
    ) {
        let via_start = self.edges.via.len() as u32;
        if let Some(hit) = resolved.via {
            self.edges.via.extend_from_slice(self.core.via(hit));
        }
        let via_len = self.edges.via.len() as u32 - via_start;
        let attr_start = self.edges.attrs.len() as u32;
        self.edges.attrs.extend_from_slice(attrs);
        self.edges.rows.push(EdgeRow {
            kind,
            res: resolved.res,
            ctx,
            scope,
            file: self.scopes[owner].file,
            owner: owner as u32,
            line,
            col,
            surface,
            target: resolved.target,
            use_,
            via: (via_start, via_len),
            attrs: (attr_start, attrs.len() as u32),
        });
    }

    /// Runs one producer for every analyzed file, each file's reads going to its own trace.
    fn each(&mut self, produce: fn(&mut Self, usize)) {
        let owners = self.scopes.len();
        for owner in 0..owners {
            self.core.rec.enter(owner, owners);
            produce(self, owner);
            self.core.rec.leave();
        }
    }

    pub fn run(&mut self) {
        self.each(Self::imports);
        self.each(Self::references);
        self.read_classes();
        self.each(Self::stores);
        self.each(Self::values);
        self.each(Self::sequences);
        let read = std::mem::take(&mut self.declared);
        let attributes = std::mem::take(&mut self.attributes);
        for (key, items) in read.into_iter().chain(attributes) {
            self.sequences.entry(key).or_default().extend(items);
        }
        self.each(Self::declarations);
        self.each(Self::strings);
        self.each(Self::dynamic_imports);
        self.each(Self::unparsed);
        self.core.rec.finish();
    }

    /// (surface, hit, loaded module) of `from source import name`.
    fn from_target(core: &mut Core, source: Sid, name: Sid) -> (Sid, Option<HitId>, Sid) {
        let surface = core.it.join(source, name);
        let mut hit = core.resolve_name(source, name);
        if hit.is_none() && core.exists(surface) {
            hit = Some(core.module_hit(surface));
        }
        let loaded = if core.exists(surface) {
            surface
        } else {
            source
        };
        (surface, hit, loaded)
    }

    fn bound_attrs(&mut self, binds: Sid, loaded: Sid) -> Vec<(Sid, Sid)> {
        let k = &self.settings.k;
        let mut attrs = vec![(k.binds, binds)];
        if self.core.exists(loaded) {
            attrs.push((k.module, loaded));
        }
        attrs
    }

    /// An edge per lazy (PEP 562) name, at the dict entry that names its module: the import
    /// `__getattr__` runs when the name is first read. It does not run on import, so its context
    /// is `function_local`, and its `lazy` attr tells it apart from an import in a function.
    fn lazy_imports(&mut self, owner: usize) {
        let file = self.scopes[owner].file;
        let module = self.scopes[owner].module;
        let Some(table) = self.core.table(module) else {
            return;
        };
        let table = self.core.table_at(table);
        if table.record != Some(file) || table.lazy_entries.is_empty() {
            return;
        }
        let entries = table.lazy_entries.clone();
        let ctx = self.core.contexts(&mut self.scopes[owner], FUNC, -1);
        for entry in entries {
            let (name, line, col) = (entry.name, entry.line, entry.col);
            // Read as `from <module> import <name>`: a third-party module makes an external edge, a
            // first-party one no module answers for an edge that does not resolve.
            let (surface, hit, loaded) = Self::from_target(self.core, entry.module, name);
            let mut attrs = self.bound_attrs(name, loaded);
            let k = &self.settings.k;
            attrs.push((k.lazy, k.one));
            let resolved = self.core.classify(surface, hit);
            self.push(
                IMPORT, owner, -1, line, col, ctx, surface, resolved, EMPTY, &attrs,
            );
        }
    }

    /// Whether an `[exports]` entry covers a file that is no test: a module whose hand-outs core
    /// records.
    fn is_export_module(&self, owner: usize) -> bool {
        let scope = &self.scopes[owner];
        let path = &self.core.facts.files[scope.file as usize].path;
        let covered = self
            .settings
            .export_surfaces
            .iter()
            .any(|surface| surface.covered_root(path).is_some());
        scope.base_ctx & ctx::TEST == 0 && covered
    }

    /// One edge per name a star import binds, as `from source import name` would bind it.
    fn star_name_edges(&mut self, owner: usize, row: &Import, source: Sid, ctx: u8) {
        let names = star_names(self.core, source).unwrap_or_default();
        for name in names {
            let (surface, hit, loaded) = Self::from_target(self.core, source, name);
            let mut attrs = self.bound_attrs(name, loaded);
            let k = &self.settings.k;
            attrs.push((k.star_attr, k.one));
            let resolved = self.core.classify(surface, hit);
            self.push(
                IMPORT, owner, row.scope, row.line, row.col, ctx, surface, resolved, EMPTY, &attrs,
            );
        }
    }

    /// One edge per import binding, in every context. In an export module a star import also
    /// gets one edge per name it binds, at the star's line, so what the module hands out is always
    /// an edge that binds the name; its `star` attr tells it apart from a named import.
    fn imports(&mut self, owner: usize) {
        let facts = self.core.facts.clone();
        let file = self.scopes[owner].file as usize;
        let export = self.is_export_module(owner);
        for row in &facts.files[file].imports {
            let core = &mut *self.core;
            let scope = &mut self.scopes[owner];
            let ctx = core.contexts(scope, row.flags, row.scope);
            let (module, is_package) = (scope.module, scope.is_package);
            let (bound, surface, source) = import_surface(&mut core.it, module, is_package, row);
            let Some(source) = source else {
                // The name stays bound, so a reader of what the module hands out sees it unresolved.
                let attrs = [(self.settings.k.binds, bound)];
                self.push(
                    IMPORT, owner, row.scope, row.line, row.col, ctx, surface, UNRESOLVED, EMPTY,
                    &attrs,
                );
                continue;
            };
            let (hit, loaded) = match row.name {
                Some(name) if name != self.settings.k.star => {
                    let (_, hit, loaded) = Self::from_target(core, source, name);
                    (hit, loaded)
                }
                _ => (Some(core.module_hit(source)), source),
            };
            let mut attrs = self.bound_attrs(bound, loaded);
            if facts.files[file].listed.contains(&bound) {
                attrs.push((self.settings.k.listed, self.settings.k.one));
            }
            let resolved = self.core.classify(surface, hit);
            self.push(
                IMPORT, owner, row.scope, row.line, row.col, ctx, surface, resolved, EMPTY, &attrs,
            );
            if export && row.name == Some(self.settings.k.star) {
                self.star_name_edges(owner, row, source, ctx);
            }
        }
        self.lazy_imports(owner);
    }

    /// The classes whose attributes reference edges read as plain attributes, each class on the
    /// way (`Outer`, `Outer.Inner` of `Outer.Inner.WORKFLOWS`; `Class` of `Class().ATTR`), so
    /// `sequences` reads the class-body bindings a registration may read. A chain that calls or
    /// subscripts stops there.
    fn read_classes(&mut self) {
        let chain_key = self.settings.k.chain;
        for at in 0..self.edges.rows.len() {
            let row = &self.edges.rows[at];
            let Some(target) = row.target.filter(|target| target.s != EMPTY) else {
                continue;
            };
            if row.kind != REFERENCE {
                continue;
            }
            let Some(&(_, chain)) = self
                .edges
                .attrs_of(row)
                .iter()
                .find(|(key, _)| *key == chain_key)
            else {
                continue;
            };
            let text = self.core.it.s(chain);
            let path = text.strip_prefix("()").unwrap_or(text);
            let Some(rest) = path.strip_prefix('.') else {
                continue;
            };
            let mut class = self.core.it.s(target.s).to_string();
            let mut classes = vec![class.clone()];
            for part in rest.split('.') {
                if part.is_empty() || part.contains(['(', '[']) {
                    break;
                }
                class = format!("{class}.{part}");
                classes.push(class.clone());
            }
            for class in classes {
                let class = self.core.it.intern(&class);
                self.read_classes.insert((target.m, class));
            }
        }
    }

    /// Whether a value tree holds every part of a list's reading: no call, whose arguments only
    /// the record's text keeps, and no conditional or comprehension, which only it spells out.
    fn spelled(&self, value: &V) -> bool {
        match value {
            V::Call(..) => false,
            V::Opaque(what) => !SPELLED_NODES.contains(&self.core.it.s(*what)),
            V::List(items) | V::Tuple(items) | V::Set(items) | V::BoolOp(items) => {
                items.iter().all(|item| self.spelled(item))
            }
            V::Dict(entries) => entries.iter().all(|(key, item)| {
                key.as_ref().is_none_or(|key| self.spelled(key)) && self.spelled(item)
            }),
            V::Star(inner) | V::Unary(inner) => self.spelled(inner),
            V::Bin(_, left, right) | V::Sub(left, right) => {
                self.spelled(left) && self.spelled(right)
            }
            _ => true,
        }
    }

    /// Whether a row binds or adds a value that can grow in place: a list, set or dict, a
    /// comprehension, an operation on one, a call's result, or another name's value. A constant,
    /// a tuple, a subscript (a type alias, say), a `type` alias and a string method's result
    /// cannot.
    fn grows(&self, touching: &Touching) -> bool {
        // A `type` alias is a type, whatever its value spells.
        if !matches!(touching.touch, Touch::Bind | Touch::Add) || touching.row.op == Op::TypeAlias {
            return false;
        }
        match &touching.row.value {
            // A method of a string literal (`"""...""".strip()`) gives a string.
            Some(V::Call(callee, ..))
                if matches!(&**callee, V::Attr(receiver, _)
                if matches!(&**receiver, V::Str(..))
                    || matches!(&**receiver, V::Opaque(what) if CONSTANT_NODES.contains(&self.core.it.s(*what)))) =>
            {
                false
            }
            Some(V::List(_) | V::Set(_) | V::Dict(_) | V::Bin(..) | V::Call(..) | V::Chain(_)) => {
                true
            }
            Some(V::Opaque(what)) => SPELLED_NODES.contains(&self.core.it.s(*what)),
            _ => false,
        }
    }

    /// Whether a value tree reads `name`: names it, or an attribute of it.
    fn reads_name(&self, value: &V, name: Sid) -> bool {
        match value {
            V::Chain(chain) => {
                let (chain, name) = (self.core.it.s(*chain), self.core.it.s(name));
                chain == name
                    || chain
                        .strip_prefix(name)
                        .is_some_and(|rest| rest.starts_with('.'))
            }
            V::List(items) | V::Tuple(items) | V::Set(items) | V::BoolOp(items) => {
                items.iter().any(|item| self.reads_name(item, name))
            }
            V::Dict(entries) => entries.iter().any(|(key, item)| {
                key.as_ref().is_some_and(|key| self.reads_name(key, name))
                    || self.reads_name(item, name)
            }),
            V::Star(inner) | V::Unary(inner) | V::Attr(inner, _) => self.reads_name(inner, name),
            V::Sub(inner, index) => self.reads_name(inner, name) || self.reads_name(index, name),
            V::Bin(_, left, right) => self.reads_name(left, name) || self.reads_name(right, name),
            V::Call(callee, _, sole) => {
                self.reads_name(callee, name)
                    || sole
                        .as_ref()
                        .is_some_and(|sole| self.reads_name(sole, name))
            }
            _ => false,
        }
    }

    /// The key a binding is stored under: the name at module level; `Class.NAME` in the body of
    /// a class outside any function, of every class with `all_classes`, else of a class whose
    /// attributes a reference edge reads (`read_classes`); else None.
    fn binding_key(
        &mut self,
        owner: usize,
        scope: i32,
        name: Sid,
        all_classes: bool,
    ) -> Option<Sid> {
        if scope == -1 {
            return Some(name);
        }
        let file = &self.scopes[owner];
        if file.in_function[scope as usize] {
            return None;
        }
        let class = file.qualnames[scope as usize];
        if !all_classes && !self.read_classes.contains(&(file.module, class)) {
            return None;
        }
        let key = format!("{}.{}", self.core.it.s(class), self.core.it.s(name));
        Some(self.core.it.intern(&key))
    }

    /// The rows of a file that touch each name it binds, by key (`binding_key`), in source order,
    /// then what else touches them: escapes (`escape_touches`), star imports after a binding
    /// (`star_rebinds`), a loader handed a computed name (`loader_namespace`), and aliases
    /// (`aliases`). Class keys are read for every class, so an alias in any class body counts;
    /// only those of classes in `read_classes` are kept, unless `all_classes`. A bare annotation
    /// touches nothing.
    fn touches<'r>(
        &mut self,
        owner: usize,
        record: &'r FileFacts,
        all_classes: bool,
    ) -> Touches<'r> {
        let mut out: Touches<'r> = FxHashMap::default();
        for (at, row) in record.assigns.iter().enumerate() {
            let touch = match row.op {
                Op::Assign | Op::Annotated if row.value.is_none() => continue,
                Op::Assign | Op::Annotated | Op::TypeAlias => Touch::Bind,
                Op::Global if row.value.is_some() => Touch::Bind,
                Op::AddAssign | Op::Mutate | Op::OrAssign => Touch::Add,
                Op::Global | Op::Other => Touch::Other,
            };
            let mut named: Vec<(Sid, Touch)> = Vec::new();
            for target in &row.targets {
                match target {
                    V::Chain(name) => {
                        let text = self.core.it.s(*name);
                        if row.op == Op::Mutate {
                            if let Some((receiver, _)) = text.rsplit_once('.') {
                                let receiver = receiver.to_string();
                                named.push((self.core.it.intern(&receiver), touch));
                            }
                        } else if !text.contains('.') {
                            named.push((*name, touch));
                        }
                    }
                    V::Sub(inner, _) if row.s == -1 && touch == Touch::Bind => {
                        if let V::Chain(name) = &**inner
                            && !self.core.it.s(*name).contains('.')
                        {
                            named.push((*name, Touch::Add));
                        }
                    }
                    V::Tuple(items) | V::List(items) if row.s == -1 => {
                        self.unpacked(target, items, row, touch, &mut named);
                    }
                    V::Star(_) if row.s == -1 => {
                        let mut names = Vec::new();
                        unpacked_targets(target, &mut names);
                        named.extend(names.into_iter().map(|name| (name, Touch::Other)));
                    }
                    _ => {}
                }
            }
            for (name, touch) in named {
                let Some(key) = self.binding_key(owner, row.s, name, true) else {
                    continue;
                };
                let entry = out.entry(key).or_insert_with(|| (name, Vec::new()));
                entry.1.push(Touching { at, row, touch });
            }
        }
        let links = self.escape_touches(owner, record, &mut out);
        self.foreign_rebinds(owner, &mut out);
        self.import_rebinds(owner, record, &mut out);
        self.loader_namespace(owner, record, &mut out);
        self.aliases(&mut out, links);
        if !all_classes {
            let module = self.scopes[owner].module;
            out.retain(|key, _| {
                let Some((class, _)) = self.core.it.s(*key).rsplit_once('.') else {
                    return true;
                };
                let class = self.core.it.get(class);
                class.is_some_and(|class| self.read_classes.contains(&(module, class)))
            });
        }
        out
    }

    /// What each escape of the file (`FileFacts::escapes`) touches, and the aliases it names
    /// (returned, as (holder, held, same object), for `aliases`):
    /// - `name` (a class attribute `Class.ATTR` too) is `Escaped`; `name.*` (a class handed on,
    ///   `setattr`, `delattr`) makes every attribute of the class `Escaped`;
    /// - `name~TARGETS`: each target may be the name's value itself (`_X = W or []`,
    ///   `_X = _L[0]`); `name^TARGETS`: each target may hold it (`_L = [W]`, `_M = list(_L)`);
    /// - `name>TARGETS@CALLEES`: records built by the callees hold it (D126): when every callee is
    ///   a confirmed class (or `dataclasses.replace`) and the targets are module-level names,
    ///   each target is an alias; else the name is `Escaped`;
    /// - `=name`, bound again by a form the reading does not read, is `Other`, and `*` makes every
    ///   name `Other`, a tuple and a constant too.
    ///
    /// A name a class body uses is the class's own attribute when the class binds one, else the
    /// module's. A held class this file defines is every attribute its body binds: a holder of
    /// a holder is a holder.
    fn escape_touches<'r>(
        &mut self,
        owner: usize,
        record: &'r FileFacts,
        out: &mut Touches<'r>,
    ) -> Vec<Link> {
        let mut links = Vec::new();
        for escape in &record.escapes {
            let text = self.core.it.s(escape.name).to_string();
            let scope = escape.scope;
            let (keys, touch): (Vec<Sid>, Touch) = if text == "*" {
                (out.keys().copied().collect(), Touch::Other)
            } else if text.starts_with('@') || text.starts_with('!') {
                // A store into a module or a class (`stores`), or a value that may be a module,
                // a class or a function (`values`), read before any file.
                continue;
            } else if let Some((name, relied)) = text.split_once('?') {
                // A read that relied on these names' bindings: a binding of one from another
                // file or through a module object makes it an escape.
                let module = self.scopes[owner].module;
                let stored = self.foreign.get(&module);
                let mut rebound = self.module_touched(module)
                    || relied.split(',').any(|dep| {
                        stored.is_some_and(|names| {
                            (names.iter()).any(|n| {
                                matches!(self.core.it.s(*n), "*") || self.core.it.s(*n) == dep
                            })
                        })
                    });
                // `#Class.ATTR`: a read that took a class attribute's body binding as its type,
                // which holds only from a stable class (`stable_class`).
                let classes: Vec<(String, String)> = (relied.split(','))
                    .filter_map(|dep| dep.strip_prefix('#')?.rsplit_once('.'))
                    .map(|(class, attribute)| (class.to_string(), attribute.to_string()))
                    .collect();
                for (class, attribute) in classes {
                    let class = Ent {
                        m: module,
                        s: self.core.it.intern(&class),
                    };
                    rebound = rebound || !self.stable_class(class, &attribute);
                }
                if !rebound {
                    continue;
                }
                (self.escape_keys(owner, scope, name, out), Touch::Escaped)
            } else if let Some(name) = text.strip_prefix('=') {
                for key in self.escape_keys(owner, scope, name, out) {
                    if let Some(entry) = out.get_mut(&key) {
                        entry.1.push(Touching {
                            at: usize::MAX,
                            row: &REBOUND,
                            touch: Touch::Other,
                        });
                    }
                }
                continue;
            } else if let Some(class) = text.strip_suffix(".*") {
                let prefix = format!("{class}.");
                let keys = (out.keys().copied())
                    .filter(|key| scope == -1 && self.core.it.s(*key).starts_with(&prefix))
                    .collect();
                (keys, Touch::Escaped)
            } else if let Some((name, targets)) = text.split_once(['~', '^']) {
                let same = text.as_bytes()[name.len()] == b'~';
                let held = self.held_keys(owner, scope, name, out);
                for target in targets.split(',') {
                    for holder in self.target_keys(owner, scope, target, out) {
                        let link = |&held| Link {
                            holder,
                            held,
                            same,
                            record: false,
                        };
                        links.extend(held.iter().map(link));
                    }
                }
                continue;
            } else if let Some((name, rest)) = text.split_once('>') {
                let (targets, callees) = rest.split_once('@').unwrap_or((rest, ""));
                let held = self.held_keys(owner, scope, name, out);
                let classes = (callees.split(','))
                    .all(|callee| self.builds_record(owner, scope, escape.line, callee));
                // T4: `dataclasses.replace` copies a record whose class reads its fields with no
                // first-party code.
                let replaces = (callees.split(','))
                    .any(|callee| self.record_callee(owner, scope, escape.line, callee).0);
                let copied = !replaces
                    || held.iter().all(|key| {
                        let rows = out
                            .get(key)
                            .map(|(_, rows)| rows.clone())
                            .unwrap_or_default();
                        self.plain_record(owner, &rows)
                    });
                let proved = classes && self.callees_proved(owner, scope, escape.line, callees);
                if !(proved && copied && scope == -1) {
                    (held, Touch::Escaped)
                } else {
                    for target in targets.split(',') {
                        for holder in self.target_keys(owner, scope, target, out) {
                            let link = |&held| Link {
                                holder,
                                held,
                                same: true,
                                record: true,
                            };
                            links.extend(held.iter().map(link));
                        }
                    }
                    continue;
                }
            } else {
                (self.escape_keys(owner, scope, &text, out), Touch::Escaped)
            };
            for key in keys {
                if let Some(entry) = out.get_mut(&key) {
                    entry.1.push(Touching {
                        at: usize::MAX,
                        row: &ESCAPED,
                        touch,
                    });
                }
            }
        }
        links
    }

    /// The key a name a scope uses touches: in a class body the class's own attribute when the
    /// file binds one, else the module's name, when the file binds it.
    fn escape_keys(&mut self, owner: usize, scope: i32, name: &str, out: &Touches) -> Vec<Sid> {
        let name = self.core.it.intern(name);
        let own = self.binding_key(owner, scope, name, true);
        if let Some(own) = own.filter(|own| out.contains_key(own)) {
            return vec![own];
        }
        let module = (scope != -1)
            .then(|| self.binding_key(owner, -1, name, true))
            .flatten();
        module
            .into_iter()
            .filter(|key| out.contains_key(key))
            .collect()
    }

    /// The keys a held name stands for: its own (`escape_keys`), or, for a class this file
    /// defines (no key of its own), every attribute its body binds (`Class.NAME`).
    fn held_keys(&mut self, owner: usize, scope: i32, name: &str, out: &Touches) -> Vec<Sid> {
        let own = self.escape_keys(owner, scope, name, out);
        if !own.is_empty() || scope != -1 {
            return own;
        }
        let prefix = format!("{name}.");
        (out.keys().copied())
            .filter(|key| self.core.it.s(*key).starts_with(&prefix))
            .collect()
    }

    /// The key a binding of a scope binds a target under, when the file binds it.
    fn target_keys(&mut self, owner: usize, scope: i32, name: &str, out: &Touches) -> Vec<Sid> {
        let name = self.core.it.intern(name);
        let key = self.binding_key(owner, scope, name, true);
        key.into_iter()
            .filter(|key| out.contains_key(key))
            .collect()
    }

    /// Whether a callee a record is built by is `dataclasses.replace` (T4: `replace` is true,
    /// and `plain_record` judges the record it copies) or resolves to a bare record class
    /// (`bare_record`, T3).
    fn builds_record(&mut self, owner: usize, scope: i32, line: u32, callee: &str) -> bool {
        let (replace, class) = self.record_callee(owner, scope, line, callee);
        replace || class.is_some_and(|class| self.bare_record(class))
    }

    /// What a record's callee is: `dataclasses.replace`, or the class it resolves to.
    fn record_callee(
        &mut self,
        owner: usize,
        scope: i32,
        line: u32,
        callee: &str,
    ) -> (bool, Option<Ent>) {
        let core = &mut *self.core;
        let chain = core.it.intern(callee);
        let names = &self.settings.names;
        let (found, rest) = core.chain_lookup(names, &mut self.scopes[owner], chain, scope, line);
        let name = core.it.join_all(found.surface, &rest);
        if core.it.s(name) == "dataclasses.replace" {
            return (true, None);
        }
        let class = found
            .hit
            .filter(|hit| rest.is_empty() && core.kind(*hit) == HitKind::Class)
            .map(|hit| core.origin(hit));
        (false, class)
    }

    /// T3's class: plain, so building a record runs no code but its constructor's body (limit
    /// 1): no base, no class keyword (no metaclass), each decorator `dataclasses.dataclass` (with
    /// any arguments), no decorated def in the body (a property, its setter or another
    /// descriptor), no hook the construction invokes with the value (`__setattr__`,
    /// `__getattribute__`, `__getattr__`, `__set_name__`, defined or assigned in the body, or a
    /// default whose class has `__get__` or `__set__`: each default must be `inert_default`), no
    /// store on the class from any file (`class_stores`), every occurrence of it accounted
    /// (`touched`), and no first-party module value that may be its module used otherwise
    /// (`module_touched`). A `__new__` it defines is a constructor body too. Anything else
    /// escapes.
    fn bare_record(&mut self, class: Ent) -> bool {
        let Some((owner, idx)) = self.def_of(class.m, class.s, HitKind::Class) else {
            return false;
        };
        if self.class_stores.contains_key(&class)
            || self.touched(class)
            || self.module_touched(class.m)
        {
            return false;
        }
        let facts = self.core.facts.clone();
        let file = self.scopes[owner].file as usize;
        let record = &facts.files[file];
        let node = &record.defs[idx as usize];
        if !node.bases.is_empty() || node.keywords || !self.dataclass_only(owner, node) {
            return false;
        }
        // A decorated def in the body may be a property, its setter, or another descriptor.
        let decorated = (record.defs.iter()).any(|def| def.s == idx as i32 && !def.dec.is_empty());
        if decorated {
            return false;
        }
        let it = &self.core.it;
        let hook = |name: Sid| RECORD_HOOKS.contains(&it.s(name));
        let defines_hook = (record.defs.iter()).any(|def| def.s == idx as i32 && hook(def.n));
        let assigns_hook = (record.assigns.iter())
            .filter(|row| row.s == idx as i32)
            .any(|row| (row.targets.iter()).any(|t| matches!(t, V::Chain(n) if hook(*n))));
        if defines_hook || assigns_hook {
            return false;
        }
        let mut text = AssignText::new(facts.clone(), file);
        let defaults: Vec<usize> = (record.assigns.iter().enumerate())
            .filter(|(_, row)| row.s == idx as i32 && row.value.is_some())
            .map(|(at, _)| at)
            .collect();
        defaults.into_iter().all(|at| {
            let row = &record.assigns[at];
            let value = row.value.as_ref().expect("a default");
            self.inert_default(owner, value, row.s, row.l, Some((&mut text, at)))
        })
    }

    /// The surface text a value names where `owner` reads it, and the text of what it resolves
    /// to (empty when nothing).
    fn named(&mut self, owner: usize, value: &V, at: i32, line: u32) -> Option<(String, String)> {
        let core = &mut *self.core;
        let found = core.value_lookup(
            &self.settings.names,
            &mut self.scopes[owner],
            value,
            at,
            line,
        )?;
        let resolved = core.classify_lookup(found);
        let target = resolved.target.map(|target| core.ent_sid(target));
        let surface = core.it.s(found.surface).to_string();
        let target = target.map(|t| core.it.s(t).to_string()).unwrap_or_default();
        Some((surface, target))
    }

    /// What a value names where `owner` reads it, when it hits a definition: the kind and where.
    fn named_hit(&mut self, owner: usize, value: &V, at: i32, line: u32) -> Option<(HitKind, Ent)> {
        let core = &mut *self.core;
        let found = core.value_lookup(
            &self.settings.names,
            &mut self.scopes[owner],
            value,
            at,
            line,
        )?;
        let hit = found.hit?;
        Some((core.kind(hit), core.origin(hit)))
    }

    /// Whether a class-body default of a record class is no descriptor, so the generated
    /// `__init__` stores the value with no first-party code: a literal, a display, a class whose
    /// metaclass is `type` or `abc.ABCMeta` (`plain_metaclass`; a function is a descriptor), or
    /// `dataclasses.field(...)` whose `default=` is one of these (read from the record's text,
    /// `text` and the row).
    fn inert_default(
        &mut self,
        owner: usize,
        value: &V,
        at: i32,
        line: u32,
        text: Option<(&mut AssignText, usize)>,
    ) -> bool {
        match value {
            V::Str(..)
            | V::Scalar
            | V::Null
            | V::Tuple(_)
            | V::List(_)
            | V::Set(_)
            | V::Dict(_) => true,
            V::Opaque(what) => matches!(
                self.core.it.s(*what),
                "..." | "longstr" | "badstr" | "JoinedStr"
            ),
            V::Chain(_) => match self.named_hit(owner, value, at, line) {
                Some((HitKind::Def, _)) => true,
                Some((HitKind::Class, class)) if self.defined_class(class) => {
                    self.plain_metaclass(class, 0)
                }
                // A builtin (`object`, `int`, `len`) is a class or a function: no `__set__`.
                _ => self
                    .named(owner, value, at, line)
                    .is_some_and(|(surface, target)| {
                        surface.starts_with("builtins.") || target.starts_with("builtins.")
                    }),
            },
            V::Call(callee, ..) => {
                let field = self
                    .named(owner, callee, at, line)
                    .is_some_and(|(surface, target)| {
                        surface == "dataclasses.field" || target == "dataclasses.field"
                    });
                let Some((text, row)) = text.filter(|_| field) else {
                    return false;
                };
                let Some(J::Object(call)) = text.value(row).cloned() else {
                    return false;
                };
                if call
                    .get("args")
                    .and_then(J::as_array)
                    .is_some_and(|a| !a.is_empty())
                {
                    return false;
                }
                let keywords = call
                    .get("kw")
                    .and_then(J::as_array)
                    .cloned()
                    .unwrap_or_default();
                keywords.iter().all(|pair| {
                    let Some([name, value]) = pair.as_array().map(Vec::as_slice) else {
                        return false;
                    };
                    match name.as_str() {
                        Some("default") => {
                            let value = value_tree(&mut self.core.it, value);
                            self.inert_default(owner, &value, at, line, None)
                        }
                        Some(_) => true,
                        None => false,
                    }
                })
            }
            _ => false,
        }
    }

    /// Whether the graph defines a class (its def is in an analyzed file).
    fn defined_class(&mut self, class: Ent) -> bool {
        self.def_of(class.m, class.s, HitKind::Class).is_some()
    }

    /// Whether a class's metaclass has no `__set__`: no class keyword on it or on any of its
    /// first-party bases, and every other base `object`, `abc.ABC` or `typing.Generic`.
    fn plain_metaclass(&mut self, class: Ent, depth: usize) -> bool {
        if depth > MAX_BASE_DEPTH {
            return false;
        }
        let Some((owner, idx)) = self.def_of(class.m, class.s, HitKind::Class) else {
            return false;
        };
        let facts = self.core.facts.clone();
        let node = &facts.files[self.scopes[owner].file as usize].defs[idx as usize];
        if node.keywords {
            return false;
        }
        node.bases.iter().all(|base| {
            if let Some((HitKind::Class, base_class)) = self.named_hit(owner, base, node.s, node.l)
                && self
                    .def_of(base_class.m, base_class.s, HitKind::Class)
                    .is_some()
            {
                return self.plain_metaclass(base_class, depth + 1);
            }
            self.named(owner, base, node.s, node.l)
                .is_some_and(|(surface, target)| {
                    PLAIN_BASES.contains(&surface.as_str())
                        || PLAIN_BASES.contains(&target.as_str())
                })
        })
    }

    /// T4's record: one built by a bare record class (`bare_record`) whose body defines no
    /// function but `__init__`, `__post_init__` and `__new__`, assigns no dunder, and binds no
    /// attribute to a call but `field(...)`, so `dataclasses.replace` reads its fields with no
    /// first-party code (no `__getattr__`, `__getattribute__`, property or descriptor); a store
    /// on the class from any file makes it not bare. The record is the name a binding row of
    /// `rows` binds to a call; one copied by `replace` itself is not judged again.
    fn plain_record(&mut self, owner: usize, rows: &[Touching]) -> bool {
        let mut binds = rows.iter().filter(|touching| touching.touch == Touch::Bind);
        let (Some(bind), None) = (binds.next(), binds.next()) else {
            return false;
        };
        let Some(V::Call(callee, ..)) = bind.row.value.as_ref() else {
            return false;
        };
        let V::Chain(callee) = &**callee else {
            return false;
        };
        let callee = self.core.it.s(*callee).to_string();
        let (_, Some(class)) = self.record_callee(owner, bind.row.s, bind.row.l, &callee) else {
            return false;
        };
        if !self.bare_record(class) {
            return false;
        }
        let Some((class_owner, idx)) = self.def_of(class.m, class.s, HitKind::Class) else {
            return false;
        };
        let facts = self.core.facts.clone();
        let record = &facts.files[self.scopes[class_owner].file as usize];
        let methods = (record.defs.iter())
            .filter(|def| def.s == idx as i32)
            .all(|def| {
                matches!(
                    self.core.it.s(def.n),
                    "__init__" | "__post_init__" | "__new__"
                )
            });
        let it = &self.core.it;
        let dunder = |name: Sid| {
            let name = it.s(name);
            name.len() > 4 && name.starts_with("__") && name.ends_with("__")
        };
        let fields = (record.assigns.iter())
            .filter(|row| row.s == idx as i32)
            .all(|row| {
                let plain_target = !(row.targets.iter())
                    .any(|target| matches!(target, V::Chain(name) if dunder(*name)));
                let plain_value = match &row.value {
                    Some(V::Call(callee, ..)) => matches!(&**callee, V::Chain(name)
                        if matches!(it.s(*name), "field" | "dataclasses.field")),
                    _ => true,
                };
                plain_target && plain_value
            });
        methods && fields
    }

    /// The `@` rows of a file, the walker's stores into what a dotted name reaches, from any
    /// scope: one into a module is a binding of that name in it, whichever file makes it
    /// (`foreign`); one into a class the graph defines changes the class (`class_stores`). A
    /// dotted name that resolves to neither (a local, an instance, a library) is no store the
    /// reading reads.
    fn stores(&mut self, owner: usize) {
        let facts = self.core.facts.clone();
        let record = &facts.files[self.scopes[owner].file as usize];
        if !record.ok() {
            return;
        }
        for escape in &record.escapes {
            let text = self.core.it.s(escape.name);
            let Some((base, attribute)) = text.strip_prefix('@').and_then(|t| t.rsplit_once('.'))
            else {
                continue;
            };
            let (base, attribute) = (base.to_string(), attribute.to_string());
            let chain = self.core.it.intern(&base);
            let attribute = self.core.it.intern(&attribute);
            let core = &mut *self.core;
            let names = &self.settings.names;
            let (found, rest) = core.chain_lookup(
                names,
                &mut self.scopes[owner],
                chain,
                escape.scope,
                escape.line,
            );
            let Some(hit) = found.hit.filter(|_| rest.is_empty()) else {
                continue;
            };
            let origin = core.origin(hit);
            match core.kind(hit) {
                HitKind::Module => {
                    self.foreign.entry(origin.m).or_default().insert(attribute);
                }
                HitKind::Class => {
                    self.class_stores
                        .entry(origin)
                        .or_default()
                        .insert(attribute);
                }
                _ => {}
            }
        }
    }

    /// The `!` rows of a file (round 9), the walker's uses of a value that may be a module, a
    /// class or a function where no entry accounts for them, from any scope, resolved where the
    /// file reads them (`Held`). A class so used, or one handed to a record constructor whose
    /// callee builds no record, is touched (`touched`); a module so used, or the module of a
    /// class or def reached through a dunder attribute, is touched for every file
    /// (`touched_modules`). The file's own list is the reading file's evidence for its records
    /// (`callees_proved`).
    fn values(&mut self, owner: usize) {
        let facts = self.core.facts.clone();
        let record = &facts.files[self.scopes[owner].file as usize];
        if !record.ok() {
            return;
        }
        let module = self.scopes[owner].module;
        for escape in &record.escapes {
            let Some(text) = self.core.it.s(escape.name).strip_prefix('!') else {
                continue;
            };
            let (text, marker) = match text.find(['@', '#', '%']) {
                Some(at) => (text[..at].to_string(), text[at..].to_string()),
                None => (text.to_string(), String::new()),
            };
            let through = marker == "%";
            let held = match text.as_str() {
                "*" => Held::Any,
                "." => Held::Module(module),
                _ if text.starts_with(':') => {
                    let named = self.core.it.intern(&text[1..]);
                    if self.core.exists(named) {
                        Held::Module(named)
                    } else {
                        Held::Any
                    }
                }
                _ => {
                    let chain = self.core.it.intern(&text);
                    let core = &mut *self.core;
                    let names = &self.settings.names;
                    let (found, rest) = core.chain_lookup(
                        names,
                        &mut self.scopes[owner],
                        chain,
                        escape.scope,
                        escape.line,
                    );
                    match found.hit {
                        Some(hit) if rest.is_empty() => {
                            let origin = core.origin(hit);
                            match core.kind(hit) {
                                HitKind::Module => Held::Module(origin.m),
                                HitKind::Class => Held::Class(origin, through),
                                HitKind::Def => Held::Def(origin, through),
                                _ => Held::Variable(origin),
                            }
                        }
                        Some(hit) if core.kind(hit) == HitKind::Class => Held::Member,
                        _ => Held::Any,
                    }
                }
            };
            match (held, marker.as_str()) {
                (Held::Class(class, _), callee) if callee.starts_with('@') => {
                    let callee = callee[1..].to_string();
                    self.handed
                        .push((class, owner, escape.scope, escape.line, callee));
                }
                (Held::Class(_, _), "#default") => {}
                (Held::Class(class, through), _) => {
                    self.touched_classes.insert(class);
                    if through {
                        self.touched_modules.insert(class.m);
                    }
                }
                (Held::Def(def, true), _) => {
                    self.touched_modules.insert(def.m);
                }
                (Held::Module(module), _) => {
                    self.touched_modules.insert(module);
                }
                _ => {}
            }
            self.values.entry(owner).or_default().push(held);
        }
    }

    /// Whether some file uses the class where no entry accounts for it (`values`): a `!` row
    /// that names it, or an argument of a record constructor whose callee builds no record.
    fn touched(&mut self, class: Ent) -> bool {
        if self.touched_classes.contains(&class) {
            return true;
        }
        if !self.touching.insert(class) {
            return false;
        }
        let handed: Vec<(Ent, usize, i32, u32, String)> = (self.handed.iter())
            .filter(|handed| handed.0 == class)
            .cloned()
            .collect();
        let out = (handed.into_iter()).any(|(_, owner, scope, line, callee)| {
            !self.builds_record(owner, scope, line, &callee)
        });
        self.touching.remove(&class);
        out
    }

    /// Whether a module is `module` or inside it (`module` is a package of it).
    fn within(&self, module: Sid, target: Sid) -> bool {
        let (module, target) = (self.core.it.s(module), self.core.it.s(target));
        target == module
            || target
                .strip_prefix(module)
                .is_some_and(|rest| rest.starts_with('.'))
    }

    /// Whether some first-party file uses a value that may be `module` where no entry accounts
    /// for it (`touched_modules`): the module itself or a package of it.
    fn module_touched(&self, module: Sid) -> bool {
        (self.touched_modules.iter()).any(|touched| self.within(*touched, module))
    }

    /// Part B (round 9): a record's callee is trusted only on positive evidence in the reading
    /// file. Every value there that may be the callee's module or the callee itself (`values`)
    /// is used only as an attribute read or a call: a class or a def the graph proves is no
    /// module, unless reached through a dunder attribute (its module then); a first-party name
    /// bound only to displays and literals is none (`plain_variable`); an attribute of a class
    /// is none. Any other value may be any module, `dataclasses` among them.
    fn callees_proved(&mut self, owner: usize, scope: i32, line: u32, callees: &str) -> bool {
        let held = self.values.get(&owner).cloned().unwrap_or_default();
        for callee in callees.split(',') {
            let (_, class) = self.record_callee(owner, scope, line, callee);
            for value in &held {
                let proved = match *value {
                    Held::Any => false,
                    Held::Member => true,
                    Held::Variable(name) => self.plain_variable(name),
                    Held::Module(module) => {
                        class.is_some_and(|class| !self.within(module, class.m))
                    }
                    Held::Class(other, through) => {
                        class != Some(other)
                            && !(through && class.is_some_and(|class| class.m == other.m))
                    }
                    Held::Def(def, through) => {
                        !(through && class.is_none_or(|class| class.m == def.m))
                    }
                };
                if !proved {
                    return false;
                }
            }
        }
        true
    }

    /// Whether a first-party module-level name is bound only by assignments of displays and
    /// literals, and by no other form (`=name`): it holds no module.
    fn plain_variable(&mut self, name: Ent) -> bool {
        let Some(table) = self.core.table(name.m) else {
            return false;
        };
        let Some(file) = self.core.table_at(table).record else {
            return false;
        };
        let facts = self.core.facts.clone();
        let record = &facts.files[file as usize];
        let text = self.core.it.s(name.s).to_string();
        let rebound = format!("={text}");
        if (record.escapes.iter()).any(|e| e.scope == -1 && self.core.it.s(e.name) == rebound) {
            return false;
        }
        let rows: Vec<&Assign> = (record.assigns.iter())
            .filter(|row| row.s == -1)
            .filter(|row| (row.targets.iter()).any(|t| matches!(t, V::Chain(n) if *n == name.s)))
            .collect();
        !rows.is_empty()
            && rows.iter().all(|row| {
                matches!(
                    row.value,
                    Some(
                        V::Str(..)
                            | V::Scalar
                            | V::Null
                            | V::Tuple(_)
                            | V::List(_)
                            | V::Set(_)
                            | V::Dict(_)
                    )
                ) && row.targets.len() == 1
            })
    }

    /// Part C (round 9, t30's ruling (a)): a class attribute read returns what the body binds
    /// only from a stable class: no file stores into it (`class_stores`), every occurrence of it
    /// is accounted (`touched`), its lineage's metaclass is `type` or `abc.ABCMeta` with no
    /// decorator but `dataclasses.dataclass` (`stable_lineage`), and its body binds the
    /// attribute once, at its top level (an assignment, a def or a class).
    fn stable_class(&mut self, class: Ent, attribute: &str) -> bool {
        let Some((owner, idx)) = self.def_of(class.m, class.s, HitKind::Class) else {
            return false;
        };
        if self.class_stores.contains_key(&class)
            || self.touched(class)
            || !self.stable_lineage(class, 0)
        {
            return false;
        }
        let facts = self.core.facts.clone();
        let record = &facts.files[self.scopes[owner].file as usize];
        let top = record.defs[idx as usize].f | crate::facts::flags::CLASS;
        let first = attribute.split('.').next().unwrap_or_default();
        let it = &self.core.it;
        let assigned = (record.assigns.iter())
            .filter(|row| row.s == idx as i32)
            .filter(|row| {
                (row.targets.iter()).any(|t| matches!(t, V::Chain(n) if it.s(*n) == first))
            })
            .map(|row| row.f);
        let defined = (record.defs.iter())
            .filter(|def| def.s == idx as i32 && it.s(def.n) == first)
            .map(|def| def.f);
        let flags: Vec<u32> = assigned.chain(defined).collect();
        matches!(flags.as_slice(), [flags] if *flags == top)
    }

    /// Whether a class and its first-party bases, at any depth, have no class keyword (so no
    /// metaclass but their bases'), no decorator but `dataclasses.dataclass`, and bases that
    /// are `object`, `abc.ABC`, `typing.Generic` or such first-party classes: the metaclass is
    /// `type` or `abc.ABCMeta` along the whole lineage.
    fn stable_lineage(&mut self, class: Ent, depth: usize) -> bool {
        if depth > MAX_BASE_DEPTH {
            return false;
        }
        let Some((owner, idx)) = self.def_of(class.m, class.s, HitKind::Class) else {
            return false;
        };
        let facts = self.core.facts.clone();
        let node = &facts.files[self.scopes[owner].file as usize].defs[idx as usize];
        if node.keywords || !self.dataclass_only(owner, node) {
            return false;
        }
        node.bases.iter().all(|base| {
            if let Some((HitKind::Class, base_class)) = self.named_hit(owner, base, node.s, node.l)
                && self
                    .def_of(base_class.m, base_class.s, HitKind::Class)
                    .is_some()
            {
                return self.stable_lineage(base_class, depth + 1);
            }
            self.named(owner, base, node.s, node.l)
                .is_some_and(|(surface, target)| {
                    PLAIN_BASES.contains(&surface.as_str())
                        || PLAIN_BASES.contains(&target.as_str())
                })
        })
    }

    /// Whether each decorator of a class is `dataclasses.dataclass` (with any arguments).
    fn dataclass_only(&mut self, owner: usize, node: &Def) -> bool {
        node.dec.iter().all(|decorator| {
            let callee = match decorator {
                V::Call(callee, ..) => &**callee,
                decorator => decorator,
            };
            let names = self.named(owner, callee, node.s, node.l);
            names.is_some_and(|(surface, target)| {
                surface == "dataclasses.dataclass" || target == "dataclasses.dataclass"
            })
        })
    }

    /// A name of this module some file stores into it (`foreign`), or an attribute of a class
    /// it defines some file stores on the class (`class_stores`): bound again by a form the
    /// reading does not read, `Other` (as `=name`).
    fn foreign_rebinds(&mut self, owner: usize, out: &mut Touches) {
        let module = self.scopes[owner].module;
        let it = &self.core.it;
        let any = |names: &FxHashSet<Sid>, name: &str| {
            names
                .iter()
                .any(|n| matches!(it.s(*n), "*") || it.s(*n) == name)
        };
        for (key, (_, rows)) in out.iter_mut() {
            let text = it.s(*key);
            let stored = match text.rsplit_once('.') {
                None => self
                    .foreign
                    .get(&module)
                    .is_some_and(|names| any(names, text)),
                Some((class, attribute)) => it.get(class).is_some_and(|class| {
                    let class = Ent {
                        m: module,
                        s: class,
                    };
                    (self.class_stores.get(&class)).is_some_and(|names| any(names, attribute))
                }),
            };
            if stored {
                rows.push(Touching {
                    at: usize::MAX,
                    row: &REBOUND,
                    touch: Touch::Other,
                });
            }
        }
    }

    /// An import at module level after a name's first binding that may bind the name binds it
    /// again: `Other`. A named import binds its name; a star import binds what the imported
    /// module hands out, or any name when that cannot be told. An import of the module itself
    /// (under any spelling) reaches every name of it: every name `Other`.
    fn import_rebinds<'r>(&mut self, owner: usize, record: &'r FileFacts, out: &mut Touches<'r>) {
        let (module, is_package) = (self.scopes[owner].module, self.scopes[owner].is_package);
        let star = self.settings.k.star;
        for row in &record.imports {
            let (bound, surface, source) =
                import_surface(&mut self.core.it, module, is_package, row);
            let itself = surface == module || (row.name.is_none() && source == Some(module));
            if row.scope != -1 && !itself {
                continue;
            }
            let names: Option<Vec<Sid>> = if itself {
                None
            } else if row.name == Some(star) {
                source.and_then(|source| star_names(self.core, source))
            } else {
                Some(vec![bound])
            };
            for (key, (_, rows)) in out.iter_mut() {
                if !itself && self.core.it.s(*key).contains('.') {
                    continue;
                }
                let bound_before = (rows.iter())
                    .any(|touching| touching.touch == Touch::Bind && touching.row.l < row.line);
                let binds = names.as_ref().is_none_or(|names| names.contains(key));
                if (itself || bound_before) && binds {
                    let row = if itself { &ESCAPED } else { &REBOUND };
                    rows.push(Touching {
                        at: usize::MAX,
                        row,
                        touch: Touch::Other,
                    });
                }
            }
        }
    }

    /// A loader handed a name it computes (a `computed` row of a loader call that names no list
    /// it takes an element of) may load this module itself and change or bind again any name of
    /// it: every name `Other`.
    fn loader_namespace<'r>(&mut self, owner: usize, record: &'r FileFacts, out: &mut Touches<'r>) {
        let mut loaders: FxHashSet<(u32, u32)> = FxHashSet::default();
        for row in &record.calls {
            if names_callee(&row.callee, &self.settings.loader_last_names)
                || self.aliased_loader(owner, row)
            {
                loaders.insert((row.line, row.col));
            }
        }
        let computed = record.computed.iter().any(|load| {
            load.form == LoadForm::Call
                && load.sequence == EMPTY
                && loaders.contains(&(load.line, load.col))
        });
        if !computed {
            return;
        }
        for (_, rows) in out.values_mut() {
            rows.push(Touching {
                at: usize::MAX,
                row: &ESCAPED,
                touch: Touch::Other,
            });
        }
    }

    /// Aliases (`links`, from `escape_touches`). A name is read only as exactly as each holder of
    /// its value is: a holder that may be the value itself and is changed (`_a.append(B)`), or
    /// any holder that escapes or is bound again by a form the reading does not read, makes the
    /// name `Other`; a record bound a second time does too (D126). An alias of an alias is an
    /// alias.
    fn aliases(&self, out: &mut Touches, links: Vec<Link>) {
        loop {
            let mut changed = false;
            for link in &links {
                let Link {
                    holder,
                    held,
                    same,
                    record,
                } = *link;
                let rows = &out[&holder].1;
                let bindings = rows
                    .iter()
                    .filter(|touching| touching.touch == Touch::Bind)
                    .count();
                let tainted = (record && bindings > 1)
                    || rows.iter().any(|touching| match touching.touch {
                        Touch::Add => same,
                        Touch::Other | Touch::Escaped => true,
                        _ => false,
                    });
                let rows = &mut out.get_mut(&held).expect("a linked key").1;
                if !tainted || rows.iter().any(|touching| touching.touch == Touch::Other) {
                    continue;
                }
                rows.push(Touching {
                    at: usize::MAX,
                    row: &ESCAPED,
                    touch: Touch::Other,
                });
                changed = true;
            }
            if !changed {
                break;
            }
        }
    }

    /// The names an unpacking binds and how: paired with the elements of a tuple or list value
    /// when the target is flat and the value has no star (a starred target takes the elements
    /// between the others), else `Other`.
    fn unpacked(
        &self,
        target: &V,
        items: &[V],
        row: &Assign,
        touch: Touch,
        out: &mut Vec<(Sid, Touch)>,
    ) {
        let flat = items.iter().all(|item| {
            let item = match item {
                V::Star(inner) => &**inner,
                item => item,
            };
            matches!(item, V::Chain(name) if !self.core.it.s(*name).contains('.'))
        });
        let values = match &row.value {
            Some(V::Tuple(values) | V::List(values))
                if !values.iter().any(|value| matches!(value, V::Star(_))) =>
            {
                Some(values.len())
            }
            _ => None,
        };
        let stars = items
            .iter()
            .filter(|item| matches!(item, V::Star(_)))
            .count();
        let paired = touch == Touch::Bind
            && flat
            && stars <= 1
            && values.is_some_and(|len| len + stars >= items.len());
        if !paired {
            let mut names = Vec::new();
            unpacked_targets(target, &mut names);
            out.extend(names.into_iter().map(|name| (name, Touch::Other)));
            return;
        }
        let len = values.unwrap_or_default();
        let after = items.len();
        let star_at = items.iter().position(|item| matches!(item, V::Star(_)));
        for (index, item) in items.iter().enumerate() {
            match item {
                V::Chain(name) => {
                    let element = match star_at {
                        Some(star) if index > star => len - (after - index),
                        _ => index,
                    };
                    out.push((*name, Touch::Element(element)));
                }
                V::Star(inner) => {
                    if let V::Chain(name) = &**inner {
                        let end = len - (after - index - 1);
                        out.push((*name, Touch::Elements(index, end)));
                    }
                }
                _ => {}
            }
        }
    }

    /// Whether a name's rows read exactly, so the union of what they hold is what the name holds:
    /// one binds it, none is `Other`, none sits in a block (a class body is none), and each
    /// binding after the first reads the name itself (`W = W + [B]`).
    fn exact(&self, name: Sid, rows: &[Touching], grows: bool) -> bool {
        let mut bound = false;
        for touching in rows {
            if touching.touch == Touch::Escaped && !grows {
                continue;
            }
            let unknown = matches!(touching.touch, Touch::Other | Touch::Escaped);
            if unknown || touching.row.f & !CLASS != 0 {
                return false;
            }
            if !matches!(
                touching.touch,
                Touch::Bind | Touch::Element(_) | Touch::Elements(..)
            ) {
                continue;
            }
            let reads_itself = touching
                .row
                .value
                .as_ref()
                .is_some_and(|value| self.reads_name(value, name));
            if bound && !reads_itself {
                return false;
            }
            bound = true;
        }
        bound
    }

    /// What a name holds, read from the rows that touch it: the union of each row's items, and
    /// an unread item when the rows do not read exactly and can hold something (a name bound
    /// twice, or in a block, to constants holds nothing either way), or when something the
    /// reading does not understand touches it.
    #[allow(clippy::too_many_arguments)]
    fn name_items(
        &mut self,
        owner: usize,
        module: Sid,
        name: Sid,
        rows: &[Touching],
        reading: Reading,
        text: &mut AssignText,
        seen: &mut FxHashSet<Ent>,
    ) -> Vec<Option<Ent>> {
        let mut found = Vec::new();
        let mut first = true;
        for touching in rows {
            let place = Place {
                owner,
                module,
                scope: touching.row.s,
                line: touching.row.l,
                binding: name,
                first,
            };
            if matches!(
                touching.touch,
                Touch::Bind | Touch::Element(_) | Touch::Elements(..)
            ) {
                first = false;
            }
            self.touch_items(place, touching, reading, text, seen, &mut found);
        }
        let unknown = rows.iter().any(|touching| touching.touch == Touch::Other);
        let escaped = rows.iter().any(|touching| touching.touch == Touch::Escaped);
        let grows = escaped && rows.iter().any(|touching| self.grows(touching));
        if (unknown || grows || !found.is_empty()) && !self.exact(name, rows, grows) {
            found.push(None);
        }
        found
    }

    /// The items one row adds to a name: its value's, an unpacked element's, or the arguments of
    /// a method that changes the name in place.
    fn touch_items(
        &mut self,
        place: Place,
        touching: &Touching,
        reading: Reading,
        text: &mut AssignText,
        seen: &mut FxHashSet<Ent>,
        out: &mut Vec<Option<Ent>>,
    ) {
        let row = touching.row;
        let Some(value) = row.value.as_ref() else {
            return;
        };
        match touching.touch {
            Touch::Other | Touch::Escaped => {}
            Touch::Element(index) | Touch::Elements(index, _) => {
                let end = match touching.touch {
                    Touch::Elements(_, end) => end,
                    _ => index + 1,
                };
                let (V::Tuple(values) | V::List(values)) = value else {
                    out.push(None);
                    return;
                };
                for at in index..end {
                    if reading == Reading::List && self.spelled(&values[at]) {
                        self.sequence_items(place, &values[at], seen, out);
                        continue;
                    }
                    let element = text.value(touching.at).and_then(|json| {
                        let values = json.get("tuple").or_else(|| json.get("list"))?;
                        values.get(at)
                    });
                    match element {
                        Some(json) => self.json_items(place, json, reading, seen, out),
                        None => out.push(None),
                    }
                }
            }
            Touch::Add if row.op == Op::Mutate => {
                let Some(call) = text.value(touching.at).and_then(J::as_object) else {
                    out.push(None);
                    return;
                };
                for arg in call_arguments(call) {
                    self.json_items(place, arg, reading, seen, out);
                }
            }
            Touch::Bind | Touch::Add => {
                if reading == Reading::List && self.spelled(value) {
                    self.sequence_items(place, value, seen, out);
                    return;
                }
                match text.value(touching.at) {
                    Some(json) => self.json_items(place, json, reading, seen, out),
                    None => out.push(None),
                }
            }
        }
    }

    /// What each name a module binds holds, for the rules to read where core registers the name
    /// (`Link::sequences`), by `name_items`; class-body bindings of the classes in
    /// `read_classes` under `Class.NAME`. A name is read exactly only when every row that binds,
    /// changes or uses it at module level, or in a class body, is one the reading understands;
    /// else it is unread. A name the link cannot read is never a name with no items. A name bound
    /// to constants holds none and is left out of the table, and is noted in `bound`.
    /// In a registry's file, the attribute its loader reads is a declaration (`Reading`).
    fn sequences(&mut self, owner: usize) {
        let facts = self.core.facts.clone();
        let file = self.scopes[owner].file as usize;
        let record = &facts.files[file];
        if !record.ok() {
            return;
        }
        let module = self.scopes[owner].module;
        let declared = self.settings.registry_attribute(&record.path);
        for node in &record.defs {
            let scope = &self.scopes[owner];
            if node.s < 0 || scope.in_function[node.s as usize] {
                continue;
            }
            let class = scope.qualnames[node.s as usize];
            if self.read_classes.contains(&(module, class)) {
                let key = format!("{}.{}", self.core.it.s(class), self.core.it.s(node.n));
                let key = self.core.it.intern(&key);
                self.defined.insert((module, key));
            }
        }
        let touches = self.touches(owner, record, false);
        if let Some(attribute) = declared
            && !touches.contains_key(&attribute)
        {
            // A module `__getattr__` may serve the attribute the file does not bind (a lazy
            // export): the declaration is unread, never empty.
            let lazy = (record.defs.iter())
                .any(|def| def.s == -1 && self.core.it.s(def.n) == "__getattr__");
            if lazy {
                self.sequences.insert((module, attribute), vec![None]);
                self.unread_causes.insert((module, attribute), "lazy");
            } else {
                self.imported_declaration(owner, module, attribute);
            }
        }
        self.rebound_imports(owner, record, &touches);
        let mut text = AssignText::new(facts.clone(), file);
        let mut keys: Vec<Sid> = touches.keys().copied().collect();
        keys.sort_unstable();
        for key in keys {
            let (name, rows) = &touches[&key];
            let reading = if declared == Some(key) {
                Reading::Declaration
            } else {
                Reading::List
            };
            let mut seen = FxHashSet::default();
            seen.insert(Ent { m: module, s: key });
            let items = self.name_items(owner, module, *name, rows, reading, &mut text, &mut seen);
            self.bound.insert((module, key));
            if items.contains(&None)
                && let Some(cause) = unread_cause(rows)
            {
                self.unread_causes.insert((module, key), cause);
            }
            if key == *name && self.holds_paths(*name, rows) {
                self.path_lists.insert((module, key));
            }
            if !items.is_empty() {
                self.sequences.insert((module, key), items);
            }
        }
    }

    /// A name a module binds by an import at module level and binds again by a form the reading
    /// cannot read (`=name`: a `def`, a `class`, a `for` target, an import in a function that
    /// declares it global): what the module hands out under the name is not what the import
    /// names, so its list is unread at this module (`bound` "rebound"), which the item cover reads
    /// before following the import (`coverage::rebound_surface`). A name bound by an assignment
    /// and then by an import is not one: the link follows the import.
    fn rebound_imports(&mut self, owner: usize, record: &FileFacts, touches: &Touches) {
        let module = self.scopes[owner].module;
        let is_package = self.scopes[owner].is_package;
        let mut imported = FxHashSet::default();
        for row in &record.imports {
            if row.scope != -1 {
                continue;
            }
            let (bound, _, _) = import_surface(&mut self.core.it, module, is_package, row);
            imported.insert(bound);
        }
        for escape in &record.escapes {
            if escape.scope != -1 {
                continue;
            }
            let text = self.core.it.s(escape.name);
            let Some(name) = text.strip_prefix('=') else {
                continue;
            };
            let Some(name) = self.core.it.get(name) else {
                continue;
            };
            if touches.contains_key(&name) || !imported.contains(&name) {
                continue;
            }
            self.sequences.insert((module, name), vec![None]);
            self.unread_causes.insert((module, name), "rebound");
            self.rebound_imports.insert((module, name));
            self.bound.insert((module, name));
        }
    }

    /// Whether a module-level name is a list or tuple of string literals the reading reads
    /// exactly: one row binds it, to such a literal, and no other row changes it or uses it in a
    /// way the reading does not understand (`exact`; a tuple cannot change, so a use of it
    /// counts for nothing).
    fn holds_paths(&self, name: Sid, rows: &[Touching]) -> bool {
        let grows = rows.iter().any(|touching| self.grows(touching));
        if !self.exact(name, rows, grows) {
            return false;
        }
        let mut changes = rows
            .iter()
            .filter(|touching| touching.touch != Touch::Escaped);
        let (Some(binding), None) = (changes.next(), changes.next()) else {
            return false;
        };
        // A relative name (`.x`) is read relative to `package=`, which the link does not read.
        let strings = |items: &[V]| {
            (items.iter()).all(
                |item| matches!(item, V::Str(text, _) if !self.core.it.s(*text).starts_with('.')),
            )
        };
        binding.touch == Touch::Bind
            && matches!(&binding.row.value, Some(V::List(items) | V::Tuple(items)) if strings(items))
    }

    /// A registry's attribute that its file binds by an import: the value is built where it is
    /// declared, which no loader reads as a declaration, so it is unread. One the file does not
    /// bind, or binds by a def or a class, registers no value.
    fn imported_declaration(&mut self, owner: usize, module: Sid, attribute: Sid) {
        let (_, resolved) = self.lookup_value(owner, &V::Chain(attribute), -1, u32::MAX);
        let elsewhere = resolved.target.is_some_and(|target| target.m != module);
        if matches!(resolved.res, Res::Resolved) && elsewhere {
            self.sequences.insert((module, attribute), vec![None]);
        }
    }

    /// The items of one value as the extractor wrote it (the record's JSON text, which keeps
    /// every argument of a call and the parts of an assigned conditional or comprehension), in
    /// order, into `out`. A subscript of a library generic holds what its parts hold (a type
    /// alias); of anything else, nothing when its parts hold nothing, else it is unread; `a or b`
    /// holds what either side holds.
    fn json_items(
        &mut self,
        at: Place,
        json: &J,
        reading: Reading,
        seen: &mut FxHashSet<Ent>,
        out: &mut Vec<Option<Ent>>,
    ) {
        let J::Object(map) = json else {
            let value = value_tree(&mut self.core.it, json);
            self.leaf_items(at, &value, reading, seen, out);
            return;
        };
        if map.contains_key("call") {
            self.call_items(at, map, reading, seen, out);
            return;
        }
        for key in ["list", "tuple", "set", "vs"] {
            if let Some(J::Array(items)) = map.get(key) {
                for item in items {
                    self.json_items(at, item, reading, seen, out);
                }
                return;
            }
        }
        if let Some(J::Array(pairs)) = map.get("dict") {
            for pair in pairs.iter().filter_map(J::as_array) {
                for part in pair.iter().filter(|part| !part.is_null()) {
                    self.json_items(at, part, reading, seen, out);
                }
            }
            return;
        }
        if let Some(inner) = map.get("star") {
            self.json_items(at, inner, reading, seen, out);
            return;
        }
        if let Some(inner) = map.get("sub") {
            let mut parts = Vec::new();
            for part in ["sub", "i"] {
                match map.get(part) {
                    Some(part) => self.json_items(at, part, reading, seen, &mut parts),
                    None => parts.push(None),
                }
            }
            let inner = value_tree(&mut self.core.it, inner);
            if self.library_generic(at, &inner) {
                out.extend(parts);
            } else if !parts.is_empty() {
                out.push(None);
            }
            return;
        }
        let parts = if map.contains_key("bin") {
            Some(["l", "r"])
        } else if map.contains_key("orelse") {
            Some(["body", "orelse"])
        } else {
            None
        };
        if let Some(parts) = parts {
            for part in parts {
                match map.get(part) {
                    Some(part) => self.json_items(at, part, reading, seen, out),
                    None => out.push(None),
                }
            }
            return;
        }
        if map.contains_key("elt") {
            // Only a comprehension that yields its own target holds what it loops over; its
            // filters can only drop items.
            let target = map.get("t").and_then(J::as_str);
            let own = target.is_some_and(|name| !name.contains('.'))
                && map.get("elt").and_then(J::as_str) == target;
            match map.get("in").filter(|_| own) {
                Some(iterable) => self.json_items(at, iterable, reading, seen, out),
                None => out.push(None),
            }
            return;
        }
        let value = value_tree(&mut self.core.it, json);
        self.leaf_items(at, &value, reading, seen, out);
    }

    /// The items of a call. A copy holds what it copies (`sorted`, `list`, `tuple` or `set` of
    /// one, `x.copy()`, `x.values()`). Otherwise the callee is an item and the module that makes
    /// the call another; then a declaration's call holds what each argument names (a record it
    /// builds), and a list's call:
    /// - of a first-party callee holds nothing more: what it builds is behind the callee;
    /// - of `itertools.chain`, the items of each argument; of `functools.partial`, the items of
    ///   the function it wraps; of `dataclasses.replace`, the items of the base and the overrides;
    /// - of any other library callee (a method of a constant too: `"-".join(...)`), nothing more
    ///   when no argument holds an item that can be registered (`registrable`), else it is
    ///   unread: the library may hand on what the arguments name.
    ///
    /// A call over the extractor's budget is unread.
    fn call_items(
        &mut self,
        at: Place,
        map: &serde_json::Map<String, J>,
        reading: Reading,
        seen: &mut FxHashSet<Ent>,
        out: &mut Vec<Option<Ent>>,
    ) {
        if map.contains_key("o") {
            out.push(None);
            return;
        }
        let args: Vec<&J> = map
            .get("args")
            .and_then(J::as_array)
            .into_iter()
            .flatten()
            .collect();
        let callee = map.get("call").and_then(J::as_str).unwrap_or("?");
        if let [sole] = args[..]
            && COPY_CALLEES.contains(&callee)
        {
            self.json_items(at, sole, reading, seen, out);
            return;
        }
        let copied = callee
            .strip_suffix(".copy")
            .or_else(|| callee.strip_suffix(".values"));
        if let Some(copied) = copied
            && args.is_empty()
            && !map.contains_key("kw")
        {
            let name = V::Chain(self.core.it.intern(copied));
            self.leaf_items(at, &name, reading, seen, out);
            return;
        }
        let callee_json = map.get("call").unwrap_or(&J::Null);
        let of_constant = callee_json.get("v").is_some_and(|base| {
            let opaque = base.get("o").and_then(J::as_str);
            base.get("s").is_some()
                || base.is_number()
                || opaque.is_some_and(|o| CONSTANT_NODES.contains(&o))
        });
        let (surface, resolved) = if of_constant {
            let library = Resolved {
                res: Res::External,
                target: None,
                via: None,
            };
            (EMPTY, library)
        } else {
            let callee_value = value_tree(&mut self.core.it, callee_json);
            self.lookup_value(at.owner, &callee_value, at.scope, at.line)
        };
        match resolved.res {
            Res::Resolved => out.push(resolved.target),
            Res::External => {}
            _ => out.push(None),
        }
        out.push(Some(Ent {
            m: at.module,
            s: EMPTY,
        }));
        if reading == Reading::Declaration {
            for arg in call_arguments(map) {
                self.json_items(at, arg, reading, seen, out);
            }
            return;
        }
        if resolved.res != Res::External {
            return;
        }
        match self.core.it.s(surface) {
            "itertools.chain" => {
                for arg in args {
                    self.json_items(at, arg, reading, seen, out);
                }
            }
            "functools.partial" => match args.first() {
                Some(wrapped) if wrapped.get("star").is_none() => {
                    self.json_items(at, wrapped, reading, seen, out);
                }
                _ => out.push(None),
            },
            "dataclasses.replace" => {
                for arg in call_arguments(map) {
                    self.json_items(at, arg, reading, seen, out);
                }
            }
            _ => {
                let mut named = Vec::new();
                for arg in call_arguments(map) {
                    self.json_items(at, arg, reading, seen, &mut named);
                }
                if named.into_iter().any(|item| self.registrable(item, 0)) {
                    out.push(None);
                }
            }
        }
    }

    /// Whether an argument a library call is handed can be registered: a class or a function, a
    /// name whose items hold one, or one the link cannot tell. A module, and a name bound to
    /// constants, cannot (`TIMEOUT = timedelta(seconds=TIMEOUT_S)`, `os.getenv("X", DEFAULT)`).
    fn registrable(&mut self, item: Option<Ent>, depth: usize) -> bool {
        let Some(ent) = item else {
            return true;
        };
        if ent.s == EMPTY {
            return false;
        }
        if let Some(&known) = self.registrable.get(&ent) {
            return known;
        }
        if depth > MAX_REGISTRABLE_DEPTH {
            return true;
        }
        // A cycle of names reads as registrable until the walk says otherwise: never a miss.
        self.registrable.insert(ent, true);
        let found = match self.core.binding_of(ent.m, ent.s) {
            Some(Binding::Module(_)) => false,
            Some(Binding::Variable | Binding::Alias(_)) => {
                let mut seen = FxHashSet::default();
                match self.binding_items(ent, Reading::List, &mut seen) {
                    Some(items) => items
                        .into_iter()
                        .any(|item| self.registrable(item, depth + 1)),
                    None => true,
                }
            }
            _ => true,
        };
        self.registrable.insert(ent, found);
        found
    }

    /// The items of a value with no call and no spelled part: a name by `chain_items`, anything
    /// else as a list's (`sequence_items`).
    fn leaf_items(
        &mut self,
        at: Place,
        value: &V,
        reading: Reading,
        seen: &mut FxHashSet<Ent>,
        out: &mut Vec<Option<Ent>>,
    ) {
        match value {
            V::Chain(_) => self.chain_items(at, value, reading, seen, out),
            _ => self.sequence_items(at, value, seen, out),
        }
    }

    /// The items of a name. It is the item its lookup names; in a declaration, what that name
    /// holds is also read as a declaration where it is bound (`bound_name`), so a record bound to
    /// a name keeps its arguments' items. A name read past what the lookup names
    /// (`Base.WORKFLOWS`, `Queues.MAIN`) is that attribute of the class: what the class body binds
    /// it to, the class itself when the body defines it by a def or a class, else unread.
    fn chain_items(
        &mut self,
        at: Place,
        value: &V,
        reading: Reading,
        seen: &mut FxHashSet<Ent>,
        out: &mut Vec<Option<Ent>>,
    ) {
        let (surface, resolved) = self.resolve_item(at, value);
        let target = match resolved.res {
            Res::Resolved => resolved.target,
            Res::External => return,
            _ => {
                out.push(None);
                return;
            }
        };
        let Some(target) = target.filter(|target| target.s != EMPTY) else {
            out.push(target);
            return;
        };
        out.push(Some(target));
        let origin = self.core.it.join(target.m, target.s);
        let rest = (self.core.it.s(surface).strip_prefix(self.core.it.s(origin)))
            .and_then(|rest| rest.strip_prefix('.'))
            .map(str::to_string);
        let Some(rest) = rest else {
            // A class member the lookup lands on (`Base.WORKFLOWS` read in another class body)
            // is stored as one: no reference edge may read it from outside. It is read back
            // only from a stable class (`stable_class`).
            let member = (self.core.it.s(target.s).rsplit_once('.'))
                .filter(|_| surface == origin)
                .map(|(class, attribute)| (class.to_string(), attribute.to_string()));
            if let Some((class, attribute)) = member {
                let class = Ent {
                    m: target.m,
                    s: self.core.it.intern(&class),
                };
                let is_class = self.def_of(class.m, class.s, HitKind::Class).is_some();
                if is_class && !self.stable_class(class, &attribute) {
                    out.push(None);
                    return;
                }
            }
            if reading == Reading::Declaration || self.core.it.s(target.s).contains('.') {
                self.bound_name(target, reading, seen);
            }
            return;
        };
        // An attribute of a class is what its body binds only when the class is stable.
        if self.def_of(target.m, target.s, HitKind::Class).is_some()
            && !self.stable_class(target, &rest)
        {
            out.push(None);
            return;
        }
        let key = format!("{}.{rest}", self.core.it.s(target.s));
        let attribute = Ent {
            m: target.m,
            s: self.core.it.intern(&key),
        };
        if self.bound_name(attribute, reading, seen) {
            out.push(Some(attribute));
        } else if !self.class_defines(target, &rest) {
            out.push(None);
        }
    }

    /// A name's lookup where a value reads it. A binding that reads the name it binds is exact
    /// only where the name is bound before it: by the scope's own earlier binding (the union of
    /// the rows is what it holds), by an import (`from x import W` then `W = W + [B]`), or, in a
    /// class body, by the module (`class R: W = W`). A first binding of a module-level name that
    /// no import binds before it (a star import, say), or whose import leads back to itself, is
    /// unread.
    fn resolve_item(&mut self, at: Place, value: &V) -> (Sid, Resolved) {
        let found = self.lookup_value(at.owner, value, at.scope, at.line);
        let reads_itself =
            at.binding != EMPTY && matches!(value, V::Chain(name) if *name == at.binding);
        if !reads_itself || !at.first {
            return found;
        }
        if at.scope != -1 {
            return self.lookup_value(at.owner, value, -1, at.line);
        }
        let itself = Some(Ent {
            m: at.module,
            s: at.binding,
        });
        if found.1.target != itself {
            return found;
        }
        let core = &mut *self.core;
        let Some(lookup) = core.import_before(&self.scopes[at.owner], at.binding, at.line) else {
            return (EMPTY, UNRESOLVED);
        };
        let resolved = core.classify_lookup(lookup);
        if resolved.target == itself {
            return (EMPTY, UNRESOLVED);
        }
        (lookup.surface, resolved)
    }

    /// Reads what a name holds where it is bound, as `reading` reads it, and stores it under its
    /// own key (`declared`, `attributes`), so an unknown there is its module's, never the reader's
    /// product's. Read once. Whether the module binds the name by assignment.
    fn bound_name(&mut self, target: Ent, reading: Reading, seen: &mut FxHashSet<Ent>) -> bool {
        let key = (target.m, target.s);
        let stored = match reading {
            Reading::Declaration => &self.declared,
            Reading::List => &self.attributes,
        };
        if stored.contains_key(&key) || !seen.insert(target) {
            return true;
        }
        let Some(items) = self.binding_items(target, reading, seen) else {
            return false;
        };
        if !items.is_empty() {
            match reading {
                Reading::Declaration => self.declared.insert(key, items),
                Reading::List => self.attributes.insert(key, items),
            };
        }
        true
    }

    /// What a name holds where its module binds it (a class attribute too, `Class.NAME`), read as
    /// `reading` reads it. None when its module binds it by no assignment (a def, a class, an
    /// import: its module is the item).
    fn binding_items(
        &mut self,
        target: Ent,
        reading: Reading,
        seen: &mut FxHashSet<Ent>,
    ) -> Option<Vec<Option<Ent>>> {
        self.core.rec.read(Kind::Alias, target.m, target.s);
        let table = self.core.table(target.m)?;
        let file = self.core.table_at(table).record?;
        let owner = *self.scope_of_file.get(&file)? as usize;
        let facts = self.core.facts.clone();
        let record = &facts.files[file as usize];
        if !record.ok() {
            return Some(vec![None]);
        }
        let mut touches = self.touches(owner, record, true);
        let (name, rows) = touches.remove(&target.s)?;
        let mut text = AssignText::new(facts.clone(), file as usize);
        let items = self.name_items(owner, target.m, name, &rows, reading, &mut text, seen);
        Some(items)
    }

    /// Whether the body of class `class` defines the first attribute of `rest` by a def or a
    /// class (a method, a nested class).
    fn class_defines(&mut self, class: Ent, rest: &str) -> bool {
        let first = rest.split('.').next().unwrap_or_default();
        let Some(table) = self.core.table(class.m) else {
            return false;
        };
        let Some(file) = self.core.table_at(table).record else {
            return false;
        };
        let Some(&owner) = self.scope_of_file.get(&file) else {
            return false;
        };
        let scope = &self.scopes[owner as usize];
        let Some(index) = (0..scope.qualnames.len())
            .find(|&at| scope.qualnames[at] == class.s && !scope.in_function[at])
        else {
            return false;
        };
        let facts = self.core.facts.clone();
        let defs = &facts.files[file as usize].defs;
        defs.iter()
            .any(|node| node.s == index as i32 && self.core.it.s(node.n) == first)
    }

    /// Whether the value a subscript reads is a name of library code (`Callable`, `list`): the
    /// subscript then builds a type alias, not an item of a value.
    fn library_generic(&mut self, at: Place, inner: &V) -> bool {
        if !matches!(inner, V::Chain(_)) {
            return false;
        }
        let (_, resolved) = self.lookup_value(at.owner, inner, at.scope, at.line);
        resolved.res == Res::External
    }

    /// The one argument of a call of `sorted`, `list`, `tuple` or `set`: a copy of a sequence.
    fn copied<'v>(&self, value: &'v V) -> Option<&'v V> {
        let V::Call(callee, _, Some(sole)) = value else {
            return None;
        };
        let V::Chain(name) = &**callee else {
            return None;
        };
        COPY_CALLEES
            .contains(&self.core.it.s(*name))
            .then_some(&**sole)
    }

    /// The items of one value tree with no call, as a list's, in order, into `out`. A name is
    /// read by `chain_items`; a subscript holds nothing when its parts hold nothing, else it is
    /// unread; `a or b` holds what either side holds; a value the tree cannot spell out is unread.
    fn sequence_items(
        &mut self,
        at: Place,
        value: &V,
        seen: &mut FxHashSet<Ent>,
        out: &mut Vec<Option<Ent>>,
    ) {
        match value {
            V::List(items) | V::Tuple(items) | V::Set(items) | V::BoolOp(items) => {
                for item in items {
                    self.sequence_items(at, item, seen, out);
                }
            }
            V::Dict(entries) => {
                for (key, item) in entries {
                    if let Some(key) = key.as_ref().filter(|key| !matches!(key, V::Str(_, None))) {
                        self.sequence_items(at, key, seen, out);
                    }
                    self.sequence_items(at, item, seen, out);
                }
            }
            V::Star(inner) | V::Unary(inner) => self.sequence_items(at, inner, seen, out),
            V::Bin(_, left, right) => {
                self.sequence_items(at, left, seen, out);
                self.sequence_items(at, right, seen, out);
            }
            // A subscript of a library generic is a type alias (`Callable[[A], B]`,
            // `Literal["a"]`): it holds what its parts hold. Of anything else, it holds nothing
            // when its parts hold nothing, else it picks an item the link cannot tell.
            V::Sub(inner, index) => {
                let mut parts = Vec::new();
                self.sequence_items(at, inner, seen, &mut parts);
                self.sequence_items(at, index, seen, &mut parts);
                if self.library_generic(at, inner) {
                    out.extend(parts);
                } else if !parts.is_empty() {
                    out.push(None);
                }
            }
            // A string holds nothing, also one that spells a type (`Literal["a"]`, `list["W"]`).
            V::Str(..) | V::Scalar | V::Null => {}
            V::Opaque(what) => match self.core.it.s(*what) {
                // A constant the tree does not spell out holds no item a module defines.
                "badstr" | "longstr" | "int" | "float" | "complex" | "bytes" | "..."
                | "JoinedStr" => {}
                // A lambda is defined where the list is.
                "Lambda" => out.push(Some(Ent {
                    m: at.module,
                    s: EMPTY,
                })),
                _ => out.push(None),
            },
            V::Chain(_) => self.chain_items(at, value, Reading::List, seen, out),
            V::Call(..) => match self.copied(value) {
                Some(sole) => self.sequence_items(at, sole, seen, out),
                None => out.push(None),
            },
            _ => out.push(None),
        }
    }

    fn references(&mut self, owner: usize) {
        let facts = self.core.facts.clone();
        let k = &self.settings.k;
        let file = self.scopes[owner].file as usize;
        for row in &facts.files[file].refs {
            let core = &mut *self.core;
            let scope = &mut self.scopes[owner];
            let tokens = chain_tokens(&row.chain);
            let leading: Vec<Sid> = tokens
                .iter()
                .take_while(|t| t.starts_with('.'))
                .map(|t| core.it.intern(&t[1..]))
                .collect();
            let first =
                core.name_lookup(&self.settings.names, scope, row.name, row.scope, row.line);
            let (found, rest) = core.names_descend(first, &leading);
            let remaining = &tokens[rest..];
            let use_text = match remaining.first() {
                Some(&"()") => "call",
                Some(&"[]") => "subscript",
                Some(_) => "attribute",
                None => up_position(&row.up),
            };
            let use_ = core.it.intern(use_text);
            let chain = core.it.intern(&remaining.concat());
            let up = core.it.intern(&row.up);
            let mut attrs = vec![(k.chain, chain), (k.up, up)];
            if row.header != -1 {
                attrs.push((k.header, scope.qualnames[row.header as usize]));
            }
            if remaining.is_empty() && listed(&row.up) {
                attrs.push((k.listed, k.one));
            }
            let resolved = core.classify_lookup(found);
            let ctx = core.contexts(scope, row.flags, row.scope);
            self.push(
                REFERENCE,
                owner,
                row.scope,
                row.line,
                row.col,
                ctx,
                found.surface,
                resolved,
                use_,
                &attrs,
            );
        }
        self.alias_references(owner);
        self.named_references(owner);
    }

    /// A lower-case name read through a module alias of an export module (`api.f()` after
    /// `from ...facade import api`) has no `refs` row, only a `uses` count: one reference edge per
    /// scope and attribute chain, at its first use. Elsewhere the module's import stands for it.
    /// A `uses` row has no statement flags, so the edge's context comes from its scope alone.
    fn alias_references(&mut self, owner: usize) {
        let facts = self.core.facts.clone();
        let k = &self.settings.k;
        let file = self.scopes[owner].file as usize;
        for row in &facts.files[file].uses {
            let core = &mut *self.core;
            let scope = &mut self.scopes[owner];
            let first =
                core.name_lookup(&self.settings.names, scope, row.root, row.scope, row.line);
            let Some(module) = first.hit.filter(|&hit| core.kind(hit) == HitKind::Module) else {
                continue;
            };
            let module = core.origin(module).m;
            // The module object itself used, not a name of it: handed to a call, stored, read
            // through `__dict__` or another dunder, or read by a name no one can write (`?`:
            // `getattr(alias, NAME)`, `vars(alias)`). What it hands out cannot be told there.
            let attrs = core.it.s(row.attrs);
            if attrs.starts_with(".__") {
                self.computed_reads.insert((scope.module, module));
            }
            // `importlib.reload(alias)` reloads the module the alias names: a read of it.
            let reloads = (facts.files[file].computed.iter())
                .filter(|load| load.form == LoadForm::Reload)
                .filter(|load| load.scope == row.scope && load.sequence == row.root)
                .count();
            let handed = attrs.is_empty() && row.count as usize > reloads;
            if handed || attrs.starts_with(".?") {
                self.computed_reads.insert((scope.module, module));
            }
            if attrs.is_empty() || attrs.starts_with(".?") {
                continue;
            }
            let export = core.module_path(module).is_some_and(|path| {
                (self.settings.export_surfaces.iter())
                    .any(|surface| surface.covered_root(path).is_some())
            });
            if !export {
                continue;
            }
            let names: Vec<String> = (core.it.s(row.attrs).split('.'))
                .filter(|name| !name.is_empty())
                .map(str::to_string)
                .collect();
            let leading: Vec<Sid> = names.iter().map(|name| core.it.intern(name)).collect();
            let (found, used) = core.names_descend(first, &leading);
            let Some(hit) = found.hit else {
                continue;
            };
            if used == 0 || core.kind(hit) == HitKind::Module {
                continue;
            }
            let remaining: String = names[used..]
                .iter()
                .map(|name| format!(".{name}"))
                .collect();
            let attrs = [(k.chain, core.it.intern(&remaining))];
            let use_ = core.it.intern("attribute");
            let resolved = core.classify_lookup(found);
            let ctx = core.contexts(scope, 0, row.scope);
            self.push(
                REFERENCE,
                owner,
                row.scope,
                row.line,
                row.col,
                ctx,
                found.surface,
                resolved,
                use_,
                &attrs,
            );
        }
    }

    /// A use of a name `[discovery] reference_names` lists: one reference edge per scope and
    /// attribute chain, at its first use, as `alias_references` makes one. Core has no `refs` row
    /// for a third-party name or a first-party function, only a `uses` count, so without this no
    /// edge says where such a name is used. A use counts when its name resolves to a listed one,
    /// through an alias or a re-export too. The position of the use is not recorded: its `use` is
    /// `attribute` when the code reads an attribute of the name, else `other`.
    fn named_references(&mut self, owner: usize) {
        if self.settings.reference_names.is_empty() {
            return;
        }
        let facts = self.core.facts.clone();
        let k = &self.settings.k;
        let file = self.scopes[owner].file as usize;
        for row in &facts.files[file].uses {
            let core = &mut *self.core;
            let scope = &mut self.scopes[owner];
            let first =
                core.name_lookup(&self.settings.names, scope, row.root, row.scope, row.line);
            let Some(hit) = first.hit else {
                continue;
            };
            let export_alias = core.kind(hit) == HitKind::Module && {
                let module = core.origin(hit).m;
                core.module_path(module).is_some_and(|path| {
                    (self.settings.export_surfaces.iter())
                        .any(|surface| surface.covered_root(path).is_some())
                })
            };
            let names: Vec<String> = (core.it.s(row.attrs).split('.'))
                .filter(|name| !name.is_empty())
                .map(str::to_string)
                .collect();
            let leading: Vec<Sid> = names.iter().map(|name| core.it.intern(name)).collect();
            let (found, used) = core.names_descend(first, &leading);
            // A name read through a module alias of an export has its edge already.
            if export_alias
                && used > 0
                && found
                    .hit
                    .is_some_and(|hit| core.kind(hit) != HitKind::Module)
            {
                continue;
            }
            let resolved = core.classify_lookup(found);
            let Some(target) = resolved.target else {
                continue;
            };
            if !self.settings.reference_names.contains(&target) {
                continue;
            }
            let remaining: String = names[used..]
                .iter()
                .map(|name| format!(".{name}"))
                .collect();
            let use_ = core.it.intern(if remaining.is_empty() {
                "other"
            } else {
                "attribute"
            });
            let mut attrs = vec![(k.chain, core.it.intern(&remaining))];
            if row.header != -1 {
                attrs.push((k.header, scope.qualnames[row.header as usize]));
            }
            let ctx = core.contexts(scope, 0, row.scope);
            self.push(
                REFERENCE,
                owner,
                row.scope,
                row.line,
                row.col,
                ctx,
                found.surface,
                resolved,
                use_,
                &attrs,
            );
        }
    }

    /// Every dotted name in a type expression. `Annotated`
    /// passes its type through as it stands.
    fn mentions(&mut self, value: &V, whole: bool, argument_of: Sid, out: &mut Vec<Named>) {
        let k = &self.settings.k;
        match value {
            V::Chain(chain) => out.push(Named {
                name: *chain,
                whole,
                subscripted: false,
                argument_of,
            }),
            V::Str(_, Some(parsed)) => self.mentions(parsed, whole, argument_of, out),
            V::Sub(inner, index) => {
                let start = out.len();
                self.mentions(inner, whole, argument_of, out);
                for found in &mut out[start..] {
                    found.subscripted = true;
                }
                let container = match &**inner {
                    V::Chain(chain) => *chain,
                    _ => EMPTY,
                };
                let last = if container == EMPTY {
                    EMPTY
                } else {
                    self.core.it.last(container)
                };
                if last == k.literal_name {
                    return;
                }
                if last == k.annotated_name
                    && let V::Tuple(items) = &**index
                    && let Some(first) = items.first()
                {
                    self.mentions(first, false, argument_of, out);
                    return;
                }
                self.mentions(index, false, container, out);
            }
            V::Bin(true, l, r) => {
                self.mentions(l, whole, argument_of, out);
                self.mentions(r, whole, argument_of, out);
            }
            V::Tuple(items) | V::List(items) => {
                for item in items {
                    self.mentions(item, false, argument_of, out);
                }
            }
            V::Star(inner) => self.mentions(inner, whole, argument_of, out),
            _ => {}
        }
    }

    /// Whether a type expression has a part `mentions` cannot read
    /// as types. A `Literal`'s values, the metadata of `Annotated` and `...` are no types.
    fn unread(&mut self, value: &V) -> bool {
        let k = &self.settings.k;
        match value {
            V::Chain(_) => false,
            V::Sub(inner, index) => {
                let last = match &**inner {
                    V::Chain(chain) => self.core.it.last(*chain),
                    _ => EMPTY,
                };
                if last == k.literal_name {
                    return false;
                }
                if last == k.annotated_name
                    && let V::Tuple(items) = &**index
                    && let Some(first) = items.first()
                {
                    return self.unread(first);
                }
                self.unread(inner) || self.unread(index)
            }
            V::Str(_, Some(parsed)) => self.unread(parsed),
            V::Bin(true, l, r) => self.unread(l) || self.unread(r),
            V::Tuple(items) | V::List(items) => items.iter().any(|item| self.unread(item)),
            V::Star(inner) => self.unread(inner),
            V::Opaque(what) => self.core.it.s(*what) != "...",
            _ => true,
        }
    }

    /// The `argument_of` attr of a type argument: the generic it is given to, as it resolves.
    fn argument_marker(&mut self, at: SlotLookup, argument_of: Sid) -> Option<Sid> {
        if argument_of == EMPTY {
            return None;
        }
        Some(self.lookup_at(at, argument_of).surface)
    }

    /// What tells a slot's rows of one name apart: an expanded
    /// alias, a generic given arguments, and the generic it is an argument of.
    fn markers(
        &mut self,
        expanded: bool,
        subscripted: bool,
        at: SlotLookup,
        argument_of: Sid,
    ) -> Marks {
        Marks {
            alias: if expanded {
                self.settings.k.expanded
            } else {
                EMPTY
            },
            subscripted,
            argument_of: self.argument_marker(at, argument_of),
        }
    }

    /// A dotted name, looked up where a slot's names are.
    fn lookup_at(&mut self, at: SlotLookup, name: Sid) -> Lookup {
        let scope = &mut self.scopes[at.owner];
        self.core
            .dotted_lookup(&self.settings.names, scope, name, at.scope, at.line)
    }

    /// Whether every step from the name the code used to the origin is first-party code.
    fn first_party_chain(&mut self, hit: HitId) -> bool {
        let core = &mut *self.core;
        let chain = core.chain(hit).to_vec();
        chain.iter().all(|step| {
            let head = core.it.head(step.m);
            core.rec.read(Kind::Head, head, EMPTY);
            self.first_party.contains(&head)
        })
    }

    /// A module-level type alias a bare name binds (`X = dict[str, Any]`): the file and assign
    /// row of its value, read from the file the module table reads the name from: the `.py`, or
    /// the stub for a name only the stub binds. Each module's assignments are read once.
    fn alias(&mut self, module: Sid, name: Sid) -> Option<(u32, u32)> {
        if self.core.it.s(name).contains('.') {
            return None;
        }
        self.core.rec.read(Kind::Alias, module, name);
        if !matches!(
            self.core.binding_of(module, name)?,
            Binding::Alias(_) | Binding::Variable
        ) {
            return None;
        }
        if !self.aliases.contains_key(&module) {
            let facts = self.core.facts.clone();
            let table = self.core.table(module)?;
            let table = self.core.table_at(table);
            let mut found = FxHashMap::default();
            if let Some(file) = table.record {
                for (name, at) in module_assigns(&self.core.it, &facts.files[file as usize]) {
                    found.insert(name, (file, at));
                }
            }
            if let Some(file) = table.stub_record.filter(|_| !table.stub_names.is_empty()) {
                for (name, at) in module_assigns(&self.core.it, &facts.files[file as usize]) {
                    if table.stub_names.contains(&name) {
                        found.insert(name, (file, at));
                    }
                }
            }
            self.aliases.insert(module, found);
        }
        let (file, at) = *self.aliases[&module].get(&name)?;
        if !self.stands_for_type(file, at) {
            return None;
        }
        self.alias_rows(module, name)?;
        Some((file, at))
    }

    /// Whether the module-level assignment at (file, row) is one the producer expands as a type
    /// alias: its value is one (`alias_value`), or a `TypeVar` with a bound (`declared_type`).
    fn stands_for_type(&mut self, file: u32, at: u32) -> bool {
        let facts = self.core.facts.clone();
        let Some(value) = &facts.files[file as usize].assigns[at as usize].value else {
            return false;
        };
        alias_value(value) || self.declared_type(file, at).is_some()
    }

    /// The type a module-level `TypeVar` stands for, when its callee is `typing`'s: its bound, or
    /// the union of its constraints. None for any other value, and for a `TypeVar` with neither.
    fn declared_type(&mut self, file: u32, at: u32) -> Option<V> {
        let facts = self.core.facts.clone();
        let row = &facts.files[file as usize].assigns[at as usize];
        let Some(V::Call(callee, _, Some(kept))) = &row.value else {
            return None;
        };
        let V::Chain(name) = **callee else {
            return None;
        };
        let lookup = SlotLookup {
            owner: *self.scope_of_file.get(&file)? as usize,
            scope: -1,
            line: row.l,
        };
        let callee = self.lookup_at(lookup, name).surface;
        if !self.settings.k.type_vars.contains(&callee) {
            return None;
        }
        Some(match &**kept {
            V::Tuple(constraints) => union(constraints),
            bound => bound.clone(),
        })
    }

    /// Every module-level assignment (file, row) of a name the module
    /// binds more than once (once per branch, say), in source order, when each one assigns a
    /// type alias; empty for a name bound once; None when one binding is no type alias.
    fn alias_rows(&mut self, module: Sid, name: Sid) -> Option<Vec<(u32, u32)>> {
        let Some(table) = self.core.table(module) else {
            return Some(Vec::new());
        };
        let table = self.core.table_at(table);
        let bindings = table.bindings.get(&name).cloned().unwrap_or_default();
        if bindings.len() < 2 {
            return Some(Vec::new());
        }
        let file = table.record?;
        let mut rows = Vec::new();
        for (_, binding, assign) in bindings {
            let at = assign?;
            if !matches!(binding, Binding::Alias(_) | Binding::Variable) {
                return None;
            }
            if !self.stands_for_type(file, at) {
                return None;
            }
            rows.push((file, at));
        }
        Some(rows)
    }

    /// The type alias a mention names, as (module, name), the (file, row) of its value, and where
    /// its names are looked up: assigned in the module that uses it, or followed through imports
    /// to the first-party module that assigns it. A module holds `Handler = Callable[[Order], None]` to
    /// shorten a signature, not to hide its types. A name that resolves elsewhere, such as in a
    /// class body, is no module-level alias of the same spelling.
    fn alias_of(&mut self, mention: Mention, found: Lookup) -> Option<Alias> {
        let module = self.scopes[mention.lookup.owner].module;
        let module_level = found.surface == self.core.it.join(module, mention.value);
        if module_level && let Some(row) = self.alias(module, mention.value) {
            return Some(Alias {
                key: (module, mention.value),
                row,
                lookup: mention.lookup,
            });
        }
        let hit = found.hit?;
        if self.core.kind(hit) != HitKind::Variable {
            return None;
        }
        if !self.first_party_chain(hit) {
            return None;
        }
        let origin = self.core.origin(hit);
        let (file, at) = self.alias(origin.m, origin.s)?;
        let owner = *self.scope_of_file.get(&file)? as usize;
        let lookup = SlotLookup {
            owner,
            scope: -1,
            line: self.core.facts.files[file as usize].assigns[at as usize].l,
        };
        Some(Alias {
            key: (origin.m, origin.s),
            row: (file, at),
            lookup,
        })
    }

    /// One `exposes` row of a callable's slot.
    fn push_slot(
        &mut self,
        rows: SlotRows,
        surface: Sid,
        resolved: Resolved,
        slot: Sid,
        position: Sid,
        extra: &[(Sid, Sid)],
    ) {
        let k = &self.settings.k;
        let mut attrs = [
            (k.callable, rows.callable),
            (k.slot, slot),
            (k.position, position),
            (EMPTY, EMPTY),
            (EMPTY, EMPTY),
            (EMPTY, EMPTY),
        ];
        let mut len = 3;
        for pair in extra
            .iter()
            .copied()
            .chain((rows.handed != EMPTY).then_some((k.handed, rows.handed)))
        {
            attrs[len] = pair;
            len += 1;
        }
        let site = rows.site;
        let use_ = k.use_annotation;
        self.push(
            EXPOSES,
            site.owner,
            site.scope,
            site.line,
            site.col,
            site.ctx,
            surface,
            resolved,
            use_,
            &attrs[..len],
        );
    }

    /// A row for a whole slot whose types are unknown.
    fn push_unresolved(
        &mut self,
        rows: SlotRows,
        surface: Sid,
        slot: Sid,
        extra: Option<(Sid, Sid)>,
    ) {
        let whole = self.settings.k.whole;
        let extra: Vec<(Sid, Sid)> = extra.into_iter().collect();
        self.push_slot(rows, surface, UNRESOLVED, slot, whole, &extra);
    }

    /// A site in a file's module body.
    fn module_site(&self, owner: usize, line: u32, col: u32) -> SlotSite {
        SlotSite {
            owner,
            scope: -1,
            line,
            col,
            ctx: self.scopes[owner].base_ctx,
        }
    }

    /// An alias's value into an expansion. A name bound more
    /// than once expands every binding's value, in source order, each looked up where it is
    /// assigned: it stands for the union.
    fn expand_alias(&mut self, alias: Alias, top: bool, expansion: &mut Expansion) {
        let facts = self.core.facts.clone();
        let rows = self
            .alias_rows(alias.key.0, alias.key.1)
            .unwrap_or_default();
        let owner = rows
            .first()
            .filter(|_| rows.len() > 1)
            .and_then(|(file, _)| self.scope_of_file.get(file).copied());
        let Some(owner) = owner else {
            let (file, at) = alias.row;
            self.expand_row(file, at, alias.lookup, top, expansion);
            return;
        };
        for (file, at) in rows {
            let lookup = SlotLookup {
                owner: owner as usize,
                scope: -1,
                line: facts.files[file as usize].assigns[at as usize].l,
            };
            self.expand_row(file, at, lookup, top, expansion);
        }
    }

    /// One assignment's value into an expansion: the type a `TypeVar` call declares, else the
    /// value as written.
    fn expand_row(
        &mut self,
        file: u32,
        at: u32,
        lookup: SlotLookup,
        top: bool,
        expansion: &mut Expansion,
    ) {
        let declared = self.declared_type(file, at);
        let facts = self.core.facts.clone();
        let written = facts.files[file as usize].assigns[at as usize]
            .value
            .as_ref();
        if let Some(value) = declared.as_ref().or(written) {
            self.expand_into(value, lookup, top, expansion);
        }
    }

    /// Depth first, the names in an alias's value, each alias in it followed once per place (top
    /// or nested). A cycle stops at the alias that closes it; an alias deeper than
    /// MAX_ALIAS_DEPTH stays a marker, because a type left unread is unknown.
    fn expand_into(&mut self, value: &V, at: SlotLookup, top: bool, expansion: &mut Expansion) {
        if self.unread(value) {
            expansion.write(Expanded {
                value: EMPTY,
                top,
                lookup: at,
                too_deep: false,
                subscripted: false,
                argument_of: EMPTY,
                unread: true,
            });
        }
        let mut names = Vec::new();
        self.mentions(value, true, EMPTY, &mut names);
        for named in names {
            let item = Expanded {
                value: named.name,
                top: top && named.whole,
                lookup: at,
                too_deep: false,
                subscripted: named.subscripted,
                argument_of: named.argument_of,
                unread: false,
            };
            expansion.write(item);
            let found = self.lookup_at(at, named.name);
            let mention = Mention {
                value: named.name,
                whole: named.whole,
                lookup: at,
            };
            let Some(alias) = self.alias_of(mention, found) else {
                continue;
            };
            let key = alias.key;
            if expansion.stack.contains(&key) || !expansion.followed.insert((key, item.top)) {
                continue;
            }
            if expansion.stack.len() >= MAX_ALIAS_DEPTH {
                expansion.write(Expanded {
                    too_deep: true,
                    ..item
                });
                continue;
            }
            expansion.stack.push(key);
            self.expand_alias(alias, item.top, expansion);
            expansion.stack.pop();
        }
    }

    /// Whether a name of an expansion is a type alias whose own expansion was written there: it
    /// was followed, not left as a too-deep marker, and it names at least one type.
    fn expanded_in(&mut self, expansion: &[Expanded], item: Expanded, found: Lookup) -> bool {
        let too_deep = Expanded {
            too_deep: true,
            ..item
        };
        if expansion.contains(&too_deep) {
            return false;
        }
        let mention = Mention {
            value: item.value,
            whole: item.top,
            lookup: item.lookup,
        };
        self.alias_of(mention, found)
            .is_some_and(|alias| !self.expansion(alias).is_empty())
    }

    /// The names a type alias expands to, worked out once per alias and place its names are
    /// looked up: the alias decides them, never the slot that names it.
    fn expansion(&mut self, alias: Alias) -> Rc<[Expanded]> {
        let Alias {
            key, lookup: at, ..
        } = alias;
        let memo = Memo::Expansion(key.0, key.1, at.owner as u32, at.scope, at.line);
        if let Some(found) = self.expansions.get(&(key, at)) {
            self.core.rec.replay(memo);
            return found.clone();
        }
        let start = self.core.rec.mark();
        let mut expansion = Expansion {
            stack: vec![key],
            followed: FxHashSet::default(),
            seen: FxHashSet::default(),
            out: Vec::new(),
        };
        self.expand_alias(alias, true, &mut expansion);
        let found: Rc<[Expanded]> = Rc::from(expansion.out);
        self.expansions.insert((key, at), found.clone());
        self.core.rec.remember(memo, start);
        found
    }

    /// Whether a name resolves to a plain builtin; outside a
    /// contract it gets no row.
    fn plain_builtin(&mut self, found: Lookup, name: Sid) -> bool {
        let core = &mut *self.core;
        found.hit.is_none() && {
            let head = core.it.head(name);
            found.surface == core.it.join(self.settings.names.builtins_module, head)
        }
    }

    /// One row per mention, each followed by a row per name of the type alias it is, when it is
    /// one: what an alias stands for is a fact in every file. Outside a contract a name that
    /// resolves to a plain builtin gets no row, in the alias's expansion too. A slot names each
    /// row an alias expands to once, however often its aliases repeat it; rows that differ in an
    /// attr (such as the generic a name is an argument of) are different rows. A name the slot
    /// holds bare and through an alias is one row, and an alias left too deep to follow is only
    /// its marker.
    fn expose_mentions(
        &mut self,
        rows: SlotRows,
        slot: Sid,
        contract: bool,
        lookup: SlotLookup,
        names: Vec<Named>,
        mut unread: bool,
    ) {
        let mut written: FxHashSet<AliasRow> = FxHashSet::default();
        for named in names {
            let mention = Mention {
                value: named.name,
                whole: named.whole,
                lookup,
            };
            let found = self.lookup_at(mention.lookup, mention.value);
            let alias = self.alias_of(mention, found);
            let expansion = alias.map(|alias| self.expansion(alias));
            let k = &self.settings.k;
            let position = if mention.whole { k.whole } else { k.nested };
            if contract || !self.plain_builtin(found, mention.value) {
                let expanded = expansion.as_ref().is_some_and(|items| !items.is_empty());
                let marks = self.markers(expanded, named.subscripted, lookup, named.argument_of);
                if written.insert((found.surface, mention.whole, marks)) {
                    let resolved = self.core.classify_lookup(found);
                    let extra = marks.attrs(&self.settings.k);
                    self.push_slot(rows, found.surface, resolved, slot, position, &extra);
                }
            }
            let Some(expansion) = expansion else {
                continue;
            };
            for &item in expansion.iter() {
                if item.unread {
                    unread = true;
                    continue;
                }
                let whole = mention.whole && item.top;
                let inner = self.lookup_at(item.lookup, item.value);
                let k = &self.settings.k;
                let position = if whole { k.whole } else { k.nested };
                if item.too_deep {
                    let marks = Marks {
                        alias: k.too_deep,
                        subscripted: false,
                        argument_of: None,
                    };
                    if written.insert((item.value, whole, marks)) {
                        let marker = marks.attrs(k);
                        self.push_slot(rows, item.value, UNRESOLVED, slot, position, &marker);
                    }
                    continue;
                }
                let too_deep = Expanded {
                    too_deep: true,
                    ..item
                };
                if expansion.contains(&too_deep) {
                    continue;
                }
                if !contract && self.plain_builtin(inner, item.value) {
                    continue;
                }
                let expanded = self.expanded_in(&expansion, item, inner);
                let marks = self.markers(expanded, item.subscripted, item.lookup, item.argument_of);
                if !written.insert((inner.surface, whole, marks)) {
                    continue;
                }
                let extra = marks.attrs(&self.settings.k);
                let resolved = self.core.classify_lookup(inner);
                self.push_slot(rows, inner.surface, resolved, slot, position, &extra);
            }
        }
        if unread {
            let k = &self.settings.k;
            let marker = Some((k.annotation, k.unread));
            self.push_unresolved(rows, EMPTY, slot, marker);
        }
    }

    /// One `exposes` edge per type name in a slot. Builtin types count only in a contract, and
    /// there an unannotated slot (annotation None) is an edge of its own. `handed` is the clause
    /// a handed-out callable comes by, EMPTY for the module's own.
    fn expose(
        &mut self,
        rows: SlotRows,
        lookup: SlotLookup,
        annotation: Option<&V>,
        slot: Sid,
        contract: bool,
    ) {
        let Some(annotation) = annotation else {
            let k = &self.settings.k;
            let missing = Some((k.annotation, k.missing));
            self.push_unresolved(rows, EMPTY, slot, missing);
            return;
        };
        let mut names = Vec::new();
        self.mentions(annotation, true, EMPTY, &mut names);
        let unread = self.unread(annotation);
        self.expose_mentions(rows, slot, contract, lookup, names, unread);
    }

    /// Whether a def's first parameter is bound by the call, whatever its name: a method's first
    /// positional parameter, unless the method is a `staticmethod`.
    fn has_receiver(&mut self, lookup: SlotLookup, node: &Def) -> bool {
        let facts = self.core.facts.clone();
        let defs = &facts.files[self.scopes[lookup.owner].file as usize].defs;
        if node.s == -1 || !defs[node.s as usize].is_class() {
            return false;
        }
        if !node.params.first().is_some_and(|param| param.positional) {
            return false;
        }
        for dec in &node.dec {
            let (surface, _) = self.lookup_value(lookup.owner, dec, node.s, node.l);
            if surface == self.settings.k.staticmethod {
                return false;
            }
        }
        true
    }

    /// The parameter and return slots of a function. In a contract a method's receiver and an
    /// unannotated return are no slots, and an unannotated parameter is one.
    fn expose_signature(&mut self, rows: SlotRows, lookup: SlotLookup, node: &Def, contract: bool) {
        let receiver = contract && self.has_receiver(lookup, node);
        let bounds = self.type_param_bounds(lookup.owner, Some(node), node.s);
        for (position, param) in node.params.iter().enumerate() {
            let in_slot = contract && !(receiver && position == 0);
            if !in_slot && param.annotation.is_none() {
                continue;
            }
            let slot = self
                .core
                .it
                .intern(&format!("param:{}", self.core.it.s(param.name)));
            let annotation = param.annotation.as_ref().map(|ann| bounded(ann, &bounds));
            self.expose(rows, lookup, annotation.as_deref(), slot, in_slot);
        }
        if let Some(ret) = &node.ret {
            let slot = self.settings.k.ret;
            self.expose(rows, lookup, Some(&*bounded(ret, &bounds)), slot, contract);
        }
    }

    /// The PEP 695 type parameters with a bound that a def's slots see: its own (`own`), then
    /// those of the defs and classes around it from `scope` out, the nearest first. A parameter
    /// hides an outer one of its name. One with no bound is left out: its name stays as written,
    /// unknown as a `TypeVar` with no bound is.
    fn type_param_bounds(&mut self, owner: usize, own: Option<&Def>, scope: i32) -> Vec<(Sid, V)> {
        let facts = self.core.facts.clone();
        let defs = &facts.files[self.scopes[owner].file as usize].defs;
        let mut around = Vec::new();
        let mut at = scope;
        while at >= 0 {
            around.push(&defs[at as usize]);
            at = defs[at as usize].s;
        }
        let mut seen: Vec<Sid> = Vec::new();
        let mut out = Vec::new();
        for node in own.into_iter().chain(around) {
            for (name, bound) in &node.type_params {
                if seen.contains(name) {
                    continue;
                }
                seen.push(*name);
                if let Some(bound) = bound {
                    out.push((*name, bound.clone()));
                }
            }
        }
        out
    }

    /// Whether an annotation is `ClassVar[...]`, by the last name of what it subscripts.
    fn is_class_var(&mut self, annotation: &V) -> bool {
        let V::Sub(inner, _) = annotation else {
            return false;
        };
        let last = match &**inner {
            V::Chain(chain) => self.core.it.last(*chain),
            V::Attr(_, name) => *name,
            _ => return false,
        };
        last == self.settings.k.class_var
    }

    /// Whether a class-body row's annotation is `ClassVar[...]`.
    fn is_class_var_row(&mut self, field: FieldRow) -> bool {
        let facts = self.core.facts.clone();
        let row = &facts.files[field.file as usize].assigns[field.at as usize];
        row.ann.as_ref().is_some_and(|ann| self.is_class_var(ann))
    }

    /// The annotated class-body rows of a class as slots of
    /// `kind`, the fields of a dataclass-like class (`field:`, of its `__init__`) or the
    /// attributes of a class (`attr:`, of the class: all rows of a class that is no dataclass, the
    /// `ClassVar` rows of one). In a contract only a row directly in a class body is a whole slot.
    /// A row the class itself declares sits at its own line, unless the class is handed out.
    fn expose_fields(&mut self, rows: SlotRows, fields: &[FieldRow], contract: bool, kind: Member) {
        let facts = self.core.facts.clone();
        for field in fields {
            let row = &facts.files[field.file as usize].assigns[field.at as usize];
            let Some(annotation) = row.ann.as_ref().filter(|ann| !ann.is_null()) else {
                continue;
            };
            let in_slot = contract && row.f == CLASS;
            if rows.handed != EMPTY && !in_slot {
                continue;
            }
            let V::Chain(name) = &row.targets[0] else {
                continue;
            };
            let slot = self
                .core
                .it
                .intern(&format!("{}:{}", kind.prefix(), self.core.it.s(*name)));
            let line_rows = if field.own && rows.handed == EMPTY {
                SlotRows {
                    site: SlotSite {
                        line: row.l,
                        ..rows.site
                    },
                    ..rows
                }
            } else {
                rows
            };
            let line_lookup = SlotLookup {
                line: row.l,
                ..field.lookup
            };
            let bounds = self.type_param_bounds(field.lookup.owner, None, field.lookup.scope);
            let annotation = bounded(annotation, &bounds);
            self.expose(line_rows, line_lookup, Some(&*annotation), slot, in_slot);
        }
    }

    /// The candidate dataclass fields of a file, by class; each file is read once.
    fn file_fields(&mut self, file: u32) -> &FxHashMap<i32, Vec<u32>> {
        let core = &self.core;
        self.fields
            .entry(file)
            .or_insert_with(|| fields_by_class(&core.it, &core.facts.files[file as usize].assigns))
    }

    /// The candidate dataclass fields of one class, as assign indexes.
    fn class_fields(&mut self, file: u32, idx: u32) -> Vec<u32> {
        self.file_fields(file)
            .get(&(idx as i32))
            .cloned()
            .unwrap_or_default()
    }

    /// Whether a class declares a candidate dataclass field.
    fn declares_fields(&mut self, file: u32, idx: u32) -> bool {
        self.file_fields(file)
            .get(&(idx as i32))
            .is_some_and(|found| !found.is_empty())
    }

    /// The first step of a hit's chain whose module binds the name
    /// more than once (once per branch, say): such a name stands for every one of its bindings.
    fn branch_bound(&mut self, hit: HitId) -> Option<Ent> {
        let chain = self.core.chain(hit).to_vec();
        for step in chain {
            if step.s == EMPTY {
                continue;
            }
            let Some(table) = self.core.table(step.m) else {
                continue;
            };
            let bound = self.core.table_at(table).bindings.get(&step.s);
            if bound.is_some_and(|found| found.len() > 1) {
                return Some(step);
            }
        }
        None
    }

    /// Whether a base's bindings are followed: it resolves to a
    /// first-party name that is no class, or through a name bound more than once.
    fn followed_base(&mut self, hit: HitId, resolved: Resolved) -> bool {
        if resolved.res == Res::Resolved && self.core.kind(hit) == HitKind::Variable {
            return true;
        }
        matches!(resolved.res, Res::Resolved | Res::External) && self.branch_bound(hit).is_some()
    }

    /// The classes a resolved name stands for, as a base: a class, a
    /// third-party name (read as a class, as a third-party base is), or a name bound more than
    /// once or to no class whose bindings all name classes. None for anything else.
    fn hit_classes(
        &mut self,
        surface: Sid,
        hit: Option<HitId>,
        seen: &mut Vec<(Sid, Sid)>,
    ) -> Option<Vec<String>> {
        if hit.is_none() && self.settings.names.builtins.contains(&surface) {
            return Some(vec![format!("builtins.{}", self.core.it.s(surface))]);
        }
        if let Some(step) = hit.and_then(|hit| self.branch_bound(hit)) {
            return self.bound_classes(step.m, step.s, seen);
        }
        let resolved = self.core.classify(surface, hit);
        if resolved.res == Res::External {
            return Some(
                resolved
                    .target
                    .map(|target| self.core.ent_text(target))
                    .into_iter()
                    .collect(),
            );
        }
        let hit = hit?;
        if resolved.res != Res::Resolved {
            return None;
        }
        let origin = self.core.origin(hit);
        match self.core.kind(hit) {
            HitKind::Class => Some(vec![self.core.ent_text(origin)]),
            HitKind::Variable => self.bound_classes(origin.m, origin.s, seen),
            _ => None,
        }
    }

    /// The classes one module-level binding of `name` stands
    /// for; None when it is no class.
    fn binding_classes(
        &mut self,
        module: Sid,
        name: Sid,
        binding: Binding,
        value: Option<&V>,
        seen: &mut Vec<(Sid, Sid)>,
    ) -> Option<Vec<String>> {
        match binding {
            Binding::Class(_, _) => Some(vec![format!(
                "{}.{}",
                self.core.it.s(module),
                self.core.it.s(name)
            )]),
            Binding::Module(dotted) => {
                let hit = self.core.resolve_binding(module, binding);
                self.hit_classes(dotted, hit, seen)
            }
            Binding::From(source, attr) => {
                let surface = self.core.it.join(source, attr);
                let hit = self.core.resolve_binding(module, binding);
                self.hit_classes(surface, hit, seen)
            }
            Binding::Alias(_) | Binding::Variable => {
                let mut value = value?;
                while let V::Sub(inner, _) = value {
                    value = inner;
                }
                let V::Chain(chain) = value else {
                    return None;
                };
                let (hit, used, total) = self.core.resolve_chain(module, *chain);
                if used != total {
                    return None;
                }
                self.hit_classes(*chain, hit, seen)
            }
            Binding::Def(_, _) => None,
        }
    }

    /// The classes a module-level name stands for, as a base:
    /// every binding of it names a class, or a subscript of one (a name bound once per branch
    /// takes the union). Sorted; None when one binding is no class, on a cycle, or past the alias
    /// depth.
    fn bound_classes(
        &mut self,
        module: Sid,
        name: Sid,
        seen: &mut Vec<(Sid, Sid)>,
    ) -> Option<Vec<String>> {
        if seen.contains(&(module, name)) || seen.len() >= MAX_ALIAS_DEPTH {
            return None;
        }
        self.core.rec.read(Kind::Alias, module, name);
        let table = self.core.table(module)?;
        let table = self.core.table_at(table);
        let bindings = table.bindings.get(&name).cloned().unwrap_or_default();
        let record = table.record;
        if bindings.is_empty() {
            return None;
        }
        let facts = self.core.facts.clone();
        seen.push((module, name));
        let mut found: Vec<String> = Vec::new();
        let mut known = true;
        for (_, binding, assign) in bindings {
            let value = record.zip(assign).and_then(|(file, at)| {
                facts.files[file as usize].assigns[at as usize]
                    .value
                    .as_ref()
            });
            match self.binding_classes(module, name, binding, value, seen) {
                Some(classes) => found.extend(classes),
                None => {
                    known = false;
                    break;
                }
            }
        }
        seen.pop();
        if !known {
            return None;
        }
        found.sort();
        found.dedup();
        Some(found)
    }

    /// The analyzed file's scope and def index of the def or class (`kind`) a module table binds
    /// a name to.
    fn def_of(&mut self, module: Sid, name: Sid, kind: HitKind) -> Option<(usize, u32)> {
        let read = if kind == HitKind::Class {
            Kind::Class
        } else {
            Kind::Def
        };
        self.core.rec.read(read, module, name);
        let (file, idx) = self.core.located_def(module, name, kind)?;
        Some((*self.scope_of_file.get(&file)? as usize, idx))
    }

    /// A class and its ancestors in MRO order, through the link's own class resolution, each as
    /// its file's scope and def index. An ancestor whose file is not analyzed is left out.
    fn lineage(&mut self, owner: usize, idx: u32) -> Vec<(usize, u32)> {
        let facts = self.core.facts.clone();
        let node = &facts.files[self.scopes[owner].file as usize].defs[idx as usize];
        let module = self.scopes[owner].module;
        let own = Ent {
            m: module,
            s: self.scopes[owner].qualnames[idx as usize],
        };
        let bases = self.core.resolved_bases(module, node);
        let mut out = vec![(owner, idx)];
        for class in self.core.ancestors(&bases) {
            if class == own {
                continue;
            }
            if let Some(found) = self.def_of(class.m, class.s, HitKind::Class) {
                out.push(found);
            }
        }
        out
    }

    /// Whether a decorator is one `dataclass_decorators` names, by spelling or target.
    fn is_dataclass_decorator(&mut self, surface: Sid, resolved: Resolved) -> bool {
        let target = resolved.target.map(|target| self.core.ent_sid(target));
        named_in(&self.settings.dataclass_decorators, surface, target)
    }

    /// Whether a class or one of its ancestors names a
    /// base of `dataclass_bases`, by spelling or target. Worked out once per class; a file that
    /// asks again reads what the answer read.
    fn has_dataclass_base(&mut self, owner: usize, idx: u32) -> bool {
        if self.settings.dataclass_bases.is_empty() {
            return false;
        }
        let memo = Memo::DataclassBase(owner as u32, idx);
        if let Some(&found) = self.dataclass_bases.get(&(owner, idx)) {
            self.core.rec.replay(memo);
            return found;
        }
        let start = self.core.rec.mark();
        let found = self.find_dataclass_base(owner, idx);
        self.dataclass_bases.insert((owner, idx), found);
        self.core.rec.remember(memo, start);
        found
    }

    fn find_dataclass_base(&mut self, owner: usize, idx: u32) -> bool {
        let facts = self.core.facts.clone();
        for (at_owner, at) in self.lineage(owner, idx) {
            let node = &facts.files[self.scopes[at_owner].file as usize].defs[at as usize];
            for base in &node.bases {
                let (surface, resolved) = self.lookup_value(at_owner, base, node.s, node.l);
                let target = resolved.target.map(|target| self.core.ent_sid(target));
                if named_in(&self.settings.dataclass_bases, surface, target) {
                    return true;
                }
            }
        }
        false
    }

    /// Whether a class takes a decorator that makes it a dataclass, or has a dataclass base.
    fn is_dataclass_like(&mut self, owner: usize, idx: u32) -> bool {
        let facts = self.core.facts.clone();
        let node = &facts.files[self.scopes[owner].file as usize].defs[idx as usize];
        for dec in &node.dec {
            let (surface, resolved) = self.lookup_value(owner, dec, node.s, node.l);
            if self.is_dataclass_decorator(surface, resolved) {
                return true;
            }
        }
        self.has_dataclass_base(owner, idx)
    }

    /// The fields of the generated `__init__`, as the dataclass machinery collects them: from
    /// every dataclass-like class along the MRO, base first, a redefinition replacing the earlier
    /// field in its place. A field's names are looked up in its class body first.
    fn mro_fields(&mut self, lineage: &[(usize, u32)]) -> Vec<FieldRow> {
        let facts = self.core.facts.clone();
        let mut fields: OrderedMap<FieldRow> = OrderedMap::default();
        for position in (0..lineage.len()).rev() {
            let (owner, idx) = lineage[position];
            let file = self.scopes[owner].file;
            let record = &facts.files[file as usize];
            let node = &record.defs[idx as usize];
            if position > 0 && !self.is_dataclass_like(owner, idx) {
                continue;
            }
            let at_lookup = SlotLookup {
                owner,
                scope: idx as i32,
                line: node.l,
            };
            for at in self.class_fields(file, idx) {
                let row = &record.assigns[at as usize];
                if row.ann.as_ref().is_some_and(|ann| self.is_class_var(ann)) {
                    continue;
                }
                let V::Chain(name) = &row.targets[0] else {
                    continue;
                };
                let field = FieldRow {
                    file,
                    at,
                    lookup: at_lookup,
                    own: position == 0,
                };
                fields.insert(*name, field);
            }
        }
        fields.iter().map(|(_, field)| field).collect()
    }

    /// One unresolved row per base along the MRO that does not resolve or is a call: the fields
    /// it adds to the generated `__init__` are unknown.
    fn expose_unresolved_bases(&mut self, rows: SlotRows, lineage: &[(usize, u32)]) {
        let facts = self.core.facts.clone();
        for &(owner, idx) in lineage {
            let node = &facts.files[self.scopes[owner].file as usize].defs[idx as usize];
            for base in &node.bases {
                let (surface, resolved) = self.lookup_value(owner, base, node.s, node.l);
                if resolved.res != Res::Unresolved && !called_base(base) {
                    continue;
                }
                self.push_unresolved(rows, surface, self.settings.k.fields, None);
            }
        }
    }

    /// The `__init__` slots of a contract dataclass (its def index in the file `lookup` names): a
    /// row per unresolved base along its MRO, then its fields as the dataclass machinery collects
    /// them.
    fn expose_dataclass(&mut self, rows: SlotRows, lookup: SlotLookup, idx: u32) {
        let lineage = self.lineage(lookup.owner, idx);
        self.expose_unresolved_bases(rows, &lineage);
        let fields = self.mro_fields(&lineage);
        self.expose_fields(rows, &fields, true, Member::Field);
    }

    fn lookup_value(&mut self, owner: usize, value: &V, at: i32, line: u32) -> (Sid, Resolved) {
        let core = &mut *self.core;
        match core.value_lookup(
            &self.settings.names,
            &mut self.scopes[owner],
            value,
            at,
            line,
        ) {
            Some(found) => (found.surface, core.classify_lookup(found)),
            None => (EMPTY, UNRESOLVED),
        }
    }

    /// (export, root) of every contract surface that covers a file, read once per file; none for
    /// a test file, which hands nothing out. A file is a contract surface when one covers it, and
    /// two files that share one belong to the same export of the same captured root.
    fn contract_roots(&mut self, owner: usize) -> &[(usize, String)] {
        if self.settings.contract_surfaces.is_empty() {
            return &[];
        }
        if !self.roots.contains_key(&owner) {
            let scope = &self.scopes[owner];
            let found = if scope.base_ctx & ctx::TEST != 0 {
                Vec::new()
            } else {
                let path = &self.core.facts.files[scope.file as usize].path;
                self.settings.surface_roots(path)
            };
            self.roots.insert(owner, found);
        }
        &self.roots[&owner]
    }

    /// The import-bound names a module's own code refers to: every `refs` and `uses` row, in any
    /// scope, and the type names of its annotations, which also reads string annotations.
    fn used_names(&mut self, owner: usize) -> FxHashSet<Sid> {
        let facts = self.core.facts.clone();
        let record = &facts.files[self.scopes[owner].file as usize];
        let mut names: FxHashSet<Sid> = record.refs.iter().map(|row| row.name).collect();
        names.extend(record.uses.iter().map(|row| row.root));
        let mut annotations: Vec<&V> = record
            .assigns
            .iter()
            .filter_map(|row| row.ann.as_ref())
            .collect();
        for node in &record.defs {
            annotations.extend(
                node.params
                    .iter()
                    .filter_map(|param| param.annotation.as_ref()),
            );
            annotations.extend(node.ret.as_ref());
        }
        let mut mentioned = Vec::new();
        for annotation in annotations {
            self.mentions(annotation, true, EMPTY, &mut mentioned);
        }
        for found in mentioned {
            names.insert(self.core.it.head(found.name));
        }
        names
    }

    /// Whether a def is a public callable of the module's contract: a top-level function or
    /// class with no leading `_` or one `__all__` lists (`listed`), or a method directly in such a
    /// class with no leading `_` (`__init__` kept).
    fn in_contract(&self, defs: &[Def], idx: usize, listed: &FxHashSet<Sid>) -> bool {
        let node = &defs[idx];
        let name = self.core.it.s(node.n);
        if node.s == -1 {
            return node.f == 0 && (!name.starts_with('_') || listed.contains(&node.n));
        }
        let parent = node.s as usize;
        let owner = &defs[parent];
        if node.is_class() || !owner.is_class() || !self.in_contract(defs, parent, listed) {
            return false;
        }
        if owner.s != -1 || node.f != CLASS {
            return false;
        }
        !name.starts_with('_') || node.n == self.settings.k.init
    }

    /// Where a handed-out name leads: the top-level function or class in an analyzed first-party
    /// file, every step of the way inside first-party code, or `Unfollowed`. None for a name
    /// that needs no row: a third-party name, a constant, a module.
    fn handed_def(&mut self, target: Sid) -> Option<Followed> {
        let core = &mut *self.core;
        let head = core.it.head(target);
        core.rec.read(Kind::Head, head, EMPTY);
        if !self.first_party.contains(&head) {
            return None;
        }
        let (hit, rest) = core.resolve_dotted(target);
        let Some(hit) = hit.filter(|&hit| rest.is_empty() || core.kind(hit) == HitKind::Class)
        else {
            return Some(Followed::Unfollowed);
        };
        let kind = core.kind(hit);
        if matches!(kind, HitKind::Variable | HitKind::Module) {
            return None;
        }
        if !matches!(kind, HitKind::Def | HitKind::Class) {
            return Some(Followed::Unfollowed);
        }
        if !self.first_party_chain(hit) {
            return Some(Followed::Unfollowed);
        }
        let origin = self.core.origin(hit);
        let Some((owner, mut idx)) = self.def_of(origin.m, origin.s, kind) else {
            return Some(Followed::Unfollowed);
        };
        // A class an assignment hands out can sit in another class's body.
        let facts = self.core.facts.clone();
        let defs = &facts.files[self.scopes[owner].file as usize].defs;
        for attr in rest {
            let nested = defs
                .iter()
                .enumerate()
                .filter(|(_, node)| node.s == idx as i32 && node.n == attr)
                .map(|(at, _)| at)
                .next_back();
            let Some(nested) = nested.filter(|&at| defs[at].is_class()) else {
                return Some(Followed::Unfollowed);
            };
            idx = nested as u32;
        }
        Some(Followed::Def(owner, idx))
    }

    /// The slots of the functions and dataclass fields a contract surface hands out from other
    /// modules, at the import that hands each one out. An `__all__` it cannot read is one
    /// unresolved row: what the module hands out is unknown. So is a handed-out name the engine
    /// cannot follow to a def. A def in a file of the same export and root (the same product's
    /// facade) gets no rows here: that file reports it.
    fn hand_out(&mut self, owner: usize) {
        let facts = self.core.facts.clone();
        let handed = HandedOut::new(self.core, &self.scopes[owner]);
        if let Some((line, col)) = handed.dunder_all.unread.filter(|_| !handed.no_functions) {
            let rows = SlotRows {
                site: self.module_site(owner, line, col),
                callable: self.settings.k.dunder_all,
                handed: self.core.it.intern(ALL_CLAUSE),
            };
            let k = &self.settings.k;
            self.push_unresolved(rows, k.dunder_all, k.names, None);
        }
        let roots = self.contract_roots(owner).to_vec();
        let used = if handed.no_functions {
            self.used_names(owner)
        } else {
            FxHashSet::default()
        };
        for item in handed.names(self.core, &self.scopes[owner], &used) {
            let Some(found) = self.handed_def(item.target) else {
                continue;
            };
            let clause = self.core.it.intern(item.clause);
            let rows = SlotRows {
                site: self.module_site(owner, item.line, item.col),
                callable: item.bound,
                handed: clause,
            };
            let Followed::Def(origin, idx) = found else {
                self.push_unresolved(rows, item.target, self.settings.k.signature, None);
                continue;
            };
            if self
                .contract_roots(origin)
                .iter()
                .any(|root| roots.contains(root))
            {
                continue;
            }
            let record = &facts.files[self.scopes[origin].file as usize];
            let node = &record.defs[idx as usize];
            let lookup = SlotLookup {
                owner: origin,
                scope: node.s,
                line: node.l,
            };
            if !node.is_class() {
                self.expose_signature(rows, lookup, node, true);
                continue;
            }
            if !self.is_dataclass_like(origin, idx) {
                continue;
            }
            let init = SlotRows {
                callable: self.core.it.join(item.bound, self.settings.k.init),
                ..rows
            };
            self.expose_dataclass(init, lookup, idx);
        }
    }

    fn declarations(&mut self, owner: usize) {
        let facts = self.core.facts.clone();
        let file = self.scopes[owner].file as usize;
        let record = &facts.files[file];
        let contract = !self.contract_roots(owner).is_empty();
        // The names a readable `__all__` of a contract module lists.
        let listed = if contract {
            HandedOut::new(self.core, &self.scopes[owner]).listed()
        } else {
            FxHashSet::default()
        };
        for (idx, node) in record.defs.iter().enumerate() {
            let k = &self.settings.k;
            let module = self.scopes[owner].module;
            let qualname = self.scopes[owner].qualnames[idx];
            let enclosing = node.s;
            let ctx = self
                .core
                .contexts(&mut self.scopes[owner], node.f, enclosing);
            let nested = self.scopes[owner].in_function[idx];
            let full = self.core.it.join(module, qualname);
            self.symbols.set(full, node.kind as u8, file as u32);
            if !nested {
                let resolved = Resolved {
                    res: Res::Resolved,
                    target: Some(Ent {
                        m: module,
                        s: qualname,
                    }),
                    via: None,
                };
                let decl = self.core.it.intern(node.kind.name());
                let body = if node.stub { k.zero } else { k.one };
                let attrs = [(k.decl, decl), (k.body, body)];
                self.push(
                    DECLARES, owner, enclosing, node.l, node.c, ctx, full, resolved, EMPTY, &attrs,
                );
            }
            let mut dataclass = false;
            for dec in &node.dec {
                let (surface, resolved) = self.lookup_value(owner, dec, enclosing, node.l);
                let k = &self.settings.k;
                let called = if matches!(dec, V::Call(..)) {
                    k.one
                } else {
                    k.zero
                };
                let mut attrs = vec![(k.decorated, qualname), (k.called, called)];
                if matches!(dec, V::Call(_, true, _)) {
                    attrs.push((k.frozen, k.one));
                }
                let use_ = k.use_decorator;
                self.push(
                    DECORATE, owner, enclosing, node.l, node.c, ctx, surface, resolved, use_,
                    &attrs,
                );
                dataclass |= self.is_dataclass_decorator(surface, resolved);
            }
            if node.is_class() {
                for (position, base) in node.bases.iter().enumerate() {
                    let (surface, resolved) = self.lookup_value(owner, base, enclosing, node.l);
                    let k = &self.settings.k;
                    let index = self.core.it.intern(&position.to_string());
                    let mut attrs = vec![(k.class, qualname), (k.index, index)];
                    let called = called_base(base);
                    if called {
                        attrs.push((k.called, k.one));
                    }
                    let followed = resolved
                        .via
                        .filter(|&hit| self.followed_base(hit, resolved));
                    if let Some(hit) = followed
                        && !called
                    {
                        let classes = self.hit_classes(surface, Some(hit), &mut Vec::new());
                        let value = match classes {
                            Some(found) => self.core.it.intern(&found.join(",")),
                            None => self.settings.k.unknown_base,
                        };
                        attrs.push((self.settings.k.classes, value));
                    }
                    let use_ = k.use_base;
                    self.push(
                        INHERIT, owner, enclosing, node.l, node.c, ctx, surface, resolved, use_,
                        &attrs,
                    );
                }
            }
            let in_contract = contract && self.in_contract(&record.defs, idx, &listed);
            // Only a class whose fields can be slots asks for its dataclass base: one that declares
            // a field, or one in a contract, which also exposes the fields it inherits.
            if node.is_class()
                && !dataclass
                && !nested
                && !self.settings.dataclass_bases.is_empty()
                && (self.declares_fields(file as u32, idx as u32) || in_contract)
            {
                dataclass = self.has_dataclass_base(owner, idx as u32);
            }
            if nested {
                continue;
            }
            let site = SlotSite {
                owner,
                scope: enclosing,
                line: node.l,
                col: node.c,
                ctx,
            };
            let lookup = SlotLookup {
                owner,
                scope: enclosing,
                line: node.l,
            };
            if !node.is_class() {
                let rows = SlotRows {
                    site,
                    callable: qualname,
                    handed: EMPTY,
                };
                self.expose_signature(rows, lookup, node, in_contract);
                continue;
            }
            // A class-body annotation sees the names of the class body first, then the module.
            let body = SlotLookup {
                owner,
                scope: idx as i32,
                line: node.l,
            };
            let own: Vec<FieldRow> = self
                .class_fields(file as u32, idx as u32)
                .into_iter()
                .map(|at| FieldRow {
                    file: file as u32,
                    at,
                    lookup: body,
                    own: true,
                })
                .collect();
            if !dataclass {
                let rows = SlotRows {
                    site,
                    callable: qualname,
                    handed: EMPTY,
                };
                self.expose_fields(rows, &own, in_contract, Member::Attribute);
                continue;
            }
            // A `ClassVar` is no field of the generated `__init__`: callers read it as an attribute.
            let (class_vars, fields): (Vec<FieldRow>, Vec<FieldRow>) = own
                .into_iter()
                .partition(|field| self.is_class_var_row(*field));
            let rows = SlotRows {
                site,
                callable: qualname,
                handed: EMPTY,
            };
            self.expose_fields(rows, &class_vars, in_contract, Member::Attribute);
            let rows = SlotRows {
                site,
                callable: self.core.it.join(qualname, self.settings.k.init),
                handed: EMPTY,
            };
            if !in_contract {
                self.expose_fields(rows, &fields, false, Member::Field);
                continue;
            }
            self.expose_dataclass(rows, lookup, idx as u32);
        }
        if contract {
            self.hand_out(owner);
        }
        self.record_hand_out(owner);
    }

    /// A `rebind` edge per name the module hands out by assigning it again, read as `from module
    /// import name`: what the module hands out is always an edge that binds the name. It is a
    /// kind of its own, so no rule that reads imports sees a second edge for one name.
    fn rebind(&mut self, owner: usize, handed: &HandedOut) {
        let facts = self.core.facts.clone();
        let record = &facts.files[self.scopes[owner].file as usize];
        let rows = module_assigns(&self.core.it, record);
        let module = self.scopes[owner].module;
        for (item, hit) in handed.assigned(self.core, &self.scopes[owner]) {
            let surface = self.core.it.join(module, item.bound);
            let resolved = self.core.classify(surface, Some(hit));
            let flags = record.assigns[rows[&item.bound] as usize].f;
            let ctx = self.core.contexts(&mut self.scopes[owner], flags, -1);
            let attrs = [(self.settings.k.binds, item.bound)];
            self.push(
                REBIND, owner, -1, item.line, item.col, ctx, surface, resolved, EMPTY, &attrs,
            );
        }
    }

    /// What an export module hands out by import or by assigning a name again, for every rule
    /// that asks what it exports. A test file hands nothing out.
    fn record_hand_out(&mut self, owner: usize) {
        if !self.is_export_module(owner) {
            return;
        }
        let handed = HandedOut::new(self.core, &self.scopes[owner]);
        self.rebind(owner, &handed);
        let used = if handed.no_functions {
            self.used_names(owner)
        } else {
            FxHashSet::default()
        };
        let mut found = handed.hand_out(self.core, &self.scopes[owner], &used);
        self.expose_aliases(owner, &handed, &used, &mut found);
        self.hand_outs.insert(self.scopes[owner].module, found);
    }

    /// The line and column of a module-level value the producer expands as a type alias:
    /// a subscript, a `|` union, the bare name of a builtin type, or a call that declares a type
    /// (`declared_type`). Another name or attribute chain is left out, so a member past a class
    /// stays unknown.
    fn type_alias(&mut self, module: Sid, name: Sid) -> Option<(u32, u32)> {
        let (file, at) = self.alias(module, name)?;
        let owner = *self.scope_of_file.get(&file)? as usize;
        let facts = self.core.facts.clone();
        let row = &facts.files[file as usize].assigns[at as usize];
        match row.value.as_ref()? {
            V::Sub(..) | V::Bin(true, ..) => Some((row.l, row.c)),
            V::Call(..) => self.declared_type(file, at).map(|_| (row.l, row.c)),
            V::Chain(chain) if !self.core.it.s(*chain).contains('.') => {
                let lookup = SlotLookup {
                    owner,
                    scope: -1,
                    line: row.l,
                };
                let found = self.lookup_at(lookup, *chain);
                self.plain_builtin(found, *chain).then_some((row.l, row.c))
            }
            _ => None,
        }
    }

    /// The annotation of a module constant (`annotated_constant`) and where its names are looked
    /// up: the module that assigns it, at its line.
    fn constant_annotation(&mut self, module: Sid, name: Sid) -> Option<(V, SlotLookup)> {
        let (file, at) = annotated_constant(self.core, module, name)?;
        let facts = self.core.facts.clone();
        let row = &facts.files[file as usize].assigns[at as usize];
        let lookup = SlotLookup {
            owner: *self.scope_of_file.get(&file)? as usize,
            scope: -1,
            line: row.l,
        };
        Some((row.ann.clone()?, lookup))
    }

    /// The rows of a handed-out or listed name whose value the producer reads as a type, at the
    /// line that hands the name out: a type alias's expansion as a slot `alias`, else a module
    /// constant's annotation as a slot `constant`. `origin` is where the value is assigned. A
    /// lazy name, which the module binds by no statement, is looked up there. False when the
    /// value is neither, so the name stays unknown.
    fn expose_value(&mut self, owner: usize, item: &Handed, origin: Ent) -> bool {
        let rows = SlotRows {
            site: self.module_site(owner, item.line, item.col),
            callable: item.bound,
            handed: self.core.it.intern(item.clause),
        };
        if let Some((line, _)) = self.type_alias(origin.m, origin.s) {
            let (name, lookup) = if item.clause == LAZY_CLAUSE {
                let Some(lookup) = self.assigned_lookup(origin, line) else {
                    return false;
                };
                (origin.s, lookup)
            } else {
                let lookup = SlotLookup {
                    owner,
                    scope: -1,
                    line: item.line,
                };
                (item.bound, lookup)
            };
            let slot = self.settings.k.alias;
            self.expose(rows, lookup, Some(&V::Chain(name)), slot, true);
            return true;
        }
        let Some((annotation, lookup)) = self.constant_annotation(origin.m, origin.s) else {
            return false;
        };
        let slot = self.settings.k.constant;
        self.expose(rows, lookup, Some(&annotation), slot, true);
        true
    }

    /// Writes the rows of every listed or handed-out value the producer reads as a type
    /// (`expose_value`), and takes those values out of what the hand-out record leaves unknown.
    fn expose_aliases(
        &mut self,
        owner: usize,
        handed: &HandedOut,
        used: &FxHashSet<Sid>,
        found: &mut HandOut,
    ) {
        let module = self.scopes[owner].module;
        let mut names: Vec<Sid> = found.unknown.iter().copied().collect();
        names.sort_by(|a, b| self.core.it.s(*a).cmp(self.core.it.s(*b)));
        for name in names {
            let assigned = self
                .type_alias(module, name)
                .or_else(|| self.annotated_at(module, name));
            let Some((line, col)) = assigned else {
                continue;
            };
            let item = Handed {
                bound: name,
                target: self.core.it.join(module, name),
                clause: ALL_CLAUSE,
                line,
                col,
            };
            let origin = Ent { m: module, s: name };
            if self.expose_value(owner, &item, origin) {
                found.unknown.remove(&name);
            }
        }
        let values = found.unknown_values.clone();
        for item in handed.names(self.core, &self.scopes[owner], used) {
            let (hit, rest) = self.core.resolve_dotted(item.target);
            let Some(hit) = hit.filter(|_| rest.is_empty()) else {
                continue;
            };
            let origin = self.core.origin(hit);
            let symbol = self.core.ent_sid(origin);
            if values.contains(&symbol) && self.expose_value(owner, &item, origin) {
                found.unknown_values.remove(&symbol);
            }
        }
    }

    /// The module scope of the file that assigns a module-level type alias, at `line`.
    fn assigned_lookup(&mut self, origin: Ent, line: u32) -> Option<SlotLookup> {
        let (file, _) = self.alias(origin.m, origin.s)?;
        let owner = *self.scope_of_file.get(&file)? as usize;
        Some(SlotLookup {
            owner,
            scope: -1,
            line,
        })
    }

    /// The line and column of a module constant's annotated assignment (`annotated_constant`).
    fn annotated_at(&mut self, module: Sid, name: Sid) -> Option<(u32, u32)> {
        let (file, at) = annotated_constant(self.core, module, name)?;
        let row = &self.core.facts.files[file as usize].assigns[at as usize];
        Some((row.l, row.c))
    }

    /// The module's lazy (PEP 562) entries: each name it loads on first read, and the module it
    /// loads it from.
    fn lazy_entries(&mut self, owner: usize) -> Vec<(String, String)> {
        let (file, module) = (self.scopes[owner].file, self.scopes[owner].module);
        let Some(table) = self.core.table(module) else {
            return Vec::new();
        };
        let table = self.core.table_at(table);
        if table.record != Some(file) {
            return Vec::new();
        }
        let entries: Vec<(Sid, Sid)> = table
            .lazy_entries
            .iter()
            .map(|entry| (entry.name, entry.module))
            .collect();
        let it = &self.core.it;
        entries
            .into_iter()
            .map(|(name, module)| (it.s(name).to_string(), it.s(module).to_string()))
            .collect()
    }

    /// Whether a call takes a path as its first argument: a loader, a path callee or a patch callee.
    /// Whether a call row's callee is a loader under a name its last name does not tell (an import
    /// alias the extractor watches: `from importlib import import_module as load`).
    fn aliased_loader(&mut self, owner: usize, row: &CallRow) -> bool {
        if names_callee(&row.callee, &self.settings.loader_last_names)
            || row.callee.starts_with('?')
        {
            return false;
        }
        let core = &mut *self.core;
        let callee = core.it.intern(&row.callee);
        let names = &self.settings.names;
        let scope = &mut self.scopes[owner];
        let (found, rest) = core.chain_lookup(names, scope, callee, row.scope, row.line);
        let loader = core.it.join_all(found.surface, &rest);
        let origin = match found.hit {
            Some(hit) if rest.is_empty() => {
                let origin = core.origin(hit);
                core.ent_sid(origin)
            }
            _ => EMPTY,
        };
        self.settings.loaders.contains(&loader) || self.settings.loaders.contains(&origin)
    }

    fn takes_path(&self, callee: &str) -> bool {
        let settings = self.settings;
        let takes = names_callee(callee, &settings.path_callees)
            || names_callee(callee, &settings.loader_last_names)
            || names_callee(callee, &settings.patch_callees);
        takes && !callee.starts_with('?')
    }

    /// The literal first arguments of the file's calls that take a path. A loop over a literal
    /// sequence gives such a call one row per value, so the sequence's strings are what it takes.
    fn taken_literals(&mut self, owner: usize) -> FxHashSet<String> {
        let file = self.scopes[owner].file as usize;
        let facts = self.core.facts.clone();
        let mut taken = FxHashSet::default();
        for row in &facts.files[file].calls {
            if let Argument::Literal(text) = &row.argument
                && (self.takes_path(&row.callee) || self.aliased_loader(owner, row))
            {
                taken.insert(text.to_string());
            }
        }
        taken
    }

    /// Whether the file hands a computed path (an f-string, a `+`, or a name it does not bind to a
    /// class, a function or an import) to a loader, a path callee or a patch callee: then every
    /// string it holds that names a module may be what it loads or patches. The facts hold no flow
    /// from a name to where it is used.
    fn hands_computed_path(&mut self, owner: usize) -> bool {
        let (file, module) = (self.scopes[owner].file as usize, self.scopes[owner].module);
        let facts = self.core.facts.clone();
        let table = self.core.table(module);
        for row in &facts.files[file].calls {
            if !self.takes_path(&row.callee) && !self.aliased_loader(owner, row) {
                continue;
            }
            let computed = match &row.argument {
                Argument::Opaque(what) => matches!(&**what, "JoinedStr" | "BinOp"),
                Argument::Name(chain) => !self.binds_object(table, chain),
                _ => false,
            };
            if computed {
                return true;
            }
        }
        false
    }

    /// Whether the module binds a chain's head to a class, a function or an import: an object,
    /// not a path (`monkeypatch.setattr(module, "name", value)`).
    fn binds_object(&self, table: Option<u32>, chain: &str) -> bool {
        let Some(table) = table else {
            return false;
        };
        let head = chain.split('.').next().unwrap_or("");
        let Some(head) = self.core.it.get(head) else {
            return false;
        };
        let names = &self.core.table_at(table).names;
        names
            .get(&head)
            .is_some_and(|binding| !matches!(binding, Binding::Variable | Binding::Alias(_)))
    }

    /// A string is a dotted-path edge only where the code holds a path. In a sink (a patch target,
    /// the first argument of a path callee) it is one whether or not it names a module. In a path
    /// place (a loader's first argument, an element of a path callee's argument vector, a path
    /// module such as a settings module, a lazy map's value, a string whose text such a call takes
    /// through a loop, a string of a module-level list such a call takes an element of, any string
    /// of a file that hands a computed path to one of those callees) it is one when it names a
    /// module. Anywhere else a string is text, whatever it names.
    fn strings(&mut self, owner: usize) {
        let facts = self.core.facts.clone();
        let file = self.scopes[owner].file as usize;
        let module = self.core.it.s(self.scopes[owner].module);
        let path_module =
            (self.settings.path_modules.iter()).any(|pattern| glob_matches(pattern, module));
        let lazy = self.lazy_entries(owner);
        let computed = self.hands_computed_path(owner);
        let taken = self.taken_literals(owner);
        let element_lists = self.element_lists(owner);
        for row in &facts.files[file].strs {
            let patch = patch_role(&row.up, &self.settings.patch_callees);
            let argument = path_argument(&row.up, self.settings);
            let sink = patch || argument == PathArgument::Sink;
            let lazy_value = row.scope == -1
                && (row.value.as_deref()).is_some_and(|text| in_lazy_map(&row.up, text, &lazy));
            let taken_value = (row.value.as_deref()).is_some_and(|text| taken.contains(text));
            let listed_value = row.scope == -1 && in_element_list(&row.up, &element_lists);
            let place = argument == PathArgument::Place
                || path_module
                || lazy_value
                || taken_value
                || listed_value
                || computed;
            if !sink && !place {
                continue;
            }
            let k = &self.settings.k;
            let mut candidates: Vec<(&str, Sid)> = Vec::new();
            match &row.parts {
                None => {
                    if let Some(value) = &row.value {
                        candidates.push((value, k.whole));
                    }
                }
                Some(parts) => {
                    for part in parts.iter().flatten() {
                        if part.contains('.') {
                            candidates.push((part, k.part));
                        }
                    }
                    if let Some(value) = &row.value {
                        candidates.push((value, k.joined));
                    }
                }
            }
            let role = if patch {
                k.patch_target
            } else {
                k.literal_role
            };
            for (text, literal) in candidates {
                let core = &mut *self.core;
                let dotted = core.it.intern(text);
                let Some((resolved, rest)) = core.string_dotted(dotted) else {
                    continue;
                };
                if resolved.res == Res::Unresolved && !sink {
                    continue;
                }
                let k = &self.settings.k;
                let up = core.it.intern(&row.up);
                let mut attrs = vec![(k.literal, literal), (k.role, role), (k.up, up)];
                let member = core.it.from_segs(&rest);
                if member != EMPTY {
                    attrs.push((k.member, member));
                }
                if resolved.target.is_some()
                    && resolved.res != Res::Unresolved
                    && let Some((module, _)) = core.longest_module(dotted)
                {
                    attrs.push((k.module, module));
                }
                let ctx = core.contexts(&mut self.scopes[owner], row.flags, row.scope);
                self.push(
                    DOTTED, owner, row.scope, row.line, row.col, ctx, dotted, resolved, EMPTY,
                    &attrs,
                );
            }
        }
    }

    fn dynamic_imports(&mut self, owner: usize) {
        let facts = self.core.facts.clone();
        let file = self.scopes[owner].file as usize;
        let loads: FxHashMap<(u32, u32), &ComputedRow> = facts.files[file]
            .computed
            .iter()
            .filter(|load| load.form == LoadForm::Call)
            .map(|load| ((load.line, load.col), load))
            .collect();
        let mut noted = FxHashSet::default();
        let mut sites = FxHashSet::default();
        for row in &facts.files[file].calls {
            let named = names_callee(&row.callee, &self.settings.loader_last_names);
            if row.callee.starts_with('?') || !(named || self.aliased_loader(owner, row)) {
                continue;
            }
            let core = &mut *self.core;
            let callee = core.it.intern(&row.callee);
            let (found, rest) = core.chain_lookup(
                &self.settings.names,
                &mut self.scopes[owner],
                callee,
                row.scope,
                row.line,
            );
            let loader = core.it.join_all(found.surface, &rest);
            let origin = match found.hit {
                Some(hit) => {
                    let origin = core.origin(hit);
                    core.ent_sid(origin)
                }
                None => EMPTY,
            };
            let is_loader =
                self.settings.loaders.contains(&loader) || self.settings.loaders.contains(&origin);
            // A callee that resolves to nothing may still be a loader (a star import): the edges
            // read its strings by its last name, so the check reads it too.
            // A loader named on any other receiver (`pkgutil.importlib.import_module`) is read like
            // one: its argument must name its modules exactly.
            if !is_loader && found.hit.is_some() && !named {
                continue;
            }
            if is_loader {
                self.loader_edge(owner, row, loader);
                let callee = self.core.it.intern(&row.callee);
                sites.insert((row.scope, callee, row.line, row.col));
            }
            // A call the walk repeats per item of a list has a `computed` row: the list decides.
            // A relative name (`.x`) is read relative to `package=`, which the link does not read.
            let load = loads.get(&(row.line, row.col)).copied();
            let exact = match load {
                Some(load) => self.names_paths(owner, load),
                None => matches!(&row.argument, Argument::Literal(text) if !text.starts_with('.')),
            };
            if !exact && noted.insert((row.line, row.col)) {
                let words = format!("{}(...) at line {}", row.callee, row.line);
                self.note_computed_load(owner, words, load);
            }
        }
        for load in &facts.files[file].computed {
            let words = match load.form {
                LoadForm::Call | LoadForm::Reload => continue,
                LoadForm::Modules => format!("sys.modules at line {}", load.line),
                LoadForm::Use => {
                    let name = self.core.it.s(load.sequence);
                    format!("{name} at line {}", load.line)
                }
            };
            let sequence = (load.form == LoadForm::Modules).then_some(load);
            self.note_computed_load(owner, words, sequence);
        }
        self.loader_uses(owner, &sites);
    }

    /// The `dynamic-import` edge of a loader call.
    fn loader_edge(&mut self, owner: usize, row: &CallRow, loader: Sid) {
        let core = &mut *self.core;
        let k = &self.settings.k;
        let ctx = core.contexts(&mut self.scopes[owner], row.flags, row.scope);
        let mut attrs = vec![(k.loader, loader)];
        let (surface, resolved) = match &row.argument {
            Argument::Literal(text) => {
                attrs.push((k.argument, core.it.intern("literal")));
                let surface = core.it.intern(text);
                let resolved = if text.is_empty() || text.starts_with('.') {
                    UNRESOLVED
                } else {
                    match core.string_dotted(surface) {
                        None => Resolved {
                            res: Res::External,
                            target: Some(Ent {
                                m: surface,
                                s: EMPTY,
                            }),
                            via: None,
                        },
                        Some((resolved, _)) => resolved,
                    }
                };
                (surface, resolved)
            }
            other => {
                let text = match other {
                    Argument::Missing => "missing".to_string(),
                    Argument::Name(name) => format!("name:{name}"),
                    Argument::Opaque(what) => format!("expr:{what}"),
                    _ => "expr".to_string(),
                };
                attrs.push((k.argument, core.it.intern(&text)));
                // No literal name: hoglint does not follow the value, which `literal` says to
                // every reader of an unresolved edge.
                attrs.push((k.literal, core.it.intern("none")));
                (EMPTY, UNRESOLVED)
            }
        };
        let use_ = k.use_call;
        self.push(
            DYNAMIC, owner, row.scope, row.line, row.col, ctx, surface, resolved, use_, &attrs,
        );
    }

    /// A loader the code uses other than by a call the walk records (a call through a name whose
    /// last name does not tell, as an import alias; the loader handed on or bound to a name), and
    /// the standard library's other ways to load a module by name (`UNREAD_LOADERS`): the link
    /// cannot read what such a use loads, so each is a computed load of its module. A `uses` row
    /// counts a name's uses in a scope; the walk records a `calls` row per call (`sites`, by
    /// scope, callee, line and column).
    fn loader_uses(&mut self, owner: usize, sites: &FxHashSet<(i32, Sid, u32, u32)>) {
        let facts = self.core.facts.clone();
        let file = self.scopes[owner].file as usize;
        let mut uses: FxHashMap<(i32, Sid), (u32, u32)> = FxHashMap::default();
        for row in &facts.files[file].uses {
            let chain = match self.core.it.s(row.attrs) {
                "" => row.root,
                attrs => {
                    let text = format!("{}{attrs}", self.core.it.s(row.root));
                    self.core.it.intern(&text)
                }
            };
            let entry = uses.entry((row.scope, chain)).or_insert((0, row.line));
            entry.0 += row.count;
            entry.1 = entry.1.min(row.line);
        }
        let mut keys: Vec<(i32, Sid)> = uses.keys().copied().collect();
        keys.sort_unstable();
        for (scope, chain) in keys {
            let (count, line) = uses[&(scope, chain)];
            let core = &mut *self.core;
            let names = &self.settings.names;
            let (found, rest) =
                core.chain_lookup(names, &mut self.scopes[owner], chain, scope, line);
            let name = core.it.join_all(found.surface, &rest);
            let origin = match found.hit {
                Some(hit) if rest.is_empty() => {
                    let origin = core.origin(hit);
                    core.ent_sid(origin)
                }
                _ => EMPTY,
            };
            let named = |names: &[&str]| {
                names
                    .iter()
                    .any(|loader| [name, origin].iter().any(|sid| core.it.s(*sid) == *loader))
            };
            let loaders = &self.settings.loaders;
            let loader = loaders.contains(&name) || loaders.contains(&origin);
            let unread = named(&UNREAD_LOADERS);
            if !loader && !unread {
                continue;
            }
            let calls = sites
                .iter()
                .filter(|site| site.0 == scope && site.1 == chain)
                .count();
            if unread || count as usize > calls {
                let words = format!("{} at line {line}", self.core.it.s(chain));
                self.note_computed_load(owner, words, None);
            }
        }
    }

    /// Whether the value a `computed` row hands a loader is an element of a list of dotted paths
    /// the reading reads exactly (`path_lists`), bound at module level in the loader's own
    /// module: each of its strings is then a `dotted-path` edge (`strings`), which names the
    /// module the loader may load. A key of `sys.modules[...]` is never one: no edge names it.
    fn names_paths(&mut self, owner: usize, load: &ComputedRow) -> bool {
        if load.form != LoadForm::Call || load.sequence == EMPTY {
            return false;
        }
        let module = self.scopes[owner].module;
        let core = &mut *self.core;
        let scope = &mut self.scopes[owner];
        let names = &self.settings.names;
        let (found, rest) = core.chain_lookup(names, scope, load.sequence, load.scope, load.line);
        let Some(hit) = found.hit.filter(|_| rest.is_empty() && !found.local) else {
            return false;
        };
        let origin = core.origin(hit);
        origin.m == module && self.path_lists.contains(&(origin.m, origin.s))
    }

    /// The module-level lists of dotted paths (`path_lists`) of this module that a call of its
    /// file that takes a path takes an element of (`computed`): each of their strings is a path
    /// place.
    fn element_lists(&mut self, owner: usize) -> FxHashSet<String> {
        let (file, module) = (self.scopes[owner].file as usize, self.scopes[owner].module);
        let facts = self.core.facts.clone();
        let record = &facts.files[file];
        let mut takers: FxHashSet<(u32, u32)> = FxHashSet::default();
        for row in &record.calls {
            if self.takes_path(&row.callee) || self.aliased_loader(owner, row) {
                takers.insert((row.line, row.col));
            }
        }
        (record.computed.iter())
            .filter(|load| load.form == LoadForm::Call && takers.contains(&(load.line, load.col)))
            .filter(|load| self.path_lists.contains(&(module, load.sequence)))
            .map(|load| self.core.it.s(load.sequence).to_string())
            .collect()
    }

    /// A place the module names a module by a value the link cannot read exactly, in words
    /// (`computed_loads`): where, and the name the value is an element of.
    fn note_computed_load(&mut self, owner: usize, mut words: String, load: Option<&ComputedRow>) {
        if let Some(load) = load.filter(|load| load.sequence != EMPTY) {
            words = format!("{words}, over {}", self.core.it.s(load.sequence));
        }
        let module = self.scopes[owner].module;
        self.computed_loads.push((module, words));
    }

    fn unparsed(&mut self, owner: usize) {
        let facts = self.core.facts.clone();
        let file = self.scopes[owner].file as usize;
        let record = &facts.files[file];
        if record.ok() {
            return;
        }
        let k = &self.settings.k;
        let status = self.core.it.intern(&record.status);
        let surface = self.core.it.intern(&record.path);
        let ctx = self.scopes[owner].base_ctx;
        let attrs = [(k.status, status)];
        self.push(
            UNPARSED, owner, -1, 0, 0, ctx, surface, UNRESOLVED, EMPTY, &attrs,
        );
    }
}
