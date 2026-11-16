//! The class an instance attribute holds, for a lookup through it: `self.family.register(...)` in
//! a method is a member of the class `self.family` holds, when the method's own class tells which
//! one. Read from the class body and its methods only (`Def::attrs`), never from a base class or
//! code outside the class.
//!
//! The annotations of the attribute decide when it has any (in the class body or on an
//! assignment); else every value the class assigns it must give the same class: a call of a
//! class, or a subscript of a module-level container whose annotation names its element class
//! (`dict[K, V]`, `list[V]`, `tuple[V, ...]` and their `typing` and `collections.abc` names) or
//! whose literal items all call one class. Anything else, or two classes, is no answer: the
//! lookup stays as unresolved as before.

use super::facts::{Assign, V};
use super::intern::Sid;
use super::resolve::{Core, Ent, HitId, HitKind};
use super::scope::{LookupNames, Scope};
use super::trace::Kind;

/// Decorators after which the first parameter is no instance.
const NOT_METHODS: [&str; 2] = ["staticmethod", "classmethod"];
/// Generics whose subscript gives a value of their last type argument.
const MAPPINGS: [&str; 6] = [
    "dict",
    "Dict",
    "Mapping",
    "MutableMapping",
    "OrderedDict",
    "defaultdict",
];
/// Generics whose subscript gives a value of their only type argument (`tuple[V, ...]` too).
const SEQUENCES: [&str; 6] = [
    "list",
    "List",
    "Sequence",
    "MutableSequence",
    "tuple",
    "Tuple",
];

/// Where a value or an annotation of an attribute sits: the def index to look its names up at,
/// and its line.
struct Source<'v> {
    at: i32,
    line: u32,
    value: &'v V,
}

impl Core {
    /// The class def index of the method at `at` whose first parameter is `head`. None for a
    /// static or class method, a nested function and a function outside a class.
    fn method_class(&mut self, scope: &Scope, at: i32, head: Sid) -> Option<i32> {
        if at == -1 {
            return None;
        }
        let facts = self.facts.clone();
        let defs = &facts.files[scope.file as usize].defs;
        let method = &defs[at as usize];
        if method.is_class() || method.s == -1 || !defs[method.s as usize].is_class() {
            return None;
        }
        let first = method.params.first().filter(|p| p.positional)?;
        if first.name != head {
            return None;
        }
        let not_method = method.dec.iter().any(|dec| match dec {
            V::Chain(name) => {
                let last = self.it.last(*name);
                NOT_METHODS.contains(&self.it.s(last))
            }
            _ => false,
        });
        (!not_method).then_some(method.s)
    }

    /// `head.attr.rest` at a site of a method whose first parameter is `head`: the member `rest`
    /// of the class `attr` holds. None when that class cannot be told.
    pub fn instance_member(
        &mut self,
        names: &LookupNames,
        scope: &mut Scope,
        head: Sid,
        attrs: &[Sid],
        at: i32,
    ) -> Option<HitId> {
        let [attr, rest @ ..] = attrs else {
            return None;
        };
        if rest.is_empty() {
            return None;
        }
        let class = self.method_class(scope, at, head)?;
        let held = self.attr_class(names, scope, class, *attr)?;
        let symbol = self.it.join_all(held.s, rest);
        Some(self.make_hit(
            &[Ent {
                m: held.m,
                s: symbol,
            }],
            HitKind::Member,
        ))
    }

    /// The one class instance attribute `attr` of class def `class` holds.
    fn attr_class(
        &mut self,
        names: &LookupNames,
        scope: &mut Scope,
        class: i32,
        attr: Sid,
    ) -> Option<Ent> {
        let facts = self.facts.clone();
        let record = &facts.files[scope.file as usize];
        let mut annotations: Vec<Source> = Vec::new();
        let mut values: Vec<Source> = Vec::new();
        for row in &record.assigns {
            if row.s != class || !matches!(row.targets[..], [V::Chain(name)] if name == attr) {
                continue;
            }
            if let Some(ann) = &row.ann {
                annotations.push(Source {
                    at: class,
                    line: row.l,
                    value: ann,
                });
            }
            if let Some(value) = &row.value {
                values.push(Source {
                    at: class,
                    line: row.l,
                    value,
                });
            }
        }
        for (at, node) in record.defs.iter().enumerate() {
            if node.s != class {
                continue;
            }
            if node.n == attr {
                return None;
            }
            for row in node.attrs.iter().filter(|row| row.name == attr) {
                let at = at as i32;
                if let Some(ann) = &row.ann {
                    annotations.push(Source {
                        at,
                        line: row.line,
                        value: ann,
                    });
                }
                values.push(Source {
                    at,
                    line: row.line,
                    value: &row.value,
                });
            }
        }
        let annotated = !annotations.is_empty();
        let sources = if annotated { annotations } else { values };
        let mut found: Option<Ent> = None;
        for source in &sources {
            let class = if annotated {
                self.annotation_class(names, scope, source)
            } else {
                self.value_class(names, scope, source)
            }?;
            if found.is_some_and(|known| known != class) {
                return None;
            }
            found = Some(class);
        }
        found
    }

