//! Module labels (hoglint/labels.py `Labeler`): the values assignments and zones give each
//! graph module, its registry and the exports that cover its path. Each module's labels are
//! computed once per run; a field's domain fills the modules no source labels.

use super::conflicts::{Conflict, conflict_key};
use super::env::Cx;
use super::glob::{Captures, ExportPaths, Glob, capture, fill};
use super::plan::{
    AssignPlan, ExportPlan, Plan, RegistryPlan, SetValue, SymbolMatchPlan, ValuePlan, ZonesPlan,
};
use super::selector::Node;
use super::value::{Value, Zone};
use super::{NO_PLACE, Rules, place_of};
use crate::link::Link;
use rustc_hash::{FxHashMap, FxHashSet};

/// A field's type as labels read it.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum FieldKind {
    Set,
    Zone,
    Other,
}

pub struct Domain {
    pub everywhere: bool,
    pub globs: Vec<Glob>,
    pub selector: Option<Node>,
}

pub struct Field {
    pub name: String,
    pub kind: FieldKind,
    pub on_symbol: bool,
    pub provided: bool,
    pub domain: Option<Domain>,
}

/// One label value, or what a field reads where no source sets it.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub enum Cell {
    Unknown,
    None,
    Text(Box<str>),
    Bool(bool),
    Set(Vec<Box<str>>),
    /// A zone by its index in `Zones::infos`.
    Zone(u32),
}

/// One source's value for a field (`labels.Candidate`), its templates filled.
#[derive(Clone, PartialEq, Eq, Debug)]
pub enum Candidate {
    Text(String),
    Bool(bool),
    Items(Vec<String>),
    /// `str()` and `repr()` of any other value.
    Other(String, String),
}

impl Candidate {
    /// What a conflict compares: two candidates with different keys disagree (`repr`).
    fn key(&self) -> (u8, &str) {
        match self {
            Candidate::Text(text) => (0, text),
            Candidate::Bool(true) => (1, "True"),
            Candidate::Bool(false) => (1, "False"),
            Candidate::Items(_) => (2, ""),
            Candidate::Other(_, repr) => (3, repr),
        }
    }

    /// `str()` of the value.
    fn text(&self) -> String {
        match self {
            Candidate::Text(text) | Candidate::Other(text, _) => text.clone(),
            Candidate::Bool(flag) => if *flag { "True" } else { "False" }.to_string(),
            Candidate::Items(items) => format!("{items:?}"),
        }
    }
}

fn compile_globs(patterns: &[String]) -> Result<Vec<Glob>, String> {
    patterns.iter().map(|pattern| Glob::new(pattern)).collect()
}

/// A module or symbol assignment, compiled.
pub struct Assign {
    matches: Vec<Glob>,
    excluded: Vec<Glob>,
    except_values: Vec<(String, Vec<String>)>,
    only_values: Vec<(String, Vec<String>)>,
    /// (field index, value) for each declared field it sets.
    sets: Vec<(usize, SetValue)>,
    pub overrides: bool,
    /// For a symbol assignment: the symbols it matches.
    pub symbols: Option<SymbolMatchPlan>,
}

impl Assign {
    fn new(plan: &AssignPlan, fields: &[Field]) -> Result<Assign, String> {
        let sets = plan
            .sets
            .iter()
            .filter_map(|(name, value)| {
                fields
                    .iter()
                    .position(|field| field.name == *name)
                    .map(|at| (at, value.clone()))
            })
            .collect();
        Ok(Assign {
            matches: compile_globs(&plan.matches)?,
            excluded: compile_globs(&plan.except_paths)?,
            except_values: plan.except_values.clone(),
            only_values: plan.only_values.clone(),
            sets,
            overrides: plan.overrides,
            symbols: plan.symbols.clone(),
        })
    }

