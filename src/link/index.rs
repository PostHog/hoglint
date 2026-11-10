//! Candidate indexes over every edge, core and pack: by kind, by resolved origin, by source
//! module and by spelling. Each one is built on its first query, and dropped when packs add
//! edges (`EdgeIndex::forget`). Ids are edge positions, so every answer is in graph order.

use super::heap;
use super::intern::{EMPTY, Interner, Sid};
use super::produce::EdgeStore;
use super::resolve::Ent;
use rustc_hash::FxHashMap;

#[derive(Default)]
pub struct EdgeIndex {
    by_kind: Option<FxHashMap<Sid, Vec<u32>>>,
    by_target: FxHashMap<Sid, FxHashMap<Ent, Vec<u32>>>,
    by_source: Option<FxHashMap<(Sid, Sid), Vec<u32>>>,
    source_order: Option<FxHashMap<Sid, Vec<Sid>>>,
    by_spelling: FxHashMap<Sid, FxHashMap<Sid, Vec<u32>>>,
}

/// Sorted union of id lists.
fn merged<'a>(lists: impl Iterator<Item = &'a Vec<u32>>) -> Vec<u32> {
    let mut out: Vec<u32> = lists.flatten().copied().collect();
    out.sort_unstable();
    out.dedup();
    out
}

fn lists_heap<K>(table: &FxHashMap<K, Vec<u32>>) -> usize {
    heap::map(table) + table.values().map(heap::vec).sum::<usize>()
}

impl EdgeIndex {
    /// Estimated heap bytes (`Link::heap`).
    pub fn heap(&self) -> usize {
        let mut out = self.by_kind.as_ref().map_or(0, lists_heap);
        out += heap::map(&self.by_target) + self.by_target.values().map(lists_heap).sum::<usize>();
        out += self.by_source.as_ref().map_or(0, lists_heap);
        if let Some(order) = &self.source_order {
            out += heap::map(order) + order.values().map(heap::vec).sum::<usize>();
        }
        out + self.by_spelling.values().map(lists_heap).sum::<usize>()
    }

    /// Drops every index: the edges changed.
    pub fn forget(&mut self) {
        *self = EdgeIndex::default();
    }

    pub fn of_kind(&mut self, edges: &EdgeStore, kind: Sid) -> &[u32] {
        let by_kind = self.by_kind.get_or_insert_with(|| {
            let mut out: FxHashMap<Sid, Vec<u32>> = FxHashMap::default();
            for id in 0..edges.len() as u32 {
                out.entry(edges.kind_of(id)).or_default().push(id);
            }
            out
        });
        by_kind.get(&kind).map_or(&[], Vec::as_slice)
    }

    /// Edges of `kind` whose resolved origin is one of `targets`. Each kind's index is built on
    /// its first query, over that kind's edges only.
    pub fn to_targets(&mut self, edges: &EdgeStore, kind: Sid, targets: &[Ent]) -> Vec<u32> {
        if !self.by_target.contains_key(&kind) {
            let ids = self.of_kind(edges, kind).to_vec();
            let mut out: FxHashMap<Ent, Vec<u32>> = FxHashMap::default();
            for id in ids {
                for &target in edges.targets_of(id) {
                    let ids = out.entry(target).or_default();
                    if ids.last() != Some(&id) {
                        ids.push(id);
                    }
                }
            }
            self.by_target.insert(kind, out);
        }
        let by_target = &self.by_target[&kind];
        merged(targets.iter().filter_map(|t| by_target.get(t)))
    }

    fn build_sources(&mut self, edges: &EdgeStore, module_of: impl Fn(u32) -> Sid) {
        if self.by_source.is_some() {
            return;
        }
        let mut by_source: FxHashMap<(Sid, Sid), Vec<u32>> = FxHashMap::default();
        let mut order: FxHashMap<Sid, Vec<Sid>> = FxHashMap::default();
        for id in 0..edges.len() as u32 {
            let (kind, module) = (edges.kind_of(id), module_of(id));
            let ids = by_source.entry((kind, module)).or_default();
            if ids.is_empty() {
                order.entry(kind).or_default().push(module);
            }
            ids.push(id);
        }
        self.by_source = Some(by_source);
        self.source_order = Some(order);
    }

    /// Edges of `kind` from one source module.
    pub fn from_module(
        &mut self,
        edges: &EdgeStore,
        module_of: impl Fn(u32) -> Sid,
        kind: Sid,
        module: Sid,
    ) -> &[u32] {
        self.build_sources(edges, module_of);
        let by_source = self.by_source.as_ref().expect("built");
        by_source.get(&(kind, module)).map_or(&[], Vec::as_slice)
    }

    /// Source modules of `kind`, in the order their first edge appears.
    pub fn source_modules(
        &mut self,
        edges: &EdgeStore,
        module_of: impl Fn(u32) -> Sid,
        kind: Sid,
    ) -> &[Sid] {
        self.build_sources(edges, module_of);
        let order = self.source_order.as_ref().expect("built");
        order.get(&kind).map_or(&[], Vec::as_slice)
    }

    /// Edges of `kind` whose surface ends in one of `names` (the last dotted part), resolved or
    /// not: what a pack turns into an unknown edge when the origin does not resolve. Each kind's
    /// index is built on its first query, so a pack that asks about one kind reads only its edges.
    pub fn spelled(
        &mut self,
        it: &mut Interner,
        edges: &EdgeStore,
        kind: Sid,
        names: &[Sid],
    ) -> Vec<u32> {
        if !self.by_spelling.contains_key(&kind) {
            let mut out: FxHashMap<Sid, Vec<u32>> = FxHashMap::default();
            let ids = self.of_kind(edges, kind).to_vec();
            for i in ids {
                let surface = edges.surface_of(i);
                if surface != EMPTY {
                    out.entry(it.last(surface)).or_default().push(i);
                }
            }
            self.by_spelling.insert(kind, out);
        }
        let by_spelling = &self.by_spelling[&kind];
        merged(names.iter().filter_map(|name| by_spelling.get(name)))
    }
}
