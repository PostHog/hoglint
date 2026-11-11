//! Module symbol tables, name resolution that follows re-exports, and class MROs
//! (hoglint/core/symbols.py). Caches and their cycle rule match the Python resolver, so
//! the order of queries gives the same answers.

use super::facts::{Assign, Def, FactSet, FileFacts, Import, Op, V};
use super::heap;
use super::intern::{EMPTY, Interner, Sid};
use super::paths::{ModuleIndex, ModuleNamer};
use rustc_hash::{FxHashMap, FxHashSet};
use std::sync::Arc;

pub const TYPE_ONLY: u32 = 1;
pub const TC_ELSE: u32 = 2;
pub const FUNC: u32 = 4;
pub const CLASS: u32 = 8;
pub const TRY: u32 = 16;
pub const IF: u32 = 32;
pub const BLOCK: u32 = 64;
pub const MODULE_LEVEL_FLAGS: u32 = TYPE_ONLY | TC_ELSE | IF | TRY | BLOCK;
pub const CONDITIONAL_FLAGS: u32 = TYPE_ONLY | TC_ELSE | IF | TRY;

pub const MODULE_ATTRIBUTES: [&str; 9] = [
    "__doc__",
    "__file__",
    "__name__",
    "__path__",
    "__spec__",
    "__loader__",
    "__package__",
    "__dict__",
    "__all__",
];

/// Whether an assigns row binds module-level names: `=`, `:` or `type` at module level.
pub fn is_module_assign(row: &Assign) -> bool {
    let assigned = matches!(row.op, Op::Assign | Op::Annotated | Op::TypeAlias);
    row.s == -1 && row.f & !MODULE_LEVEL_FLAGS == 0 && assigned
}

/// name -> row index of the last module-level assignment with a value of each bare name, in one
/// record: the value a module table reads for the name.
pub fn module_assigns(it: &Interner, record: &FileFacts) -> FxHashMap<Sid, u32> {
    let mut rows = FxHashMap::default();
    if !record.ok() {
        return rows;
    }
    for (at, row) in record.assigns.iter().enumerate() {
        if !is_module_assign(row) || row.value.is_none() {
            continue;
        }
        for target in &row.targets {
            if let V::Chain(name) = target
                && !it.s(*name).contains('.')
            {
                rows.insert(*name, at as u32);
            }
        }
    }
    rows
}

/// A language-qualified entity: a module (symbol EMPTY) or a symbol inside one.
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug, PartialOrd, Ord)]
pub struct Ent {
    pub m: Sid,
    pub s: Sid,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum HitKind {
    Class,
    Def,
    Variable,
    Module,
    Missing,
    Member,
}

impl HitKind {
    pub fn name(self) -> &'static str {
        match self {
            HitKind::Class => "class",
            HitKind::Def => "def",
            HitKind::Variable => "variable",
            HitKind::Module => "module",
            HitKind::Missing => "missing",
            HitKind::Member => "member",
        }
    }
}

/// A resolved name: `chain` (in the arena) runs from the name the code used to the origin.
#[derive(Clone, Copy)]
struct HitRec {
    start: u32,
    len: u32,
    kind: HitKind,
}

pub type HitId = u32;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Binding {
    /// Def index, and whether the def comes from the module's `.pyi` stub.
    Class(u32, bool),
    Def(u32, bool),
    Variable,
    Module(Sid),
    From(Sid, Sid),
    /// `name = a.b.c`: the chain.
    Alias(Sid),
}

impl Binding {
    pub fn is_import(self) -> bool {
        matches!(self, Binding::Module(_) | Binding::From(_, _))
    }
}

/// A map that keeps the order keys were first inserted in, as a Python dict does.
pub struct OrderedMap<V> {
    map: FxHashMap<Sid, V>,
    order: Vec<Sid>,
}

impl<V> Default for OrderedMap<V> {
    fn default() -> Self {
        OrderedMap {
            map: FxHashMap::default(),
            order: Vec::new(),
        }
    }
}

