//! Which edges a rule reads, and in what order the Python engine judges them.

use crate::link::Link;
use crate::link::intern::Sid;
use crate::link::scope::Res;
use rustc_hash::FxHashMap;

impl Link {
    /// Edges a rule sees (`EdgeRule.sees`): kind in `kinds` (every kind when empty), resolution
    /// not not-applicable, contexts meeting `include` (any bit) and avoiding `exclude`; in id
    /// order.
    pub fn candidates(&mut self, kinds: &[Sid], include: u8, exclude: u8) -> Vec<u32> {
        let lists: Vec<Vec<u32>> = if kinds.is_empty() {
            vec![(0..self.edges.len() as u32).collect()]
        } else {
            kinds
                .iter()
                .map(|&kind| self.index.of_kind(&self.edges, kind).to_vec())
                .collect()
        };
        let edges = &self.edges;
        let mut out: Vec<u32> = lists
            .into_iter()
            .flatten()
            .filter(|&id| {
                let ctx = edges.seen_ctx(id);
                edges.res_of(id) != Res::NotApplicable
                    && (include == 0 || ctx & include != 0)
                    && ctx & exclude == 0
            })
            .collect();
        out.sort_unstable();
        out
    }

    /// Positions into `ids` in the order a rule judges its edges: kind by kind as `kinds` lists
    /// them, then source module by source module in the order each module's first edge of that
    /// kind appears, then graph order. None when an id's kind is not in `kinds`.
    pub fn rule_order(&mut self, ids: &[u32], kinds: &[Sid]) -> Option<Vec<u32>> {
        let rank: FxHashMap<Sid, u32> = kinds
            .iter()
            .enumerate()
            .map(|(at, &kind)| (kind, at as u32))
            .collect();
        let mut first: FxHashMap<(Sid, Sid), u32> = FxHashMap::default();
        let mut keyed = Vec::with_capacity(ids.len());
        for (at, &id) in ids.iter().enumerate() {
            let kind = self.edges.kind_of(id);
            let module = self.module_of_edge(id);
            let start = match first.get(&(kind, module)) {
                Some(&start) => start,
                None => {
                    let start = self.ids_from(kind, module).first().copied().unwrap_or(id);
                    first.insert((kind, module), start);
                    start
                }
            };
            keyed.push((*rank.get(&kind)?, start, id, at as u32));
        }
        keyed.sort_unstable();
        Some(keyed.into_iter().map(|(_, _, _, at)| at).collect())
    }

    /// The edges of `kind` in graph order.
    pub fn ids_of_kind(&mut self, kind: Sid) -> Vec<u32> {
        self.index.of_kind(&self.edges, kind).to_vec()
    }
}
