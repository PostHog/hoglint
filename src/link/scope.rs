//! Per-file scopes, contexts and name lookup at a site (hoglint/core/sites.py and the lookups of
//! hoglint/core/producers.py BuildContext).

use super::facts::V;
use super::heap;
use super::intern::{EMPTY, Sid};
use super::resolve::{
    Binding, Core, Ent, FUNC, HitId, HitKind, IF, TC_ELSE, TRY, TYPE_ONLY, import_binding,
    unpacked_names,
};
use rustc_hash::{FxHashMap, FxHashSet};

/// Edge contexts as bits; names in `CONTEXT_NAMES` order.
pub mod ctx {
    pub const PROD: u8 = 1;
    pub const TEST: u8 = 2;
    pub const MIGRATION: u8 = 4;
    pub const TYPE_ONLY: u8 = 8;
    pub const FUNCTION_LOCAL: u8 = 16;
    pub const CONDITIONAL: u8 = 32;
    pub const TYPE_CHECKING_ELSE: u8 = 64;
}

pub const CONTEXT_NAMES: [&str; 7] = [
    "prod",
    "test",
    "migration",
    "type_only",
    "function_local",
    "conditional",
    "type_checking_else",
];

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Res {
    Resolved,
    Unresolved,
    External,
    NotApplicable,
    /// Only pack edges have it: a target among candidates.
    Candidates,
}

impl Res {
    pub fn name(self) -> &'static str {
        match self {
            Res::Resolved => "resolved",
            Res::Unresolved => "unresolved",
            Res::External => "external",
            Res::NotApplicable => "not-applicable",
            Res::Candidates => "candidates",
        }
    }

    pub fn of_name(name: &str) -> Option<Res> {
        [
            Res::Resolved,
            Res::Unresolved,
            Res::External,
            Res::NotApplicable,
            Res::Candidates,
        ]
        .into_iter()
        .find(|res| res.name() == name)
    }
}

/// A name at a site: the dotted surface it stands for and what it resolves to. `local` marks a
/// name no scope binds, used inside a function: a parameter or local variable.
#[derive(Clone, Copy, Debug)]
pub struct Lookup {
    pub surface: Sid,
    pub hit: Option<HitId>,
    pub local: bool,
}

/// An edge's resolution, target and the hit whose chain gives `via`.
#[derive(Clone, Copy, Debug)]
pub struct Resolved {
    pub res: Res,
    pub target: Option<Ent>,
    pub via: Option<HitId>,
}

pub const UNRESOLVED: Resolved = Resolved {
    res: Res::Unresolved,
    target: None,
    via: None,
};

/// Scopes, contexts and bindings of one analyzed file.
pub struct Scope {
    pub file: u32,
    pub module: Sid,
    pub is_package: bool,
    pub qualnames: Vec<Sid>,
    pub in_function: Vec<bool>,
    pub base_ctx: u8,
    local_bindings: FxHashMap<(i32, Sid), Vec<(u32, Binding)>>,
    members: FxHashMap<(i32, Sid), (HitKind, Sid)>,
    scoped_names: FxHashSet<Sid>,
    module_lookups: FxHashMap<Sid, Option<Lookup>>,
    ctx_cache: FxHashMap<(u32, bool), u8>,
}

impl Scope {
    /// Drops the bindings and lookup caches; the def names and flags stay for site names
    /// (`Link::release_facts`).
    pub fn release(&mut self) {
        self.local_bindings = FxHashMap::default();
        self.members = FxHashMap::default();
        self.scoped_names = FxHashSet::default();
        self.module_lookups = FxHashMap::default();
        self.ctx_cache = FxHashMap::default();
    }

    /// Estimated heap bytes (`Link::heap`).
    pub fn heap(&self) -> usize {
        let mut out = heap::vec(&self.qualnames) + heap::vec(&self.in_function);
        out += heap::map(&self.local_bindings)
            + self.local_bindings.values().map(heap::vec).sum::<usize>();
        out += heap::map(&self.members) + heap::set(&self.scoped_names);
        out + heap::map(&self.module_lookups) + heap::map(&self.ctx_cache)
    }

    pub fn scope_name(&self, scope: i32, module_scope: Sid) -> Sid {
        if scope == -1 {
            module_scope
        } else {
            self.qualnames[scope as usize]
        }
    }