    /// The captures of the first `match` glob that matches and passes `except` and `only`
    /// (`PathAssignment.candidates`).
    fn captures(&self, path: &str) -> Option<Captures> {
        if self.excluded.iter().any(|glob| glob.is_match(path)) {
            return None;
        }
        for glob in &self.matches {
            let Some(captures) = glob.captures(path) else {
                continue;
            };
            let excepted = self.except_values.iter().any(|(name, values)| {
                capture(&captures, name).is_some_and(|value| values.iter().any(|v| v == value))
            });
            if excepted {
                continue;
            }
            let left_out = self.only_values.iter().any(|(name, values)| {
                capture(&captures, name).is_some_and(|value| !values.iter().any(|v| v == value))
            });
            if left_out {
                continue;
            }
            return Some(captures);
        }
        None
    }

    /// The values the assignment sets on one field.
    pub fn sets_of(&self, field: usize) -> impl Iterator<Item = &SetValue> {
        self.sets
            .iter()
            .filter(move |(at, _)| *at == field)
            .map(|(_, value)| value)
    }

    /// Whether a symbol assignment's `match` globs let a symbol at `path` through.
    pub fn allows(&self, path: Option<&str>) -> bool {
        self.matches.is_empty()
            || path.is_some_and(|path| self.matches.iter().any(|glob| glob.is_match(path)))
    }

    /// (field, candidate) for every field the assignment sets, templates filled from `captures`.
    pub fn candidates(
        &self,
        captures: &[(String, String)],
        fields: &[Field],
    ) -> Vec<(usize, Candidate, bool)> {
        self.sets
            .iter()
            .map(|(field, value)| {
                let candidate = match value {
                    SetValue::Text(text) => Candidate::Text(fill(text, captures)),
                    SetValue::Bool(flag) => Candidate::Bool(*flag),
                    SetValue::Items(items) => Candidate::Items(items.clone()),
                    SetValue::Other(text, repr) => Candidate::Other(text.clone(), repr.clone()),
                };
                let candidate = match (fields[*field].kind, candidate) {
                    (FieldKind::Set, Candidate::Text(text)) => Candidate::Items(vec![text]),
                    (FieldKind::Set, Candidate::Bool(flag)) => {
                        Candidate::Items(vec![Candidate::Bool(flag).text()])
                    }
                    (FieldKind::Set, Candidate::Other(text, _)) => Candidate::Items(vec![text]),
                    (_, candidate) => candidate,
                };
                (*field, candidate, self.overrides)
            })
            .collect()
    }
}

pub struct ZoneDef {
    name: String,
    parent: String,
    globs: Vec<Glob>,
    overrides: bool,
}

/// [zones]: the zone tree, and where each zone applies.
pub struct Zones {
    within: Vec<(String, Glob)>,
    default: String,
    defs: Vec<ZoneDef>,
    /// Every zone name a label holds, with its ancestors.
    pub infos: Vec<Zone>,
    at: FxHashMap<String, u32>,
}

impl Zones {
    fn new(plan: &ZonesPlan) -> Result<Zones, String> {
        let within = plan
            .within
            .iter()
            .map(|root| Ok((root.clone(), Glob::new(&format!("{root}/**"))?)))
            .collect::<Result<Vec<_>, String>>()?;
        let defs = plan
            .zones
            .iter()
            .map(|zone| {
                Ok(ZoneDef {
                    name: zone.name.clone(),
                    parent: zone.parent.clone(),
                    globs: compile_globs(&zone.paths)?,
                    overrides: zone.overrides,
                })
            })
            .collect::<Result<Vec<_>, String>>()?;
        Ok(Zones {
            within,
            default: plan.default.clone(),
            defs,
            infos: Vec::new(),
            at: FxHashMap::default(),
        })
    }

    /// The zone and its ancestors, nearest first (`ZoneTable.chain`).
    fn chain(&self, name: &str) -> Vec<String> {
        let mut out: Vec<String> = Vec::new();
        let mut current = name.to_string();
        while !current.is_empty() && !out.contains(&current) {
            out.push(current.clone());
            current = self
                .defs
                .iter()
                .find(|zone| zone.name == current)
                .map_or(String::new(), |zone| zone.parent.clone());
        }
        out
    }

