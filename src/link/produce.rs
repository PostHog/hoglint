//! The core producers (hoglint/core/producers.py): import, reference, declaration, dotted-path,
//! dynamic-import and unparsed edges, in the order and with the fields Python makes them.

use super::facts::{Argument, Assign, Def, Op, V};
use super::intern::{EMPTY, Interner, Sid};
use super::resolve::{
    Binding, CLASS, Core, Ent, HitId, HitKind, OrderedMap, absolute_import, module_assigns,
};
use super::rules::glob::ExportPaths;
use super::scope::{Lookup, LookupNames, Res, Resolved, Scope, UNRESOLVED, ctx};
use super::surface::{ALL_CLAUSE, HandedOut};
use rustc_hash::{FxHashMap, FxHashSet};

pub const KIND_NAMES: [&str; 9] = [
    "import",
    "reference",
    "declares",
    "decorate",
    "inherit",
    "exposes",
    "dotted-path",
    "dynamic-import",
    "unparsed",
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
    /// Refinements by edge id. Python checks that two producers never set one field apart.
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
    pub patch_callees: Vec<String>,
    /// The files of exports whose signatures are part of their contract.
    pub contract_surfaces: Vec<ExportPaths>,
    k: Keys,
}

/// Attr keys and fixed values.
struct Keys {
    binds: Sid,
    module: Sid,
    chain: Sid,
    up: Sid,
    header: Sid,
    decl: Sid,
    body: Sid,
    decorated: Sid,
    called: Sid,
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
    handed: Sid,
    ret: Sid,
    alias: Sid,
    too_deep: Sid,
    dunder_all: Sid,
    names: Sid,
    signature: Sid,
    staticmethod: Sid,
    fields: Sid,
    class_var: Sid,
}

impl Producing {
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
        builtins: &[String],
        loaders: &[String],
        dataclass_decorators: &[String],
        patch_callees: &[String],
        contract_surfaces: Vec<ExportPaths>,
    ) -> Producing {
        let mut last_names: Vec<String> = loaders
            .iter()
            .map(|loader| loader.rsplit('.').next().unwrap_or("").to_string())
            .collect();
        last_names.sort();
        last_names.dedup();
        let k = Keys {
            binds: it.intern("binds"),
            module: it.intern("module"),
            chain: it.intern("chain"),
            up: it.intern("up"),
            header: it.intern("header"),
            decl: it.intern("decl"),
            body: it.intern("body"),
            decorated: it.intern("decorated"),
            called: it.intern("called"),
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
            handed: it.intern("handed"),
            ret: it.intern("return"),
            alias: it.intern("alias"),
            too_deep: it.intern("too-deep"),
            dunder_all: it.intern("__all__"),
            names: it.intern("names"),
            signature: it.intern("signature"),
            staticmethod: it.intern("builtins.staticmethod"),
            fields: it.intern("fields"),
            class_var: it.intern("ClassVar"),
        };
        Producing {
            names: LookupNames {
                builtins: builtins.iter().map(|name| it.intern(name)).collect(),
                builtins_module: it.intern("builtins"),
            },
            module_scope: it.intern("<module>"),
            loaders: loaders.iter().map(|loader| it.intern(loader)).collect(),
            loader_last_names: last_names,
            dataclass_decorators: dataclass_decorators
                .iter()
                .map(|name| it.intern(name))
                .collect(),
            patch_callees: patch_callees.to_vec(),
            contract_surfaces,
            k,
        }
    }
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

/// Where an `exposes` edge is written (hoglint/core/producers.py `SlotSite`).
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
/// annotation, when the alias is), where it is looked up, and whether the expansion stopped at
/// it because the alias it names is too deep to follow.
#[derive(Clone, Copy, PartialEq, Eq, Hash)]
pub struct Expanded {
    value: Sid,
    top: bool,
    lookup: SlotLookup,
    too_deep: bool,
}