impl<V: Copy> OrderedMap<V> {
    pub fn get(&self, key: &Sid) -> Option<&V> {
        self.map.get(key)
    }

    /// `d[key] = value`: a new key goes last, an old one keeps its place.
    pub fn insert(&mut self, key: Sid, value: V) {
        if self.map.insert(key, value).is_none() {
            self.order.push(key);
        }
    }

    pub fn iter(&self) -> impl Iterator<Item = (Sid, V)> + '_ {
        self.order.iter().map(|key| (*key, self.map[key]))
    }
}

pub struct ModuleTable {
    pub name: Sid,
    pub is_package: bool,
    pub names: OrderedMap<Binding>,
    pub star_imports: Vec<Sid>,
    pub lazy: OrderedMap<Sid>,
    pub record: Option<u32>,
    pub stub_record: Option<u32>,
    /// Names only the `.pyi` beside the module binds.
    pub stub_names: FxHashSet<Sid>,
}

/// One module-level binding in source order: (line, column, order, flags, binding or None for
/// a star import, bound name, star source).
type BindingEvent = (u32, u32, u8, u32, Option<Binding>, Sid, Option<Sid>);

/// What the core shares between producers and pack queries for one snapshot.
pub struct Core {
    pub it: Interner,
    pub facts: Arc<FactSet>,
    pub namer: ModuleNamer,
    pub index: ModuleIndex,
    tables: Vec<ModuleTable>,
    table_of: FxHashMap<Sid, Option<u32>>,
    hits: Vec<HitRec>,
    chains: Vec<Ent>,
    module_hits: FxHashMap<Sid, HitId>,
    name_cache: FxHashMap<(Sid, Sid), Option<HitId>>,
    in_progress: FxHashSet<(Sid, Sid)>,
    from_cache: FxHashMap<(Sid, Sid), Option<HitId>>,
    met_cycle: bool,
    pub module_attributes: FxHashSet<Sid>,
    getattr_id: Sid,
    star_id: Sid,
    mro_cache: FxHashMap<Ent, Vec<Ent>>,
    bases_cache: FxHashMap<Ent, Vec<Ent>>,
    ancestor_cache: FxHashMap<Ent, FxHashSet<Sid>>,
}

/// The absolute module named by `from <level dots><target> import ...` inside `module`.
pub fn absolute_import(
    it: &mut Interner,
    module: Sid,
    is_package: bool,
    target: Option<Sid>,
    level: u32,
) -> Option<Sid> {
    if level == 0 {
        return target;
    }
    let package = if is_package {
        module
    } else {
        it.parent(module)
    };
    let parts: Vec<Sid> = if package == EMPTY {
        Vec::new()
    } else {
        it.segs(package).to_vec()
    };
    let climb = (level - 1) as usize;
    if climb >= parts.len() {
        return None;
    }
    let base = it.from_segs(&parts[..parts.len() - climb]);
    Some(match target {
        Some(target) if target != EMPTY => it.join(base, target),
        _ => base,
    })
}

/// (bound name, binding) of one import row; None for a star import or a bad relative one.
pub fn import_binding(
    it: &mut Interner,
    star: Sid,
    module: Sid,
    is_package: bool,
    row: &Import,
) -> Option<(Sid, Binding)> {
    match row.name {
        None => {
            let target = row.target.unwrap_or(EMPTY);
            if let Some(asname) = row.asname.filter(|&a| a != EMPTY) {
                return Some((asname, Binding::Module(target)));
            }
            let head = it.head(target);
            Some((head, Binding::Module(head)))
        }
        Some(name) if name == star => None,
        Some(name) => {
            let source = absolute_import(it, module, is_package, row.target, row.level)?;
            let bound = row.asname.filter(|&a| a != EMPTY).unwrap_or(name);
            Some((bound, Binding::From(source, name)))
        }
    }
}