    /// The index of a zone name a label already holds.
    pub fn code_of(&self, name: &str) -> Option<u32> {
        self.at.get(name).copied()
    }

    pub fn code(&mut self, name: &str) -> u32 {
        if let Some(&at) = self.at.get(name) {
            return at;
        }
        let at = self.infos.len() as u32;
        self.infos.push(Zone {
            name: name.to_string(),
            chain: self.chain(name),
        });
        self.at.insert(name.to_string(), at);
        at
    }

    /// The path's zone (`Labeler.zone_candidate`): None outside every `within`, and Err when
    /// several leaf zones match and none overrides, a conflict.
    fn of(&self, path: &str) -> Result<Option<&str>, ()> {
        for (within, inside) in &self.within {
            let Some(root) = inside.captures(path) else {
                continue;
            };
            let relative = path.get(fill(within, &root).len() + 1..).unwrap_or("");
            let matched: Vec<&ZoneDef> = self
                .defs
                .iter()
                .filter(|zone| zone.globs.iter().any(|glob| glob.is_match(relative)))
                .collect();
            let ancestors: Vec<String> = matched
                .iter()
                .flat_map(|zone| self.chain(&zone.name).into_iter().skip(1))
                .collect();
            let leaves: Vec<&ZoneDef> = matched
                .into_iter()
                .filter(|zone| !ancestors.contains(&zone.name))
                .collect();
            return match leaves.as_slice() {
                [] => Ok(Some(&self.default)),
                [one] => Ok(Some(&one.name)),
                several => {
                    let overriding: Vec<&&ZoneDef> =
                        several.iter().filter(|zone| zone.overrides).collect();
                    match overriding.as_slice() {
                        [one] => Ok(Some(&one.name)),
                        _ => Err(()),
                    }
                }
            };
        }
        Ok(None)
    }
}

pub struct Registry {
    pub name: String,
    glob: Glob,
    loader_module: String,
    pub lifecycle: String,
}

impl Registry {
    fn new(plan: &RegistryPlan) -> Result<Registry, String> {
        Ok(Registry {
            name: plan.name.clone(),
            glob: Glob::new(&plan.module)?,
            loader_module: plan
                .loader
                .rsplit_once('.')
                .map_or(String::new(), |(head, _)| head.to_string()),
            lifecycle: plan.lifecycle.clone(),
        })
    }
}

pub struct Export {
    paths: ExportPaths,
    pub subclass_of: Vec<String>,
    pub symbols: Vec<String>,
    pub audience: Option<Node>,
}

impl Export {
    pub fn new(plan: &ExportPlan, audience: Option<Node>) -> Result<Export, String> {
        Ok(Export {
            paths: ExportPaths::new(&plan.within, &plan.paths)?,
            subclass_of: plan.subclass_of.clone(),
            symbols: plan.symbols.clone(),
            audience,
        })
    }

    /// Whether the path lies inside the export's `within` and `paths` (`Labeler.export_covers`).
    fn covers(&self, path: &str) -> bool {
        self.paths.covers(path)
    }
}

/// A file name no glob spells (`labels.NAMESPACE_PROBE`): a namespace directory is labelled by
/// the path of a module file directly inside it.
const NAMESPACE_PROBE: &str = "\0.py";

/// A directory without `__init__.py` that holds graph modules (`labels.NamespaceDirectories`):
/// an edge can name one, and the path of a file directly inside it labels it.
pub struct Namespace {
    pub path: String,
    /// Per field, the label that path's assignments and zones give; None where none does and for
    /// symbol and provider fields; unknown where two sources disagree or a module file directly
    /// inside the directory reads the field otherwise.
    pub labels: Vec<Option<Cell>>,
}

