//! The native link: schema-v3 facts in, modules, resolution, class ancestry and the core edges
//! out, with the semantics of hoglint/core. A pure core: no files, no clock, no globals. The
//! Python binding (`hoglint-native`) and the `hoglint-link` debug binary are its shells.
//!
//! These sources live below `src/`, so the extractor build id (top-level `src/*.rs`) and the
//! fact cache keyed on it stay valid when only the link changes.

pub mod facts;
pub mod index;
pub mod intern;
pub mod packs;
pub mod paths;
pub mod produce;
pub mod queries;
pub mod resolve;
pub mod rules;
pub mod scope;
pub mod store;
pub mod surface;
pub mod trace;

/// Estimated heap bytes of a link's parts, for memory reports (`Link.heap()` in the binding).
/// The estimate counts allocated capacity and hash table buckets; allocator overhead and
/// fragmentation are not in it, so it stays below the process RSS.
pub mod heap {
    use super::facts::{Argument, FileFacts, V};
    use rustc_hash::{FxHashMap, FxHashSet};
    use std::mem::size_of;

    pub fn vec<T>(items: &Vec<T>) -> usize {
        items.capacity() * size_of::<T>()
    }

    pub fn text(s: &str) -> usize {
        s.len()
    }

    /// Buckets of a hash table that holds `capacity` items at hashbrown's 7/8 load factor.
    fn buckets(capacity: usize) -> usize {
        if capacity == 0 {
            return 0;
        }
        (capacity * 8 / 7).next_power_of_two()
    }

    pub fn map<K, T>(table: &FxHashMap<K, T>) -> usize {
        buckets(table.capacity()) * (size_of::<(K, T)>() + 1)
    }

    pub fn set<K>(table: &FxHashSet<K>) -> usize {
        buckets(table.capacity()) * (size_of::<K>() + 1)
    }

    pub fn value(v: &V) -> usize {
        match v {
            V::Null | V::Chain(_) | V::Opaque(_) | V::Other => 0,
            V::Str(_, parsed) => parsed.as_ref().map_or(0, |p| size_of::<V>() + value(p)),
            V::Call(inner, _, sole) => {
                size_of::<V>() + value(inner) + sole.as_deref().map_or(0, value)
            }
            V::Attr(inner, _) | V::Star(inner) => size_of::<V>() + value(inner),
            V::Sub(a, b) | V::Bin(_, a, b) => 2 * size_of::<V>() + value(a) + value(b),
            V::Tuple(items) | V::List(items) | V::Set(items) => values(items),
            V::Dict(pairs) => {
                let mut out = vec(pairs);
                for (key, val) in pairs {
                    out += key.as_ref().map_or(0, value) + value(val);
                }
                out
            }
        }
    }

    fn values(items: &Vec<V>) -> usize {
        vec(items) + items.iter().map(value).sum::<usize>()
    }

    /// The kinds `record` splits a record's heap into.
    pub const KINDS: [&str; 10] = [
        "facts.imports",
        "facts.defs",
        "facts.assigns",
        "facts.refs",
        "facts.strs",
        "facts.calls",
        "facts.uses",
        "facts.counts",
        "facts.tags",
        "facts.keys",
    ];

    /// Heap bytes of one record per kind (`KINDS`), its rows, values and text together.
    pub fn record(f: &FileFacts) -> [usize; 10] {
        let mut defs = vec(&f.defs);
        for d in &f.defs {
            defs += values(&d.bases) + values(&d.dec) + vec(&d.params);
            defs += d
                .params
                .iter()
                .map(|p| p.annotation.as_ref().map_or(0, value))
                .sum::<usize>();
            defs += d.ret.as_ref().map_or(0, value);
        }
        let mut assigns = vec(&f.assigns);
        for a in &f.assigns {
            assigns += values(&a.targets);
            assigns += a.value.as_ref().map_or(0, value) + a.ann.as_ref().map_or(0, value);
        }
        let refs = vec(&f.refs)
            + f.refs
                .iter()
                .map(|r| text(&r.chain) + text(&r.up))
                .sum::<usize>();
        let mut strs = vec(&f.strs);
        for r in &f.strs {
            strs += r.value.as_deref().map_or(0, text) + text(&r.up);
            if let Some(parts) = &r.parts {
                strs += vec(parts);
                strs += parts
                    .iter()
                    .map(|p| p.as_deref().map_or(0, text))
                    .sum::<usize>();
            }
        }
        let mut calls = vec(&f.calls);
        for c in &f.calls {
            calls += text(&c.callee);
            calls += match &c.argument {
                Argument::Literal(s) | Argument::Name(s) | Argument::Opaque(s) => text(s),
                Argument::Missing | Argument::Expr => 0,
            };
        }
        let mut keys = vec(&f.keys) + text(&f.path) + text(&f.status);
        keys += f.keys.iter().map(|(k, _)| text(k)).sum::<usize>();
        [
            vec(&f.imports),
            defs,
            assigns,
            refs,
            strs,
            calls,
            vec(&f.uses),
            vec(&f.callees) + vec(&f.names),
            vec(&f.tags),
            keys,
        ]
    }
}

