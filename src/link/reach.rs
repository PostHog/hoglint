//! Reach over the link's adjacency (D25): a breadth-first search from each start module through
//! the edges a reach rule follows. Hops are taken in the order the Python search takes them, so
//! each start reports the same targets through the same witness paths. Python decides what a hop
//! means (its verdict class, the verdict of each start's class against it); the link walks.

use super::Link;
use super::groups::Role;
use super::intern::{EMPTY, Sid};
use super::scope::Res;
use rustc_hash::{FxHashMap, FxHashSet};
use std::collections::VecDeque;

/// A verdict code (`Link::reach`): the `deny` truth in the low bits, `unless` excusing on top.
pub const VERDICT_FALSE: u8 = 0;
pub const VERDICT_TRUE: u8 = 1;
pub const VERDICT_UNKNOWN: u8 = 2;
pub const EXCUSED: u8 = 4;

/// One edge out of a module: its destination as the Python endpoint names it, and the class
/// of its verdict.
struct Hop {
    edge: u32,
    /// The destination module the search walks on to; None for an unresolved or external one.
    next: Option<Sid>,
    /// `Endpoint.module` and `Endpoint.symbol`, EMPTY for None: the order hops are taken in.
    module: Sid,
    symbol: Sid,
    surface: Sid,
    unresolved: bool,
    class: u32,
}

/// What a search found, in the order it found it.
pub enum Found {
    /// A hop whose destination did not resolve.
    Unresolved { source: u32, edge: u32 },
    /// A denied target, first reached along `path` (edge ids from the start module).
    Denied {
        source: u32,
        path: Vec<u32>,
        verdict: u8,
    },
}

impl Link {
    /// `Endpoint.module`, `Endpoint.symbol` (EMPTY for None) of edge `id`'s destination as the
    /// Python engine makes it for `role`, and whether it is unresolved and external.
    fn dst_names(&mut self, id: u32, role: Role) -> (Sid, Sid, bool, bool) {
        let (res, surface) = (self.edges.res_of(id), self.edges.surface_of(id));
        match role {
            Role::Origin => match self.edges.target_of(id) {
                Some(target) if res != Res::Unresolved => {
                    let external = res == Res::External;
                    let symbol = if external || target.s != EMPTY {
                        self.core.ent_sid(target)
                    } else {
                        EMPTY
                    };
                    (target.m, symbol, false, external)
                }
                _ => (EMPTY, surface, true, false),
            },
            Role::Surface => {
                let module = self.repo_module_of(surface);
                if res == Res::External {
                    return (module.unwrap_or(surface), surface, false, true);
                }
                match module {
                    Some(module) => (
                        module,
                        if module == surface { EMPTY } else { surface },
                        false,
                        false,
                    ),
                    None => (EMPTY, surface, true, false),
                }
            }
        }
    }

    /// The hops out of each source module: `ids` in the order Python lists a module's hops (kind
    /// by kind, then graph order), stably sorted by destination module, symbol and surface.
    fn hops_by_module(
        &mut self,
        ids: &[u32],
        classes: &[u32],
        role: Role,
    ) -> FxHashMap<Sid, Vec<Hop>> {
        let mut out: FxHashMap<Sid, Vec<Hop>> = FxHashMap::default();
        for (&id, &class) in ids.iter().zip(classes) {
            let (module, symbol, unresolved, external) = self.dst_names(id, role);
            let hop = Hop {
                edge: id,
                next: (!unresolved && !external).then_some(module),
                module,
                symbol,
                surface: self.edges.surface_of(id),
                unresolved,
                class,
            };
            out.entry(self.module_of_edge(id)).or_default().push(hop);
        }
        let it = &self.core.it;
        for hops in out.values_mut() {
            hops.sort_by(|a, b| {
                (it.s(a.module), it.s(a.symbol), it.s(a.surface)).cmp(&(
                    it.s(b.module),
                    it.s(b.symbol),
                    it.s(b.surface),
                ))
            });
        }
        out
    }

    /// Searches from each source (module, class of its verdicts) over the hops `ids` with their
    /// verdict classes. `verdicts` holds a code per (source class, hop class), `width` hop
    /// classes per source class. A denied target is reported once per source and not walked
    /// through; an unresolved hop is reported each time a search meets it. None when `classes`
    /// or `verdicts` is short.
    pub fn reach(
        &mut self,
        ids: &[u32],
        classes: &[u32],
        role: Role,
        sources: &[(Sid, u32)],
        verdicts: &[u8],
        width: usize,
    ) -> Option<Vec<Found>> {
        if classes.len() != ids.len() {
            return None;
        }
        let hops = self.hops_by_module(ids, classes, role);
        let mut out = Vec::new();
        for (at, &(source, origin)) in sources.iter().enumerate() {
            let at = at as u32;
            let mut parents: FxHashMap<Sid, Option<(Sid, u32)>> = FxHashMap::default();
            parents.insert(source, None);
            let mut reported: FxHashSet<Sid> = FxHashSet::default();
            let mut queue = VecDeque::from([source]);
            while let Some(module) = queue.pop_front() {
                for hop in hops.get(&module).map_or(&[][..], Vec::as_slice) {
                    if hop.unresolved {
                        out.push(Found::Unresolved {
                            source: at,
                            edge: hop.edge,
                        });
                        continue;
                    }
                    let code = *verdicts.get(origin as usize * width + hop.class as usize)?;
                    let target = if hop.symbol != EMPTY {
                        hop.symbol
                    } else {
                        hop.module
                    };
                    if code & !EXCUSED != VERDICT_FALSE
                        && code & EXCUSED == 0
                        && reported.insert(target)
                    {
                        let mut path = vec![hop.edge];
                        let mut step = parents.get(&module).copied().flatten();
                        while let Some((previous, edge)) = step {
                            path.push(edge);
                            step = parents.get(&previous).copied().flatten();
                        }
                        path.reverse();
                        out.push(Found::Denied {
                            source: at,
                            path,
                            verdict: code & !EXCUSED,
                        });
                        continue;
                    }
                    let Some(next) = hop.next else { continue };
                    if parents.contains_key(&next) {
                        continue;
                    }
                    parents.insert(next, Some((module, hop.edge)));
                    queue.push_back(next);
                }
            }
        }
        Some(out)
    }
}
