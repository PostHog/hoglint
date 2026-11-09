//! The native link: schema-v3 facts in, modules, resolution, class ancestry and the core edges
//! out, with the semantics of hoglint/core. A pure core: no files, no clock, no globals. The
//! Python binding (`hoglint-native`) and the `hoglint-link` debug binary are its shells.
//!
//! These sources live below `src/`, so the extractor build id (top-level `src/*.rs`) and the
//! fact cache keyed on it stay valid when only the link changes.

pub mod facts;
pub mod groups;
pub mod index;
pub mod intern;
pub mod paths;
pub mod produce;
pub mod resolve;
pub mod scope;
pub mod store;

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
            V::Call(inner, _) | V::Attr(inner, _) | V::Star(inner) => size_of::<V>() + value(inner),
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

    /// (rows and values, row text) of one record.
    pub fn record(f: &FileFacts) -> (usize, usize) {
        let mut rows = vec(&f.imports) + vec(&f.defs) + vec(&f.assigns) + vec(&f.refs);
        rows += vec(&f.strs) + vec(&f.calls) + vec(&f.keys);
        let mut texts = text(&f.path) + text(&f.status);
        texts += f.keys.iter().map(|(k, _)| text(k)).sum::<usize>();
        for d in &f.defs {
            rows += values(&d.bases) + values(&d.dec) + vec(&d.params);
            rows += d
                .params
                .iter()
                .map(|p| p.annotation.as_ref().map_or(0, value))
                .sum::<usize>();
            rows += d.ret.as_ref().map_or(0, value);
        }
        for a in &f.assigns {
            rows += values(&a.targets);
            rows += a.value.as_ref().map_or(0, value) + a.ann.as_ref().map_or(0, value);
        }
        for r in &f.refs {
            texts += text(&r.chain) + text(&r.up);
        }
        for r in &f.strs {
            texts += r.value.as_deref().map_or(0, text) + text(&r.up);
            if let Some(parts) = &r.parts {
                rows += vec(parts);
                texts += parts
                    .iter()
                    .map(|p| p.as_deref().map_or(0, text))
                    .sum::<usize>();
            }
        }
        for c in &f.calls {
            texts += text(&c.callee);
            texts += match &c.argument {
                Argument::Literal(s) | Argument::Name(s) | Argument::Opaque(s) => text(s),
                Argument::Missing | Argument::Expr => 0,
            };
        }
        (rows, texts)
    }
}

use facts::FactSet;
use index::EdgeIndex;
use intern::{Interner, Sid};
use paths::{ModuleIndex, ModuleNamer, PathGlobs};
use produce::{EdgeStore, KIND_NAMES, Producers, Producing, Symbols};
use resolve::Core;
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
    #[serde(default)]
    pub patch_callees: Vec<String>,
    /// Python's `dir(builtins)` of the interpreter that runs the packs.
    #[serde(default)]
    pub builtins: Vec<String>,
    /// Third-party file -> its path inside its site-packages root.
    #[serde(default)]
    pub external: Vec<(String, String)>,
    /// Files that produce edges, in order. Every other record only resolves.
    pub analyzed: Vec<String>,
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