use facts::FactSet;
use index::EdgeIndex;
use intern::{Interner, Sid};
use paths::{ModuleIndex, ModuleNamer, PathGlobs};
use produce::{EdgeStore, KIND_NAMES, NO_FILE, PackRow, Producers, Producing, Symbols};
use resolve::Core;
use rules::glob::ExportPaths;
use rustc_hash::{FxHashMap, FxHashSet};
use scope::{CONTEXT_NAMES, Scope, ctx};
use serde::Deserialize;
use serde_json::{Value as J, json};
use std::sync::Arc;
use std::time::Instant;

/// What the link needs to know about a repo (hoglint/core/config.py CoreConfig), plus the
/// inputs Python computes: the analyzed files, site-packages paths and builtin names.
#[derive(Debug, Clone, Deserialize, Default)]
pub struct LinkConfig {
    pub source_roots: Vec<String>,
    #[serde(default)]
    pub first_party: Vec<String>,
    #[serde(default)]
    pub test_paths: Vec<String>,
    #[serde(default)]
    pub migration_paths: Vec<String>,
    #[serde(default)]
    pub dynamic_loaders: Vec<String>,
    #[serde(default)]
    pub dataclass_decorators: Vec<String>,
    /// Those of `dataclass_decorators` that always make a frozen dataclass.
    #[serde(default)]
    pub frozen_dataclass_decorators: Vec<String>,
    /// Base classes whose subclasses' annotated class attributes are `__init__` fields too.
    #[serde(default)]
    pub dataclass_bases: Vec<String>,
    #[serde(default)]
    pub patch_callees: Vec<String>,
    /// Last names of other callees whose first argument is a dotted path.
    #[serde(default)]
    pub path_callees: Vec<String>,
    /// The files of exports whose signatures are part of their contract.
    #[serde(default)]
    pub contract_surfaces: Vec<SurfaceConfig>,
    /// The files of every export.
    #[serde(default)]
    pub export_surfaces: Vec<SurfaceConfig>,
    /// Python's `dir(builtins)` of the interpreter that runs the packs.
    #[serde(default)]
    pub builtins: Vec<String>,
    /// Third-party file -> its path inside its site-packages root.
    #[serde(default)]
    pub external: Vec<(String, String)>,
    /// Files that produce edges, in order. Every other record only resolves.
    pub analyzed: Vec<String>,
    /// Record what each analyzed file's edges read from other files, and every repo file's
    /// interface (`trace`). Off in a normal check.
    #[serde(default)]
    pub trace: bool,
}

/// One contract surface: an export's `within` and `paths` (hoglint/globs.py `ExportPaths`).
#[derive(Debug, Clone, Deserialize, Default)]
pub struct SurfaceConfig {
    pub within: String,
    pub paths: Vec<String>,
}

impl LinkConfig {
    pub fn from_json(text: &str) -> Result<LinkConfig, serde_json::Error> {
        serde_json::from_str(text)
    }
}