    /// The import binding of `name` in exactly this scope, the latest one at or before `line`.
    fn binding_at(&self, scope: i32, name: Sid, line: u32) -> Option<Binding> {
        let rows = self.local_bindings.get(&(scope, name))?;
        let before = rows.iter().rev().find(|(at, _)| *at <= line);
        Some(before.map_or(rows[0].1, |(_, binding)| *binding))
    }
}

/// What lookups read besides the core: builtins and the module scope's name.
pub struct LookupNames {
    pub builtins: FxHashSet<Sid>,
    pub builtins_module: Sid,
}

impl Core {
    pub fn new_scope(&mut self, file: u32, base_ctx: u8) -> Scope {
        let facts = self.facts.clone();
        let record = &facts.files[file as usize];
        let (name, is_package) = self.namer.module_of(&record.path);
        let module = self.it.intern(&name);
        let mut qualnames: Vec<Sid> = Vec::with_capacity(record.defs.len());
        let mut in_function: Vec<bool> = Vec::with_capacity(record.defs.len());
        for d in &record.defs {
            if d.s == -1 {
                qualnames.push(d.n);
                in_function.push(false);
            } else {
                let parent = d.s as usize;
                qualnames.push(self.it.join(qualnames[parent], d.n));
                in_function.push(in_function[parent] || !record.defs[parent].is_class());
            }
        }
        let star = self.star();
        let mut local_bindings: FxHashMap<(i32, Sid), Vec<(u32, Binding)>> = FxHashMap::default();
        for row in &record.imports {
            if let Some((bound, binding)) =
                import_binding(&mut self.it, star, module, is_package, row)
            {
                local_bindings
                    .entry((row.scope, bound))
                    .or_default()
                    .push((row.line, binding));
            }
        }
        let mut members: FxHashMap<(i32, Sid), (HitKind, Sid)> = FxHashMap::default();
        for (i, d) in record.defs.iter().enumerate() {
            if d.s != -1 {
                let kind = if d.is_class() {
                    HitKind::Class
                } else {
                    HitKind::Def
                };
                members.insert((d.s, d.n), (kind, qualnames[i]));
            }
        }
        for row in &record.assigns {
            if row.s == -1 {
                continue;
            }
            for target in &row.targets {
                let mut names = Vec::new();
                unpacked_names(&self.it, target, &mut names);
                for name in names {
                    if let std::collections::hash_map::Entry::Vacant(e) =
                        members.entry((row.s, name))
                    {
                        let qualname = self.it.join(qualnames[row.s as usize], name);
                        e.insert((HitKind::Variable, qualname));
                    }
                }
            }
        }
        let mut scoped_names: FxHashSet<Sid> = members.keys().map(|key| key.1).collect();
        scoped_names.extend(
            local_bindings
                .keys()
                .filter(|key| key.0 != -1)
                .map(|key| key.1),
        );
        Scope {
            file,
            module,
            is_package,
            qualnames,
            in_function,
            base_ctx,
            local_bindings,
            members,
            scoped_names,
            module_lookups: FxHashMap::default(),
            ctx_cache: FxHashMap::default(),
        }
    }

    pub fn is_function_scope(&self, scope: &Scope, at: i32) -> bool {
        if at == -1 {
            return false;
        }
        !self.facts.files[scope.file as usize].defs[at as usize].is_class()
            || scope.in_function[at as usize]
    }

    /// Path contexts plus syntax contexts. Only the body of `if TYPE_CHECKING:` is type-only;
    /// its else branch gets its own context.
    pub fn contexts(&self, scope: &mut Scope, flags: u32, at: i32) -> u8 {
        let key = (flags, self.is_function_scope(scope, at));
        if let Some(&found) = scope.ctx_cache.get(&key) {
            return found;
        }
        let mut found = scope.base_ctx;
        if flags & TYPE_ONLY != 0 {
            found |= ctx::TYPE_ONLY;
        }
        if flags & TC_ELSE != 0 {
            found |= ctx::TYPE_CHECKING_ELSE;
        }
        if flags & FUNC != 0 || key.1 {
            found |= ctx::FUNCTION_LOCAL;
        }
        if flags & (TRY | IF) != 0 {
            found |= ctx::CONDITIONAL;
        }
        scope.ctx_cache.insert(key, found);
        found
    }

    pub fn binding_surface(&mut self, binding: Binding) -> Sid {
        match binding {
            Binding::Module(dotted) => dotted,
            Binding::From(source, attr) => self.it.join(source, attr),
            _ => EMPTY,
        }
    }