/// Plain names a tuple or list assignment target binds, nested ones included.
pub fn unpacked_names(it: &Interner, target: &V, out: &mut Vec<Sid>) {
    match target {
        V::Chain(name) => {
            if !it.s(*name).contains('.') {
                out.push(*name);
            }
        }
        V::Tuple(items) | V::List(items) => {
            for item in items {
                unpacked_names(it, item, out);
            }
        }
        V::Star(inner) => unpacked_names(it, inner, out),
        _ => {}
    }
}

/// The C3 merge of the bases' MROs and the bases themselves.
pub fn c3_merge(mut sequences: Vec<Vec<Ent>>) -> Vec<Ent> {
    let mut result = Vec::new();
    loop {
        sequences.retain(|s| !s.is_empty());
        if sequences.is_empty() {
            break;
        }
        let mut head = sequences[0][0];
        for seq in &sequences {
            let candidate = seq[0];
            if !sequences.iter().any(|s| s[1..].contains(&candidate)) {
                head = candidate;
                break;
            }
        }
        result.push(head);
        for seq in sequences.iter_mut() {
            if seq.first() == Some(&head) {
                seq.remove(0);
            }
        }
    }
    result
}

fn def_binding(i: usize, d: &Def, stub: bool) -> Binding {
    if d.is_class() {
        Binding::Class(i as u32, stub)
    } else {
        Binding::Def(i as u32, stub)
    }
}

impl<V> OrderedMap<V> {
    fn heap(&self) -> usize {
        heap::map(&self.map) + heap::vec(&self.order)
    }
}

impl Core {
    /// Drops the module tables, the hits and every resolver cache: once the fact rows are gone
    /// nothing resolves again (`Link::release_facts`).
    pub fn release(&mut self) {
        self.tables = Vec::new();
        self.table_of = FxHashMap::default();
        self.hits = Vec::new();
        self.chains = Vec::new();
        self.module_hits = FxHashMap::default();
        self.name_cache = FxHashMap::default();
        self.in_progress = FxHashSet::default();
        self.from_cache = FxHashMap::default();
        self.mro_cache = FxHashMap::default();
        self.bases_cache = FxHashMap::default();
        self.ancestor_cache = FxHashMap::default();
    }

    /// Estimated heap bytes of the interner, the module index, the tables and the resolver
    /// caches (`Link::heap`).
    pub fn heap(&self) -> Vec<(&'static str, usize)> {
        let interner = self.it.heap();
        let index = &self.index;
        let mut module_index = heap::map(&index.path_of) + heap::vec(&index.modules);
        module_index += heap::vec(&index.module_of_file) + heap::map(&index.package);
        module_index +=
            heap::set(&index.dirs) + heap::map(&index.stub_of) + heap::set(&index.repo_heads);
        let tables: usize = self
            .tables
            .iter()
            .map(|t| {
                t.names.heap()
                    + heap::vec(&t.star_imports)
                    + t.lazy.heap()
                    + heap::set(&t.stub_names)
            })
            .sum();
        let mut resolver =
            heap::vec(&self.hits) + heap::vec(&self.chains) + heap::map(&self.module_hits);
        resolver += heap::map(&self.name_cache)
            + heap::set(&self.in_progress)
            + heap::map(&self.from_cache);
        let classes = |cache: &FxHashMap<Ent, Vec<Ent>>| -> usize {
            heap::map(cache) + cache.values().map(heap::vec).sum::<usize>()
        };
        resolver += classes(&self.mro_cache) + classes(&self.bases_cache);
        resolver += heap::map(&self.ancestor_cache)
            + self.ancestor_cache.values().map(heap::set).sum::<usize>();
        vec![
            ("interner", interner),
            ("module_index", module_index),
            (
                "module_tables",
                heap::vec(&self.tables) + tables + heap::map(&self.table_of),
            ),
            ("resolver_caches", resolver),
        ]
    }

