//! Candidate indexes over the core edges: by kind, by resolved origin, by source module and by
//! spelling. Each one is built on its first query. Ids are edge positions, so every answer is
//! in graph order.

use super::intern::{EMPTY, Interner, Sid};
use super::produce::{EdgeRow, KIND_NAMES};
use super::resolve::Ent;
use super::scope::Scope;
use rustc_hash::FxHashMap;

#[derive(Default)]
pub struct EdgeIndex {
    by_kind: Option<Vec<Vec<u32>>>,
    by_target: Option<FxHashMap<Ent, Vec<u32>>>,
    by_source: Option<FxHashMap<(u8, Sid), Vec<u32>>>,
    source_order: Option<Vec<Vec<Sid>>>,
    by_spelling: Option<FxHashMap<(u8, Sid), Vec<u32>>>,
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
    /// not: what a pack turns into an unknown edge when the origin does not resolve.
    pub fn spelled(
        &mut self,
        it: &mut Interner,
        rows: &[EdgeRow],
        kind: u8,
        names: &[Sid],
    ) -> Vec<u32> {
        if self.by_spelling.is_none() {
            let mut out: FxHashMap<(u8, Sid), Vec<u32>> = FxHashMap::default();
            for (i, row) in rows.iter().enumerate() {
                if row.surface != EMPTY {
                    let last = it.last(row.surface);
                    out.entry((row.kind, last)).or_default().push(i as u32);
                }
            }
            self.by_spelling = Some(out);
        }
        let by_spelling = self.by_spelling.as_ref().expect("built");
        merged(
            names
                .iter()
                .filter_map(|name| by_spelling.get(&(kind, *name))),
        )
    }
}
