//! Candidate indexes over the core edges: by kind, by resolved origin, by source module and by
//! spelling. Each one is built on its first query. Ids are edge positions, so every answer is
//! in graph order.

use super::heap;
use super::intern::{EMPTY, Interner, Sid};
use super::produce::{EdgeRow, KIND_NAMES};
use super::resolve::Ent;
use super::scope::Scope;
use rustc_hash::FxHashMap;

/// Edge ids of one kind by (file, line, column, surface).
type BySite = FxHashMap<(u32, u32, u32, Sid), Vec<u32>>;

#[derive(Default)]
pub struct EdgeIndex {
    by_kind: Option<Vec<Vec<u32>>>,
    by_target: Option<FxHashMap<Ent, Vec<u32>>>,
    by_source: Option<FxHashMap<(u8, Sid), Vec<u32>>>,
    source_order: Option<Vec<Vec<Sid>>>,
    by_spelling: FxHashMap<u8, FxHashMap<Sid, Vec<u32>>>,
    by_site: FxHashMap<u8, BySite>,
}

pub fn kind_code(kind: &str) -> Option<u8> {
    KIND_NAMES
        .iter()
        .position(|k| *k == kind)
        .map(|code| code as u8)
}

/// Sorted union of id lists.
fn merged<'a>(lists: impl Iterator<Item = &'a Vec<u32>>) -> Vec<u32> {
    let mut out: Vec<u32> = lists.flatten().copied().collect();
    out.sort_unstable();
    out.dedup();
    out
}

impl EdgeIndex {
    /// Estimated heap bytes (`Link::heap`).
    pub fn heap(&self) -> usize {
        let lists =
            |lists: &Vec<Vec<u32>>| heap::vec(lists) + lists.iter().map(heap::vec).sum::<usize>();
        let mut out = self.by_kind.as_ref().map_or(0, lists);
        if let Some(by_target) = &self.by_target {
            out += heap::map(by_target) + by_target.values().map(heap::vec).sum::<usize>();
        }
        if let Some(by_source) = &self.by_source {
            out += heap::map(by_source) + by_source.values().map(heap::vec).sum::<usize>();
        }
        for by_name in self.by_spelling.values() {
            out += heap::map(by_name) + by_name.values().map(heap::vec).sum::<usize>();
        }
        for by_site in self.by_site.values() {
            out += heap::map(by_site) + by_site.values().map(heap::vec).sum::<usize>();
        }
        out
    }

    pub fn of_kind(&mut self, rows: &[EdgeRow], kind: u8) -> &[u32] {
        let by_kind = self.by_kind.get_or_insert_with(|| {
            let mut out = vec![Vec::new(); KIND_NAMES.len()];
            for (i, row) in rows.iter().enumerate() {
                out[row.kind as usize].push(i as u32);
            }
            out
        });
        &by_kind[kind as usize]
    }

    /// Edges of `kind` whose resolved origin is one of `targets`.
    pub fn to_targets(&mut self, rows: &[EdgeRow], kind: u8, targets: &[Ent]) -> Vec<u32> {
        let by_target = self.by_target.get_or_insert_with(|| {
            let mut out: FxHashMap<Ent, Vec<u32>> = FxHashMap::default();
            for (i, row) in rows.iter().enumerate() {
                if let Some(target) = row.target {
                    out.entry(target).or_default().push(i as u32);
                }
            }
            out
        });
        let mut ids = merged(targets.iter().filter_map(|t| by_target.get(t)));
        ids.retain(|&i| rows[i as usize].kind == kind);
        ids
    }

    fn build_sources(&mut self, rows: &[EdgeRow], scopes: &[Scope]) {
        if self.by_source.is_some() {
            return;
        }
        let mut by_source: FxHashMap<(u8, Sid), Vec<u32>> = FxHashMap::default();
        let mut order = vec![Vec::new(); KIND_NAMES.len()];
        for (i, row) in rows.iter().enumerate() {
            let module = scopes[row.owner as usize].module;
            let ids = by_source.entry((row.kind, module)).or_default();
            if ids.is_empty() {
                order[row.kind as usize].push(module);
            }
            ids.push(i as u32);
        }
        self.by_source = Some(by_source);
        self.source_order = Some(order);
    }

    /// Edges of `kind` from one source module.
    pub fn from_module(
        &mut self,
        rows: &[EdgeRow],
        scopes: &[Scope],
        kind: u8,
        module: Sid,
    ) -> &[u32] {
        self.build_sources(rows, scopes);
        let by_source = self.by_source.as_ref().expect("built");
        by_source.get(&(kind, module)).map_or(&[], Vec::as_slice)
    }

    /// Source modules of `kind`, in the order their first edge appears.
    pub fn source_modules(&mut self, rows: &[EdgeRow], scopes: &[Scope], kind: u8) -> &[Sid] {
        self.build_sources(rows, scopes);
        &self.source_order.as_ref().expect("built")[kind as usize]
    }

    /// Edges of `kind` whose surface ends in one of `names` (the last dotted part), resolved or
    /// not: what a pack turns into an unknown edge when the origin does not resolve. Each kind's
    /// index is built on its first query, so a pack that asks about one kind reads only its edges.
    pub fn spelled(
        &mut self,
        it: &mut Interner,
        rows: &[EdgeRow],
        kind: u8,
        names: &[Sid],
    ) -> Vec<u32> {
        if !self.by_spelling.contains_key(&kind) {
            let mut out: FxHashMap<Sid, Vec<u32>> = FxHashMap::default();
            for &i in self.of_kind(rows, kind) {
                let row = &rows[i as usize];
                if row.surface != EMPTY {
                    out.entry(it.last(row.surface)).or_default().push(i);
                }
            }
            self.by_spelling.insert(kind, out);
        }
        let by_spelling = &self.by_spelling[&kind];
        merged(names.iter().filter_map(|name| by_spelling.get(name)))
    }

    /// Whether a core edge of `kind` sits at (file, line, column) with this surface and target:
    /// the edge a pack edge with the same fields copies.
    pub fn has_twin(
        &mut self,
        rows: &[EdgeRow],
        kind: u8,
        site: (u32, u32, u32),
        surface: Sid,
        target: Option<Ent>,
    ) -> bool {
        let by_site = self.by_site.entry(kind).or_insert_with(|| {
            let mut out: BySite = FxHashMap::default();
            for (i, row) in rows.iter().enumerate() {
                if row.kind == kind {
                    out.entry((row.file, row.line, row.col, row.surface))
                        .or_default()
                        .push(i as u32);
                }
            }
            out
        });
        by_site
            .get(&(site.0, site.1, site.2, surface))
            .is_some_and(|ids| ids.iter().any(|&i| rows[i as usize].target == target))
    }
}
