//! The paths a coverage rule needs covered (hoglint/rules/entities.py
//! `CoveragePrimitive.required`): another rule's column read off its findings' witnesses, and the
//! endpoints of an edge selection. Whether an outside input covers each path stays with the
//! coverage adapter, a Python plugin.

use super::env::{Cx, Endpoint, Env, Role};
use super::export::python_repr;
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
    Rule {
        rule: String,
        column: Ref,
        /// The column as the config writes it.
        text: String,
    },
    /// The `src` or `dst` paths of the edges a selection keeps.
    Edges {
        /// The kinds the selection pins, sorted; every edge when it pins none.
        kinds: Option<Vec<String>>,
        /// The conjuncts that read only an edge's kind and contexts.
        by_edge: Option<Node>,
        /// The other conjuncts, staged as an edge rule's selection is.
        rest: Staged,
        src: bool,
        text: String,
    },
}

impl Cover {
    pub fn new(plan: &CoverPlan, edge: &Compiler, fields: &[Field]) -> Result<Cover, String> {
        if let Some(rule) = &plan.rule {
            return Ok(Cover::Rule {
                rule: rule.clone(),
                column: edge.reference(&plan.column),
                text: plan.column_text.clone(),
            });
        }
        let rest = edge.nodes(&plan.rest)?;
        let redefined = vec![false; rest.len()];
        Ok(Cover::Edges {
            kinds: plan.kinds.clone(),
            by_edge: plan
                .by_edge
                .as_ref()
                .map(|node| edge.node(node))
                .transpose()?,
            rest: Staged::new(&rest, &redefined, fields),
            src: plan.endpoint == "src",
            text: plan.text.clone(),
        })
    }

    pub fn refs(&self, out: &mut Vec<Ref>) {
        match self {
            Cover::Rule { column, .. } => out.push(*column),
            Cover::Edges { by_edge, rest, .. } => {
                for node in by_edge
                    .iter()
                    .chain(rest.parts.iter().map(|part| &part.node))
                {
                    node.refs(out);
                }
            }
        }
    }
}

/// What a coverage rule's covers need: every path with why and the module that needs it (the
/// first cover to name a path wins), sorted by path; and the items it cannot place, in order.
#[derive(Default)]
pub struct Required {
    pub paths: Vec<(String, String, Option<String>)>,
    /// (subject, module, reason).
    pub incomplete: Vec<(String, Option<String>, String)>,
}

/// The edge ids each cover reads: the witnesses of the named rule's findings, in order, or None
/// when that rule did not run; every edge of the cover's kinds for an edge selection.
pub type CoverInputs = Vec<Option<Vec<u32>>>;

/// `CoveragePrimitive.required` over the inputs Python gathered (`CoverInputs`).
pub fn required(cx: Cx, covers: &[Cover], inputs: &CoverInputs) -> Required {
    let mut out = Required::default();
    let mut found: FxHashMap<String, (String, Option<String>)> = FxHashMap::default();
    let mut dsts: Vec<Endpoint> = Vec::with_capacity(2);
    for (cover, ids) in covers.iter().zip(inputs) {
        match cover {
            Cover::Rule { rule, column, text } => {
                let Some(ids) = ids else {
                    out.incomplete.push((
                        format!("rules.{rule}"),
                        None,
                        format!("rule {rule} did not run before this coverage rule"),
                    ));
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
                    found
                        .entry(render(&value))
                        .or_insert_with(|| (format!("rules.{rule}.{text}"), None));
                }
            }
            Cover::Edges {
                by_edge,
                rest,
                src: from_src,
                text,
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
                        let truth = all_of([
                            edge_truth,
                            rest.truth(&Env::new(cx, Some(id), src, *dst), false),
                        ]);
                        if truth == Truth::False {
                            continue;
                        }
                        let endpoint = if *from_src { &src } else { dst };
                        let module = src.module.map(|module| cx.text(module).to_string());
                        if endpoint.unresolved || truth == Truth::Unknown {
                            let surface = cx.text(cx.link.edges.surface_of(id));
                            out.incomplete.push((
                                format!("{}|{surface}", module.as_deref().unwrap_or("")),
                                module,
                                format!("cannot place {} for coverage", python_repr(surface)),
                            ));
                            continue;
                        }
                        let Some(path) = endpoint.path.filter(|path| !path.is_empty()) else {
                            continue;
                        };
                        if endpoint.external.is_some() {
                            continue;
                        }
                        let owner = endpoint.module.map(|module| cx.text(module).to_string());
                        found
                            .entry(path.to_string())
                            .or_insert_with(|| (text.clone(), owner));
                    }
                }
            }
        }
    }
    let mut paths: Vec<(String, String, Option<String>)> = found
        .into_iter()
        .map(|(path, (why, module))| (path, why, module))
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
