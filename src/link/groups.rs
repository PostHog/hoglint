//! Grouping core edges for rule evaluation. Python judges one representative per group and
//! keeps or drops the whole group, so a rule reads each label class once instead of each edge.
//!
//! Keys are coarse on purpose: a key holds exactly what decides the value of the fields a rule
//! reads at its granularity. Python computes those values from a real representative edge, so
//! the link never re-implements a rule's meaning.

use super::Link;
use super::heap;
use super::intern::{EMPTY, Sid};
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

/// Which end of an edge a key names: the source, or the destination as a rule role reads it.
#[derive(Clone, Copy, PartialEq, Eq, Hash)]
pub enum End {
    Src,
    Dst(Role),
}

/// Stable ids for rule endpoints at two levels (D30), kept for the life of the link so Python
/// works out each key's label values once for every rule.
/// - A basis is what an endpoint's module-level values depend on: its module (or the marker of
///   an unresolved endpoint), whether it is external, and for a source its record. Every end
///   shares one basis table.
/// - An entity key is a basis plus the entity (a source's scope, a destination's symbol), for
///   the values that depend on the entity. Each end has its own table.
#[derive(Default)]
pub struct EndpointTables {
    bases: Table,
    entities: FxHashMap<End, Table>,
    /// `repo_module_of` per dotted name: surfaces repeat across edges and rules.
    module_of: FxHashMap<Sid, Option<Sid>>,
}

#[derive(Default)]
struct Table {
    ids: FxHashMap<[u32; 2], u32>,
    keys: Vec<[u32; 2]>,
}

/// Stable keys of some ids: the key of each id, the table size before the call, the
/// representative id of each key the call added, and the distinct keys in first-seen order.
pub struct Stable {
    pub key_of: Vec<u32>,
    pub known: u32,
    pub added: Vec<u32>,
    pub distinct: Vec<u32>,
}

impl Table {
    fn heap(&self) -> usize {
        heap::map(&self.ids) + heap::vec(&self.keys)
    }

    fn stable(&mut self, ids: &[u32], keys: Vec<[u32; 2]>) -> Stable {
        let known = self.keys.len() as u32;
        let mut out = Stable {
            key_of: Vec::with_capacity(ids.len()),
            known,
            added: Vec::new(),
            distinct: Vec::new(),
        };
        let mut seen = FxHashSet::default();
        for (&id, key) in ids.iter().zip(keys) {
            let next = self.keys.len() as u32;
            let found = *self.ids.entry(key).or_insert(next);
            if found == next {
                self.keys.push(key);
                out.added.push(id);
            }
            if seen.insert(found) {
                out.distinct.push(found);
            }
            out.key_of.push(found);
        }
        out
    }
}

/// The record slot of a destination basis: a destination's module values never depend on the
/// edge's own file, only on whether it is external. No record key reaches these
/// (`Link::file_key`).
const DST_INTERNAL: u32 = u32::MAX;
const DST_EXTERNAL: u32 = u32::MAX - 1;
/// The entity slot of a masked entity key: no interned string has this id.
const NO_ENTITY: Sid = u32::MAX;

/// The dst endpoint of an edge at module or entity granularity: (module or a marker for an
/// unresolved endpoint, external flag, entity or EMPTY).
type DstKey = (Sid, bool, Sid);
const UNRESOLVED_MODULE: Sid = u32::MAX;

impl EndpointTables {
    /// Estimated heap bytes (`Link::heap`).
    pub fn heap(&self) -> usize {
        let entities: usize = self.entities.values().map(Table::heap).sum();
        self.bases.heap() + heap::map(&self.entities) + entities + heap::map(&self.module_of)
    }
}

impl Link {
    /// Edges a rule sees: kind in `kinds` (every kind when empty), resolution not
    /// not-applicable, contexts meeting `include` (any bit) and avoiding `exclude`.
    pub fn candidates(&mut self, kinds: &[Sid], include: u8, exclude: u8) -> Vec<u32> {
        let mut out = Vec::new();
        let lists: Vec<Vec<u32>> = if kinds.is_empty() {
            vec![(0..self.edges.len() as u32).collect()]
        } else {
            kinds
                .iter()
                .map(|&kind| self.index.of_kind(&self.edges, kind).to_vec())
                .collect()
        };
        let edges = &self.edges;
        for list in lists {
            for id in list {
                if edges.res_of(id) == Res::NotApplicable {
                    continue;
                }
                let ctx = edges.seen_ctx(id);
                if include != 0 && ctx & include == 0 {
                    continue;
                }
                if ctx & exclude != 0 {
                    continue;
                }
                out.push(id);
            }
        }
        out.sort_unstable();
        out
    }