/// The directories of a module's path, and where the module's first part sits among them, when
/// the path ends in the module's own parts (`a/b/c.py` or `a/b/c/__init__.py` for `a.b.c`)
/// (`NamespaceDirectories.directories`).
fn module_directories<'p>(module: &str, path: &'p str) -> Option<(Vec<&'p str>, usize)> {
    let (dir, name) = path.rsplit_once('/').unwrap_or(("", path));
    // `PurePosixPath.stem`: a suffix is the text from the last dot, which is neither first nor last.
    let stem = match name.rfind('.') {
        Some(dot) if dot > 0 && dot + 1 < name.len() => &name[..dot],
        _ => name,
    };
    let mut directories: Vec<&str> = dir.split('/').filter(|part| !part.is_empty()).collect();
    if stem != "__init__" {
        directories.push(stem);
    }
    let parts: Vec<&str> = module.split('.').collect();
    if directories.len() < parts.len()
        || directories[directories.len() - parts.len()..] != parts[..]
    {
        return None;
    }
    let root = directories.len() - parts.len();
    Some((directories, root))
}

/// The namespace directories of the graph's modules, by dotted name; a name two modules place
/// in two directories is left out.
fn namespace_paths(paths: &[&str], modules: &[&str]) -> Vec<(String, String)> {
    let names: FxHashSet<&str> = modules.iter().copied().collect();
    let mut found: FxHashMap<String, String> = FxHashMap::default();
    let mut clashes: FxHashSet<String> = FxHashSet::default();
    for (path, module) in paths.iter().zip(modules) {
        let Some((directories, root)) = module_directories(module, path) else {
            continue;
        };
        // Each dotted parent is a prefix of the module: most are normal packages, so the
        // membership test comes before any text is built.
        for (end, (dot, _)) in module.match_indices('.').enumerate() {
            let name = &module[..dot];
            if names.contains(name) || clashes.contains(name) {
                continue;
            }
            let directory = directories[..root + end + 1].join("/");
            match found.get(name) {
                Some(seen) if *seen != directory => {
                    found.remove(name);
                    clashes.insert(name.to_string());
                }
                Some(_) => {}
                None => {
                    found.insert(name.to_string(), directory);
                }
            }
        }
    }
    let mut out: Vec<(String, String)> = found.into_iter().collect();
    out.sort();
    out
}

/// The label tables of one run: per graph module, each field's label (None where no source
/// sets it), its registry and the exports covering its path.
pub struct Labels {
    pub fields: Vec<Field>,
    pub assigns: Vec<Assign>,
    pub zones: Zones,
    pub registries: Vec<Registry>,
    /// `labels[field][module]`: the label sources give the module.
    pub labels: Vec<Vec<Option<Cell>>>,
    pub registry_of: Vec<Option<u16>>,
    pub exports_of: Vec<u64>,
    /// Fields whose provider failed.
    pub failed: Vec<bool>,
    /// Fields the plan sent a provider column for.
    pub columns: Vec<bool>,
    /// (module, field) where two sources set a module's field apart, with no field where two
    /// leaf zones claim its path: config errors the Python labeler words with their sources.
    pub conflicts: Vec<(u32, Option<usize>)>,
    /// Per module, whether a Python provider's labels of it disagree with another source: met
    /// where something reads a provider column of the module, as `Labeler.label_provided` is.
    pub provider_conflicts: Vec<bool>,
    /// The namespace directories by dotted name.
    pub namespaces: FxHashMap<Box<str>, Namespace>,
}

/// The most exports a run reads: one bit each.
const MAX_EXPORTS: usize = 64;