/// Seconds per link stage.
#[derive(Debug, Default, Clone)]
pub struct Timings {
    pub stages: Vec<(&'static str, f64)>,
}

impl Timings {
    fn mark(&mut self, name: &'static str, started: Instant) -> Instant {
        self.stages.push((name, started.elapsed().as_secs_f64()));
        Instant::now()
    }
}

/// One inherit or decorate fact for `SymbolFacts`: (owner symbol, surface, names, frozen).
pub type ClassFact = (String, String, Vec<String>, bool);

/// A pack edge to add (`Link::add_pack`), its strings interned and its record found.
pub struct NewPackRow {
    pub kind: Sid,
    pub producer: Sid,
    pub path: Sid,
    pub file: u32,
    pub module: Sid,
    pub scope: Sid,
    pub line: u32,
    pub col: u32,
    pub surface: Sid,
    pub targets: Vec<resolve::Ent>,
    pub res: scope::Res,
    pub ctx: u8,
    pub via: Vec<resolve::Ent>,
    pub use_: Sid,
    pub shape: Sid,
    pub shape_detail: Sid,
    pub attrs: Vec<(Sid, Sid)>,
}

pub struct Link {
    pub core: Core,
    pub scopes: Vec<Scope>,
    pub scope_of_file: FxHashMap<u32, u32>,
    pub settings: Producing,
    pub edges: EdgeStore,
    pub index: EdgeIndex,
    pub symbols: Symbols,
    /// What each export module hands out by import (surface.rs `HandOut`).
    pub hand_outs: FxHashMap<Sid, surface::HandOut>,
    /// `config.first_party` or, when empty, every repo top-level module.
    pub first_party: FxHashSet<Sid>,
    /// `config.dataclass_decorators` and `config.frozen_dataclass_decorators`, for the rules'
    /// symbol facts.
    pub dataclass_kinds: DataclassKinds,
    pub timings: Timings,
    /// Every repo file's interface by file id, when the link records traces.
    pub interfaces: Vec<(u32, Vec<(String, u64)>)>,
}

/// The decorators that make a dataclass (`make`), and those of them that always freeze it
/// (hoglint/core/config.py `DataclassDecorators`).
#[derive(Default)]
pub struct DataclassKinds {
    pub make: FxHashSet<String>,
    pub freeze: FxHashSet<String>,
}

/// (module, scope name) of the site of edge `id`, from the parts of a link, so a caller can
/// hold its index mutably at the same time.
fn edge_site(edges: &EdgeStore, scopes: &[Scope], module_scope: Sid, id: u32) -> (Sid, Sid) {
    match edges.pack_row(id) {
        Some(pack) => (pack.module, pack.scope),
        None => {
            let row = &edges.rows[id as usize];
            let scope = &scopes[row.owner as usize];
            (scope.module, scope.scope_name(row.scope, module_scope))
        }
    }
}

/// The parsed globs of each surface; an error names the config key.
fn surface_paths(key: &str, surfaces: &[SurfaceConfig]) -> Result<Vec<ExportPaths>, String> {
    surfaces
        .iter()
        .map(|surface| ExportPaths::new(&surface.within, &surface.paths))
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| format!("{key}: {e}"))
}

fn base_contexts(path: &str, tests: &PathGlobs, migrations: &PathGlobs) -> u8 {
    let mut found = if tests.matches(path) {
        ctx::TEST
    } else if migrations.matches(path) {
        ctx::MIGRATION
    } else {
        ctx::PROD
    };
    if path.ends_with(".pyi") {
        found |= ctx::TYPE_ONLY;
    }
    found
}