    pub fn new(it: Interner, facts: Arc<FactSet>, namer: ModuleNamer, index: ModuleIndex) -> Core {
        let mut it = it;
        let module_attributes = MODULE_ATTRIBUTES
            .iter()
            .map(|name| it.intern(name))
            .collect();
        let getattr_id = it.intern("__getattr__");
        let star_id = it.intern("*");
        Core {
            it,
            facts,
            namer,
            index,
            tables: Vec::new(),
            table_of: FxHashMap::default(),
            hits: Vec::new(),
            chains: Vec::new(),
            module_hits: FxHashMap::default(),
            name_cache: FxHashMap::default(),
            in_progress: FxHashSet::default(),
            from_cache: FxHashMap::default(),
            met_cycle: false,
            module_attributes,
            getattr_id,
            star_id,
            mro_cache: FxHashMap::default(),
            bases_cache: FxHashMap::default(),
            ancestor_cache: FxHashMap::default(),
        }
    }

    pub fn star(&self) -> Sid {
        self.star_id
    }

    pub fn exists(&self, module: Sid) -> bool {
        self.index.exists(module)
    }

    pub fn is_external(&mut self, module: Sid) -> bool {
        self.index.is_external(&mut self.it, module)
    }

    // ── tables ──

    /// Module-level names of one record: later bindings win, except that a conditional def or
    /// assignment never replaces an import (`except ImportError: Bar = None` is a fallback).
    fn collect(&mut self, table: &mut ModuleTable, file: u32, stub: bool) {
        let module = table.name;
        let is_package = table.is_package;
        let star = self.star_id;
        let facts = self.facts.clone();
        let record = &facts.files[file as usize];
        let mut events: Vec<BindingEvent> = Vec::new();
        for (i, d) in record.defs.iter().enumerate() {
            if d.s == -1 && d.f & !MODULE_LEVEL_FLAGS == 0 {
                events.push((d.l, d.c, 0, d.f, Some(def_binding(i, d, stub)), d.n, None));
            }
        }
        for row in &record.imports {
            if row.scope != -1 || row.flags & !MODULE_LEVEL_FLAGS != 0 {
                continue;
            }
            if row.name == Some(star) {
                let source =
                    absolute_import(&mut self.it, module, is_package, row.target, row.level);
                events.push((row.line, row.col, 1, row.flags, None, star, source));
                continue;
            }
            if let Some((name, binding)) =
                import_binding(&mut self.it, star, module, is_package, row)
            {
                events.push((row.line, row.col, 1, row.flags, Some(binding), name, None));
            }
        }
        for row in &record.assigns {
            if !is_module_assign(row) {
                continue;
            }
            for target in &row.targets {
                if let V::Chain(name) = target
                    && !self.it.s(*name).contains('.')
                {
                    let binding = match &row.value {
                        Some(V::Chain(chain)) => Binding::Alias(*chain),
                        _ => Binding::Variable,
                    };
                    events.push((row.l, row.c, 2, row.f, Some(binding), *name, None));
                    continue;
                }
                let mut names = Vec::new();
                unpacked_names(&self.it, target, &mut names);
                for name in names {
                    events.push((row.l, row.c, 2, row.f, Some(Binding::Variable), name, None));
                }
            }
        }
        events.sort_by_key(|e| (e.0, e.1, e.2));
        for (_, _, _, flags, binding, name, source) in events {
            let Some(binding) = binding else {
                if let Some(source) = source.filter(|&s| s != EMPTY) {
                    table.star_imports.push(source);
                }
                continue;
            };
            if !binding.is_import()
                && flags & CONDITIONAL_FLAGS != 0
                && let Some(current) = table.names.get(&name)
                && current.is_import()
            {
                continue;
            }
            table.names.insert(name, binding);
        }
    }