    fn class_at(
        &mut self,
        names: &LookupNames,
        scope: &mut Scope,
        value: &V,
        at: i32,
        line: u32,
    ) -> Option<Ent> {
        let hit = self.value_lookup(names, scope, value, at, line)?.hit?;
        (self.kind(hit) == HitKind::Class).then(|| self.origin(hit))
    }

    fn annotation_class(
        &mut self,
        names: &LookupNames,
        scope: &mut Scope,
        source: &Source,
    ) -> Option<Ent> {
        match source.value {
            V::Chain(_) | V::Str(_, Some(_)) => {
                self.class_at(names, scope, source.value, source.at, source.line)
            }
            _ => None,
        }
    }

    /// The class of an instance a value makes: a call of a class, or an item of a module-level
    /// container.
    fn value_class(
        &mut self,
        names: &LookupNames,
        scope: &mut Scope,
        source: &Source,
    ) -> Option<Ent> {
        match source.value {
            V::Call(callee, _, _) => self.class_at(names, scope, callee, source.at, source.line),
            V::Sub(container, _) => {
                let hit = self
                    .value_lookup(names, scope, container, source.at, source.line)?
                    .hit?;
                if self.kind(hit) != HitKind::Variable {
                    return None;
                }
                self.item_class(self.origin(hit))
            }
            _ => None,
        }
    }

    /// The class of an item of a module-level container `variable`, from the one assignment that
    /// binds it: its annotation, else its literal items.
    fn item_class(&mut self, variable: Ent) -> Option<Ent> {
        if self.it.s(variable.s).contains('.') {
            return None;
        }
        self.rec.read(Kind::Alias, variable.m, variable.s);
        let file = self.binding_file(variable.m, variable.s)?;
        let facts = self.facts.clone();
        let rows: Vec<&Assign> = facts.files[file as usize]
            .assigns
            .iter()
            .filter(|row| {
                row.s == -1
                    && row
                        .targets
                        .iter()
                        .any(|t| matches!(t, V::Chain(n) if *n == variable.s))
            })
            .collect();
        let [row] = rows[..] else {
            return None;
        };
        match &row.ann {
            Some(ann) => self.annotated_item(variable.m, ann),
            None => self.literal_item(variable.m, row.value.as_ref()?),
        }
    }

    fn annotated_item(&mut self, module: Sid, ann: &V) -> Option<Ent> {
        let V::Sub(generic, args) = ann else {
            return None;
        };
        let V::Chain(generic) = &**generic else {
            return None;
        };
        let last = self.it.last(*generic);
        let last = self.it.s(last);
        let item = match &**args {
            V::Tuple(items) if MAPPINGS.contains(&last) && items.len() == 2 => &items[1],
            V::Tuple(items) if SEQUENCES.contains(&last) => match &items[..] {
                [item, V::Opaque(dots)] if self.it.s(*dots) == "..." => item,
                _ => return None,
            },
            item if SEQUENCES.contains(&last) => item,
            _ => return None,
        };
        self.resolve_class(module, item)
    }

    fn literal_item(&mut self, module: Sid, value: &V) -> Option<Ent> {
        let items: Vec<&V> = match value {
            V::Dict(pairs) if pairs.iter().all(|(key, _)| key.is_some()) => {
                pairs.iter().map(|(_, item)| item).collect()
            }
            V::List(items) | V::Tuple(items) => items.iter().collect(),
            _ => return None,
        };
        let mut found: Option<Ent> = None;
        for item in items {
            let V::Call(callee, _, _) = item else {
                return None;
            };
            let class = self.resolve_class(module, callee)?;
            if found.is_some_and(|known| known != class) {
                return None;
            }
            found = Some(class);
        }
        found
    }
}
