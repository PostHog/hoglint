//! The search of a reach rule (hoglint/rules/graphs.py `ReachPrimitive`): from each selected
//! start module, breadth first through the edges the rule follows, with each hop's selection and
//! each (start, hop) verdict judged by the evaluator. Hops are taken in the order the Python
//! search takes them, so a start reports the same targets through the same witness paths.

use super::env::{Cx, Endpoint, Env};
use super::staged::truth;
use super::value::Truth;
use super::{Reach, RuleKind, Rules};
use crate::link::Link;
use crate::link::intern::Sid;
use rustc_hash::{FxHashMap, FxHashSet};
use std::collections::VecDeque;

/// One destination of one edge the rule may follow.
struct Hop<'a> {
    edge: u32,
    target: u32,
    dst: Endpoint<'a>,
    selected: Truth,
}

/// A step of a witness path: an edge and the place of the destination followed.
pub type Step = (u32, u32);

/// What the search found, in the order the Python search finds it.
pub enum Found {
    /// A start module whose `from` is unknown: the rule cannot tell whether to search from it.
    UnknownStart { module: Sid },
    /// A hop whose destination did not resolve.
    Unresolved { source: Sid, step: Step },
    /// A hop the rule cannot tell whether to follow.
    Unknown { source: Sid, step: Step },
    /// A denied target, first reached along `path` from the start.
    Denied {
        source: Sid,
        path: Vec<Step>,
        verdict: Truth,
    },
}

/// The hops out of each module, made when a search first reads them, as `ReachPrimitive.hops`
/// makes them: the module's candidate edges kind by kind in graph order, each destination the
/// selection does not rule out, stably sorted by destination module, symbol and surface.
struct Hops<'a> {
    cx: Cx<'a>,
    rule: &'a Reach,
    ids: FxHashMap<Sid, Vec<u32>>,
    /// The modules with an edge of the rule's kinds, seen by the rule or not.
    sources: FxHashSet<Sid>,
    made: FxHashMap<Sid, Vec<Hop<'a>>>,
}

impl<'a> Hops<'a> {
    fn of(&mut self, module: Sid) -> &[Hop<'a>] {
        if !self.made.contains_key(&module) {
            let hops = self.make(module);
            self.made.insert(module, hops);
        }
        &self.made[&module]
    }

    fn make(&self, module: Sid) -> Vec<Hop<'a>> {
        let cx = self.cx;
        let rules = cx.rules;
        let edges = &self.rule.edges;
        if rules.touch && edges.staged.src_module_level && self.sources.contains(&module) {
            rules.read_source(&cx, edges, module);
        }
        let mut out = Vec::new();
        let mut dsts = Vec::with_capacity(2);
        for &id in self.ids.get(&module).map_or(&[][..], Vec::as_slice) {
            let src = cx.src_endpoint(id);
            cx.dst_endpoints(id, edges.role, &mut dsts);
            for (target, dst) in dsts.iter().enumerate() {
                let env = Env::new(cx, Some(id), src, *dst);
                let selected = edges.staged.truth(&env);
                if selected != Truth::False {
                    out.push(Hop {
                        edge: id,
                        target: target as u32,
                        dst: *dst,
                        selected,
                    });
                }
            }
        }
        out.sort_by_cached_key(|hop| {
            (
                hop.dst
                    .module
                    .map_or("", |module| cx.text(module))
                    .to_string(),
                cx.symbol_text(hop.dst.symbol)
                    .unwrap_or_default()
                    .into_owned(),
                cx.text(cx.link.edges.surface_of(hop.edge)).to_string(),
            )
        });
        out
    }
}

