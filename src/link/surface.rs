//! What a contract surface hands out (hoglint/core/surfaces.py `HandedOut`).
//!
//! A module hands out a name it binds by import when `__all__` lists it, when the import binds
//! it under its own name (`from x import f as f`), when it is a lazy (PEP 562) name, or when the
//! module has no top-level function and its own code does not refer to the name. A surface also
//! imports the code it calls, and that code is not part of its contract. An import in any block
//! of the module body counts (`try`, `except`, `if`, `else`, `with`), as it binds the name at run
//! time; one under `if TYPE_CHECKING:` does not.
//!
//! A star import hands out the names it binds (the source's `__all__`, else its public top-level
//! names with those of its own star imports, never a `__future__` name) under the same clauses; in
//! an export module each name it binds is an import edge of its own. A source that cannot be read
//! hands out `*`, which nothing follows: the export rule reports it as an import that did not
//! resolve. `import a.b as name` binds the module `a.b` under `name` and is handed out by the same
//! clauses; a plain `import a.b` binds `a`, no surface of its own, and hands out nothing. A relative
//! import that climbs above the top package resolves to nothing and still binds its name: handed
//! out, it leaves what the module hands out unresolved.
//!
//! Every rule reads this notion of what a module exports: core records it for every export
//! module (`Link::hand_outs`).
//!
//! `__all__` is read in every form whose names a reader can list (`ListedNames`): `__all__ = [...]`,
//! annotated or not, `+=` with a literal, a sum of literals, and copies of a module-level literal
//! such as `sorted(_LAZY)`. One built by other code, changed by a method or set under a condition
//! cannot be read: what the module hands out is then unknown.

use super::facts::{Assign, COPY_CALLEES, Import, Op, V};
use super::intern::{EMPTY, Interner, Sid};
use super::queries::named;
use super::resolve::{Binding, Core, RUN_TIME_MODULE_FLAGS, import_surface, module_level};
use super::scope::Scope;
use super::trace::Kind;
use rustc_hash::FxHashSet;

pub const ALL_CLAUSE: &str = "all";
pub const ALIAS_CLAUSE: &str = "alias";
pub const LAZY_CLAUSE: &str = "lazy";
pub const NO_FUNCTIONS_CLAUSE: &str = "no-functions";
/// The module whose names a star import never hands on.
const FUTURE: &str = "__future__";

/// A name a module hands out: where it binds it, what it names, and the clause that holds.
pub struct Handed {
    pub bound: Sid,
    pub target: Sid,
    pub clause: &'static str,
    pub line: u32,
    pub col: u32,
}

/// What one export module binds by import, split by the hand-out rule (hoglint/model.py
/// `HandOut`): the names it hands out, the names it keeps to itself, and whether its `__all__`
/// cannot be read.
pub struct HandOut {
    pub handed: FxHashSet<Sid>,
    pub kept: FxHashSet<Sid>,
    pub unread: bool,
}

/// The names of a hand-out set as text, sorted: the order every reader of `HandOut` prints.
pub fn sorted_names<'a>(it: &'a Interner, names: &FxHashSet<Sid>) -> Vec<&'a str> {
    let mut out: Vec<&str> = names.iter().map(|&name| it.s(name)).collect();
    out.sort_unstable();
    out
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
                V::Chain(name) if COPY_CALLEES.contains(&self.it.s(*name)) => self.of(sole),
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

/// Whether a module binds a name by `from __future__ import`, read through the traced lookup.
fn from_future(core: &mut Core, source: Sid, name: Sid) -> bool {
    let future = core.it.intern(FUTURE);
    matches!(core.binding_of(source, name), Some(Binding::From(from, _)) if from == future)
}