    fn module_level(&mut self, scope: &Scope, name: Sid, line: u32) -> Option<Lookup> {
        if let Some(binding) = self.binding_of(scope.module, name) {
            if binding.is_import() {
                let surface = self.binding_surface(binding);
                let hit = self.resolve_binding(scope.module, binding);
                return Some(Lookup {
                    surface,
                    hit,
                    local: false,
                });
            }
            let surface = self.it.join(scope.module, name);
            let hit = self.resolve_name(scope.module, name);
            return Some(Lookup {
                surface,
                hit,
                local: false,
            });
        }
        let binding = scope.binding_at(-1, name, line)?;
        let surface = self.binding_surface(binding);
        let hit = self.resolve_binding(scope.module, binding);
        Some(Lookup {
            surface,
            hit,
            local: false,
        })
    }

    fn module_lookup(&mut self, scope: &mut Scope, name: Sid, line: u32) -> Option<Lookup> {
        if scope
            .local_bindings
            .get(&(-1, name))
            .is_some_and(|rows| rows.len() > 1)
        {
            return self.module_level(scope, name, line);
        }
        if let Some(&found) = scope.module_lookups.get(&name) {
            return found;
        }
        let found = self.module_level(scope, name, line);
        scope.module_lookups.insert(name, found);
        found
    }

    /// Python scoping: enclosing function scopes and the starting class body, then the module.
    fn scoped_lookup(
        &mut self,
        scope: &mut Scope,
        name: Sid,
        at: i32,
        line: u32,
    ) -> Option<Lookup> {
        if at == -1 || !scope.scoped_names.contains(&name) {
            return self.module_lookup(scope, name, line);
        }
        let facts = self.facts.clone();
        let defs = &facts.files[scope.file as usize].defs;
        let mut candidate = at;
        let mut depth = 0;
        while candidate != -1 {
            if !(depth > 0 && defs[candidate as usize].is_class()) {
                if let Some(binding) = scope.binding_at(candidate, name, line) {
                    let surface = self.binding_surface(binding);
                    let hit = self.resolve_binding(scope.module, binding);
                    return Some(Lookup {
                        surface,
                        hit,
                        local: false,
                    });
                }
                if let Some(&(kind, qualname)) = scope.members.get(&(candidate, name)) {
                    let surface = self.it.join(scope.module, qualname);
                    let hit = self.make_hit(
                        &[Ent {
                            m: scope.module,
                            s: qualname,
                        }],
                        kind,
                    );
                    return Some(Lookup {
                        surface,
                        hit: Some(hit),
                        local: false,
                    });
                }
            }
            candidate = defs[candidate as usize].s;
            depth += 1;
        }
        self.module_lookup(scope, name, line)
    }

    /// A bare name; unbound names are builtins or missing module globals.
    pub fn name_lookup(
        &mut self,
        names: &LookupNames,
        scope: &mut Scope,
        name: Sid,
        at: i32,
        line: u32,
    ) -> Lookup {
        if let Some(found) = self.scoped_lookup(scope, name, at, line) {
            return found;
        }
        if names.builtins.contains(&name) {
            let surface = self.it.join(names.builtins_module, name);
            return Lookup {
                surface,
                hit: None,
                local: false,
            };
        }
        if self.is_function_scope(scope, at) {
            return Lookup {
                surface: name,
                hit: None,
                local: true,
            };
        }
        let surface = self.it.join(scope.module, name);
        Lookup {
            surface,
            hit: None,
            local: false,
        }
    }

    /// Follows attributes while the name stands for a module; the surface grows with them. A
    /// missing attribute of a known module joins the surface and leaves the hit empty. Returns
    /// the lookup and where the attributes left over start.
    pub fn names_descend(&mut self, found: Lookup, attrs: &[Sid]) -> (Lookup, usize) {
        if found.hit.is_none() {
            return (found, 0);
        }
        let (mut hit, used) = self.descend(found.hit, attrs);
        let mut surface = self.it.join_all(found.surface, &attrs[..used]);
        let mut rest = used;
        if rest < attrs.len()
            && let Some(current) = hit
            && self.kind(current) == HitKind::Module
        {
            surface = self.it.join(surface, attrs[rest]);
            rest += 1;
            hit = None;
        }
        (
            Lookup {
                surface,
                hit,
                local: false,
            },
            rest,
        )
    }