    /// PEP 562 lazy exports: module-level dicts of name -> module string in a module with
    /// `__getattr__`. A value is the tail of a module-level string prefix, an absolute module,
    /// or a module under the package or one of its ancestors (nearest first).
    fn lazy_map(&mut self, module: Sid, is_package: bool, file: u32) -> OrderedMap<Sid> {
        let package = if is_package {
            module
        } else {
            self.it.parent(module)
        };
        let parts = self.it.segs(package);
        let ancestors: Vec<Sid> = (1..=parts.len())
            .rev()
            .map(|end| self.it.from_segs(&parts[..end]))
            .collect();
        let facts = self.facts.clone();
        let record = &facts.files[file as usize];
        let mut prefixes: Vec<String> = Vec::new();
        let mut maps: Vec<&Vec<(Option<V>, V)>> = Vec::new();
        for row in &record.assigns {
            if row.s != -1 {
                continue;
            }
            match &row.value {
                Some(V::Str(text, None)) if self.it.s(*text).ends_with('.') => {
                    prefixes.push(self.it.s(*text).to_string());
                }
                Some(V::Dict(items)) => maps.push(items),
                _ => {}
            }
        }
        let mut out = OrderedMap::default();
        for items in maps {
            for (key, value) in items {
                let (Some(V::Str(key, _)), V::Str(value, _)) = (key, value) else {
                    continue;
                };
                let tail = self.it.s(*value).to_string();
                let mut candidates: Vec<Sid> = prefixes
                    .iter()
                    .map(|p| self.it.intern(&format!("{p}{tail}")))
                    .collect();
                candidates.push(*value);
                for &ancestor in &ancestors {
                    candidates.push(self.it.join(ancestor, *value));
                }
                if let Some(found) = candidates.into_iter().find(|&c| self.index.exists(c)) {
                    out.insert(*key, found);
                }
            }
        }
        out
    }

    fn build_table(&mut self, module: Sid) -> Option<ModuleTable> {
        if !self.index.exists(module) {
            return None;
        }
        let mut table = ModuleTable {
            name: module,
            is_package: self.index.is_package(module),
            names: OrderedMap::default(),
            star_imports: Vec::new(),
            lazy: OrderedMap::default(),
            record: None,
            stub_record: None,
            stub_names: FxHashSet::default(),
        };
        if let Some(&file) = self.index.path_of.get(&module) {
            table.record = Some(file);
            if self.facts.files[file as usize].ok() {
                self.collect(&mut table, file, false);
                if matches!(table.names.get(&self.getattr_id), Some(Binding::Def(_, _))) {
                    table.lazy = self.lazy_map(module, table.is_package, file);
                }
            }
        }
        if let Some(&stub) = self.index.stub_of.get(&module) {
            table.stub_record = Some(stub);
            if self.facts.files[stub as usize].ok() {
                let mut stub_table = ModuleTable {
                    name: module,
                    is_package: table.is_package,
                    names: OrderedMap::default(),
                    star_imports: Vec::new(),
                    lazy: OrderedMap::default(),
                    record: None,
                    stub_record: None,
                    stub_names: FxHashSet::default(),
                };
                self.collect(&mut stub_table, stub, true);
                for (name, binding) in stub_table.names.iter() {
                    if table.names.get(&name).is_none() {
                        table.names.insert(name, binding);
                        table.stub_names.insert(name);
                    }
                }
            }
        }
        Some(table)
    }

    pub fn table(&mut self, module: Sid) -> Option<u32> {
        if let Some(&found) = self.table_of.get(&module) {
            return found;
        }
        let built = self.build_table(module).map(|table| {
            self.tables.push(table);
            (self.tables.len() - 1) as u32
        });
        self.table_of.insert(module, built);
        built
    }

    pub fn table_at(&self, id: u32) -> &ModuleTable {
        &self.tables[id as usize]
    }

    pub fn binding_of(&mut self, module: Sid, name: Sid) -> Option<Binding> {
        let table = self.table(module)?;
        self.tables[table as usize].names.get(&name).copied()
    }

    // ── hits ──

    pub fn make_hit(&mut self, chain: &[Ent], kind: HitKind) -> HitId {
        let start = self.chains.len() as u32;
        self.chains.extend_from_slice(chain);
        self.hits.push(HitRec {
            start,
            len: chain.len() as u32,
            kind,
        });
        (self.hits.len() - 1) as HitId
    }

