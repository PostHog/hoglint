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

pub struct Link {
    pub core: Core,
    pub scopes: Vec<Scope>,
    pub scope_of_file: FxHashMap<u32, u32>,
    pub settings: Producing,
    pub edges: EdgeStore,
    pub index: EdgeIndex,
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
        let edges = std::mem::take(&mut producers.edges);
        let symbols = std::mem::take(&mut producers.symbols);
        timings.mark("producers", started);
        Ok(Link {
            core,
            scopes,
            scope_of_file,
            settings,
            edges,
            index: EdgeIndex::default(),
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
    /// (owner symbol, surface, names) where the owner is "module.<class or decorated attr>" and
    /// the names are the targets, `?<surface>` when unresolved, else the surface.
    pub fn class_facts(&self, kind: u8, owner_attr: &str) -> Vec<(String, String, Vec<String>)> {
        let core = &self.core;
        let Some(owner_key) = core.it.get(owner_attr) else {
            return Vec::new();
        };
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
            out.push((
                format!("{}.{}", core.it.s(module), core.it.s(owner)),
                surface,
                names,
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
