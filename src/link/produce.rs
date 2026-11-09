//! The core producers (hoglint/core/producers.py): import, reference, declaration, dotted-path,
//! dynamic-import and unparsed edges, in the order and with the fields Python makes them.

use super::facts::{Argument, Assign, Op, V};
use super::intern::{EMPTY, Interner, Sid};
use super::resolve::{Core, Ent, absolute_import};
use super::scope::{LookupNames, Res, Resolved, Scope, UNRESOLVED};
use rustc_hash::FxHashMap;

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

#[derive(Default)]
pub struct EdgeStore {
    pub rows: Vec<EdgeRow>,
    pub via: Vec<Ent>,
    pub attrs: Vec<(Sid, Sid)>,
    /// Refinements by edge id. Python checks that two producers never set one field apart.
    pub refined: FxHashMap<u32, Refinement>,
}

impl EdgeStore {
    pub fn via_of(&self, row: &EdgeRow) -> &[Ent] {
        &self.via[row.via.0 as usize..(row.via.0 + row.via.1) as usize]
    }

    /// The attrs core gave the edge, without what packs added (`attrs_with`).
    pub fn attrs_of(&self, row: &EdgeRow) -> &[(Sid, Sid)] {
        &self.attrs[row.attrs.0 as usize..(row.attrs.0 + row.attrs.1) as usize]
    }

    /// Every attr of edge `id`: its own, then those refinements added.
    pub fn attrs_with(&self, id: u32) -> impl Iterator<Item = &(Sid, Sid)> {
        let added = self
            .refined
            .get(&id)
            .map_or(&[][..], |r| r.attrs.as_slice());
        self.attrs_of(&self.rows[id as usize]).iter().chain(added)
    }

    /// (shape, shape detail) of edge `id`: EMPTY for an edge no pack refined.
    pub fn shape_of(&self, id: u32) -> (Sid, Sid) {
        self.refined
            .get(&id)
            .map_or((EMPTY, EMPTY), |r| (r.shape, r.shape_detail))
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
        let found = self.refined.entry(id).or_default();
        if let Some(shape) = shape {
            found.shape = shape;
        }
        if let Some(detail) = shape_detail {
            found.shape_detail = detail;
        }
        let row = &self.rows[id as usize];
        let own = &self.attrs[row.attrs.0 as usize..(row.attrs.0 + row.attrs.1) as usize];
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
}

impl Symbols {
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
}

impl Producing {
    pub fn new(
        it: &mut Interner,
        builtins: &[String],
        loaders: &[String],
        dataclass_decorators: &[String],
        patch_callees: &[String],
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

/// Builds the core edges of every analyzed file, producer by producer.
pub struct Producers<'p> {
    pub core: &'p mut Core,
    pub scopes: &'p mut [Scope],
    pub settings: &'p Producing,
    pub edges: EdgeStore,
    pub symbols: Symbols,
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

    #[allow(clippy::too_many_arguments)]
    fn expose(
        &mut self,
        owner: usize,
        idx: usize,
        annotation: &V,
        slot: Sid,
        callable: Sid,
        line: u32,
    ) {
        let facts = self.core.facts.clone();
        let k = &self.settings.k;
        let file = self.scopes[owner].file as usize;
        let node = &facts.files[file].defs[idx];
        let enclosing = node.s;
        let ctx = self
            .core
            .contexts(&mut self.scopes[owner], node.f, enclosing);
        let mut found_names = Vec::new();
        self.mentions(annotation, true, &mut found_names);
        for (value, whole) in found_names {
            let core = &mut *self.core;
            let head = core.it.head(value);
            let Some(found) = core.value_lookup(
                &self.settings.names,
                &mut self.scopes[owner],
                &V::Chain(value),
                enclosing,
                line,
            ) else {
                continue;
            };
            if found.hit.is_none() {
                let builtin = core.it.join(self.settings.names.builtins_module, head);
                if found.surface == builtin {
                    continue;
                }
            }
            let resolved = core.classify_lookup(found);
            let position = if whole { k.whole } else { k.nested };
            let attrs = [
                (k.callable, callable),
                (k.slot, slot),
                (k.position, position),
            ];
            let use_ = k.use_annotation;
            self.push(
                EXPOSES,
                owner,
                enclosing,
                line,
                node.c,
                ctx,
                found.surface,
                resolved,
                use_,
                &attrs,
            );
        }
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

    fn declarations(&mut self, owner: usize) {
        let facts = self.core.facts.clone();
        let file = self.scopes[owner].file as usize;
        let record = &facts.files[file];
        let mut fields: rustc_hash::FxHashMap<i32, Vec<&Assign>> = rustc_hash::FxHashMap::default();
        for row in &record.assigns {
            if row.op == Op::Annotated
                && row.targets.len() == 1
                && let V::Chain(name) = &row.targets[0]
                && !self.core.it.s(*name).contains('.')
            {
                fields.entry(row.s).or_default().push(row);
            }
        }
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
            let mut decorators: Vec<Sid> = Vec::new();
            for dec in &node.dec {
                let (surface, resolved) = self.lookup_value(owner, dec, enclosing, node.l);
                let k = &self.settings.k;
                let called = if matches!(dec, V::Call(..)) {
                    k.one
                } else {
                    k.zero
                };
                let mut attrs = vec![(k.decorated, qualname), (k.called, called)];
                if matches!(dec, V::Call(_, true)) {
                    attrs.push((k.frozen, k.one));
                }
                let use_ = k.use_decorator;
                self.push(
                    DECORATE, owner, enclosing, node.l, node.c, ctx, surface, resolved, use_,
                    &attrs,
                );
                decorators.push(surface);
                if let Some(target) = resolved.target {
                    decorators.push(self.core.ent_sid(target));
                }
            }
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
                let dataclass = decorators
                    .iter()
                    .any(|name| self.settings.dataclass_decorators.contains(name));
                if !nested && dataclass {
                    let init = self.core.it.join(qualname, self.settings.k.init);
                    for row in fields.get(&(idx as i32)).into_iter().flatten() {
                        let V::Chain(name) = &row.targets[0] else {
                            continue;
                        };
                        let slot = self
                            .core
                            .it
                            .intern(&format!("field:{}", self.core.it.s(*name)));
                        let annotation = row.ann.clone().unwrap_or(V::Null);
                        self.expose(owner, idx, &annotation, slot, init, row.l);
                    }
                }
                continue;
            }
            if nested {
                continue;
            }
            for param in &node.params {
                if let Some(annotation) = &param.annotation {
                    let slot = self
                        .core
                        .it
                        .intern(&format!("param:{}", self.core.it.s(param.name)));
                    self.expose(owner, idx, annotation, slot, qualname, node.l);
                }
            }
            if let Some(ret) = &node.ret {
                let slot = self.core.it.intern("return");
                self.expose(owner, idx, ret, slot, qualname, node.l);
            }
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