impl Link {
    pub fn build(config: &LinkConfig, mut it: Interner, facts: FactSet) -> Result<Link, String> {
        let mut timings = Timings::default();
        let started = Instant::now();
        let tests = PathGlobs::new(&config.test_paths).map_err(|e| format!("test_paths: {e}"))?;
        let migrations =
            PathGlobs::new(&config.migration_paths).map_err(|e| format!("migration_paths: {e}"))?;
        let external: FxHashMap<String, String> = config.external.iter().cloned().collect();
        let namer = ModuleNamer::new(&config.source_roots, external);
        let index = ModuleIndex::build(&mut it, &facts, &namer, &config.first_party);
        let first_party: FxHashSet<Sid> = if config.first_party.is_empty() {
            index.repo_heads.clone()
        } else {
            config
                .first_party
                .iter()
                .map(|head| it.intern(head))
                .collect()
        };
        let file_of: FxHashMap<&str, u32> = facts
            .files
            .iter()
            .enumerate()
            .map(|(i, f)| (&*f.path, i as u32))
            .collect();
        let analyzed: Vec<(u32, u8)> = config
            .analyzed
            .iter()
            .filter(|path| !namer.external.contains_key(path.as_str()))
            .filter_map(|path| {
                file_of
                    .get(path.as_str())
                    .map(|&file| (file, base_contexts(path, &tests, &migrations)))
            })
            .collect();
        drop(file_of);
        let contract_surfaces = surface_paths("contract_surfaces", &config.contract_surfaces)?;
        let export_surfaces = surface_paths("export_surfaces", &config.export_surfaces)?;
        let settings = Producing::new(&mut it, config, contract_surfaces, export_surfaces);
        let mut core = Core::new(it, Arc::new(facts), namer, index);
        core.rec.on = config.trace;
        let mut scopes: Vec<Scope> = analyzed
            .iter()
            .map(|&(file, base)| core.new_scope(file, base))
            .collect();
        let scope_of_file = scopes
            .iter()
            .enumerate()
            .map(|(i, s)| (s.file, i as u32))
            .collect();
        let started = timings.mark("index_scopes", started);
        let mut producers = Producers {
            core: &mut core,
            scopes: &mut scopes,
            settings: &settings,
            first_party: &first_party,
            edges: EdgeStore::default(),
            symbols: Symbols::default(),
            aliases: FxHashMap::default(),
            owner_of: FxHashMap::default(),
            expansions: FxHashMap::default(),
            fields: FxHashMap::default(),
            roots: FxHashMap::default(),
            hand_outs: FxHashMap::default(),
            dataclass_bases: FxHashMap::default(),
        };
        producers.run();
        let mut edges = std::mem::take(&mut producers.edges);
        edges.rows.shrink_to_fit();
        edges.via.shrink_to_fit();
        edges.attrs.shrink_to_fit();
        let symbols = std::mem::take(&mut producers.symbols);
        let hand_outs = std::mem::take(&mut producers.hand_outs);
        edges.kind_names = KIND_NAMES.iter().map(|kind| core.it.intern(kind)).collect();
        edges.producers = (0..KIND_NAMES.len() as u8)
            .map(|kind| core.it.intern(&Self::producer_name(kind)))
            .collect();
        let started = timings.mark("producers", started);
        let mut interfaces = Vec::new();
        if config.trace {
            interfaces = trace::interfaces(&core);
            timings.mark("interfaces", started);
        }
        Ok(Link {
            core,
            scopes,
            scope_of_file,
            settings,
            edges,
            index: EdgeIndex::default(),
            symbols,
            hand_outs,
            first_party,
            dataclass_kinds: DataclassKinds {
                make: config.dataclass_decorators.iter().cloned().collect(),
                freeze: config.frozen_dataclass_decorators.iter().cloned().collect(),
            },
            timings,
            interfaces,
        })
    }

    pub fn producer_name(kind: u8) -> String {
        format!("core-{}@1", KIND_NAMES[kind as usize])
    }

