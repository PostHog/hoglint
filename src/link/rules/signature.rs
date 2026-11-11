//! What a signature rule adds to an edge rule (hoglint/rules/edges.py `SignaturePrimitive`): the
//! edges it sees, and the destinations whose type it cannot use.

use super::env::{Cx, Endpoint, attr_sid};
use crate::link::Link;
use crate::link::intern::Sid;

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

/// The callable an `exposes` edge names: its `callable` attr, else its site's scope, as a pack
/// names it (`attr` is the interned name `callable`).
pub fn callable_of(link: &Link, id: u32, attr: Option<Sid>) -> Sid {
    attr_sid(link, id, attr)
        .filter(|value| !link.core.it.s(*value).is_empty())
        .unwrap_or_else(|| link.site_of(id).1)
}

impl Signature {
    /// `SignaturePrimitive.sees` past what every edge rule sees: the edges whose callable is
    /// public. Keeps the order of `ids`.
    pub fn seen(&self, link: &Link, ids: Vec<u32>) -> Vec<u32> {
        let callable = link.core.it.get("callable");
        ids.into_iter()
            .filter(|&id| {
                let named = attr_sid(link, id, callable).map_or("", |value| link.core.it.s(value));
                !is_private(named)
            })
            .collect()
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
