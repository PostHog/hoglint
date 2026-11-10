//! The group pairs of a cycle rule (hoglint/rules/graphs.py `CyclePrimitive.consider`): what each
//! candidate destination makes of the rule, in the order the Python engine judges them. Python
//! finds the strongly connected parts.

use super::env::Env;
use super::value::{Truth, Value, render};
use super::{Judgment, RuleKind, Rules, evaluate_ref};
use crate::link::Link;

/// One destination a cycle rule cannot leave alone: its edge and place, its judgment
/// (unresolved, unknown selection, or a violation for a pair), and for a pair the two groups.
pub struct PairRow {
    pub edge: u32,
    pub target: u32,
    pub judgment: Judgment,
    pub pair: Option<(String, String)>,
}

impl Rules {
    /// What a cycle rule makes of every candidate destination (`CyclePrimitive.consider`), in
    /// the order the Python engine judges them: unresolved ones, ones whose selection is
    /// unknown, and the ones whose two groups are known and differ, with the groups.
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
                });
            }
        });
        self.in_rule_order(link, rule_id, edges, rows, |row| row.edge)
    }
}
