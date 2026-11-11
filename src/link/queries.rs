//! Pack queries the link answers from its own rows, so no pack decodes whole records to find
//! its candidates (D23).

use super::Link;
use super::intern::{EMPTY, Sid};
use super::resolve::{Ent, HitKind, absolute_import};
use super::scope::Lookup;
use crate::strings::leading_attrs;
use rustc_hash::{FxHashMap, FxHashSet};

/// A `uses` row whose chain reaches a class among the query's targets.
pub struct UseHit {
    pub scope: u32,
    pub at: i32,
    pub line: u32,
    pub col: u32,
    pub count: u32,
    pub found: Lookup,
}

/// `a or b` for optional names: an empty name counts as absent, as in Python.
pub(crate) fn named(first: Option<Sid>, fallback: Sid) -> Sid {
    first.filter(|&s| s != EMPTY).unwrap_or(fallback)
}

impl Link {
    /// Those of `roots` a first-party import binds anywhere in the file of scope `index`. A name
    /// that only third-party imports bind cannot lead into first-party code.
    fn first_party_roots(&mut self, index: usize, roots: &FxHashSet<Sid>) -> FxHashSet<Sid> {
        let facts = self.core.facts.clone();
        let scope = &self.scopes[index];
        let (file, module, is_package) = (scope.file as usize, scope.module, scope.is_package);
        let it = &mut self.core.it;
        let star = it.intern("*");
        let mut out = FxHashSet::default();
        for row in &facts.files[file].imports {
            let Some(name) = row.name else {
                let Some(target) = row.target else {
                    continue;
                };
                let head = it.head(target);
                let bound = named(row.asname, head);
                if roots.contains(&bound) && self.first_party.contains(&head) {
                    out.insert(bound);
                }
                continue;
            };
            let bound = named(row.asname, name);
            if !roots.contains(&bound) || name == star {
                continue;
            }
            let Some(source) = absolute_import(it, module, is_package, row.target, row.level)
            else {
                continue;
            };
            if self.first_party.contains(&it.head(source)) {
                out.insert(bound);
            }
        }
        out
    }

    /// `import a.b` then `a.b.Model.objects`: every `uses` row with attributes whose root a
    /// first-party import binds, and whose chain resolves to a class among `targets` with at
    /// least one attribute resolved past the root. In scope order, then row order.
    pub fn uses_to(&mut self, targets: &FxHashSet<Ent>) -> Vec<UseHit> {
        let facts = self.core.facts.clone();
        let mut out = Vec::new();
        for index in 0..self.scopes.len() {
            let file = &facts.files[self.scopes[index].file as usize];
            let rows: Vec<_> = file.uses.iter().filter(|row| row.attrs != EMPTY).collect();
            if rows.is_empty() {
                continue;
            }
            let roots = rows.iter().map(|row| row.root).collect();
            let bound = self.first_party_roots(index, &roots);
            for row in rows {
                if !bound.contains(&row.root) {
                    continue;
                }
                let attrs = leading_attrs(self.core.it.s(row.attrs));
                let names = attrs.matches('.').count();
                let chain = format!("{}{attrs}", self.core.it.s(row.root));
                let chain = self.core.it.intern(&chain);
                let (found, rest) = self.core.chain_lookup(
                    &self.settings.names,
                    &mut self.scopes[index],
                    chain,
                    row.scope,
                    row.line,
                );
                let Some(hit) = found.hit else {
                    continue;
                };
                if self.core.kind(hit) != HitKind::Class
                    || rest.len() == names
                    || !targets.contains(&self.core.origin(hit))
                {
                    continue;
                }
                out.push(UseHit {
                    scope: index as u32,
                    at: row.scope,
                    line: row.line,
                    col: row.col,
                    count: row.count,
                    found,
                });
            }
        }
        out
    }

    /// `from x import name` rows of the analyzed files by imported name: (file, row index) in
    /// fact order, then row order.
    pub fn imports_by_name(&self) -> FxHashMap<Sid, Vec<(u32, u32)>> {
        let mut out: FxHashMap<Sid, Vec<(u32, u32)>> = FxHashMap::default();
        for scope in &self.scopes {
            let rows = &self.core.facts.files[scope.file as usize].imports;
            for (at, row) in rows.iter().enumerate() {
                if let Some(name) = row.name {
                    out.entry(name).or_default().push((scope.file, at as u32));
                }
            }
        }
        out
    }

    /// The module each import of a file names, in row order: `import a.b` names a.b, `from a
    /// import b` names a.b. A relative import that climbs out of the repo names nothing.
    pub fn imported_modules(&mut self, file: usize) -> Vec<Sid> {
        let facts = self.core.facts.clone();
        let Some(&scope) = self.scope_of_file.get(&(file as u32)) else {
            return Vec::new();
        };
        let scope = &self.scopes[scope as usize];
        let (module, is_package) = (scope.module, scope.is_package);
        let it = &mut self.core.it;
        let mut out = Vec::new();
        for row in &facts.files[file].imports {
            let Some(name) = row.name else {
                out.extend(row.target);
                continue;
            };
            if let Some(source) = absolute_import(it, module, is_package, row.target, row.level) {
                out.push(it.join(source, name));
            }
        }
        out
    }
}