/// One alias's expansion while it is worked out: the aliases above the current one (`stack`),
/// the (alias, top) pairs followed, and the names written, each once, in order.
struct Expansion {
    stack: Vec<(Sid, Sid)>,
    followed: FxHashSet<((Sid, Sid), bool)>,
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

/// A field of a dataclass's generated `__init__`: its assign row (file and index), where its
/// names are looked up, and whether the class itself declares it (else a base does).
#[derive(Clone, Copy)]
struct FieldRow {
    file: u32,
    at: u32,
    lookup: SlotLookup,
    own: bool,
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
    /// The scope of each analyzed file, by file.
    pub owner_of: FxHashMap<u32, usize>,
    /// What each type alias expands to, by alias and the place its names are looked up.
    pub expansions: FxHashMap<((Sid, Sid), SlotLookup), Vec<Expanded>>,
    /// The candidate dataclass fields of each file, by file (`fields_by_class`).
    pub fields: FxHashMap<u32, FxHashMap<i32, Vec<u32>>>,
    /// The contract roots of each file, by its scope (`contract_roots`).
    pub roots: FxHashMap<usize, Vec<(usize, String)>>,
}

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

    pub fn run(&mut self) {
        for owner in 0..self.scopes.len() {
            self.imports(owner);
        }
        for owner in 0..self.scopes.len() {
            self.references(owner);
        }
        self.owner_of = self
            .scopes
            .iter()
            .enumerate()
            .map(|(owner, scope)| (scope.file, owner))
            .collect();
        for owner in 0..self.scopes.len() {
            self.declarations(owner);
        }
        for owner in 0..self.scopes.len() {
            self.strings(owner);
        }
        for owner in 0..self.scopes.len() {
            self.dynamic_imports(owner);
        }
        for owner in 0..self.scopes.len() {
            self.unparsed(owner);
        }
    }