/// The names one source of a star import binds (`StarNames.bound`); None when it, or a source it
/// star-imports, cannot be read. A source already read adds nothing more.
fn star_bound(core: &mut Core, source: Sid, seen: &mut FxHashSet<Sid>) -> Option<FxHashSet<Sid>> {
    if !seen.insert(source) {
        return Some(FxHashSet::default());
    }
    core.rec.read(Kind::Names, source, EMPTY);
    let table = core.table(source)?;
    let file = core.table_at(table).record?;
    if !core.facts.files[file as usize].ok() {
        return None;
    }
    let listing = dunder_all(core, file as usize);
    if listing.unread.is_some() {
        return None;
    }
    if let Some(listed) = listing.names {
        return Some(
            listed
                .into_iter()
                .filter(|&name| !from_future(core, source, name))
                .collect(),
        );
    }
    let public: Vec<Sid> = core
        .table_at(table)
        .names
        .iter()
        .map(|(name, _)| name)
        .filter(|name| !core.it.s(*name).starts_with('_'))
        .collect();
    let mut names: FxHashSet<Sid> = public
        .into_iter()
        .filter(|&name| !from_future(core, source, name))
        .collect();
    core.rec.read(Kind::Stars, source, EMPTY);
    let stars = core.table_at(table).star_imports.clone();
    for star in stars {
        let found = star_bound(core, star, seen)?;
        names.extend(
            found
                .into_iter()
                .filter(|name| !core.it.s(*name).starts_with('_')),
        );
    }
    Some(names)
}

/// The names `from source import *` binds (`StarNames.of`): the source's `__all__`, else its
/// public top-level names with those of its own star imports; never a name a `from __future__
/// import` binds. Sorted; None when the source, or a source it star-imports, cannot be read.
pub fn star_names(core: &mut Core, source: Sid) -> Option<Vec<Sid>> {
    let mut names: Vec<Sid> = star_bound(core, source, &mut FxHashSet::default())?
        .into_iter()
        .collect();
    names.sort_by(|a, b| core.it.s(*a).cmp(core.it.s(*b)));
    Some(names)
}

/// An import of the module body that runs at run time, in whatever block it sits: one under
/// `if TYPE_CHECKING:` binds nothing at run time, and one in a function or class binds no module
/// name.
fn binds_at_run_time(row: &Import) -> bool {
    module_level(row.scope, row.flags, RUN_TIME_MODULE_FLAGS)
}

/// The names one module hands out, in import order and then lazy-name order.
///
/// `no_functions`: the module body defines no function outside any block, so it hands out every
/// import it does not use itself, whatever `__all__` says.
pub struct HandedOut {
    pub no_functions: bool,
    pub dunder_all: DunderAll,
}

impl HandedOut {
    pub fn new(core: &mut Core, scope: &Scope) -> HandedOut {
        let file = scope.file as usize;
        let no_functions = !core.facts.files[file]
            .defs
            .iter()
            .any(|d| d.s == -1 && d.f == 0 && !d.is_class());
        HandedOut {
            no_functions,
            dunder_all: dunder_all(core, file),
        }
    }