impl Labels {
    pub fn new(
        plan: &Plan,
        fields: Vec<Field>,
        paths: &[&str],
        modules: &[&str],
        exports: &[Export],
    ) -> Result<Labels, String> {
        if exports.len() > MAX_EXPORTS {
            return Err(format!(
                "{} exports; the evaluator reads at most {MAX_EXPORTS}",
                exports.len()
            ));
        }
        let assigns = plan
            .assigns
            .iter()
            .map(|assign| Assign::new(assign, &fields))
            .collect::<Result<Vec<_>, String>>()?;
        let registries = plan
            .registries
            .iter()
            .map(Registry::new)
            .collect::<Result<Vec<_>, String>>()?;
        let failed: Vec<bool> = fields
            .iter()
            .map(|field| plan.failed.contains(&field.name))
            .collect();
        let mut out = Labels {
            labels: vec![Vec::with_capacity(paths.len()); fields.len()],
            fields,
            assigns,
            zones: Zones::new(&plan.zones)?,
            registries,
            registry_of: Vec::with_capacity(paths.len()),
            exports_of: Vec::with_capacity(paths.len()),
            columns: vec![false; failed.len()],
            failed,
            conflicts: Vec::new(),
            provider_conflicts: vec![false; paths.len()],
            namespaces: FxHashMap::default(),
        };
        for (at, (path, module)) in paths.iter().zip(modules).enumerate() {
            out.label_module(at as u32, path, module, exports);
        }
        let directories = namespace_paths(paths, modules);
        let mut inside: FxHashMap<&str, Vec<u32>> = FxHashMap::default();
        for (_, directory) in &directories {
            inside.insert(directory.as_str(), Vec::new());
        }
        for (at, path) in paths.iter().enumerate() {
            let parent = path.rsplit_once('/').map_or("", |(parent, _)| parent);
            if let Some(found) = inside.get_mut(parent) {
                found.push(at as u32);
            }
        }
        for (name, directory) in &directories {
            let path = format!("{directory}/{NAMESPACE_PROBE}");
            let labels = out.label_namespace(&path, &inside[directory.as_str()]);
            out.namespaces
                .insert(name.as_str().into(), Namespace { path, labels });
        }
        out.fill_provided(plan)?;
        Ok(out)
    }

    /// What assignments and zones say about one path, and whether two leaf zones claim it.
    fn path_candidates(&self, path: &str) -> (Vec<Vec<(Candidate, bool)>>, bool) {
        let mut collected: Vec<Vec<(Candidate, bool)>> = vec![Vec::new(); self.fields.len()];
        for assign in self
            .assigns
            .iter()
            .filter(|assign| assign.symbols.is_none())
        {
            let Some(captures) = assign.captures(path) else {
                continue;
            };
            for (field, candidate, overrides) in assign.candidates(&captures, &self.fields) {
                collected[field].push((candidate, overrides));
            }
        }
        let mut zone_conflict = false;
        if self
            .fields
            .iter()
            .any(|field| field.kind == FieldKind::Zone)
        {
            match self.zones.of(path) {
                Ok(Some(zone)) => {
                    let zone = zone.to_string();
                    for (at, field) in self.fields.iter().enumerate() {
                        if field.kind == FieldKind::Zone {
                            collected[at].push((Candidate::Text(zone.clone()), false));
                        }
                    }
                }
                Ok(None) => {}
                Err(()) => zone_conflict = true,
            }
        }
        (collected, zone_conflict)
    }

    /// A namespace directory's labels (`Labeler.label_namespace`): those of `path`, a file
    /// directly inside it. A field two sources set apart, or that one of the module files
    /// directly inside (`inside`) reads otherwise, is unknown; nothing is recorded as a conflict.
    fn label_namespace(&mut self, path: &str, inside: &[u32]) -> Vec<Option<Cell>> {
        let (collected, zone_conflict) = self.path_candidates(path);
        let mut out = Vec::with_capacity(collected.len());
        for (at, candidates) in collected.iter().enumerate() {
            let field = &self.fields[at];
            if field.provided || field.on_symbol {
                out.push(None);
                continue;
            }
            let zone = field.kind == FieldKind::Zone;
            let label = match self.resolve(at, candidates) {
                Ok(_) if zone && zone_conflict => Some(Cell::Unknown),
                Ok(label) => label,
                Err(()) => Some(Cell::Unknown),
            };
            let apart = inside
                .iter()
                .any(|&module| self.labels[at][module as usize] != label);
            out.push(if apart { Some(Cell::Unknown) } else { label });
        }
        out
    }