    pub fn kind(&self, hit: HitId) -> HitKind {
        self.hits[hit as usize].kind
    }

    pub fn chain(&self, hit: HitId) -> &[Ent] {
        let rec = self.hits[hit as usize];
        &self.chains[rec.start as usize..(rec.start + rec.len) as usize]
    }

    pub fn origin(&self, hit: HitId) -> Ent {
        let rec = self.hits[hit as usize];
        self.chains[(rec.start + rec.len - 1) as usize]
    }

    /// chain[1:-1]: the hops between the name the code used and the origin.
    pub fn via(&self, hit: HitId) -> &[Ent] {
        let chain = self.chain(hit);
        if chain.len() <= 2 {
            &[]
        } else {
            &chain[1..chain.len() - 1]
        }
    }

    pub fn behind(&mut self, hit: HitId, step: Ent) -> HitId {
        if self.chain(hit).first() == Some(&step) {
            return hit;
        }
        let mut chain = Vec::with_capacity(self.chain(hit).len() + 1);
        chain.push(step);
        chain.extend_from_slice(self.chain(hit));
        let kind = self.kind(hit);
        self.make_hit(&chain, kind)
    }

    pub fn module_hit(&mut self, module: Sid) -> HitId {
        if let Some(&hit) = self.module_hits.get(&module) {
            return hit;
        }
        let hit = self.make_hit(
            &[Ent {
                m: module,
                s: EMPTY,
            }],
            HitKind::Module,
        );
        self.module_hits.insert(module, hit);
        hit
    }

    pub fn ent_text(&self, e: Ent) -> String {
        if e.s != EMPTY {
            format!("{}.{}", self.it.s(e.m), self.it.s(e.s))
        } else {
            self.it.s(e.m).to_string()
        }
    }

    pub fn ent_sid(&mut self, e: Ent) -> Sid {
        if e.s != EMPTY {
            self.it.join(e.m, e.s)
        } else {
            e.m
        }
    }

    // ── resolver ──

    /// A re-export cycle reads as unresolved; results that met a cycle are not cached.
    pub fn resolve_name(&mut self, module: Sid, name: Sid) -> Option<HitId> {
        let key = (module, name);
        if let Some(&cached) = self.name_cache.get(&key) {
            return cached;
        }
        if self.in_progress.contains(&key) {
            self.met_cycle = true;
            return None;
        }
        let outer = self.met_cycle;
        self.met_cycle = false;
        self.in_progress.insert(key);
        let hit = self.lookup(module, name);
        self.in_progress.remove(&key);
        // A result that met a cycle is never cached, so each top-level answer is a pure function
        // of the tables.
        if !self.met_cycle {
            self.name_cache.insert(key, hit);
        }
        self.met_cycle = outer || self.met_cycle;
        hit
    }

    pub fn resolve_from(&mut self, source: Sid, attr: Sid) -> Option<HitId> {
        if !self.index.exists(source) {
            let missing = self.it.join(source, attr);
            return Some(self.make_hit(
                &[Ent {
                    m: missing,
                    s: EMPTY,
                }],
                HitKind::Missing,
            ));
        }
        if let Some(hit) = self.resolve_name(source, attr) {
            return Some(hit);
        }
        let sub = self.it.join(source, attr);
        if self.index.exists(sub) {
            return Some(self.module_hit(sub));
        }
        None
    }

    /// Follows attributes through modules. Inside a module with no sources every attribute is
    /// part of one dotted name, and the hit becomes `missing`. Returns the hit and how many
    /// attributes it consumed.
    pub fn descend(&mut self, hit: Option<HitId>, attrs: &[Sid]) -> (Option<HitId>, usize) {
        let mut hit = hit;
        let mut used = 0;
        while let Some(current) = hit {
            if self.kind(current) != HitKind::Module || used >= attrs.len() {
                break;
            }
            let module = self.origin(current).m;
            if !self.index.exists(module) {
                let missing = self.it.join_all(module, &attrs[used..]);
                let mut chain = self.chain(current).to_vec();
                chain.push(Ent {
                    m: missing,
                    s: EMPTY,
                });
                return (Some(self.make_hit(&chain, HitKind::Missing)), attrs.len());
            }
            let Some(next) = self.resolve_name(module, attrs[used]) else {
                break;
            };
            hit = Some(next);
            used += 1;
        }
        (hit, used)
    }

