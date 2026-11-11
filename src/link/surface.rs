//! What a contract surface hands out (hoglint/core/surfaces.py `HandedOut`).
//!
//! A module hands out a name it binds by import when `__all__` lists it, when the import binds
//! it under its own name (`from x import f as f`), when it is a lazy (PEP 562) name, or when the
//! module has no top-level function. This is not the export primitive's notion of what a module
//! exports (every module-level import): a surface also imports the code it calls.

use super::facts::{Op, V};
use super::intern::{EMPTY, Sid};
use super::resolve::{Core, absolute_import};
use super::scope::Scope;
use rustc_hash::FxHashSet;

pub const ALL_CLAUSE: &str = "all";
pub const ALIAS_CLAUSE: &str = "alias";
pub const LAZY_CLAUSE: &str = "lazy";
pub const NO_FUNCTIONS_CLAUSE: &str = "no-functions";

/// A name a module hands out: where it binds it, what it names, and the clause that holds.
pub struct Handed {
    pub bound: Sid,
    pub target: Sid,
    pub clause: &'static str,
    pub line: u32,
    pub col: u32,
}

/// Whether the module body defines a function outside any block.
fn has_functions(core: &Core, file: usize) -> bool {
    core.facts.files[file]
        .defs
        .iter()
        .any(|d| d.s == -1 && d.f == 0 && !d.is_class())
}

/// The names of the last plain `__all__ = [...]` in the module body.
fn dunder_all(core: &mut Core, file: usize) -> Option<FxHashSet<Sid>> {
    let facts = core.facts.clone();
    let all = core.it.intern("__all__");
    let mut found = None;
    for row in &facts.files[file].assigns {
        let listed = row
            .targets
            .iter()
            .any(|target| matches!(target, V::Chain(name) if *name == all));
        if row.s != -1 || row.f != 0 || row.op != Op::Assign || !listed {
            continue;
        }
        if let Some(V::List(items) | V::Tuple(items)) = &row.value {
            found = Some(
                items
                    .iter()
                    .filter_map(|item| match item {
                        V::Str(text, None) => Some(*text),
                        _ => None,
                    })
                    .collect(),
            );
        }
    }
    found
}

/// The names one module hands out, in import order and then lazy-name order.
pub fn handed_out(core: &mut Core, scope: &Scope) -> Vec<Handed> {
    let file = scope.file as usize;
    let no_functions = !has_functions(core, file);
    let listed = if no_functions {
        None
    } else {
        dunder_all(core, file)
    };
    let facts = core.facts.clone();
    let mut out = Vec::new();
    let mut seen: FxHashSet<(Sid, Sid)> = FxHashSet::default();
    for row in &facts.files[file].imports {
        let Some(name) = row.name else {
            continue;
        };
        if core.it.s(name) == "*" || row.flags != 0 {
            continue;
        }
        let Some(source) = absolute_import(
            &mut core.it,
            scope.module,
            scope.is_package,
            row.target,
            row.level,
        ) else {
            continue;
        };
        let bound = row.asname.filter(|&a| a != EMPTY).unwrap_or(name);
        let clause = if no_functions {
            NO_FUNCTIONS_CLAUSE
        } else if listed.as_ref().is_some_and(|names| names.contains(&bound)) {
            ALL_CLAUSE
        } else if row.asname == Some(name) {
            ALIAS_CLAUSE
        } else {
            continue;
        };
        let target = core.it.join(source, name);
        if seen.insert((bound, target)) {
            out.push(Handed {
                bound,
                target,
                clause,
                line: row.line,
                col: row.col,
            });
        }
    }
    let lazy: Vec<(Sid, Sid)> = match core.table(scope.module) {
        Some(table) => core.table_at(table).lazy.iter().collect(),
        None => Vec::new(),
    };
    for (name, module) in lazy {
        let target = core.it.join(module, name);
        if seen.insert((name, target)) {
            out.push(Handed {
                bound: name,
                target,
                clause: LAZY_CLAUSE,
                line: 0,
                col: 0,
            });
        }
    }
    out
}
