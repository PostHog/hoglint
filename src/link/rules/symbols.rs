//! Symbol facts, class ancestry and kinds (hoglint/index.py `SymbolFacts`, hoglint/labels.py
//! `KindSets`): what the core and packs wrote about a symbol, read from the link.

use super::plan::KindPlan;
use super::value::split_list;
use crate::link::Link;
use crate::link::intern::Sid;
use rustc_hash::{FxHashMap, FxHashSet};

/// The link's symbol kind codes (`Link.symbol_columns`).
const SYMBOL_KINDS: [&str; 3] = ["def", "async", "class"];

/// `DECL_NAMES`: a def is a function.
fn decl_name(raw: &str) -> &str {
    match raw {
        "def" | "async" => "function",
        other => other,
    }
}

/// Bases and decorators of every class the link knows, by owner symbol, and the classes that
/// name each base (`SymbolFacts.children`).
pub struct SymbolFacts {
    bases: FxHashMap<String, Vec<String>>,
    decorators: FxHashMap<String, Vec<String>>,
    children: FxHashMap<String, Vec<String>>,
    /// The names of each class's decorators that a call passes `frozen=True`
    /// (`SymbolFacts.frozen_by`).
    frozen_by: FxHashMap<String, Vec<String>>,
}

/// (members of each kind in plan order, the kinds that hold each name, the names of unknown kind).
pub struct KindSets {
    pub kinds_of: FxHashMap<String, Vec<usize>>,
    pub unknown: FxHashSet<String>,
}

impl SymbolFacts {
    pub fn new(link: &mut Link) -> SymbolFacts {
        let mut bases: FxHashMap<String, Vec<String>> = FxHashMap::default();
        let mut decorators: FxHashMap<String, Vec<String>> = FxHashMap::default();
        if let Some(inherit) = link.kind_sid("inherit") {
            for (owner, surface, names, _, called, resolved, classes) in
                link.class_facts(inherit, "class")
            {
                let names = Self::base_names(link, &surface, names, called, resolved, &classes);
                bases.entry(owner).or_default().extend(names);
            }
        }
        let mut frozen_by: FxHashMap<String, Vec<String>> = FxHashMap::default();
        if let Some(decorate) = link.kind_sid("decorate") {
            for (owner, surface, names, is_frozen, _, _, _) in
                link.class_facts(decorate, "decorated")
            {
                if is_frozen {
                    let found = frozen_by.entry(owner.clone()).or_default();
                    found.push(surface.clone());
                    found.extend(names.iter().cloned());
                }
                let found = decorators.entry(owner).or_default();
                found.push(surface);
                found.extend(names);
            }
        }
        let mut children: FxHashMap<String, Vec<String>> = FxHashMap::default();
        for (owner, names) in &bases {
            for base in names {
                children
                    .entry(base.clone())
                    .or_default()
                    .push(owner.clone());
            }
        }
        let it = &link.core.it;
        let mut written: Vec<(&Sid, &Vec<(Sid, Sid)>)> = link.symbols.added.iter().collect();
        written.sort_by_key(|(name, _)| it.s(**name));
        for (name, facts) in written {
            let raw = facts
                .iter()
                .find(|(key, _)| it.s(*key) == "bases")
                .map_or("", |(_, value)| it.s(*value));
            for base in split_list(raw) {
                children
                    .entry(base.to_string())
                    .or_default()
                    .push(it.s(*name).to_string());
            }
        }
        SymbolFacts {
            bases,
            decorators,
            children,
            frozen_by,
        }
    }

    /// The names of an inherit edge as ancestry reads them: a call's result is an unknown base;
    /// a first-party name the producer followed is the classes it wrote, or unknown; any other
    /// first-party name that is no declared class is unknown (`SymbolFacts.base_names`).
    fn base_names(
        link: &Link,
        surface: &str,
        names: Vec<String>,
        called: bool,
        resolved: bool,
        classes: &str,
    ) -> Vec<String> {
        if called {
            return vec![format!("?{surface}()")];
        }
        if classes == "?" {
            return names.into_iter().map(|name| format!("?{name}")).collect();
        }
        if !classes.is_empty() {
            return classes.split(',').map(str::to_string).collect();
        }
        if !resolved {
            return names;
        }
        names
            .into_iter()
            .map(|name| {
                if Self::declared_class(link, &name) {
                    name
                } else {
                    format!("?{name}")
                }
            })
            .collect()
    }