impl Rules {
    /// The search of reach rule `index` (`ReachPrimitive.sources` and `search`).
    pub fn reach(&self, link: &mut Link, index: usize) -> Result<Vec<Found>, String> {
        let (_, rule) = self.rule_as(index, "reach", |kind| match kind {
            RuleKind::Reach(found) => Some(found),
            _ => None,
        })?;
        let kinds = self.kind_sids(link, &rule.edges);
        let mut starts: Vec<Sid> = Vec::new();
        let mut ids: FxHashMap<Sid, Vec<u32>> = FxHashMap::default();
        for &kind in &kinds {
            starts.extend(link.source_modules(kind));
            for id in link.candidates(&[kind], rule.edges.include, rule.edges.exclude) {
                ids.entry(link.module_of_edge(id)).or_default().push(id);
            }
        }
        let link: &Link = link;
        let it = &link.core.it;
        starts.sort_by(|a, b| it.s(*a).cmp(it.s(*b)));
        starts.dedup();
        let cx = Cx { link, rules: self };
        let mut hops = Hops {
            cx,
            rule,
            ids,
            sources: starts.iter().copied().collect(),
            made: FxHashMap::default(),
        };
        let mut out = Vec::new();
        for module in starts {
            let truth = self.start_truth(&cx, rule, module);
            if truth == Truth::False || hops.of(module).is_empty() {
                continue;
            }
            match truth {
                Truth::Unknown => out.push(Found::UnknownStart { module }),
                _ => self.search(&cx, rule, &mut hops, module, &mut out),
            }
        }
        Ok(out)
    }

    /// Whether the rule searches from a module: its `from`, read of the module alone.
    fn start_truth(&self, cx: &Cx, rule: &Reach, module: Sid) -> Truth {
        let Some(start) = &rule.start else {
            return Truth::True;
        };
        truth(start, &cx.module_env(module))
    }

    /// `ReachPrimitive.search` from one start module.
    fn search<'a>(
        &self,
        cx: &Cx<'a>,
        rule: &Reach,
        hops: &mut Hops<'a>,
        source: Sid,
        out: &mut Vec<Found>,
    ) {
        let origin = cx.module_endpoint(source);
        let mut parents: FxHashMap<Sid, Option<(Sid, Step)>> = FxHashMap::default();
        parents.insert(source, None);
        let mut queue = VecDeque::from([source]);
        let mut reported: FxHashSet<String> = FxHashSet::default();
        while let Some(module) = queue.pop_front() {
            for hop in hops.of(module) {
                let step = (hop.edge, hop.target);
                if hop.dst.unresolved {
                    out.push(Found::Unresolved { source, step });
                    continue;
                }
                if hop.selected == Truth::Unknown {
                    out.push(Found::Unknown { source, step });
                    continue;
                }
                let env = Env::new(*cx, Some(hop.edge), origin, hop.dst);
                let verdict = rule
                    .deny
                    .as_ref()
                    .map_or(Truth::False, |deny| truth(deny, &env));
                let excused = rule
                    .unless
                    .as_ref()
                    .is_some_and(|unless| truth(unless, &env) == Truth::True);
                let target = match cx
                    .symbol_text(hop.dst.symbol)
                    .filter(|symbol| !symbol.is_empty())
                {
                    Some(symbol) => symbol.into_owned(),
                    None => hop
                        .dst
                        .module
                        .map_or(String::new(), |module| cx.text(module).to_string()),
                };
                if verdict != Truth::False && !excused && !reported.contains(&target) {
                    reported.insert(target);
                    let mut path = vec![step];
                    let mut back = parents.get(&module).copied().flatten();
                    while let Some((previous, step)) = back {
                        path.push(step);
                        back = parents.get(&previous).copied().flatten();
                    }
                    path.reverse();
                    out.push(Found::Denied {
                        source,
                        path,
                        verdict,
                    });
                    continue;
                }
                let Some(next) = hop.dst.module.filter(|_| hop.dst.external.is_none()) else {
                    continue;
                };
                if parents.contains_key(&next) {
                    continue;
                }
                parents.insert(next, Some((module, step)));
                queue.push_back(next);
            }
        }
    }
}
