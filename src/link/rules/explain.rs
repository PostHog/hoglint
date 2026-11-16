//! What a rule over edges reads of one edge, for `explain edge:` and the couplings a rename
//! scope keeps (hoglint/cli.py `explain_edge`, hoglint/ledger/couplings.py `CouplingScan`): which
//! edges the rule sees, and per destination the truth of each selector or the values of some
//! columns.

use super::env::{Cx, Endpoint, Env};
use super::plan::check_columns;
use super::selector::{Compiler, Reading, Ref, evaluate};
use super::value::{Truth, Value, render};
use super::{Edges, Judged, Judgment, RuleKind, Rules, Verdict, decide, evaluate_ref};
use crate::link::Link;
use crate::link::intern::Sid;
use rustc_hash::FxHashSet;

/// One destination of an edge as `explain edge:` shows it: its symbol and module, and the
/// truth of each selector of the rule, in the rule's order.
pub type Explained = (Option<String>, Option<String>, Vec<(String, Truth)>);

/// The parts of a rule over edges that say which edges it sees and how it reads a destination.
struct Seeing<'r> {
    edges: &'r Edges,
    judged: Option<&'r Judged>,
}

impl Rules {
    fn seeing(&self, index: usize) -> Result<Seeing<'_>, String> {
        let rule = self
            .rules
            .get(index)
            .ok_or_else(|| format!("no rule {index} in the plan"))?;
        match &rule.kind {
            RuleKind::Judge(found) => Ok(Seeing {
                edges: &found.edges,
                judged: Some(found),
            }),
            RuleKind::Reach(found) => Ok(Seeing {
                edges: &found.edges,
                judged: None,
            }),
            RuleKind::Cycle(found) => Ok(Seeing {
                edges: &found.edges,
                judged: None,
            }),
            _ => Err(format!("rule {} is no rule over edges", rule.id)),
        }
    }

    /// Whether the rule sees each edge of `ids`, with what a signature or entrypoint rule adds.
    fn sees(&self, link: &mut Link, seeing: &Seeing, ids: &[u32]) -> Vec<bool> {
        let candidates = self.candidates(link, seeing.edges);
        let mut seen: Vec<bool> = ids
            .iter()
            .map(|id| candidates.binary_search(id).is_ok())
            .collect();
        let Some(rule) = seeing.judged else {
            return seen;
        };
        if let Some(signature) = &rule.signature {
            for (at, &id) in ids.iter().enumerate() {
                seen[at] = seen[at] && !signature.seen(link, vec![id]).is_empty();
            }
        }
        let reference = link.kind_sid("reference");
        let asks = |id: u32| {
            reference.is_some() && link.edges.kind_of(id) == reference.unwrap_or_default()
        };
        if !rule.uses_at_import || !ids.iter().any(|&id| asks(id)) {
            return seen;
        }
        let reported = self.reported_imports(link, rule, &candidates);
        for (at, &id) in ids.iter().enumerate() {
            if asks(id) && super::judged_at_import(link, &reported, id) {
                seen[at] = false;
            }
        }
        seen
    }

    /// The (module, surface) of each import edge the rule judges other than clean for some
    /// destination.
    fn reported_imports(
        &self,
        link: &Link,
        rule: &Judged,
        candidates: &[u32],
    ) -> FxHashSet<(Sid, Sid)> {
        let mut out = FxHashSet::default();
        let Some(import) = link.kind_sid("import") else {
            return out;
        };
        let cx = Cx { link, rules: self };
        let mut dsts = Vec::with_capacity(2);
        for &id in candidates {
            if link.edges.kind_of(id) != import {
                continue;
            }
            let key = (link.module_of_edge(id), link.edges.surface_of(id));
            if out.contains(&key) {
                continue;
            }
            let src = cx.src_endpoint(id);
            cx.dst_endpoints(id, rule.edges.role, &mut dsts);
            if dsts.iter().any(|dst| {
                decide(rule, &self.env(cx, Some(rule), id, src, *dst)) != Judgment::Clean
            }) {
                out.insert(key);
            }
        }
        out
    }

    /// The environment of one destination as the rule reads it: a use-shape rule's own shape.
    fn env<'a>(
        &self,
        cx: Cx<'a>,
        rule: Option<&'a Judged>,
        id: u32,
        src: Endpoint<'a>,
        dst: Endpoint<'a>,
    ) -> Env<'a> {
        let mut env = Env::new(cx, Some(id), src, dst);
        if let Some(Verdict::UseShape(use_shape)) = rule.map(|rule| &rule.verdict) {
            env.shape = use_shape.shape(&cx, id);
        }
        env
    }

    /// Each destination's environment of each edge of `ids` the rule sees, None for an edge it
    /// does not see.
    fn seen_envs<T>(
        &self,
        link: &mut Link,
        index: usize,
        ids: &[u32],
        read: impl Fn(&Env) -> T,
    ) -> Result<Vec<Option<Vec<T>>>, String> {
        let seeing = self.seeing(index)?;
        let seen = self.sees(link, &seeing, ids);
        let link: &Link = link;
        let cx = Cx { link, rules: self };
        let role = seeing.edges.role;
        let mut dsts = Vec::with_capacity(2);
        let mut out = Vec::with_capacity(ids.len());
        for (&id, seen) in ids.iter().zip(seen) {
            if !seen {
                out.push(None);
                continue;
            }
            let src = cx.src_endpoint(id);
            cx.dst_endpoints(id, role, &mut dsts);
            let read_all = dsts
                .iter()
                .map(|dst| read(&self.env(cx, seeing.judged, id, src, *dst)))
                .collect();
            out.push(Some(read_all));
        }
        Ok(out)
    }

    /// `explain edge:` of edge `id` under rule `index`: per destination its symbol, module and
    /// the truth of each selector; None when the rule does not see the edge.
    pub fn explain_edge(
        &self,
        link: &mut Link,
        index: usize,
        id: u32,
    ) -> Result<Option<Vec<Explained>>, String> {
        let selectors = &self.rules[index].selectors;
        let mut found = self.seen_envs(link, index, &[id], |env| {
            let cx = env.cx;
            (
                cx.symbol_text(env.dst.symbol).map(|name| name.into_owned()),
                env.dst.module.map(|module| cx.text(module).to_string()),
                selectors
                    .iter()
                    .map(|(name, node)| (name.clone(), evaluate(node, env)))
                    .collect(),
            )
        })?;
        Ok(found.pop().flatten())
    }

    /// The rendered values of `columns` (paths of edge refs) per destination of each edge of
    /// `ids` rule `index` sees, None for one it does not see (`CouplingScan.counts`).
    pub fn edge_values(
        &self,
        link: &mut Link,
        index: usize,
        ids: &[u32],
        columns: &[Vec<String>],
    ) -> Result<Vec<Option<Vec<Vec<String>>>>, String> {
        let refs: Vec<Ref> = {
            let compiler = Compiler {
                fields: &self.field_names,
                it: &link.core.it,
                reading: Reading::Edge,
            };
            columns
                .iter()
                .map(|path| compiler.reference(path))
                .collect()
        };
        check_columns(&self.labels, "a column given at run time", &refs)?;
        self.seen_envs(link, index, ids, |env| {
            refs.iter()
                .map(|field| render(&evaluate_ref(*field, env)))
                .collect()
        })
    }

    /// The zones module `module` hands out names from (`Labeler.handed_zones`), for `explain`.
    pub fn handed_zones<'a>(&'a self, link: &'a Link, module: &str) -> Value<'a> {
        let cx = Cx { link, rules: self };
        cx.handed_zones(link.core.it.get(module))
    }
}