    /// `head.a.b` at a site: the lookup and the attributes left over.
    pub fn chain_lookup(
        &mut self,
        names: &LookupNames,
        scope: &mut Scope,
        chain: Sid,
        at: i32,
        line: u32,
    ) -> (Lookup, Vec<Sid>) {
        let parts = self.it.segs(chain);
        let first = self.name_lookup(names, scope, parts[0], at, line);
        let (found, rest) = self.names_descend(first, &parts[1..]);
        (found, parts[1 + rest..].to_vec())
    }

    /// A dotted name; attributes past what resolves make a member of the deepest hit.
    pub fn dotted_lookup(
        &mut self,
        names: &LookupNames,
        scope: &mut Scope,
        dotted: Sid,
        at: i32,
        line: u32,
    ) -> Lookup {
        let (found, rest) = self.chain_lookup(names, scope, dotted, at, line);
        if rest.is_empty() {
            return found;
        }
        let surface = self.it.join_all(found.surface, &rest);
        let Some(hit) = found.hit else {
            return Lookup {
                surface,
                hit: None,
                local: found.local,
            };
        };
        let origin = self.origin(hit);
        let symbol = if origin.s != EMPTY {
            self.it.join_all(origin.s, &rest)
        } else {
            self.it.from_segs(&rest)
        };
        let mut chain = self.chain(hit).to_vec();
        chain.push(Ent {
            m: origin.m,
            s: symbol,
        });
        let member = self.make_hit(&chain, HitKind::Member);
        Lookup {
            surface,
            hit: Some(member),
            local: false,
        }
    }

    /// A name-like value tree: a chain, a subscript's value, a call's callee, a string annotation.
    pub fn value_lookup(
        &mut self,
        names: &LookupNames,
        scope: &mut Scope,
        value: &V,
        at: i32,
        line: u32,
    ) -> Option<Lookup> {
        match value {
            V::Chain(chain) => Some(self.dotted_lookup(names, scope, *chain, at, line)),
            V::Sub(inner, _) | V::Call(inner, _, _) => {
                self.value_lookup(names, scope, inner, at, line)
            }
            V::Str(_, Some(parsed)) => self.value_lookup(names, scope, parsed, at, line),
            _ => None,
        }
    }

    /// A surface and a resolver hit as an edge's resolution, target and via. A hit on a module
    /// or name with no sources is external when its head is not repo code.
    pub fn classify(&mut self, surface: Sid, hit: Option<HitId>) -> Resolved {
        if let Some(hit) = hit {
            let origin = self.origin(hit);
            let kind = self.kind(hit);
            let known =
                !matches!(kind, HitKind::Missing | HitKind::Module) || self.index.exists(origin.m);
            if known {
                let res = if self.is_external(origin.m) {
                    Res::External
                } else {
                    Res::Resolved
                };
                return Resolved {
                    res,
                    target: Some(origin),
                    via: Some(hit),
                };
            }
            if self.is_external(origin.m) {
                return Resolved {
                    res: Res::External,
                    target: Some(origin),
                    via: Some(hit),
                };
            }
            return Resolved {
                res: Res::Unresolved,
                target: None,
                via: Some(hit),
            };
        }
        if surface != EMPTY && self.is_external(surface) {
            return Resolved {
                res: Res::External,
                target: Some(Ent {
                    m: surface,
                    s: EMPTY,
                }),
                via: None,
            };
        }
        UNRESOLVED
    }

    pub fn classify_lookup(&mut self, found: Lookup) -> Resolved {
        if found.local {
            return Resolved {
                res: Res::NotApplicable,
                target: None,
                via: None,
            };
        }
        self.classify(found.surface, found.hit)
    }

    /// A dotted path in a string. None unless its head names repo code; else the resolution
    /// and the member text left over.
    pub fn string_dotted(&mut self, dotted: Sid) -> Option<(Resolved, Vec<Sid>)> {
        if self.is_external(dotted) {
            return None;
        }
        let (hit, rest) = self.resolve_dotted(dotted);
        let unresolved = match hit {
            None => true,
            Some(found) => !rest.is_empty() && self.kind(found) == HitKind::Module,
        };
        if unresolved {
            return Some((
                Resolved {
                    res: Res::Unresolved,
                    target: None,
                    via: hit,
                },
                Vec::new(),
            ));
        }
        Some((self.classify(dotted, hit), rest))
    }
}