    /// One module's labels (`Labeler.label_module`), its registry and exports.
    fn label_module(&mut self, module_at: u32, path: &str, module: &str, exports: &[Export]) {
        let (collected, zone_conflict) = self.path_candidates(path);
        if zone_conflict {
            self.conflicts.push((module_at, None));
        }
        for (at, candidates) in collected.iter().enumerate() {
            let field = &self.fields[at];
            let (provided, on_symbol) = (field.provided, field.on_symbol);
            let label = if provided {
                None
            } else {
                match self.resolve(at, candidates) {
                    Ok(label) => label,
                    Err(()) => {
                        self.conflicts.push((module_at, Some(at)));
                        None
                    }
                }
            };
            // A module assignment that sets a symbol field labels no symbol, but its
            // conflicts are config errors all the same.
            self.labels[at].push(if on_symbol { None } else { label });
        }
        let registry = self
            .registries
            .iter()
            .position(|registry| registry.glob.is_match(path) || registry.loader_module == module)
            .map(|at| at as u16);
        self.registry_of.push(registry);
        let mut bits = 0u64;
        for (at, export) in exports.iter().enumerate() {
            if export.covers(path) {
                bits |= 1 << at;
            }
        }
        self.exports_of.push(bits);
    }

    /// The label of one field from its candidates (`Labeler.resolve_candidates`): a set field
    /// unions them; otherwise the overriding ones win, and two that differ are a conflict.
    fn resolve(
        &mut self,
        field: usize,
        candidates: &[(Candidate, bool)],
    ) -> Result<Option<Cell>, ()> {
        if candidates.is_empty() {
            return Ok(None);
        }
        let kind = self.fields[field].kind;
        if kind == FieldKind::Set {
            let mut items: Vec<Box<str>> = Vec::new();
            for (candidate, _) in candidates {
                match candidate {
                    Candidate::Items(found) => {
                        items.extend(found.iter().map(|item| item.as_str().into()))
                    }
                    other => items.push(other.text().into()),
                }
            }
            items.sort();
            items.dedup();
            return Ok(Some(Cell::Set(items)));
        }
        let overriding: Vec<&Candidate> = candidates
            .iter()
            .filter(|(_, overrides)| *overrides)
            .map(|(c, _)| c)
            .collect();
        let pool: Vec<&Candidate> = if overriding.is_empty() {
            candidates.iter().map(|(c, _)| c).collect()
        } else {
            overriding
        };
        if pool
            .iter()
            .any(|candidate| candidate.key() != pool[0].key())
        {
            return Err(());
        }
        Ok(Some(match (kind, pool[0]) {
            (FieldKind::Zone, candidate) => Cell::Zone(self.zones.code(&candidate.text())),
            (_, Candidate::Bool(flag)) => Cell::Bool(*flag),
            (_, candidate) => Cell::Text(candidate.text().into()),
        }))
    }

    /// The columns a Python provider set, from the plan.
    fn fill_provided(&mut self, plan: &Plan) -> Result<(), String> {
        for provided in &plan.provided {
            let Some(at) = self
                .fields
                .iter()
                .position(|field| field.name == provided.field)
            else {
                return Err(format!(
                    "a provided column for {}, which no field declares",
                    provided.field
                ));
            };
            if provided.labels.len() != self.registry_of.len() {
                return Err(format!(
                    "the provided column of {} has a value per module of another graph",
                    provided.field
                ));
            }
            self.labels[at] = provided
                .labels
                .iter()
                .map(|value| self.cell_of(value))
                .collect();
            self.columns[at] = true;
        }
        for &at in &plan.provider_conflicts {
            let Some(slot) = self.provider_conflicts.get_mut(at as usize) else {
                return Err(format!(
                    "a provider conflict on module {at} of another graph"
                ));
            };
            *slot = true;
        }
        Ok(())
    }