    /// `ids` split into those a group may speak for and the irregular ones (several targets,
    /// or attrs that override endpoint labels), which rules judge one by one.
    pub fn split_irregular(&self, ids: &[u32]) -> (Vec<u32>, Vec<u32>) {
        ids.iter().partition(|&&id| !self.edges.is_irregular(id))
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

    fn dst_key(&mut self, id: u32, role: Role, entity: bool) -> DstKey {
        let (res, surface) = (self.edges.res_of(id), self.edges.surface_of(id));
        match role {
            Role::Origin => match self.edges.target_of(id) {
                Some(target) if res != Res::Unresolved => {
                    let symbol = if entity {
                        self.core.ent_sid(target)
                    } else {
                        EMPTY
                    };
                    (target.m, res == Res::External, symbol)
                }
                _ => (
                    UNRESOLVED_MODULE,
                    false,
                    if entity { surface } else { EMPTY },
                ),
            },
            Role::Surface => {
                let symbol = if entity { surface } else { EMPTY };
                let module = self.repo_module_of(surface);
                if res == Res::External {
                    return (module.unwrap_or(surface), true, symbol);
                }
                match module {
                    Some(module) => (module, false, symbol),
                    None => (UNRESOLVED_MODULE, false, symbol),
                }
            }
        }
    }

    /// The src key of edge `id`: its module and record, and its scope when `scope` is set.
    fn src_key(&self, id: u32, scope: bool) -> [u32; 3] {
        let (module, scope_name) = self.site_of(id);
        [
            module,
            self.file_key(id),
            if scope { scope_name } else { EMPTY },
        ]
    }

    /// Src keys (the edge's module and record, or with its scope too) and dst keys of `ids`.
    pub fn endpoint_keys(
        &mut self,
        ids: &[u32],
        role: Role,
        src_scope: bool,
        dst_entity: bool,
    ) -> (Keyed, Keyed) {
        let src = Keyed::of(ids, |id| self.src_key(id, src_scope));
        let dst_keys: Vec<DstKey> = ids
            .iter()
            .map(|&id| self.dst_key(id, role, dst_entity))
            .collect();
        let mut at = 0;
        let dst = Keyed::of(ids, |_| {
            let key = dst_keys[at];
            at += 1;
            key
        });
        (src, dst)
    }

    /// The basis of edge `id` at `end` (see `EndpointTables`) as a table key.
    fn basis_key(&mut self, id: u32, end: End) -> [u32; 2] {
        match end {
            End::Src => {
                let (module, _) = self.site_of(id);
                [module, self.file_key(id)]
            }
            End::Dst(role) => {
                let (module, external, _) = self.dst_key(id, role, false);
                [module, if external { DST_EXTERNAL } else { DST_INTERNAL }]
            }
        }
    }

    /// The entity of edge `id` at `end`: a source's scope name, a destination's symbol.
    fn entity_of(&mut self, id: u32, end: End) -> Sid {
        match end {
            End::Src => self.site_of(id).1,
            End::Dst(role) => self.dst_key(id, role, true).2,
        }
    }

    /// The stable basis of each id at `end`.
    pub fn end_bases(&mut self, ids: &[u32], end: End) -> Stable {
        let keys: Vec<[u32; 2]> = ids.iter().map(|&id| self.basis_key(id, end)).collect();
        self.endpoints.bases.stable(ids, keys)
    }

    /// The stable entity key of each id at `end`, given its basis. With `mask` (one byte per
    /// basis), a basis whose byte is zero keeps no entity: its endpoints share one key, which no
    /// key with an entity meets. None when `basis_of` or `mask` is short.
    pub fn end_entities(
        &mut self,
        ids: &[u32],
        end: End,
        basis_of: &[u32],
        mask: Option<&[u8]>,
    ) -> Option<Stable> {
        if basis_of.len() != ids.len() {
            return None;
        }
        let mut keys = Vec::with_capacity(ids.len());
        for (&id, &basis) in ids.iter().zip(basis_of) {
            let keep = match mask {
                Some(mask) => *mask.get(basis as usize)? != 0,
                None => true,
            };
            let entity = if keep {
                self.entity_of(id, end)
            } else {
                NO_ENTITY
            };
            keys.push([basis, entity]);
        }
        Some(
            self.endpoints
                .entities
                .entry(end)
                .or_default()
                .stable(ids, keys),
        )
    }

    /// The basis of each entity key of `end` in `keys`; None for a key the table does not hold.
    pub fn entity_bases(&self, end: End, keys: &[u32]) -> Option<Vec<u32>> {
        let table = self.endpoints.entities.get(&end)?;
        keys.iter()
            .map(|&key| table.keys.get(key as usize).map(|k| k[0]))
            .collect()
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

    /// Edge keys over the edge fields a rule reads: `kind`, `producer`, `context`,
    /// `resolution`, `use`, `surface`, `shape`, `shape_detail`, `label` (kind, shape and detail;
    /// whether the destination resolved is in its dst key), or an attr name, refinements
    /// included.
    pub fn edge_keys(&mut self, ids: &[u32], fields: &[String]) -> Keyed {
        let attr_keys: Vec<Option<Sid>> =
            fields.iter().map(|name| self.core.it.get(name)).collect();
        let edges = &self.edges;
        Keyed::of(ids, |id| {
            let (shape, detail) = edges.shape_of(id);
            let mut key: Vec<u32> = Vec::with_capacity(fields.len() + 2);
            key.push(edges.kind_of(id));
            for (name, attr) in fields.iter().zip(&attr_keys) {
                match name.as_str() {
                    "kind" => key.push(0),
                    "producer" => key.push(edges.producer_of(id)),
                    "shape" => key.push(shape),
                    "shape_detail" => key.push(detail),
                    "label" => key.extend([shape, detail]),
                    "context" => key.push(edges.ctx_of(id) as u32),
                    "resolution" => key.push(edges.res_of(id) as u32),
                    "use" => key.push(edges.use_of(id)),
                    "surface" => key.push(edges.surface_of(id)),
                    _ => key.push(
                        attr.and_then(|a| edges.attrs_with(id).find(|(k, _)| *k == a))
                            .map_or(EMPTY, |(_, v)| *v),
                    ),
                }
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