    fn imports(&mut self, owner: usize) {
        let facts = self.core.facts.clone();
        let k = &self.settings.k;
        let file = self.scopes[owner].file as usize;
        for row in &facts.files[file].imports {
            let core = &mut *self.core;
            let scope = &mut self.scopes[owner];
            let ctx = core.contexts(scope, row.flags, row.scope);
            let (module, is_package) = (scope.module, scope.is_package);
            let (surface, hit, loaded, binds);
            match row.name {
                None => {
                    let target = row.target.unwrap_or(EMPTY);
                    surface = target;
                    hit = Some(core.module_hit(target));
                    loaded = target;
                    binds = row
                        .asname
                        .filter(|&a| a != EMPTY)
                        .unwrap_or_else(|| core.it.head(target));
                }
                Some(name) => {
                    let Some(source) =
                        absolute_import(&mut core.it, module, is_package, row.target, row.level)
                    else {
                        let mut text = ".".repeat(row.level as usize);
                        if let Some(target) = row.target.filter(|&t| t != EMPTY) {
                            text.push_str(core.it.s(target));
                            text.push('.');
                        }
                        text.push_str(core.it.s(name));
                        let surface = core.it.intern(&text);
                        self.push(
                            IMPORT,
                            owner,
                            row.scope,
                            row.line,
                            row.col,
                            ctx,
                            surface,
                            UNRESOLVED,
                            EMPTY,
                            &[],
                        );
                        continue;
                    };
                    if name == k.star {
                        surface = core.it.join(source, k.star);
                        hit = Some(core.module_hit(source));
                        loaded = source;
                        binds = k.star;
                    } else {
                        surface = core.it.join(source, name);
                        let mut found = core.resolve_name(source, name);
                        if found.is_none() && core.exists(surface) {
                            found = Some(core.module_hit(surface));
                        }
                        hit = found;
                        loaded = if core.exists(surface) {
                            surface
                        } else {
                            source
                        };
                        binds = row.asname.filter(|&a| a != EMPTY).unwrap_or(name);
                    }
                }
            }
            let mut attrs = vec![(k.binds, binds)];
            if core.exists(loaded) {
                attrs.push((k.module, loaded));
            }
            let resolved = core.classify(surface, hit);
            self.push(
                IMPORT, owner, row.scope, row.line, row.col, ctx, surface, resolved, EMPTY, &attrs,
            );
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
    }

    /// Every dotted name in a type expression, with whether it is the whole annotation.
    fn mentions(&mut self, value: &V, whole: bool, out: &mut Vec<(Sid, bool)>) {
        let k = &self.settings.k;
        match value {
            V::Chain(chain) => out.push((*chain, whole)),
            V::Str(_, Some(parsed)) => self.mentions(parsed, whole, out),
            V::Sub(inner, index) => {
                self.mentions(inner, whole, out);
                let last = match &**inner {
                    V::Chain(chain) => self.core.it.last(*chain),
                    _ => EMPTY,
                };
                if last == k.literal_name {
                    return;
                }
                if last == k.annotated_name
                    && let V::Tuple(items) = &**index
                    && let Some(first) = items.first()
                {
                    self.mentions(first, false, out);
                    return;
                }
                self.mentions(index, false, out);
            }
            V::Bin(true, l, r) => {
                self.mentions(l, whole, out);
                self.mentions(r, whole, out);
            }
            V::Tuple(items) | V::List(items) => {
                for item in items {
                    self.mentions(item, false, out);
                }
            }
            _ => {}
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
        chain
            .iter()
            .all(|step| self.first_party.contains(&core.it.head(step.m)))
    }

    /// A module-level type alias a bare name binds (`X = dict[str, Any]`): the file and assign
    /// row of its value, read from the file the module table reads the name from: the `.py`, or
    /// the stub for a name only the stub binds. Each module's assignments are read once.
    fn alias(&mut self, module: Sid, name: Sid) -> Option<(u32, u32)> {
        if self.core.it.s(name).contains('.') {
            return None;
        }
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
        let value = self.core.facts.files[file as usize].assigns[at as usize]
            .value
            .as_ref()?;
        match value {
            V::Chain(_) | V::Attr(..) | V::Sub(..) | V::Bin(..) => Some((file, at)),
            _ => None,
        }
    }

    /// The type alias a mention names, as (module, name), its value, and where its names are
    /// looked up: assigned in the module that uses it, or followed through imports to the
    /// first-party module that assigns it. A module holds `Handler = Callable[[Order], None]` to
    /// shorten a signature, not to hide its types.
    fn alias_of(&mut self, mention: Mention, found: Lookup) -> Option<((Sid, Sid), V, SlotLookup)> {
        let module = self.scopes[mention.lookup.owner].module;
        if let Some((file, at)) = self.alias(module, mention.value) {
            let value = self.core.facts.files[file as usize].assigns[at as usize]
                .value
                .clone()?;
            return Some(((module, mention.value), value, mention.lookup));
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
        let owner = *self.owner_of.get(&file)?;
        let row = &self.core.facts.files[file as usize].assigns[at as usize];
        let lookup = SlotLookup {
            owner,
            scope: -1,
            line: row.l,
        };
        Some(((origin.m, origin.s), row.value.clone()?, lookup))
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
        let mut attrs = vec![
            (k.callable, rows.callable),
            (k.slot, slot),
            (k.position, position),
        ];
        attrs.extend_from_slice(extra);
        if rows.handed != EMPTY {
            attrs.push((k.handed, rows.handed));
        }
        let site = rows.site;
        let use_ = k.use_annotation;
        self.push(
            EXPOSES, site.owner, site.scope, site.line, site.col, site.ctx, surface, resolved,
            use_, &attrs,
        );
    }

    /// A row for a whole slot whose types are unknown.
    fn push_unresolved(&mut self, rows: SlotRows, surface: Sid, slot: Sid, extra: &[(Sid, Sid)]) {
        let whole = self.settings.k.whole;
        self.push_slot(rows, surface, UNRESOLVED, slot, whole, extra);
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

    /// Depth first, the names in an alias's value, each alias in it followed once per place (top
    /// or nested). A cycle stops at the alias that closes it; an alias deeper than
    /// MAX_ALIAS_DEPTH stays a marker, because a type left unread is unknown.
    fn expand_into(&mut self, value: &V, at: SlotLookup, top: bool, expansion: &mut Expansion) {
        let mut names = Vec::new();
        self.mentions(value, true, &mut names);
        for (name, whole) in names {
            let item = Expanded {
                value: name,
                top: top && whole,
                lookup: at,
                too_deep: false,
            };
            expansion.write(item);
            let found = self.lookup_at(at, name);
            let mention = Mention {
                value: name,
                whole,
                lookup: at,
            };
            let Some((key, inner, inner_at)) = self.alias_of(mention, found) else {
                continue;
            };
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
            self.expand_into(&inner, inner_at, item.top, expansion);
            expansion.stack.pop();
        }
    }

    /// The names a type alias expands to, worked out once per alias and place its names are
    /// looked up: the alias decides them, never the slot that names it.
    fn expansion(&mut self, key: (Sid, Sid), value: &V, at: SlotLookup) -> Vec<Expanded> {
        if let Some(found) = self.expansions.get(&(key, at)) {
            return found.clone();
        }
        let mut expansion = Expansion {
            stack: vec![key],
            followed: FxHashSet::default(),
            seen: FxHashSet::default(),
            out: Vec::new(),
        };
        self.expand_into(value, at, true, &mut expansion);
        self.expansions.insert((key, at), expansion.out.clone());
        expansion.out
    }

    /// One row per mention, each followed (in a contract only) by a row per name of the type
    /// alias it is, when it is one. A slot names each name an alias expands to once per
    /// position, however often its aliases repeat it.
    fn expose_mentions(
        &mut self,
        rows: SlotRows,
        slot: Sid,
        contract: bool,
        mentions: Vec<Mention>,
    ) {
        let mut written: FxHashSet<(Sid, bool, bool)> = FxHashSet::default();
        for mention in mentions {
            let found = self.lookup_at(mention.lookup, mention.value);
            let core = &mut *self.core;
            let k = &self.settings.k;
            let position = if mention.whole { k.whole } else { k.nested };
            let builtin = !contract && found.hit.is_none() && {
                let head = core.it.head(mention.value);
                found.surface == core.it.join(self.settings.names.builtins_module, head)
            };
            if !builtin {
                let resolved = core.classify_lookup(found);
                self.push_slot(rows, found.surface, resolved, slot, position, &[]);
            }
            if !contract {
                continue;
            }
            let Some((key, value, at)) = self.alias_of(mention, found) else {
                continue;
            };
            for item in self.expansion(key, &value, at) {
                let whole = mention.whole && item.top;
                let inner = self.lookup_at(item.lookup, item.value);
                let surface = if item.too_deep {
                    item.value
                } else {
                    inner.surface
                };
                if !written.insert((surface, whole, item.too_deep)) {
                    continue;
                }
                let k = &self.settings.k;
                let position = if whole { k.whole } else { k.nested };
                if item.too_deep {
                    let marker = [(k.alias, k.too_deep)];
                    self.push_slot(rows, surface, UNRESOLVED, slot, position, &marker);
                    continue;
                }
                let resolved = self.core.classify_lookup(inner);
                self.push_slot(rows, surface, resolved, slot, position, &[]);
            }
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
            let missing = [(k.annotation, k.missing)];
            self.push_unresolved(rows, EMPTY, slot, &missing);
            return;
        };
        let mut found = Vec::new();
        self.mentions(annotation, true, &mut found);
        let mentions = found
            .into_iter()
            .map(|(value, whole)| Mention {
                value,
                whole,
                lookup,
            })
            .collect();
        self.expose_mentions(rows, slot, contract, mentions);
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
        for (position, param) in node.params.iter().enumerate() {
            let in_slot = contract && !(receiver && position == 0);
            if !in_slot && (param.annotation.is_none() || rows.handed != EMPTY) {
                continue;
            }
            let slot = self
                .core
                .it
                .intern(&format!("param:{}", self.core.it.s(param.name)));
            self.expose(rows, lookup, param.annotation.as_ref(), slot, in_slot);
        }
        if let Some(ret) = &node.ret {
            let slot = self.settings.k.ret;
            self.expose(rows, lookup, Some(ret), slot, contract);
        }
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

    /// The fields of a dataclass-like class, as `field:` slots of its `__init__`. In a contract
    /// only fields directly in a class body count, and a `ClassVar` is no field. A field the class
    /// itself declares sits at its own line, unless the class is handed out.
    fn expose_fields(&mut self, rows: SlotRows, fields: &[FieldRow], contract: bool) {
        let facts = self.core.facts.clone();
        for field in fields {
            let row = &facts.files[field.file as usize].assigns[field.at as usize];
            let Some(annotation) = row.ann.as_ref().filter(|ann| !ann.is_null()) else {
                continue;
            };
            let in_slot = contract && row.f == CLASS && !self.is_class_var(annotation);
            if rows.handed != EMPTY && !in_slot {
                continue;
            }
            let V::Chain(name) = &row.targets[0] else {
                continue;
            };
            let slot = self
                .core
                .it
                .intern(&format!("field:{}", self.core.it.s(*name)));
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
            self.expose(line_rows, line_lookup, Some(annotation), slot, in_slot);
        }
    }

    /// The candidate dataclass fields of one class, as assign indexes; each file is read once.
    fn class_fields(&mut self, file: u32, idx: u32) -> Vec<u32> {
        if !self.fields.contains_key(&file) {
            let record = &self.core.facts.files[file as usize];
            let found = fields_by_class(&self.core.it, &record.assigns);
            self.fields.insert(file, found);
        }
        self.fields[&file]
            .get(&(idx as i32))
            .cloned()
            .unwrap_or_default()
    }

    /// The analyzed file's scope and def index of the def or class (`kind`) a module table binds
    /// a name to.
    fn def_of(&mut self, module: Sid, name: Sid, kind: HitKind) -> Option<(usize, u32)> {
        let (file, idx) = self.core.located_def(module, name, kind)?;
        Some((*self.owner_of.get(&file)?, idx))
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
        let wanted = &self.settings.dataclass_decorators;
        wanted.contains(&surface) || target.is_some_and(|name| wanted.contains(&name))
    }

    /// Whether a class takes a decorator that makes it a dataclass.
    fn is_dataclass_like(&mut self, owner: usize, node: &Def) -> bool {
        for dec in &node.dec {
            let (surface, resolved) = self.lookup_value(owner, dec, node.s, node.l);
            if self.is_dataclass_decorator(surface, resolved) {
                return true;
            }
        }
        false
    }

    /// The fields of the generated `__init__`, as the dataclass machinery collects them: from
    /// every dataclass-like class along the MRO, base first, a redefinition replacing the earlier
    /// field in its place.
    fn mro_fields(&mut self, lineage: &[(usize, u32)], lookup: SlotLookup) -> Vec<FieldRow> {
        let facts = self.core.facts.clone();
        let mut fields: OrderedMap<FieldRow> = OrderedMap::default();
        for position in (0..lineage.len()).rev() {
            let (owner, idx) = lineage[position];
            let file = self.scopes[owner].file;
            let record = &facts.files[file as usize];
            let node = &record.defs[idx as usize];
            if position > 0 && !self.is_dataclass_like(owner, node) {
                continue;
            }
            let at_lookup = if position == 0 {
                lookup
            } else {
                SlotLookup {
                    owner,
                    scope: node.s,
                    line: node.l,
                }
            };
            for at in self.class_fields(file, idx) {
                let V::Chain(name) = &record.assigns[at as usize].targets[0] else {
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

    /// One unresolved row per base along the MRO that does not resolve: the fields it adds to
    /// the generated `__init__` are unknown.
    fn expose_unresolved_bases(&mut self, rows: SlotRows, lineage: &[(usize, u32)]) {
        let facts = self.core.facts.clone();
        for &(owner, idx) in lineage {
            let node = &facts.files[self.scopes[owner].file as usize].defs[idx as usize];
            for base in &node.bases {
                let (surface, resolved) = self.lookup_value(owner, base, node.s, node.l);
                if resolved.res != Res::Unresolved {
                    continue;
                }
                self.push_unresolved(rows, surface, self.settings.k.fields, &[]);
            }
        }
    }

    /// The `__init__` slots of a contract dataclass (its def index in the file `lookup` names): a
    /// row per unresolved base along its MRO, then its fields as the dataclass machinery collects
    /// them.
    fn expose_dataclass(&mut self, rows: SlotRows, lookup: SlotLookup, idx: u32) {
        let lineage = self.lineage(lookup.owner, idx);
        self.expose_unresolved_bases(rows, &lineage);
        let fields = self.mro_fields(&lineage, lookup);
        self.expose_fields(rows, &fields, true);
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
            self.mentions(annotation, true, &mut mentioned);
        }
        for (value, _) in mentioned {
            names.insert(self.core.it.head(value));
        }
        names
    }

    /// Whether a def is a public callable of the module's contract: a top-level function or
    /// class, or a method directly in a top-level class, with no leading `_` (`__init__` kept).
    fn in_contract(&self, defs: &[Def], idx: usize) -> bool {
        let node = &defs[idx];
        let name = self.core.it.s(node.n);
        if node.s == -1 {
            return node.f == 0 && !name.starts_with('_');
        }
        let parent = node.s as usize;
        let owner = &defs[parent];
        if node.is_class() || !owner.is_class() || !self.in_contract(defs, parent) {
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
        if !self.first_party.contains(&head) {
            return None;
        }
        let (hit, rest) = core.resolve_dotted(target);
        let Some(hit) = hit.filter(|_| rest.is_empty()) else {
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
        match self.def_of(origin.m, origin.s, kind) {
            Some((owner, idx)) => Some(Followed::Def(owner, idx)),
            None => Some(Followed::Unfollowed),
        }
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
            self.push_unresolved(rows, k.dunder_all, k.names, &[]);
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
                self.push_unresolved(rows, item.target, self.settings.k.signature, &[]);
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
            if !self.is_dataclass_like(origin, node) {
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
            let in_contract = contract && self.in_contract(&record.defs, idx);
            if node.is_class() {
                for (position, base) in node.bases.iter().enumerate() {
                    let (surface, resolved) = self.lookup_value(owner, base, enclosing, node.l);
                    let k = &self.settings.k;
                    let index = self.core.it.intern(&position.to_string());
                    let attrs = [(k.class, qualname), (k.index, index)];
                    let use_ = k.use_base;
                    self.push(
                        INHERIT, owner, enclosing, node.l, node.c, ctx, surface, resolved, use_,
                        &attrs,
                    );
                }
                if !nested && dataclass {
                    let rows = SlotRows {
                        site,
                        callable: self.core.it.join(qualname, self.settings.k.init),
                        handed: EMPTY,
                    };
                    if !in_contract {
                        let own: Vec<FieldRow> = self
                            .class_fields(file as u32, idx as u32)
                            .into_iter()
                            .map(|at| FieldRow {
                                file: file as u32,
                                at,
                                lookup,
                                own: true,
                            })
                            .collect();
                        self.expose_fields(rows, &own, false);
                        continue;
                    }
                    self.expose_dataclass(rows, lookup, idx as u32);
                }
                continue;
            }
            if nested {
                continue;
            }
            let rows = SlotRows {
                site,
                callable: qualname,
                handed: EMPTY,
            };
            self.expose_signature(rows, lookup, node, in_contract);
        }
        if contract {
            self.hand_out(owner);
        }
    }

    fn strings(&mut self, owner: usize) {
        let facts = self.core.facts.clone();
        let file = self.scopes[owner].file as usize;
        for row in &facts.files[file].strs {
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
            let patch = patch_role(&row.up, &self.settings.patch_callees);
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
                if resolved.res == Res::Unresolved && !(patch || text.matches('.').count() >= 2) {
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
                    && let Some((module, _)) = core.index.longest_module(&mut core.it, dotted)
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
        for row in &facts.files[file].calls {
            let last = row.callee.rsplit('.').next().unwrap_or("");
            if !self
                .settings
                .loader_last_names
                .iter()
                .any(|name| name == last)
                || row.callee.starts_with('?')
            {
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
            if !self.settings.loaders.contains(&loader) && !self.settings.loaders.contains(&origin)
            {
                continue;
            }
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
                    (EMPTY, UNRESOLVED)
                }
            };
            let use_ = k.use_call;
            self.push(
                DYNAMIC, owner, row.scope, row.line, row.col, ctx, surface, resolved, use_, &attrs,
            );
        }
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
