//! Locals derived from tracked uses (schema.md, `refs` rule 2), resolved with a worklist.
//!
//! A use whose escape path starts with an assignment to plain names derives those names in
//! its scope. Loads of a derived name in that scope or a nested def are use sites too, and
//! they can derive further names. The walk records every load that could become such a use
//! (a candidate); the worklist activates candidates as their names get derived.

use crate::defs::DefTable;
use crate::facts::Ref;
use std::collections::{HashMap, HashSet};

const WRAPPER_STEPS: [&str; 4] = ["await", "ifexp:body", "ifexp:else", "star"];

/// A load of a name no import binds: its row once derived, and its statement flags.
pub struct Candidate {
    pub row: Ref,
    pub flags: u32,
}

/// The names a use derives: the plain-name targets of the assignment it flows into.
fn derived_names(up: &str) -> Option<Vec<&str>> {
    let mut steps = up
        .split_whitespace()
        .skip_while(|step| WRAPPER_STEPS.contains(step));
    let first = steps.next()?;
    let (kind, rest) = first.split_once(':').unwrap_or((first, ""));
    if !matches!(kind, "assign" | "annassign" | "walrus") {
        return None;
    }
    let names: Vec<&str> = rest.split(',').collect();
    let plain = names
        .iter()
        .all(|n| !n.is_empty() && !n.starts_with(['.', '[', '(', '*', '?']));
    plain.then_some(names)
}

pub struct Derivation<'d, 'a> {
    defs: &'d DefTable<'a>,
    derived: HashMap<i64, HashSet<String>>,
}

impl<'d, 'a> Derivation<'d, 'a> {
    pub fn new(defs: &'d DefTable<'a>) -> Self {
        Derivation {
            defs,
            derived: HashMap::new(),
        }
    }

    /// Records what a use in `scope` derives and queues each name that is new there.
    fn note(&mut self, scope: i64, up: &str, queue: &mut Vec<String>) {
        if self.defs.is_class(scope) {
            return;
        }
        let Some(names) = derived_names(up) else {
            return;
        };
        let known = self.derived.entry(scope).or_default();
        for name in names {
            if known.insert(name.to_string()) {
                queue.push(name.to_string());
            }
        }
    }

    /// The nearest enclosing non-class scope that derives `name`.
    fn deriving_scope(&self, name: &str, scope: i64) -> Option<i64> {
        self.defs
            .ancestry(scope)
            .into_iter()
            .filter(|idx| !self.defs.is_class(*idx))
            .find(|idx| {
                self.derived
                    .get(idx)
                    .is_some_and(|names| names.contains(name))
            })
    }

    /// The active candidates, each row with its deriving scope as `origin`.
    pub fn resolve(mut self, tracked: &[Ref], candidates: Vec<Candidate>) -> Vec<Candidate> {
        let mut queue: Vec<String> = Vec::new();
        for row in tracked {
            self.note(row.site.scope, &row.up, &mut queue);
        }
        let mut waiting: HashMap<&str, Vec<usize>> = HashMap::new();
        for (i, candidate) in candidates.iter().enumerate() {
            waiting
                .entry(candidate.row.root.as_str())
                .or_default()
                .push(i);
        }
        let mut active = vec![false; candidates.len()];
        while let Some(name) = queue.pop() {
            let Some(indexes) = waiting.get(name.as_str()) else {
                continue;
            };
            for &i in indexes {
                let row = &candidates[i].row;
                if active[i] || self.deriving_scope(&row.root, row.site.scope).is_none() {
                    continue;
                }
                active[i] = true;
                self.note(row.site.scope, &row.up, &mut queue);
            }
        }
        let mut rows = Vec::new();
        for (mut candidate, is_active) in candidates.into_iter().zip(active) {
            if !is_active {
                continue;
            }
            let row = &mut candidate.row;
            row.origin = self.deriving_scope(&row.root, row.site.scope);
            rows.push(candidate);
        }
        rows
    }
}