    /// A value from the plan as a label: `none` is no label.
    pub fn cell_of(&mut self, value: &ValuePlan) -> Option<Cell> {
        Some(match value {
            ValuePlan::None => return None,
            ValuePlan::Text(text) => Cell::Text(text.as_str().into()),
            ValuePlan::Bool(flag) => Cell::Bool(*flag),
            ValuePlan::Set(items) => {
                Cell::Set(items.iter().map(|item| item.as_str().into()).collect())
            }
            ValuePlan::Zone(name) => Cell::Zone(self.zones.code(name)),
        })
    }
}

impl Rules {
    /// (field, module) of every provider-set module field on a module whose provider labels
    /// are set two ways: reading any of them reads all of the module's (`Labeler.label_provided`).
    pub(super) fn provider_reads(&self) -> FxHashSet<(usize, u32)> {
        let labels = &self.labels;
        let mut out = FxHashSet::default();
        for (at, _) in labels
            .provider_conflicts
            .iter()
            .enumerate()
            .filter(|(_, set)| **set)
        {
            for (field, spec) in labels.fields.iter().enumerate() {
                if spec.provided && !spec.on_symbol {
                    out.insert((field, at as u32));
                }
            }
        }
        out
    }

    /// `values` of every module field (`Labeler.compute_value`): the label, else unknown where
    /// the provider failed, else what the domain says. A value whose domain read provider labels
    /// that are set two ways is tainted: reading it meets that conflict, as in Python, where the
    /// domain is read only when the value is.
    pub(super) fn module_values(&mut self, link: &Link) -> Vec<Vec<Cell>> {
        let cx = Cx { link, rules: self };
        let count = self.modules.len();
        let mut out = Vec::with_capacity(self.labels.fields.len());
        let mut tainted = Vec::new();
        for (field, spec) in self.labels.fields.iter().enumerate() {
            if spec.on_symbol {
                out.push(Vec::new());
                continue;
            }
            let column = (0..count as u32)
                .map(|at| match &self.labels.labels[field][at as usize] {
                    Some(label) => label.clone(),
                    None if self.labels.failed[field] => Cell::Unknown,
                    None => {
                        let module = Some(self.modules[at as usize]);
                        let (value, read) = cx.domain_value_read(field, module, Some(at), None);
                        if read {
                            tainted.push((field, at));
                        }
                        match value {
                            Value::Unknown => Cell::Unknown,
                            _ => Cell::None,
                        }
                    }
                })
                .collect();
            out.push(column);
        }
        self.tainted.extend(tainted);
        out
    }

