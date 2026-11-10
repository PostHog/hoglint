//! The search of a reach rule (hoglint/rules/graphs.py `ReachPrimitive`): from each selected
//! start module, breadth first through the edges the rule follows, with each hop's selection and
//! each (start, hop) verdict judged by the evaluator. Hops are taken in the order the Python
//! search takes them, so a start reports the same targets through the same witness paths.

use super::env::{Cx, Endpoint, Env, Symbol};
use super::plan::Mode;
use super::selector::evaluate;
use super::value::{Truth, all_of};
use super::{Rule, Rules};
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

/// The endpoint `from` reads no destination of: every `dst.*` field is unknown, as `ModuleEnv`
/// answers it.
const NO_DESTINATION: Endpoint<'static> = Endpoint {
    module: None,
    at: None,
    symbol: Symbol::None,
    path: None,
    external: None,
    unresolved: true,
    scope: "",
};

impl Rules {
    /// The search of reach rule `index` (`ReachPrimitive.sources` and `search`).
    pub fn reach(&self, link: &mut Link, index: usize) -> Result<Vec<Found>, String> {
        let rule = self.rule(index, Mode::Reach)?;
        let kinds = self.kind_sids(link, rule);
        let mut starts: Vec<Sid> = Vec::new();
        let mut ids: Vec<u32> = Vec::new();
        for &kind in &kinds {
            starts.extend(link.source_modules(kind));
            ids.extend(link.candidates(&[kind], rule.include, rule.exclude));
        }
        let link: &Link = link;
        let it = &link.core.it;
        starts.sort_by(|a, b| it.s(*a).cmp(it.s(*b)));
        starts.dedup();
        let found = self.over_candidates(link, &ids, |cx, id, dsts, out| {
            let src = cx.src_endpoint(id);
            cx.dst_endpoints(id, rule.role, dsts);
            for (target, dst) in dsts.iter().enumerate() {
                let env = Env::new(*cx, Some(id), src, *dst);
                let selected = all_of(rule.selection.iter().map(|node| evaluate(node, &env)));
                if selected != Truth::False {
                    out.push((
                        src.module.unwrap_or_default(),
                        Hop {
                            edge: id,
                            target: target as u32,
                            dst: *dst,
                            selected,
                        },
                    ));
                }
            }
        });
        let cx = Cx { link, rules: self };
        let hops = Self::hops_by_module(&cx, found);
        let mut out = Vec::new();
        for module in starts {
            if !hops.contains_key(&module) {
                continue;
            }
            match self.start_truth(&cx, rule, module) {
                Truth::False => {}
                Truth::Unknown => out.push(Found::UnknownStart { module }),
                Truth::True => self.search(&cx, rule, &hops, module, &mut out),
            }
        }
        Ok(out)
    }

    /// Each module's hops, stably sorted by destination module, symbol and surface, as
    /// `ReachPrimitive.hops` sorts them.
    fn hops_by_module<'a>(cx: &Cx<'a>, found: Vec<(Sid, Hop<'a>)>) -> FxHashMap<Sid, Vec<Hop<'a>>> {
        let mut out: FxHashMap<Sid, Vec<Hop<'a>>> = FxHashMap::default();
        for (module, hop) in found {
            out.entry(module).or_default().push(hop);
        }
        let surface = |hop: &Hop| cx.text(cx.link.edges.surface_of(hop.edge));
        for hops in out.values_mut() {
            hops.sort_by_cached_key(|hop| {
                (
                    hop.dst
                        .module
                        .map_or("", |module| cx.text(module))
                        .to_string(),
                    cx.symbol_text(hop.dst.symbol)
                        .unwrap_or_default()
                        .into_owned(),
                    surface(hop).to_string(),
                )
            });
        }
        out
    }

    /// Whether the rule searches from a module: its `from`, read of the module alone.
    fn start_truth(&self, cx: &Cx, rule: &Rule, module: Sid) -> Truth {
        let Some(start) = &rule.start else {
            return Truth::True;
        };
        let env = Env::new(*cx, None, cx.module_endpoint(module), NO_DESTINATION);
        evaluate(start, &env)
    }

    /// `ReachPrimitive.search` from one start module.
    fn search<'a>(
        &self,
        cx: &Cx<'a>,
        rule: &Rule,
        hops: &FxHashMap<Sid, Vec<Hop<'a>>>,
        source: Sid,
        out: &mut Vec<Found>,
    ) {
        let origin = cx.module_endpoint(source);
        let mut parents: FxHashMap<Sid, Option<(Sid, Step)>> = FxHashMap::default();
        parents.insert(source, None);
        let mut queue = VecDeque::from([source]);
        let mut reported: FxHashSet<String> = FxHashSet::default();
        while let Some(module) = queue.pop_front() {
            for hop in hops.get(&module).map_or(&[][..], Vec::as_slice) {
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
                    .map_or(Truth::False, |deny| evaluate(deny, &env));
                let excused = rule
                    .unless
                    .as_ref()
                    .is_some_and(|unless| evaluate(unless, &env) == Truth::True);
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