    pub fn context_names(mask: u8) -> Vec<&'static str> {
        let mut names: Vec<&'static str> = CONTEXT_NAMES
            .iter()
            .enumerate()
            .filter(|(bit, _)| mask & (1 << bit) != 0)
            .map(|(_, n)| *n)
            .collect();
        names.sort_unstable();
        names
    }

    /// The interned name of an edge kind; None for a kind no edge has.
    pub fn kind_sid(&self, kind: &str) -> Option<Sid> {
        self.core.it.get(kind)
    }

    /// Edges of `kind` from one source module, in graph order.
    pub fn ids_from(&mut self, kind: Sid, module: Sid) -> Vec<u32> {
        let (edges, scopes, module_scope) = (&self.edges, &self.scopes, self.settings.module_scope);
        let module_of = |id| edge_site(edges, scopes, module_scope, id).0;
        self.index
            .from_module(edges, module_of, kind, module)
            .to_vec()
    }

    /// Source modules of `kind`, in the order their first edge appears.
    pub fn source_modules(&mut self, kind: Sid) -> Vec<Sid> {
        let (edges, scopes, module_scope) = (&self.edges, &self.scopes, self.settings.module_scope);
        let module_of = |id| edge_site(edges, scopes, module_scope, id).0;
        self.index.source_modules(edges, module_of, kind).to_vec()
    }

    /// (module, scope name) of the site of edge `id`.
    pub fn site_of(&self, id: u32) -> (Sid, Sid) {
        edge_site(&self.edges, &self.scopes, self.settings.module_scope, id)
    }

    pub fn module_of_edge(&self, id: u32) -> Sid {
        self.site_of(id).0
    }

    /// The record of the site of edge `id`: its file id, or `path_key` of a pack edge whose
    /// path names no record. A key, not an index.
    pub fn file_key(&self, id: u32) -> u32 {
        match self.edges.pack_row(id) {
            Some(pack) if pack.file == NO_FILE => 0x8000_0000 | pack.path,
            Some(pack) => pack.file,
            None => self.edges.rows[id as usize].file,
        }
    }

    pub fn path_of_edge(&self, id: u32) -> &str {
        match self.edges.pack_row(id) {
            Some(pack) => self.core.it.s(pack.path),
            None => &self.core.facts.files[self.edges.rows[id as usize].file as usize].path,
        }
    }

    /// (line, column) of the site of edge `id`.
    pub fn line_col(&self, id: u32) -> (u32, u32) {
        match self.edges.pack_row(id) {
            Some(pack) => (pack.line, pack.col),
            None => {
                let row = &self.edges.rows[id as usize];
                (row.line, row.col)
            }
        }
    }

    /// Appends pack edges after every edge the link holds, and drops the indexes built so far.
    pub fn add_pack(&mut self, rows: Vec<NewPackRow>) {
        for new in rows {
            let targets = (self.edges.targets.len() as u32, new.targets.len() as u32);
            self.edges.targets.extend(new.targets.iter().copied());
            let via = (self.edges.via.len() as u32, new.via.len() as u32);
            self.edges.via.extend(new.via.iter().copied());
            let attrs = (self.edges.attrs.len() as u32, new.attrs.len() as u32);
            self.edges.attrs.extend(new.attrs.iter().copied());
            self.edges.pack.push(PackRow {
                kind: new.kind,
                producer: new.producer,
                path: new.path,
                file: new.file,
                module: new.module,
                scope: new.scope,
                line: new.line,
                col: new.col,
                surface: new.surface,
                targets,
                res: new.res,
                ctx: new.ctx,
                via,
                use_: new.use_,
                shape: new.shape,
                shape_detail: new.shape_detail,
                attrs,
            });
        }
        self.index.forget();
    }

    /// One edge in the parity tool's normalized form: every Edge field and attr.
    pub fn edge_json(&self, at: usize) -> J {
        let id = at as u32;
        let core = &self.core;
        let it = &core.it;
        let edges = &self.edges;
        let (module, scope) = self.site_of(id);
        let (line, col) = self.line_col(id);
        let targets: Vec<String> = edges
            .targets_of(id)
            .iter()
            .map(|&t| core.ent_text(t))
            .collect();
        let via: Vec<String> = edges
            .via_of_id(id)
            .iter()
            .map(|&e| core.ent_text(e))
            .collect();
        let attrs: Vec<J> = edges
            .attrs_with(id)
            .map(|&(k, v)| json!([it.s(k), it.s(v)]))
            .collect();
        let (shape, shape_detail) = edges.shape_of(id);
        json!([
            it.s(edges.kind_of(id)),
            self.path_of_edge(id),
            it.s(module),
            it.s(scope),
            line,
            col,
            it.s(edges.surface_of(id)),
            targets,
            edges.res_of(id).name(),
            Self::context_names(edges.ctx_of(id)),
            it.s(edges.producer_of(id)),
            via,
            it.s(edges.use_of(id)),
            it.s(shape),
            it.s(shape_detail),
            attrs,
        ])
    }

    /// `graph.symbols` in insertion order: [symbol, kind, path].
    pub fn symbols_json(&self) -> Vec<J> {
        let kinds = ["def", "async", "class"];
        self.symbols
            .order
            .iter()
            .map(|&(name, kind, file)| {
                json!([
                    self.core.it.s(name),
                    kinds[kind as usize],
                    &*self.core.facts.files[file as usize].path
                ])
            })
            .collect()
    }

    /// `graph.modules`: repo modules with a file, in the Python index's insertion order.
    pub fn modules_json(&self) -> Vec<J> {
        let index = &self.core.index;
        index
            .modules
            .iter()
            .filter_map(|module| {
                let file = index.path_of[module];
                (!index.site[file as usize]).then(|| {
                    json!([
                        self.core.it.s(*module),
                        &*self.core.facts.files[file as usize].path
                    ])
                })
            })
            .collect()
    }

    /// What `SymbolFacts` reads of the inherit or decorate edges, core and pack, in graph order:
    /// (owner symbol, surface, names, frozen) where the owner is "module.<class or decorated
    /// attr>", the names are the targets, `?<surface>` when unresolved, else the surface, and
    /// frozen says a decorator call passed `frozen=True`.
    pub fn class_facts(&mut self, kind: Sid, owner_attr: &str) -> Vec<ClassFact> {
        let Some(owner_key) = self.core.it.get(owner_attr) else {
            return Vec::new();
        };
        let frozen = (self.core.it.get("frozen"), self.core.it.get("1"));
        let ids = self.index.of_kind(&self.edges, kind).to_vec();
        let core = &self.core;
        let edges = &self.edges;
        let mut out = Vec::new();
        for id in ids {
            let Some(&(_, owner)) = edges.attrs_with(id).find(|(k, _)| *k == owner_key) else {
                continue;
            };
            if owner == intern::EMPTY {
                continue;
            }
            let module = self.module_of_edge(id);
            let surface = core.it.s(edges.surface_of(id)).to_string();
            let targets = edges.targets_of(id);
            let names = if edges.res_of(id) == scope::Res::Unresolved {
                vec![format!("?{surface}")]
            } else if !targets.is_empty() {
                targets
                    .iter()
                    .map(|&target| core.ent_text(target))
                    .collect()
            } else if surface.is_empty() {
                Vec::new()
            } else {
                vec![surface.clone()]
            };
            let is_frozen = match frozen {
                (Some(key), Some(one)) => edges.attrs_with(id).any(|&pair| pair == (key, one)),
                _ => false,
            };
            out.push((
                format!("{}.{}", core.it.s(module), core.it.s(owner)),
                surface,
                names,
                is_frozen,
            ));
        }
        out
    }

    /// Edge kinds in the order their first edge appears: core kinds, then the kinds packs added.
    pub fn kind_order(&self) -> Vec<Sid> {
        let mut out: Vec<Sid> = Vec::new();
        for row in &self.edges.rows {
            let kind = self.edges.kind_names[row.kind as usize];
            if !out.contains(&kind) {
                out.push(kind);
            }
        }
        for pack in &self.edges.pack {
            if !out.contains(&pack.kind) {
                out.push(pack.kind);
            }
        }
        out
    }

    pub fn first_party_heads(&self) -> Vec<&str> {
        let mut out: Vec<&str> = self
            .first_party
            .iter()
            .map(|&s| self.core.it.s(s))
            .collect();
        out.sort_unstable();
        out
    }
}