    /// `a.b.c` from `module`: the deepest hit and how many parts it consumed (the head counts).
    pub fn resolve_chain(&mut self, module: Sid, chain: Sid) -> (Option<HitId>, usize, usize) {
        let parts = self.it.segs(chain);
        let head = self.resolve_name(module, parts[0]);
        let (hit, used) = self.descend(head, &parts[1..]);
        (hit, used + 1, parts.len())
    }

    pub fn resolve_binding(&mut self, module: Sid, binding: Binding) -> Option<HitId> {
        match binding {
            Binding::From(source, attr) => {
                let key = (source, attr);
                if let Some(&cached) = self.from_cache.get(&key) {
                    return cached;
                }
                let outer = self.met_cycle;
                self.met_cycle = false;
                let hit = self.resolve_from(source, attr);
                if !self.met_cycle {
                    self.from_cache.insert(key, hit);
                }
                self.met_cycle = outer || self.met_cycle;
                hit
            }
            Binding::Module(dotted) => Some(self.module_hit(dotted)),
            Binding::Alias(chain) => {
                let (hit, used, total) = self.resolve_chain(module, chain);
                if used == total { hit } else { None }
            }
            Binding::Class(_, _) | Binding::Def(_, _) | Binding::Variable => None,
        }
    }

    fn lookup(&mut self, module: Sid, name: Sid) -> Option<HitId> {
        let table = self.table(module)? as usize;
        let step = Ent { m: module, s: name };
        if let Some(binding) = self.tables[table].names.get(&name).copied() {
            match binding {
                Binding::Class(_, _) => return Some(self.make_hit(&[step], HitKind::Class)),
                Binding::Def(_, _) => return Some(self.make_hit(&[step], HitKind::Def)),
                Binding::Variable => return Some(self.make_hit(&[step], HitKind::Variable)),
                _ => {
                    let found = self.resolve_binding(module, binding);
                    if found.is_none() && matches!(binding, Binding::Alias(_)) {
                        return Some(self.make_hit(&[step], HitKind::Variable));
                    }
                    return found.map(|hit| self.behind(hit, step));
                }
            }
        }
        let stars = self.tables[table].star_imports.clone();
        for &source in &stars {
            if let Some(hit) = self.resolve_name(source, name) {
                return Some(self.behind(hit, step));
            }
        }
        let sub = self.it.join(module, name);
        if self.index.exists(sub) {
            return Some(self.module_hit(sub));
        }
        if self.module_attributes.contains(&name) {
            return Some(self.make_hit(&[step], HitKind::Variable));
        }
        if let Some(&lazy) = self.tables[table].lazy.get(&name) {
            let found = self.resolve_binding(module, Binding::From(lazy, name));
            return found.map(|hit| self.behind(hit, step));
        }
        if let Some(&unknown) = stars.iter().find(|&&source| !self.index.exists(source)) {
            let missing = self.it.join(unknown, name);
            return Some(self.make_hit(
                &[
                    step,
                    Ent {
                        m: missing,
                        s: EMPTY,
                    },
                ],
                HitKind::Missing,
            ));
        }
        None
    }

    /// A value tree: a chain, a subscript's value, an attribute, a string annotation.
    pub fn resolve_value(&mut self, module: Sid, value: &V) -> Option<HitId> {
        match value {
            V::Chain(chain) => {
                let (hit, used, total) = self.resolve_chain(module, *chain);
                if used == total { hit } else { None }
            }
            V::Sub(inner, _) => self.resolve_value(module, inner),
            V::Str(_, Some(parsed)) => self.resolve_value(module, parsed),
            V::Attr(inner, attr) => {
                let base = self.resolve_value(module, inner)?;
                if self.kind(base) == HitKind::Module {
                    let module = self.origin(base).m;
                    return self.resolve_name(module, *attr);
                }
                None
            }
            _ => None,
        }
    }