    /// The label symbol assignments give a symbol's field (`Labeler.label_symbol`), as a value.
    /// Their values are fixed: a symbol assignment fills no template.
    pub fn symbol_label<'a>(
        &'a self,
        link: &Link,
        symbol: &str,
        field: usize,
    ) -> Option<Value<'a>> {
        if self.symbol_assigns.is_empty() || symbol.is_empty() {
            return None;
        }
        let cx = Cx { link, rules: self };
        let path = cx
            .module_of_symbol(symbol)
            .map(|(_, at)| self.module_path(link, at));
        let mut candidates: Vec<(&'a SetValue, bool)> = Vec::new();
        for &at in &self.symbol_assigns {
            let assign = &self.labels.assigns[at];
            if self.symbol_matches(link, assign, symbol, path) {
                candidates.extend(assign.sets_of(field).map(|value| (value, assign.overrides)));
            }
        }
        if candidates.is_empty() {
            return None;
        }
        let spec = &self.labels.fields[field];
        if spec.kind == FieldKind::Set {
            let mut items: Vec<&'a str> = Vec::new();
            for (value, _) in &candidates {
                match value {
                    SetValue::Items(found) => items.extend(found.iter().map(String::as_str)),
                    SetValue::Text(text) | SetValue::Other(text, _) => items.push(text),
                    SetValue::Bool(flag) => items.push(if *flag { "True" } else { "False" }),
                }
            }
            items.sort_unstable();
            items.dedup();
            return Some(Value::Set(items));
        }
        let overriding: Vec<&SetValue> = candidates
            .iter()
            .filter(|(_, overrides)| *overrides)
            .map(|(value, _)| *value)
            .collect();
        let pool: Vec<&SetValue> = if overriding.is_empty() {
            candidates.iter().map(|(value, _)| *value).collect()
        } else {
            overriding
        };
        if pool
            .iter()
            .any(|value| conflict_key(value) != conflict_key(pool[0]))
        {
            self.meet(Conflict::Symbol(symbol.to_string(), field));
            return None;
        }
        let text: &'a str = match pool[0] {
            SetValue::Bool(flag) if spec.kind != FieldKind::Zone => {
                return Some(Value::Bool(*flag));
            }
            SetValue::Bool(flag) => {
                if *flag {
                    "True"
                } else {
                    "False"
                }
            }
            SetValue::Text(text) | SetValue::Other(text, _) => text,
            SetValue::Items(_) => return Some(Value::Unknown),
        };
        if spec.kind == FieldKind::Zone {
            let code = self.labels.zones.code_of(text)?;
            return Some(Value::Zone(&self.labels.zones.infos[code as usize]));
        }
        Some(Value::text(text))
    }

    /// `Labeler.symbol_matches`.
    pub fn symbol_matches(
        &self,
        link: &Link,
        assign: &Assign,
        symbol: &str,
        path: Option<&str>,
    ) -> bool {
        let Some(SymbolMatchPlan {
            decorated_by,
            subclass_of,
            names,
        }) = &assign.symbols
        else {
            return false;
        };
        if !names.is_empty() && !names.iter().any(|name| name == symbol) {
            return false;
        }
        if !assign.allows(path) {
            return false;
        }
        if !decorated_by.is_empty() {
            let decorators = self.facts.decorators(link, symbol);
            if !decorated_by
                .iter()
                .any(|name| decorators.contains(&name.as_str()))
            {
                return false;
            }
        }
        if !subclass_of.is_empty() {
            let (ancestors, _) = self.facts.ancestors(link, symbol);
            if !subclass_of
                .iter()
                .any(|name| ancestors.contains(name.as_str()))
            {
                return false;
            }
        }
        true
    }

    /// A module field's value on every graph module (`Labeler.field_value`), for Python to read
    /// instead of labelling each module again: the distinct values, and each module's place
    /// among them. A module whose value reads provider labels set two ways has `NO_PLACE`:
    /// Python reads that one itself, to meet the conflict where the Python engine meets it.
    pub fn value_column(&self, name: &str) -> Result<(Vec<&Cell>, Vec<u32>), String> {
        let field = self
            .labels
            .fields
            .iter()
            .position(|field| field.name == name && !field.on_symbol)
            .ok_or_else(|| format!("no module field {name}"))?;
        let mut distinct: Vec<&Cell> = Vec::new();
        let mut places: FxHashMap<&Cell, u32> = FxHashMap::default();
        let mut codes = Vec::with_capacity(self.values[field].len());
        for (at, cell) in self.values[field].iter().enumerate() {
            if self.tainted.contains(&(field, at as u32)) {
                codes.push(NO_PLACE);
                continue;
            }
            codes.push(place_of(&mut places, &mut distinct, cell));
        }
        Ok((distinct, codes))
    }

    /// Each graph module's registry, by its place in the plan's registries; `NO_PLACE` where
    /// none holds the module.
    pub fn registry_column(&self) -> Vec<u32> {
        self.labels
            .registry_of
            .iter()
            .map(|found| found.map_or(NO_PLACE, u32::from))
            .collect()
    }
}