    /// Whether the core declared the name a class: a pack may give a class another kind
    /// (`SymbolFacts.declared_class`).
    fn declared_class(link: &Link, name: &str) -> bool {
        let Some(sid) = link.core.it.get(name) else {
            return false;
        };
        link.symbols
            .at
            .get(&sid)
            .is_some_and(|&at| SYMBOL_KINDS[link.symbols.order[at].1 as usize] == "class")
    }

    /// Whether `graph.symbols` holds the name.
    pub fn has(link: &Link, name: &str) -> bool {
        link.core
            .it
            .get(name)
            .is_some_and(|sid| link.symbols.has(sid))
    }

    /// One fact of a symbol, "" when it has none (`NativeSymbols.fact`).
    pub fn fact<'l>(link: &'l Link, name: &str, key: &str) -> &'l str {
        link.core
            .it
            .get(name)
            .map_or("", |sid| Self::fact_of(link, sid, key))
    }

    /// `fact` of an interned name.
    fn fact_of<'l>(link: &'l Link, sid: Sid, key: &str) -> &'l str {
        let it = &link.core.it;
        if let Some(facts) = link.symbols.added.get(&sid) {
            return facts
                .iter()
                .find(|(k, _)| it.s(*k) == key)
                .map_or("", |(_, value)| it.s(*value));
        }
        let Some(&at) = link.symbols.at.get(&sid) else {
            return "";
        };
        let (_, kind, file) = link.symbols.order[at];
        match key {
            "kind" => SYMBOL_KINDS[kind as usize],
            "path" => &link.core.facts.files[file as usize].path,
            _ => "",
        }
    }

    /// `decl` of an interned name.
    fn decl_of(link: &Link, sid: Sid) -> &str {
        let raw = Self::fact_of(link, sid, "decl");
        decl_name(if raw.is_empty() {
            Self::fact_of(link, sid, "kind")
        } else {
            raw
        })
    }

    pub fn decl<'l>(link: &'l Link, name: &str) -> &'l str {
        let raw = Self::fact(link, name, "decl");
        decl_name(if raw.is_empty() {
            Self::fact(link, name, "kind")
        } else {
            raw
        })
    }

    pub fn bases<'s>(&'s self, link: &'s Link, name: &str) -> Vec<&'s str> {
        let mut out: Vec<&str> = self.bases.get(name).map_or(Vec::new(), |found| {
            found.iter().map(String::as_str).collect()
        });
        out.extend(split_list(Self::fact(link, name, "bases")));
        out
    }

    pub fn decorators<'s>(&'s self, link: &'s Link, name: &str) -> Vec<&'s str> {
        let mut out: Vec<&str> = self.decorators.get(name).map_or(Vec::new(), |found| {
            found.iter().map(String::as_str).collect()
        });
        out.extend(split_list(Self::fact(link, name, "decorators")));
        out
    }

    /// Whether a dataclass decorator makes the class, or its facts say it is a dataclass.
    pub fn is_dataclass(&self, link: &Link, name: &str) -> bool {
        let make = &link.dataclass_kinds.make;
        self.decorators(link, name)
            .iter()
            .any(|found| make.contains(*found))
            || Self::fact(link, name, "dataclass") == "true"
    }

    /// Whether a dataclass decorator call passes `frozen=True`, or a decorator that always
    /// freezes makes the class.
    pub fn is_frozen(&self, link: &Link, name: &str) -> bool {
        if Self::fact(link, name, "frozen") == "true" {
            return true;
        }
        let kinds = &link.dataclass_kinds;
        let frozen_call = self
            .frozen_by
            .get(name)
            .is_some_and(|found| found.iter().any(|item| kinds.make.contains(item)));
        frozen_call
            || self
                .decorators(link, name)
                .iter()
                .any(|found| kinds.freeze.contains(*found))
    }

    /// Every base class reachable from `name`, and whether the walk saw every base
    /// (`SymbolFacts.walk_ancestors`).
    pub fn ancestors<'s>(&'s self, link: &'s Link, name: &str) -> (FxHashSet<&'s str>, bool) {
        let mut seen: FxHashSet<&str> = FxHashSet::default();
        let mut complete = true;
        let mut queue = self.bases(link, name);
        while let Some(base) = queue.pop() {
            if base.starts_with('?') {
                complete = false;
                continue;
            }
            if !seen.insert(base) {
                continue;
            }
            queue.extend(self.bases(link, base));
        }
        (seen, complete)
    }

    /// Every name whose ancestors include one of `targets`.
    fn descendants<'s>(&'s self, targets: &[&str]) -> FxHashSet<&'s str> {
        let mut seen: FxHashSet<&str> = FxHashSet::default();
        let mut queue: Vec<&str> = targets.to_vec();
        while let Some(name) = queue.pop() {
            for child in self.children.get(name).map_or(&[][..], Vec::as_slice) {
                if seen.insert(child) {
                    queue.push(child);
                }
            }
        }
        seen
    }

    /// Every name in `graph.symbols`: the core's, then the ones only packs wrote.
    pub fn all_symbols(link: &Link) -> Vec<Sid> {
        let mut out: Vec<Sid> = link
            .symbols
            .order
            .iter()
            .map(|(name, _, _)| *name)
            .collect();
        let mut added: Vec<Sid> = link
            .symbols
            .added
            .keys()
            .filter(|name| !link.symbols.at.contains_key(*name))
            .copied()
            .collect();
        added.sort_unstable();
        out.extend(added);
        out
    }

    /// Whether a symbol with facts passes a kind's tests other than `subclass_of`.
    fn kind_tests_pass(&self, link: &Link, kind: &KindPlan, name: &str) -> bool {
        if !kind.decl.is_empty() && Self::decl(link, name) != kind.decl {
            return false;
        }
        if kind.abstract_ == Some(false) && Self::fact(link, name, "abstract") == "true" {
            return false;
        }
        if kind.decorated_by.is_empty() {
            return true;
        }
        let decorators = self.decorators(link, name);
        kind.decorated_by
            .iter()
            .any(|wanted| decorators.contains(&wanted.as_str()))
    }

    /// Every name the kind holds (`Labeler.find_kind_members`).
    fn members(&self, link: &Link, kind: &KindPlan) -> FxHashSet<String> {
        let mut out: FxHashSet<String> = kind.subclass_of.iter().cloned().collect();
        let pool: Vec<&str> = if !kind.subclass_of.is_empty() {
            let targets: Vec<&str> = kind.subclass_of.iter().map(String::as_str).collect();
            self.descendants(&targets)
                .into_iter()
                .filter(|name| Self::has(link, name))
                .collect()
        } else if !kind.decl.is_empty() {
            Self::all_symbols(link)
                .into_iter()
                .filter(|sid| Self::decl_of(link, *sid) == kind.decl)
                .map(|sid| link.core.it.s(sid))
                .collect()
        } else {
            Self::all_symbols(link)
                .into_iter()
                .map(|sid| link.core.it.s(sid))
                .collect()
        };
        out.extend(
            pool.into_iter()
                .filter(|name| self.kind_tests_pass(link, kind, name))
                .map(str::to_string),
        );
        out
    }

    /// [kinds] as sets (`Labeler.kind_sets`).
    pub fn kind_sets(&self, link: &Link, kinds: &[KindPlan]) -> KindSets {
        let mut kinds_of: FxHashMap<String, Vec<usize>> = FxHashMap::default();
        for (at, kind) in kinds.iter().enumerate() {
            for name in self.members(link, kind) {
                kinds_of.entry(name).or_default().push(at);
            }
        }
        let classed: Vec<&KindPlan> = kinds
            .iter()
            .filter(|kind| !kind.subclass_of.is_empty())
            .collect();
        let mut unknown = FxHashSet::default();
        if !classed.is_empty() {
            let unresolved: Vec<&str> = self
                .children
                .keys()
                .filter(|name| name.starts_with('?'))
                .map(String::as_str)
                .collect();
            for name in self.descendants(&unresolved) {
                if kinds_of.contains_key(name) || !Self::has(link, name) {
                    continue;
                }
                if classed
                    .iter()
                    .any(|kind| self.kind_tests_pass(link, kind, name))
                {
                    unknown.insert(name.to_string());
                }
            }
        }
        KindSets { kinds_of, unknown }
    }
}