    pub fn resolve_class(&mut self, module: Sid, value: &V) -> Option<Ent> {
        let hit = self.resolve_value(module, value)?;
        if self.kind(hit) == HitKind::Class {
            Some(self.origin(hit))
        } else {
            None
        }
    }

    /// A dotted path with no local scope: the longest module prefix, then names inside it.
    /// Returns the hit and the parts left over.
    pub fn resolve_dotted(&mut self, dotted: Sid) -> (Option<HitId>, Vec<Sid>) {
        let parts = self.it.segs(dotted);
        let Some((module, end)) = self.index.longest_module(&mut self.it, dotted) else {
            return (None, parts.to_vec());
        };
        let start = self.module_hit(module);
        let (hit, used) = self.descend(Some(start), &parts[end..]);
        (hit, parts[end + used..].to_vec())
    }

    // ── classes ──

    /// The file a module table reads a name's binding from: the `.pyi` for a name only the stub
    /// binds, else the module's own file.
    pub fn binding_file(&mut self, module: Sid, name: Sid) -> Option<u32> {
        let table = self.table(module)?;
        let table = &self.tables[table as usize];
        if table.stub_names.contains(&name) {
            table.stub_record
        } else {
            table.record
        }
    }

    /// The record that defines the def or class (`kind`) a module table binds a name to, and the
    /// def's index in it.
    pub fn located_def(&mut self, module: Sid, name: Sid, kind: HitKind) -> Option<(u32, u32)> {
        let idx = match (kind, self.binding_of(module, name)?) {
            (HitKind::Class, Binding::Class(idx, _)) | (HitKind::Def, Binding::Def(idx, _)) => idx,
            _ => return None,
        };
        Some((self.binding_file(module, name)?, idx))
    }

    /// The record that defines a class and the class's def index in it.
    pub fn located(&mut self, class: Ent) -> Option<(u32, u32)> {
        self.located_def(class.m, class.s, HitKind::Class)
    }

    pub fn bases(&mut self, class: Ent) -> Vec<Ent> {
        if let Some(found) = self.bases_cache.get(&class) {
            return found.clone();
        }
        let mut out = Vec::new();
        if let Some((file, idx)) = self.located(class) {
            let facts = self.facts.clone();
            for base in &facts.files[file as usize].defs[idx as usize].bases {
                if let Some(hit) = self.resolve_value(class.m, base)
                    && self.kind(hit) == HitKind::Class
                {
                    out.push(self.origin(hit));
                }
            }
        }
        self.bases_cache.insert(class, out.clone());
        out
    }

    /// C3 over resolved bases. A class that inherits itself sees only itself.
    pub fn mro(&mut self, class: Ent) -> Vec<Ent> {
        if let Some(found) = self.mro_cache.get(&class) {
            return found.clone();
        }
        self.mro_cache.insert(class, vec![class]);
        let bases = self.bases(class);
        if bases.len() <= 1 {
            let mut result = vec![class];
            if let Some(&base) = bases.first() {
                result.extend(self.mro(base));
            }
            self.mro_cache.insert(class, result.clone());
            return result;
        }
        let mut sequences: Vec<Vec<Ent>> = bases.iter().map(|&base| self.mro(base)).collect();
        sequences.push(bases.clone());
        let mut result = vec![class];
        result.extend(c3_merge(sequences));
        self.mro_cache.insert(class, result.clone());
        result
    }

    pub fn is_subclass(&mut self, class: Ent, qualified: Sid) -> bool {
        if !self.ancestor_cache.contains_key(&class) {
            let ancestors = self
                .mro(class)
                .into_iter()
                .map(|e| self.ent_sid(e))
                .collect();
            self.ancestor_cache.insert(class, ancestors);
        }
        self.ancestor_cache[&class].contains(&qualified)
    }
}