    /// `kept`: the import is one a module with no function keeps only to hand it on.
    fn clause(&self, bound: Sid, name: Sid, asname: Option<Sid>, kept: bool) -> &'static str {
        if kept {
            return NO_FUNCTIONS_CLAUSE;
        }
        let listed = self.dunder_all.names.as_ref();
        if listed.is_some_and(|names| names.contains(&bound)) {
            return ALL_CLAUSE;
        }
        if asname == Some(name) {
            return ALIAS_CLAUSE;
        }
        ""
    }

    /// (bound, target, clause) of each name a star import hands out. A source that cannot be
    /// read, or a relative one above the top package (`source` None), hands out `*` at the
    /// import's `surface`: a name nothing can follow.
    fn star(
        &self,
        core: &mut Core,
        source: Option<Sid>,
        surface: Sid,
    ) -> Vec<(Sid, Sid, &'static str)> {
        let listed = if self.no_functions {
            None
        } else {
            self.dunder_all.names.as_ref()
        };
        if !self.no_functions && listed.is_none() {
            return Vec::new();
        }
        let clause = if self.no_functions {
            NO_FUNCTIONS_CLAUSE
        } else {
            ALL_CLAUSE
        };
        let star = core.star();
        let found = match source {
            Some(source) => star_names(core, source).map(|found| (source, found)),
            None => None,
        };
        let Some((source, found)) = found else {
            return vec![(star, surface, clause)];
        };
        found
            .into_iter()
            .filter(|name| listed.is_none_or(|names| names.contains(name)))
            .map(|name| (name, core.it.join(source, name), clause))
            .collect()
    }

    /// (bound, target, clause) of an import that binds one name; `used` as in `names`.
    fn single(
        &self,
        target: Sid,
        name: Sid,
        asname: Option<Sid>,
        used: &FxHashSet<Sid>,
    ) -> (Sid, Sid, &'static str) {
        let bound = named(asname, name);
        let kept = self.no_functions && !used.contains(&bound);
        (bound, target, self.clause(bound, name, asname, kept))
    }

    /// (bound, target, clause) of each name one import of the module body binds. `import a.b`
    /// binds `a`, no surface of its own, so it hands out nothing; `import a.b as name` binds the
    /// module. A relative import that climbs above the top package resolves to nothing and still
    /// binds its name.
    fn import_names(
        &self,
        core: &mut Core,
        scope: &Scope,
        row: &Import,
        used: &FxHashSet<Sid>,
    ) -> Vec<(Sid, Sid, &'static str)> {
        let asname = row.asname.filter(|&a| a != EMPTY);
        if row.name.is_none() && asname.is_none() {
            return Vec::new();
        }
        let (_, surface, source) =
            import_surface(&mut core.it, scope.module, scope.is_package, row);
        let name = row.name.unwrap_or(surface);
        if name == core.star() {
            return self.star(core, source, surface);
        }
        vec![self.single(surface, name, asname, used)]
    }

    /// `used`: the names the module's own code refers to. In a module with no function such a
    /// name is no import kept only to be handed on, so another clause has to hand it out.
    pub fn names(&self, core: &mut Core, scope: &Scope, used: &FxHashSet<Sid>) -> Vec<Handed> {
        let facts = core.facts.clone();
        let mut out = Vec::new();
        let mut seen: FxHashSet<(Sid, Sid)> = FxHashSet::default();
        for row in &facts.files[scope.file as usize].imports {
            if !binds_at_run_time(row) {
                continue;
            }
            for (bound, target, clause) in self.import_names(core, scope, row, used) {
                if clause.is_empty() || !seen.insert((bound, target)) {
                    continue;
                }
                out.push(Handed {
                    bound,
                    target,
                    clause,
                    line: row.line,
                    col: row.col,
                });
            }
        }
        let entries = match core.table(scope.module) {
            Some(table) => core.table_at(table).lazy_entries.clone(),
            None => Vec::new(),
        };
        for entry in entries {
            let target = core.it.join(entry.module, entry.name);
            if seen.insert((entry.name, target)) {
                out.push(Handed {
                    bound: entry.name,
                    target,
                    clause: LAZY_CLAUSE,
                    line: entry.line,
                    col: entry.col,
                });
            }
        }
        out
    }

    /// The names the module hands out and the ones it binds by import and keeps to itself.
    /// `used` as in `names`.
    pub fn hand_out(&self, core: &mut Core, scope: &Scope, used: &FxHashSet<Sid>) -> HandOut {
        let handed: FxHashSet<Sid> = self
            .names(core, scope, used)
            .into_iter()
            .map(|item| item.bound)
            .collect();
        let star = core.star();
        let facts = core.facts.clone();
        let mut bound: FxHashSet<Sid> = FxHashSet::default();
        for row in facts.files[scope.file as usize].imports.iter() {
            if !binds_at_run_time(row) {
                continue;
            }
            match row.name {
                Some(name) if name == star => {
                    let (_, _, source) =
                        import_surface(&mut core.it, scope.module, scope.is_package, row);
                    if let Some(found) = source.and_then(|source| star_names(core, source)) {
                        bound.extend(found);
                    }
                }
                Some(name) => {
                    bound.insert(named(row.asname, name));
                }
                None => bound.extend(row.asname.filter(|&a| a != EMPTY)),
            }
        }
        let kept = bound
            .into_iter()
            .filter(|bound| !handed.contains(bound))
            .collect();
        HandOut {
            handed,
            kept,
            unread: !self.no_functions && self.dunder_all.unread.is_some(),
        }
    }
}
