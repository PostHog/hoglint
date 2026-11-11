//! What a contract surface hands out (hoglint/core/surfaces.py `HandedOut`).
//!
//! A module hands out a name it binds by import when `__all__` lists it, when the import binds
//! it under its own name (`from x import f as f`), when it is a lazy (PEP 562) name, or when the
//! module has no top-level function. This is not the export primitive's notion of what a module
//! exports (every module-level import): a surface also imports the code it calls.
//!
//! A star import hands out the names it binds (the source's `__all__`, else its public top-level
//! names) under the same clauses; a source that cannot be read hands out `*`, which nothing
//! follows.
//!
//! `__all__` is read in every form whose names a reader can list (`ListedNames`): `__all__ = [...]`,
//! annotated or not, `+=` with a literal, a sum of literals, and copies of a module-level literal
//! such as `sorted(_LAZY)`. One built by other code, changed by a method or set under a condition
//! cannot be read: what the module hands out is then unknown.

use super::facts::{Assign, Op, V};
use super::intern::{EMPTY, Interner, Sid};
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

/// The names a module's `__all__` lists, or where it first cannot be read (`unread`).
pub struct DunderAll {
    pub names: Option<FxHashSet<Sid>>,
    pub unread: Option<(u32, u32)>,
}

/// The names a value lists, read from one module's assigns: a list, tuple or set of string
/// literals, items starred from a readable value, a sum of such values, `sorted`, `list`, `tuple`
/// or `set` of one readable value, and a module-level name assigned once, unconditionally, to a
/// literal of strings (a dict gives its keys). None for anything else.
struct ListedNames<'a> {
    it: &'a Interner,
    assigns: &'a [Assign],
}

impl ListedNames<'_> {
    const COPIES: [&'static str; 4] = ["sorted", "list", "tuple", "set"];

    /// The strings a module-level name holds, when one unconditional assignment binds it to a
    /// literal list, tuple, set or dict of string literals.
    fn literal(&self, name: Sid) -> Option<FxHashSet<Sid>> {
        let mut rows = self.assigns.iter().filter(|row| {
            row.s == -1
                && row
                    .targets
                    .iter()
                    .any(|target| matches!(target, V::Chain(bound) if *bound == name))
        });
        let row = rows.next()?;
        if rows.next().is_some() || row.f != 0 || !matches!(row.op, Op::Assign | Op::Annotated) {
            return None;
        }
        let text = |item: Option<&V>| match item {
            Some(V::Str(text, None)) => Some(*text),
            _ => None,
        };
        match row.value.as_ref()? {
            V::Dict(items) => items.iter().map(|(key, _)| text(key.as_ref())).collect(),
            V::List(items) | V::Tuple(items) | V::Set(items) => {
                items.iter().map(|item| text(Some(item))).collect()
            }
            _ => None,
        }
    }

    fn of(&self, value: &V) -> Option<FxHashSet<Sid>> {
        match value {
            V::Chain(name) if !self.it.s(*name).contains('.') => self.literal(*name),
            V::Bin(false, left, right) => {
                let mut names = self.of(left)?;
                names.extend(self.of(right)?);
                Some(names)
            }
            V::Call(callee, _, Some(sole)) => match &**callee {
                V::Chain(name) if Self::COPIES.contains(&self.it.s(*name)) => self.of(sole),
                _ => None,
            },
            V::List(items) | V::Tuple(items) | V::Set(items) => {
                let mut names = FxHashSet::default();
                for item in items {
                    match item {
                        V::Star(inner) => names.extend(self.of(inner)?),
                        V::Str(text, None) => {
                            names.insert(*text);
                        }
                        _ => return None,
                    }
                }
                Some(names)
            }
            _ => None,
        }
    }
}

/// Whether a callee is a method of `__all__`: `__all__.extend`.
fn method_of_all(callee: &str) -> bool {
    callee.strip_prefix("__all__.").is_some_and(|rest| {
        !rest.is_empty() && rest.chars().all(|c| c == '_' || c.is_alphanumeric())
    })
}

