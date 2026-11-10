//! The paths a coverage rule needs covered (hoglint/rules/entities.py
//! `CoveragePrimitive.required`): another rule's column read off its findings' witnesses, and the
//! endpoints of an edge selection. Whether an outside input covers each path stays with the
//! coverage adapter, a Python plugin.

use super::env::{Cx, Endpoint, Env, Role};
use super::labels::Field;
use super::plan::CoverPlan;
use super::selector::{Compiler, Node, Ref, Resolve, evaluate};
use super::staged::Staged;
use super::value::{Truth, Value, all_of, render};
use crate::link::Link;
use rustc_hash::FxHashMap;

/// One thing a coverage rule needs covered (`CoverSpec`), compiled.
pub enum Cover {
    /// A column of another rule's findings, read off each witness with its first destination.
    Rule { column: Ref },
    /// The `src` or `dst` paths of the edges a selection keeps.
    Edges {
        /// The kinds the selection pins, sorted; every edge when it pins none.
        kinds: Option<Vec<String>>,
        /// The conjuncts that read only an edge's kind and contexts.
        by_edge: Option<Node>,
        /// The other conjuncts, staged as an edge rule's selection is.
        rest: Staged,
        src: bool,
    },
}

impl Cover {
    pub fn new(plan: &CoverPlan, edge: &Compiler, fields: &[Field]) -> Result<Cover, String> {
        if plan.rule.is_some() {
            return Ok(Cover::Rule {
                column: edge.reference(&plan.column),
            });
        }
        let rest = edge.nodes(&plan.rest)?;
        Ok(Cover::Edges {
            kinds: plan.kinds.clone(),
            by_edge: plan
                .by_edge
                .as_ref()
                .map(|node| edge.node(node))
                .transpose()?,
            rest: Staged::new(&rest, fields),
            src: plan.endpoint == "src",
        })
    }

    pub fn refs(&self, out: &mut Vec<Ref>) {
        match self {
            Cover::Rule { column, .. } => out.push(*column),
            Cover::Edges { by_edge, rest, .. } => {
                for node in by_edge.iter().chain(rest.parts.iter()) {
                    node.refs(out);
                }
            }
        }
    }
}

/// What a coverage rule's covers need, as data the rule's Python class words
/// (`CoveragePrimitive`): every path with the cover that names it first and the module that
/// needs it, sorted by path; and in order, what it cannot tell, (what, cover, source module,
/// surface): "not-run" for a cover whose rule did not run, "unplaced" for an edge it cannot place.
#[derive(Default)]
pub struct Required {
    pub paths: Vec<(String, u32, Option<String>)>,
    pub incomplete: Vec<(&'static str, u32, Option<String>, String)>,
}

/// The edge ids each cover reads: the witnesses of the named rule's findings, in order, or None
/// when that rule did not run; every edge of the cover's kinds for an edge selection.
pub type CoverInputs = Vec<Option<Vec<u32>>>;

/// `CoveragePrimitive.required` over the inputs Python gathered (`CoverInputs`).
pub fn required(cx: Cx, covers: &[Cover], inputs: &CoverInputs) -> Required {
    let mut out = Required::default();
    let mut found: FxHashMap<String, (u32, Option<String>)> = FxHashMap::default();
    let mut dsts: Vec<Endpoint> = Vec::with_capacity(2);
    for (at, (cover, ids)) in covers.iter().zip(inputs).enumerate() {
        let at = at as u32;
        match cover {
            Cover::Rule { column } => {
                let Some(ids) = ids else {
                    out.incomplete.push(("not-run", at, None, String::new()));
                    continue;
                };
                for &id in ids {
                    let src = cx.src_endpoint(id);
                    cx.dst_endpoints(id, Role::Origin, &mut dsts);
                    let env = Env::new(cx, Some(id), src, dsts[0]);
                    let value = env.resolve(*column);
                    if matches!(value, Value::Unknown | Value::None) {
                        continue;
                    }
                    found.entry(render(&value)).or_insert((at, None));
                }
            }
            Cover::Edges {
                by_edge,
                rest,
                src: from_src,
                ..
            } => {
                for &id in ids.as_deref().unwrap_or_default() {
                    let src = cx.src_endpoint(id);
                    let edge_truth = match by_edge {
                        Some(node) => evaluate(node, &Env::new(cx, Some(id), src, src)),
                        None => Truth::True,
                    };
                    if edge_truth == Truth::False {
                        continue;
                    }
                    cx.dst_endpoints(id, Role::Origin, &mut dsts);
                    for dst in dsts.iter() {
                        let truth =
                            all_of([edge_truth, rest.truth(&Env::new(cx, Some(id), src, *dst))]);
                        if truth == Truth::False {
                            continue;
                        }
                        let endpoint = if *from_src { &src } else { dst };
                        let module = src.module.map(|module| cx.text(module).to_string());
                        if endpoint.unresolved || truth == Truth::Unknown {
                            let surface = cx.text(cx.link.edges.surface_of(id));
                            out.incomplete
                                .push(("unplaced", at, module, surface.to_string()));
                            continue;
                        }
                        let Some(path) = endpoint.path.filter(|path| !path.is_empty()) else {
                            continue;
                        };
                        if endpoint.external.is_some() {
                            continue;
                        }
                        let owner = endpoint.module.map(|module| cx.text(module).to_string());
                        found.entry(path.to_string()).or_insert((at, owner));
                    }
                }
            }
        }
    }
    let mut paths: Vec<(String, u32, Option<String>)> = found
        .into_iter()
        .map(|(path, (cover, module))| (path, cover, module))
        .collect();
    paths.sort();
    out.paths = paths;
    out
}

/// The edges an edge cover reads, in the order Python reads them: kind by kind, or every edge.
pub fn cover_edges(link: &mut Link, cover: &Cover) -> Vec<u32> {
    let Cover::Edges { kinds, .. } = cover else {
        return Vec::new();
    };
    match kinds {
        None => (0..link.edges.len() as u32).collect(),
        Some(kinds) => {
            let mut out = Vec::new();
            for kind in kinds {
                if let Some(sid) = link.kind_sid(kind) {
                    out.extend(link.ids_of_kind(sid));
                }
            }
            out
        }
    }
}
