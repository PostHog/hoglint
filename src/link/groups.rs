//! Grouping core edges for rule evaluation. Python judges one representative per group and
//! keeps or drops the whole group, so a rule reads each label class once instead of each edge.
//!
//! Keys are coarse on purpose: a key holds exactly what decides the value of the fields a rule
//! reads at its granularity. Python computes those values from a real representative edge, so
//! the link never re-implements a rule's meaning.

use super::Link;
use super::heap;
use super::intern::{EMPTY, Sid};
use super::produce::EdgeRow;
use super::scope::Res;
use rustc_hash::{FxHashMap, FxHashSet};

/// Which end of an edge a rule judges as `dst`.
#[derive(Clone, Copy, PartialEq, Eq, Hash)]
pub enum Role {
    Origin,
    Surface,
}

/// Dense ids for distinct keys, with the first edge of each as its representative.
#[derive(Default)]
pub struct Keyed {
    pub key_of: Vec<u32>,
    pub reps: Vec<u32>,
}

impl Keyed {
    fn of<K: std::hash::Hash + Eq>(ids: &[u32], mut key: impl FnMut(u32) -> K) -> Keyed {
        let mut seen: FxHashMap<K, u32> = FxHashMap::default();
        let mut out = Keyed {
            key_of: Vec::with_capacity(ids.len()),
            reps: Vec::new(),
        };
        for &id in ids {
            let next = out.reps.len() as u32;
            let found = *seen.entry(key(id)).or_insert(next);
            if found == next {
                out.reps.push(id);
            }
            out.key_of.push(found);
        }
        out
    }
}

/// Which endpoint table a key lives in: the end, and its granularity.
#[derive(Clone, Copy, PartialEq, Eq, Hash)]
pub enum Endpoint {
    SrcModule,
    SrcScope,
    Dst(Role, bool),
}

/// Stable ids for endpoints: an endpoint keeps its id for the life of the link, so Python works
/// out an endpoint's label class once for every rule, and rules pass classes by id.
#[derive(Default)]
pub struct EndpointTables {
    tables: FxHashMap<Endpoint, Table>,
    /// `repo_module_of` per dotted name: surfaces repeat across edges and rules.
    module_of: FxHashMap<Sid, Option<Sid>>,
}

#[derive(Default)]
struct Table {
    ids: FxHashMap<[u32; 3], u32>,
    reps: Vec<u32>,
}

/// The dst endpoint of an edge at module or entity granularity: (module or a marker for an
/// unresolved endpoint, external flag, entity or EMPTY).
type DstKey = (Sid, bool, Sid);
const UNRESOLVED_MODULE: Sid = u32::MAX;

impl EndpointTables {
    /// Estimated heap bytes (`Link::heap`).
    pub fn heap(&self) -> usize {
        let tables: usize = self
            .tables
            .values()
            .map(|t| heap::map(&t.ids) + heap::vec(&t.reps))
            .sum();
        heap::map(&self.tables) + tables + heap::map(&self.module_of)
    }
}

impl Link {
    /// Edges a rule sees: kind in `kinds` (every kind when empty), resolution not
    /// not-applicable, contexts meeting `include` (any bit) and avoiding `exclude`.
    pub fn candidates(&mut self, kinds: &[u8], include: u8, exclude: u8) -> Vec<u32> {
        let mut out = Vec::new();
        let lists: Vec<Vec<u32>> = if kinds.is_empty() {
            vec![(0..self.edges.rows.len() as u32).collect()]
        } else {
            kinds
                .iter()
                .map(|&kind| self.index.of_kind(&self.edges.rows, kind).to_vec())
                .collect()
        };
        for list in lists {
            for id in list {
                let row = &self.edges.rows[id as usize];
                if row.res == Res::NotApplicable {
                    continue;
                }
                if include != 0 && row.ctx & include == 0 {
                    continue;
                }
                if row.ctx & exclude != 0 {
                    continue;
                }
                out.push(id);
            }
        }
        out.sort_unstable();
        out
    }

    /// The longest dotted prefix of `dotted` that is a repo module with a file: what
    /// `GraphIndex.module_of_symbol` answers over `graph.modules`.
    fn repo_module_of(&mut self, dotted: Sid) -> Option<Sid> {
        if let Some(&found) = self.endpoints.module_of.get(&dotted) {
            return found;
        }
        let found = self.find_repo_module(dotted);
        self.endpoints.module_of.insert(dotted, found);
        found
    }

    fn find_repo_module(&mut self, dotted: Sid) -> Option<Sid> {
        let parts = self.core.it.segs(dotted);
        for end in (1..=parts.len()).rev() {
            let candidate = self.core.it.from_segs(&parts[..end]);
            if let Some(&file) = self.core.index.path_of.get(&candidate)
                && !self.core.index.site[file as usize]
            {
                return Some(candidate);
            }
        }
        None
    }