/// The module body's `__all__`, statement by statement in source order.
fn dunder_all(core: &mut Core, file: usize) -> DunderAll {
    let facts = core.facts.clone();
    let all = core.it.intern("__all__");
    let mut names: Option<FxHashSet<Sid>> = None;
    let mut unread: Vec<(u32, u32)> = Vec::new();
    let reader = ListedNames {
        it: &core.it,
        assigns: &facts.files[file].assigns,
    };
    for row in &facts.files[file].assigns {
        let listed = row
            .targets
            .iter()
            .any(|target| matches!(target, V::Chain(name) if *name == all));
        if row.s != -1 || !listed {
            continue;
        }
        if row.op == Op::Annotated && row.value.is_none() {
            continue;
        }
        let found = row.value.as_ref().and_then(|value| reader.of(value));
        match (found, row.op) {
            (Some(found), Op::Assign | Op::Annotated) if row.f == 0 => names = Some(found),
            (Some(found), Op::AddAssign) if row.f == 0 && names.is_some() => {
                names.get_or_insert_with(FxHashSet::default).extend(found);
            }
            _ => unread.push((row.l, row.c)),
        }
    }
    for row in &facts.files[file].callees {
        if row.scope == -1 && row.header == -1 && method_of_all(core.it.s(row.text)) {
            unread.push((row.line, row.col));
        }
    }
    DunderAll {
        names,
        unread: unread.into_iter().min(),
    }
}

/// The names `from source import *` binds: the source's `__all__`, else its public top-level
/// names, sorted; None when the source cannot be read.
fn star_names(core: &mut Core, source: Sid) -> Option<Vec<Sid>> {
    let table = core.table(source)?;
    let file = core.table_at(table).record?;
    if !core.facts.files[file as usize].ok() {
        return None;
    }
    let listing = dunder_all(core, file as usize);
    if listing.unread.is_some() {
        return None;
    }
    let mut names: Vec<Sid> = match listing.names {
        Some(names) => names.into_iter().collect(),
        None => core
            .table_at(table)
            .names
            .iter()
            .map(|(name, _)| name)
            .filter(|name| !core.it.s(*name).starts_with('_'))
            .collect(),
    };
    names.sort_by(|a, b| core.it.s(*a).cmp(core.it.s(*b)));
    Some(names)
}

/// The module's `__all__`; None for a module with no top-level function, which hands out every
/// import whatever `__all__` says.
pub fn listing(core: &mut Core, scope: &Scope) -> Option<DunderAll> {
    let file = scope.file as usize;
    if has_functions(core, file) {
        Some(dunder_all(core, file))
    } else {
        None
    }
}

/// The names one module hands out, in import order and then lazy-name order.
pub fn handed_out(core: &mut Core, scope: &Scope, listing: Option<&DunderAll>) -> Vec<Handed> {
    let file = scope.file as usize;
    let no_functions = listing.is_none();
    let listed = listing.and_then(|found| found.names.as_ref());
    let facts = core.facts.clone();
    let mut out = Vec::new();
    let mut seen: FxHashSet<(Sid, Sid)> = FxHashSet::default();
    let star = core.star();
    for row in &facts.files[file].imports {
        let Some(name) = row.name else {
            continue;
        };
        if row.flags != 0 {
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
        let mut handed: Vec<(Sid, Sid, &'static str)> = Vec::new();
        if name == star {
            if !no_functions && listed.is_none() {
                continue;
            }
            let clause = if no_functions {
                NO_FUNCTIONS_CLAUSE
            } else {
                ALL_CLAUSE
            };
            match star_names(core, source) {
                None => handed.push((star, core.it.join(source, star), clause)),
                Some(found) => {
                    for name in found {
                        if listed.is_none_or(|names| names.contains(&name)) {
                            handed.push((name, core.it.join(source, name), clause));
                        }
                    }
                }
            }
        } else {
            let bound = row.asname.filter(|&a| a != EMPTY).unwrap_or(name);
            let clause = if no_functions {
                NO_FUNCTIONS_CLAUSE
            } else if listed.is_some_and(|names| names.contains(&bound)) {
                ALL_CLAUSE
            } else if row.asname == Some(name) {
                ALIAS_CLAUSE
            } else {
                continue;
            };
            handed.push((bound, core.it.join(source, name), clause));
        }
        for (bound, target, clause) in handed {
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