pub struct Link {
    pub core: Core,
    pub scopes: Vec<Scope>,
    pub scope_of_file: FxHashMap<u32, u32>,
    pub settings: Producing,
    pub edges: EdgeStore,
    pub index: EdgeIndex,
    pub endpoints: groups::EndpointTables,
    pub symbols: Symbols,
    /// `config.first_party` or, when empty, every repo top-level module.
    pub first_party: FxHashSet<Sid>,
    pub timings: Timings,
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
        let settings = Producing::new(
            &mut it,
            &config.builtins,
            &config.dynamic_loaders,
            &config.dataclass_decorators,
            &config.patch_callees,
        );
        let mut core = Core::new(it, Arc::new(facts), namer, index);
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
            edges: EdgeStore::default(),
            symbols: Symbols::default(),
        };
        producers.run();
        let mut edges = std::mem::take(&mut producers.edges);
        edges.rows.shrink_to_fit();
        edges.via.shrink_to_fit();
        edges.attrs.shrink_to_fit();
        let symbols = std::mem::take(&mut producers.symbols);
        timings.mark("producers", started);
        Ok(Link {
            core,
            scopes,
            scope_of_file,
            settings,
            edges,
            index: EdgeIndex::default(),
            endpoints: groups::EndpointTables::default(),
            symbols,
            first_party,
            timings,
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

    /// One edge in the parity tool's normalized form: every Edge field and attr.
    pub fn edge_json(&self, at: usize) -> J {
        let row = &self.edges.rows[at];
        let core = &self.core;
        let it = &core.it;
        let scope = &self.scopes[row.owner as usize];
        let path = &core.facts.files[row.file as usize].path;
        let scope_name = scope.scope_name(row.scope, self.settings.module_scope);
        let targets: Vec<String> = row.target.iter().map(|&t| core.ent_text(t)).collect();
        let via: Vec<String> = self
            .edges
            .via_of(row)
            .iter()
            .map(|&e| core.ent_text(e))
            .collect();
        let attrs: Vec<J> = self
            .edges
            .attrs_of(row)
            .iter()
            .map(|&(k, v)| json!([it.s(k), it.s(v)]))
            .collect();
        json!([
            KIND_NAMES[row.kind as usize],
            &**path,
            it.s(scope.module),
            it.s(scope_name),
            row.line,
            row.col,
            it.s(row.surface),
            targets,
            row.res.name(),
            Self::context_names(row.ctx),
            Self::producer_name(row.kind),
            via,
            it.s(row.use_),
            "",
            "",
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

    /// What `SymbolFacts` reads of the core's inherit or decorate edges, in graph order:
    /// (owner symbol, surface, names, frozen) where the owner is "module.<class or decorated
    /// attr>", the names are the targets, `?<surface>` when unresolved, else the surface, and
    /// frozen says a decorator call passed `frozen=True`.
    pub fn class_facts(&self, kind: u8, owner_attr: &str) -> Vec<ClassFact> {
        let core = &self.core;
        let Some(owner_key) = core.it.get(owner_attr) else {
            return Vec::new();
        };
        let frozen = (core.it.get("frozen"), core.it.get("1"));
        let mut out = Vec::new();
        for row in self.edges.rows.iter().filter(|row| row.kind == kind) {
            let Some(&(_, owner)) = self
                .edges
                .attrs_of(row)
                .iter()
                .find(|(k, _)| *k == owner_key)
            else {
                continue;
            };
            if owner == intern::EMPTY {
                continue;
            }
            let module = self.scopes[row.owner as usize].module;
            let surface = core.it.s(row.surface).to_string();
            let names = if row.res == scope::Res::Unresolved {
                vec![format!("?{surface}")]
            } else if let Some(target) = row.target {
                vec![core.ent_text(target)]
            } else if surface.is_empty() {
                Vec::new()
            } else {
                vec![surface.clone()]
            };
            let is_frozen = match frozen {
                (Some(key), Some(one)) => self.edges.attrs_of(row).contains(&(key, one)),
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

    /// Core edge kinds in the order their first edge appears.
    pub fn kind_order(&self) -> Vec<u8> {
        let mut out: Vec<u8> = Vec::new();
        for row in &self.edges.rows {
            if !out.contains(&row.kind) {
                out.push(row.kind);
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
    pub fn heap(&self) -> Vec<(&'static str, usize)> {
        let facts = &self.core.facts;
        let (mut rows, mut texts) = (heap::vec(&facts.files), 0);
        for f in &facts.files {
            let (r, t) = heap::record(f);
            rows += r;
            texts += t;
        }
        let mut out = vec![("facts.rows", rows), ("facts.row_text", texts)];
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
        out.push(("edges.endpoint_tables", self.endpoints.heap()));
        let symbols = heap::vec(&self.symbols.order) + heap::map(&self.symbols.at);
        out.push(("symbols", symbols));
        out
    }
}