    fn dst_key(&mut self, row: EdgeRow, role: Role, entity: bool) -> DstKey {
        match role {
            Role::Origin => match row.target {
                Some(target) if row.res != Res::Unresolved => {
                    let symbol = if entity {
                        self.core.ent_sid(target)
                    } else {
                        EMPTY
                    };
                    (target.m, row.res == Res::External, symbol)
                }
                _ => (
                    UNRESOLVED_MODULE,
                    false,
                    if entity { row.surface } else { EMPTY },
                ),
            },
            Role::Surface => {
                let symbol = if entity { row.surface } else { EMPTY };
                let module = self.repo_module_of(row.surface);
                if row.res == Res::External {
                    return (module.unwrap_or(row.surface), true, symbol);
                }
                match module {
                    Some(module) => (module, false, symbol),
                    None => (UNRESOLVED_MODULE, false, symbol),
                }
            }
        }
    }

    /// Src keys (the edge's file, or file and scope) and dst keys of `ids`.
    pub fn endpoint_keys(
        &mut self,
        ids: &[u32],
        role: Role,
        src_scope: bool,
        dst_entity: bool,
    ) -> (Keyed, Keyed) {
        let rows = &self.edges.rows;
        let src = Keyed::of(ids, |id| {
            let row = &rows[id as usize];
            (row.owner, if src_scope { row.scope } else { -2 })
        });
        let dst_keys: Vec<DstKey> = ids
            .iter()
            .map(|&id| self.dst_key(self.edges.rows[id as usize], role, dst_entity))
            .collect();
        let mut at = 0;
        let dst = Keyed::of(ids, |_| {
            let key = dst_keys[at];
            at += 1;
            key
        });
        (src, dst)
    }

    /// The stable key of each id in table `which`, the representative edges of the keys this
    /// call added (keys `known..`, where `known` is the table size before the call), and the
    /// distinct keys of `ids` in the order they first appear.
    pub fn stable_keys(
        &mut self,
        ids: &[u32],
        which: Endpoint,
    ) -> (Vec<u32>, u32, Vec<u32>, Vec<u32>) {
        let keys: Vec<[u32; 3]> = ids
            .iter()
            .map(|&id| {
                let row = self.edges.rows[id as usize];
                match which {
                    Endpoint::SrcModule => [row.owner, 0, 0],
                    Endpoint::SrcScope => [row.owner, row.scope as u32, 0],
                    Endpoint::Dst(role, entity) => {
                        let (module, external, symbol) = self.dst_key(row, role, entity);
                        [module, external as u32, symbol]
                    }
                }
            })
            .collect();
        let table = self.endpoints.tables.entry(which).or_default();
        let known = table.reps.len() as u32;
        let mut key_of = Vec::with_capacity(ids.len());
        let mut seen = FxHashSet::default();
        let mut distinct = Vec::new();
        for (&id, key) in ids.iter().zip(keys) {
            let next = table.reps.len() as u32;
            let found = *table.ids.entry(key).or_insert(next);
            if found == next {
                table.reps.push(id);
            }
            if seen.insert(found) {
                distinct.push(found);
            }
            key_of.push(found);
        }
        let added = table.reps[known as usize..].to_vec();
        (key_of, known, added, distinct)
    }

    /// The ids whose key's class keeps: `class_of` maps a key to its class, `keep` holds one
    /// flag per class.
    pub fn keep_classes(
        ids: &[u32],
        key_of: &[u32],
        class_of: &[u32],
        keep: &[u8],
    ) -> Option<Vec<u32>> {
        let mut out = Vec::with_capacity(ids.len());
        for (&id, &key) in ids.iter().zip(key_of) {
            let class = *class_of.get(key as usize)?;
            if *keep.get(class as usize)? != 0 {
                out.push(id);
            }
        }
        Some(out)
    }

    /// Edge keys over the edge fields a rule reads: `kind`, `context`, `resolution`, `use`,
    /// `surface`, or an attr name. Other built-in fields (`shape`, `label`, `producer`) follow
    /// from the kind for core edges.
    pub fn edge_keys(&mut self, ids: &[u32], fields: &[String]) -> Keyed {
        let attr_keys: Vec<Option<Sid>> =
            fields.iter().map(|name| self.core.it.get(name)).collect();
        let rows = &self.edges.rows;
        let edges = &self.edges;
        Keyed::of(ids, |id| {
            let row = &rows[id as usize];
            let attrs = edges.attrs_of(row);
            let mut key: Vec<u32> = Vec::with_capacity(fields.len() + 1);
            key.push(row.kind as u32);
            for (name, attr) in fields.iter().zip(&attr_keys) {
                key.push(match name.as_str() {
                    "kind" | "shape" | "label" | "producer" | "shape_detail" => 0,
                    "context" => row.ctx as u32,
                    "resolution" => row.res as u32,
                    "use" => row.use_,
                    "surface" => row.surface,
                    _ => attr
                        .and_then(|a| attrs.iter().find(|(k, _)| *k == a))
                        .map_or(EMPTY, |(_, v)| *v),
                });
            }
            key
        })
    }

    /// Groups `ids` by the classes Python gave their keys: each layer is (key of each id, class
    /// of each key).
    pub fn group(ids: &[u32], layers: &[(Vec<u32>, Vec<u32>)]) -> Keyed {
        let mut at = 0;
        Keyed::of(ids, |_| {
            let key: Vec<u32> = layers
                .iter()
                .map(|(key_of, class_of)| class_of[key_of[at] as usize])
                .collect();
            at += 1;
            key
        })
    }
}
