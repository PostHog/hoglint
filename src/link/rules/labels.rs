//! Module labels (hoglint/labels.py `Labeler`): the values assignments and zones give each
//! graph module, its registry and the exports that cover its path. Each module's labels are
//! computed once per run; a field's domain fills the modules no source labels.

use super::glob::{Captures, Glob, capture, fill};
use super::plan::{AssignPlan, ExportPlan, Plan, RegistryPlan, SetValue, ValuePlan, ZonesPlan};
use super::selector::Node;
use super::value::Zone;
use rustc_hash::FxHashMap;

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
#[derive(Clone, Debug, PartialEq, Eq)]
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
    /// For a symbol assignment: (decorated_by, subclass_of).
    pub symbols: Option<(Vec<String>, Vec<String>)>,
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
            symbols: plan
                .symbols
                .as_ref()
                .map(|found| (found.decorated_by.clone(), found.subclass_of.clone())),
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
    within: Option<(String, Glob)>,
    paths: Vec<Glob>,
    pub subclass_of: Vec<String>,
    pub audience: Option<Node>,
}

impl Export {
    pub fn new(plan: &ExportPlan, audience: Option<Node>) -> Result<Export, String> {
        let within = match plan.within.as_str() {
            "" => None,
            root => Some((root.to_string(), Glob::new(&format!("{root}/**"))?)),
        };
        Ok(Export {
            within,
            paths: compile_globs(&plan.paths)?,
            subclass_of: plan.subclass_of.clone(),
            audience,
        })
    }

    /// Whether the path lies inside the export's `within` and `paths` (`Labeler.export_covers`).
    fn covers(&self, path: &str) -> bool {
        let relative = match &self.within {
            None => path,
            Some((root, glob)) => match glob.captures(path) {
                Some(captures) => path.get(fill(root, &captures).len() + 1..).unwrap_or(""),
                None => return false,
            },
        };
        self.paths.iter().any(|glob| glob.is_match(relative))
    }
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
        };
        for (at, (path, module)) in paths.iter().zip(modules).enumerate() {
            out.label_module(at as u32, path, module, exports);
        }
        out.fill_provided(plan)?;
        Ok(out)
    }

    /// One module's labels (`Labeler.label_module`), its registry and exports.
    fn label_module(&mut self, module_at: u32, path: &str, module: &str, exports: &[Export]) {
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
                Err(()) => self.conflicts.push((module_at, None)),
            }
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