impl Link {
    /// Estimated heap bytes per part, in a fixed order.
    /// Drops the rows of the query kinds no pack declares (`FactSet::release_kinds`). False when
    /// another holder shares the fact set, so nothing is dropped.
    pub fn release_kinds(&mut self, kept: &[String]) -> bool {
        let Some(facts) = Arc::get_mut(&mut self.core.facts) else {
            return false;
        };
        facts.release_kinds(kept);
        true
    }

    /// Drops every record's fact rows (`FactSet::release_rows`), and with them what only
    /// resolution reads: the module tables, the resolver caches and the scopes' bindings. False
    /// when another holder shares the fact set, so nothing is dropped.
    pub fn release_facts(&mut self) -> bool {
        let Some(facts) = Arc::get_mut(&mut self.core.facts) else {
            return false;
        };
        facts.release_rows();
        self.core.release();
        for scope in &mut self.scopes {
            scope.release();
        }
        true
    }

    pub fn heap(&self) -> Vec<(&'static str, usize)> {
        let facts = &self.core.facts;
        let mut kinds = [0usize; 10];
        kinds[9] = heap::vec(&facts.files);
        for f in &facts.files {
            for (total, size) in kinds.iter_mut().zip(heap::record(f)) {
                *total += size;
            }
        }
        let mut out: Vec<(&'static str, usize)> = heap::KINDS.into_iter().zip(kinds).collect();
        out.extend(self.core.heap());
        let scopes = heap::vec(&self.scopes) + self.scopes.iter().map(|s| s.heap()).sum::<usize>();
        out.push(("scopes", scopes + heap::map(&self.scope_of_file)));
        let edges = &self.edges;
        out.push(("edges.rows", heap::vec(&edges.rows)));
        out.push((
            "edges.via_attrs",
            heap::vec(&edges.via) + heap::vec(&edges.attrs),
        ));
        out.push(("edges.index", self.index.heap()));
        let symbols = heap::vec(&self.symbols.order) + heap::map(&self.symbols.at);
        out.push(("symbols", symbols));
        out
    }
}
