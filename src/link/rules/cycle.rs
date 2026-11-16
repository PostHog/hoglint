//! The group pairs of a cycle rule: what each candidate destination makes of the rule, in rule
//! order, with what its message names. Python finds the strongly connected parts
//! (hoglint/rules/graphs.py `strongly_connected`).

use super::env::Env;
use super::value::{Truth, Value, render};
use super::{Judgment, Named, RuleKind, Rules, evaluate_ref};
use crate::link::Link;
use rustc_hash::FxHashSet;

/// One destination a cycle rule cannot leave alone: its edge and place, its judgment
/// (unresolved, unknown selection, or a violation for a pair), for a pair the two groups, and
/// what its message names: for each row Python words alone and for the first row of each pair.
/// A pair row names no identity: only a pair inside a cycle makes a finding, and Python finds
/// the cycles, then asks for the identities it needs (`cycle_identities`).
pub struct PairRow {
    pub edge: u32,
    pub target: u32,
    pub judgment: Judgment,
    pub pair: Option<(String, String)>,
    pub src_module: Option<crate::link::intern::Sid>,
    pub named: Option<Named>,
}

impl Rules {
    /// What a cycle rule makes of every candidate destination, in rule order: unresolved ones,
    /// ones whose selection is unknown, and the ones whose two groups are known and differ, with
    /// the groups.
    pub fn pairs(&self, link: &mut Link, index: usize) -> Result<Vec<PairRow>, String> {
        let (rule_id, rule) = self.rule_as(index, "cycle", |kind| match kind {
            RuleKind::Cycle(found) => Some(found),
            _ => None,
        })?;
        let (edges, (src_group, dst_group)) = (&rule.edges, rule.group);
        self.read_sources(link, edges);
        let ids = self.candidates(link, edges);
        let rows = self.over_candidates(link, &ids, |cx, id, dsts, out| {
            let src = cx.src_endpoint(id);
            cx.dst_endpoints(id, edges.role, dsts);
            self.touch_sources(cx, edges, id, src, dsts);
            for (target, dst) in dsts.iter().enumerate() {
                let env = Env::new(*cx, Some(id), src, *dst);
                let selected = edges.staged.truth(&env);
                let judgment = match selected {
                    Truth::False => continue,
                    _ if dst.unresolved => Judgment::Unresolved,
                    Truth::Unknown => Judgment::UnknownSelection,
                    Truth::True => Judgment::Violation,
                };
                let mut pair = None;
                if judgment == Judgment::Violation {
                    let (from, to) = (evaluate_ref(src_group, &env), evaluate_ref(dst_group, &env));
                    let known = |value: &Value| !matches!(value, Value::Unknown | Value::None);
                    if !known(&from) || !known(&to) {
                        continue;
                    }
                    // Most edges stay inside their group: same text, nothing to render.
                    if let (Value::Text(left), Value::Text(right)) = (&from, &to)
                        && left == right
                    {
                        continue;
                    }
                    let (from, to) = (render(&from), render(&to));
                    if from == to {
                        continue;
                    }
                    pair = Some((from, to));
                }
                out.push(PairRow {
                    edge: id,
                    target: target as u32,
                    judgment,
                    pair,
                    src_module: src.module,
                    named: None,
                });
            }
        });
        let mut rows = self.in_rule_order(link, rule_id, edges, rows, |row| row.edge)?;
        // The finding of a pair takes its identity, owner and subjects from its first row.
        let mut first: FxHashSet<(String, String)> = FxHashSet::default();
        for row in &mut rows {
            if row
                .pair
                .as_ref()
                .is_some_and(|pair| !first.insert(pair.clone()))
            {
                continue;
            }
            let env = self.env_at(link, edges.role, row.edge, row.target);
            let identity = match row.pair {
                Some(_) => Vec::new(),
                None => rule.identity.of(row.judgment, &env),
            };
            row.named = Some(Named::of(identity, &env));
        }
        Ok(rows)
    }

    /// The identity of each destination `steps` names, an (edge, place) of cycle rule `index`:
    /// the first rows of the pairs inside a cycle, which Python finds.
    pub fn cycle_identities(
        &self,
        link: &Link,
        index: usize,
        steps: &[(u32, u32)],
    ) -> Result<Vec<Vec<String>>, String> {
        let (_, rule) = self.rule_as(index, "cycle", |kind| match kind {
            RuleKind::Cycle(found) => Some(found),
            _ => None,
        })?;
        Ok(steps
            .iter()
            .map(|&(edge, target)| {
                let env = self.env_at(link, rule.edges.role, edge, target);
                rule.identity.columns(&env)
            })
            .collect())
    }
}
