//! What a signature rule adds to an edge rule (hoglint/rules/edges.py `SignaturePrimitive`): the
//! edges it sees, and the destinations whose type it cannot use.

use super::env::{Cx, Endpoint};
use crate::link::Link;
use crate::link::intern::{EMPTY, Sid};
use crate::link::resolve::Ent;
use rustc_hash::{FxHashMap, FxHashSet};

/// The producer names of core edges start with this (`index.CORE_PRODUCER_PREFIX`).
const CORE_PRODUCER_PREFIX: &str = "core-";

/// (callable, slot, targets) of an `exposes` edge (`edges.slot_key`).
type SlotKey = (Sid, Sid, Vec<Ent>);

pub struct Signature {
    /// Destination symbols whose type reads as unknown (`RuleSpec.unknown_types`).
    pub unknown_types: Vec<String>,
    /// The interned name of the `annotation` attr, when any edge has one.
    pub annotation: Option<Sid>,
}

/// A callable is private when any part of its qualname starts with `_`, dunders excepted
/// (`edges.is_private`).
fn is_private(callable: &str) -> bool {
    callable
        .split('.')
        .any(|part| part.starts_with('_') && !part.ends_with("__"))
}

fn is_core(link: &Link, id: u32) -> bool {
    link.core
        .it
        .s(link.edges.producer_of(id))
        .starts_with(CORE_PRODUCER_PREFIX)
}

/// The interned value of attr `name` of edge `id`, if it has one.
fn attr_sid(link: &Link, id: u32, name: Option<Sid>) -> Option<Sid> {
    let name = name?;
    link.edges
        .attrs_with(id)
        .find(|(key, _)| *key == name)
        .map(|(_, value)| *value)
}

/// The value of attr `name` of edge `id`; empty when it has none.
fn attr(link: &Link, id: u32, name: Option<Sid>) -> &str {
    attr_sid(link, id, name).map_or("", |value| link.core.it.s(value))
}

/// The edge's slot key: a pack names the callable by its site's scope.
fn slot_key(link: &Link, id: u32, callable: Option<Sid>, slot: Option<Sid>) -> SlotKey {
    let named = attr_sid(link, id, callable).filter(|value| !link.core.it.s(*value).is_empty());
    let callable = named.unwrap_or_else(|| link.site_of(id).1);
    let slot = attr_sid(link, id, slot).unwrap_or(EMPTY);
    (callable, slot, link.edges.targets_of(id).to_vec())
}

impl Signature {
    /// `SignaturePrimitive.sees` past what every edge rule sees: the edges whose callable is
    /// public, less a pack's edge for a slot a core `exposes` edge of its module names (the slot
    /// is judged once, from the core edge). Keeps the order of `ids`.
    pub fn seen(&self, link: &mut Link, ids: Vec<u32>) -> Vec<u32> {
        let it = &link.core.it;
        let (callable, slot) = (it.get("callable"), it.get("slot"));
        let exposes = link.kind_sid("exposes");
        let mut core_slots: FxHashMap<Sid, FxHashSet<SlotKey>> = FxHashMap::default();
        let mut out = Vec::with_capacity(ids.len());
        for id in ids {
            if is_private(attr(link, id, callable)) {
                continue;
            }
            if is_core(link, id) {
                out.push(id);
                continue;
            }
            let module = link.module_of_edge(id);
            let named = core_slots.entry(module).or_insert_with(|| {
                let others = match exposes {
                    Some(kind) => link.ids_from(kind, module),
                    None => Vec::new(),
                };
                others
                    .into_iter()
                    .filter(|&other| is_core(link, other))
                    .map(|other| slot_key(link, other, callable, slot))
                    .collect()
            });
            if !named.contains(&slot_key(link, id, callable, slot)) {
                out.push(id);
            }
        }
        out
    }

    /// `SignaturePrimitive.unknown_type`: the slot has no annotation, or its type is one the rule
    /// reads as unknown.
    pub fn unknown_type(&self, cx: &Cx, id: u32, dst: &Endpoint) -> bool {
        if cx.attr(id, self.annotation) == "missing" {
            return true;
        }
        let symbol = cx.symbol_text(dst.symbol).unwrap_or_default();
        self.unknown_types.iter().any(|name| *name == symbol)
    }
}
