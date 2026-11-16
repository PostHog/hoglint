//! Labels set two ways (`Labeler.conflicts`, D44): the evaluator finds them, Python words them
//! with their sources and raises the config error.
//!
//! Some are found before any rule runs, as `Labeler.find_conflicts` finds them. The rest are met
//! only where a rule reads them: a symbol label outside the graph's symbols, and a module's
//! provider labels.

use super::Rules;
use super::env::Cx;
use super::labels::{Assign, FieldKind};
use super::plan::SetValue;
use super::symbols::SymbolFacts;
use crate::link::Link;
use rustc_hash::{FxHashMap, FxHashSet};

/// A label set two ways, by the entity Python words it for.
#[derive(Clone, PartialEq, Eq, Hash, PartialOrd, Ord, Debug)]
pub enum Conflict {
    /// A graph module's field that two sources set apart (`Labeler.label_module`).
    Module(u32, usize),
    /// Leaf zones that all claim a graph module's path (`Labeler.zone_candidate`).
    Zones(u32),
    /// A name two kinds hold (`Labeler.find_kind_conflicts`).
    Kind(String),
    /// A symbol's field that symbol assignments set apart (`Labeler.label_symbol`).
    Symbol(String, usize),
    /// A graph module whose provider labels disagree with another source
    /// (`Labeler.label_provided`).
    Provided(u32),
}

/// What a conflict between two assignment values compares (`repr`).
pub fn conflict_key(value: &SetValue) -> (u8, &str) {
    match value {
        SetValue::Text(text) => (0, text),
        SetValue::Bool(true) => (1, "True"),
        SetValue::Bool(false) => (1, "False"),
        SetValue::Items(_) => (2, ""),
        SetValue::Other(_, repr) => (3, repr),
    }
}

impl Rules {
    /// Every conflict `Labeler.find_conflicts` finds on this graph, whatever the rules read:
    /// module labels, kinds, and symbol assignments where two rivals reach a symbol's path.
    pub fn conflicts(&self, link: &Link) -> Vec<Conflict> {
        let mut out: Vec<Conflict> = self
            .labels
            .conflicts
            .iter()
            .map(|&(module, field)| match field {
                Some(field) => Conflict::Module(module, field),
                None => Conflict::Zones(module),
            })
            .collect();
        let mut kinds: Vec<Conflict> = self
            .kind_sets
            .kinds_of
            .iter()
            .filter(|(_, kinds)| kinds.len() > 1)
            .map(|(name, _)| Conflict::Kind(name.clone()))
            .collect();
        kinds.sort();
        out.extend(kinds);
        out.extend(self.symbol_conflicts(link));
        out
    }

    /// The conflicts rules met while they ran, sorted.
    pub fn met(&self) -> Vec<Conflict> {
        let met = self
            .met
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let mut out: Vec<Conflict> = met.iter().cloned().collect();
        out.sort();
        out
    }

    /// Records a conflict a read met.
    pub fn meet(&self, conflict: Conflict) {
        let mut met = self
            .met
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        met.insert(conflict);
    }

    /// A read of a module field's value on graph module `at`: the provider labels it reads, as
    /// `Labeler.field_value` reads them, may be set two ways.
    pub fn read_module_field(&self, field: usize, at: u32) {
        if !self.tainted.is_empty() && self.tainted.contains(&(field, at)) {
            self.meet(Conflict::Provided(at));
        }
    }

    /// The non-set fields two symbol assignments set to different values
    /// (`Labeler.rival_symbol_assigns`).
    pub fn contested_fields(&self) -> Vec<usize> {
        let assigns = &self.labels.assigns;
        let mut keys: FxHashMap<usize, Vec<(u8, &str)>> = FxHashMap::default();
        for &at in &self.symbol_assigns {
            for (field, spec) in self.labels.fields.iter().enumerate() {
                if spec.kind == FieldKind::Set {
                    continue;
                }
                for value in assigns[at].sets_of(field) {
                    let found = keys.entry(field).or_default();
                    if !found.contains(&conflict_key(value)) {
                        found.push(conflict_key(value));
                    }
                }
            }
        }
        let mut out: Vec<usize> = keys
            .into_iter()
            .filter(|(_, found)| found.len() > 1)
            .map(|(field, _)| field)
            .collect();
        out.sort_unstable();
        out
    }

    /// `Labeler.find_symbol_assignment_conflicts`: only symbols whose path two rival assignments
    /// reach are labelled.
    fn symbol_conflicts(&self, link: &Link) -> Vec<Conflict> {
        let contested = self.contested_fields();
        let assigns = &self.labels.assigns;
        let rivals: Vec<&Assign> = self
            .symbol_assigns
            .iter()
            .map(|&at| &assigns[at])
            .filter(|assign| {
                contested
                    .iter()
                    .any(|&field| assign.sets_of(field).next().is_some())
            })
            .collect();
        if rivals.len() < 2 {
            return Vec::new();
        }
        let cx = Cx { link, rules: self };
        let mut reached: FxHashMap<Option<u32>, bool> = FxHashMap::default();
        let mut out = Vec::new();
        for name in SymbolFacts::all_symbols(link) {
            let symbol = link.core.it.s(name);
            let at = cx.module_of_symbol(symbol).map(|(_, at)| at);
            let contested_here = *reached.entry(at).or_insert_with(|| {
                let path = at.map(|at| self.module_path(link, at));
                rivals.iter().filter(|assign| assign.allows(path)).count() > 1
            });
            if !contested_here {
                continue;
            }
            for field in self.symbol_fields_set_apart(link, symbol, at) {
                out.push(Conflict::Symbol(symbol.to_string(), field));
            }
        }
        let mut seen = FxHashSet::default();
        out.retain(|conflict| seen.insert(conflict.clone()));
        out
    }

    /// The fields the symbol assignments that reach a symbol set apart.
    fn symbol_fields_set_apart(&self, link: &Link, symbol: &str, at: Option<u32>) -> Vec<usize> {
        let path = at.map(|at| self.module_path(link, at));
        let matching: Vec<&Assign> = self
            .symbol_assigns
            .iter()
            .map(|&at| &self.labels.assigns[at])
            .filter(|assign| self.symbol_matches(link, assign, symbol, path))
            .collect();
        (0..self.labels.fields.len())
            .filter(|&field| {
                if self.labels.fields[field].kind == FieldKind::Set {
                    return false;
                }
                let candidates: Vec<(&SetValue, bool)> = matching
                    .iter()
                    .flat_map(|assign| assign.sets_of(field).map(|value| (value, assign.overrides)))
                    .collect();
                set_apart(&candidates)
            })
            .collect()
    }
}

/// Whether candidates of one field disagree once the overriding ones win
/// (`Labeler.resolve_candidates`).
pub fn set_apart(candidates: &[(&SetValue, bool)]) -> bool {
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
    pool.iter()
        .any(|value| conflict_key(value) != conflict_key(pool[0]))
}
