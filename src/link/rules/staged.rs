//! The order in which the evaluator reads a rule's selectors.
//!
//! A selector's truth never depends on this order. Which labels a run reads does: a label set two
//! ways is a config error only where something reads it (a provider's labels of a module, the
//! labels of a symbol outside the graph's symbols). So the order is part of the output. A selection
//! is judged in parts (`Staged`), and in a config that has such a label (`Rules::touch`) every field
//! a part names is read before the part is judged; `tests/native/test_evaluator.py`
//! (`test_a_selection_part_reads_every_field_it_names_before_it_is_judged`) holds it.

use super::env::{Cx, Endpoint, Env};
use super::labels::Field;
use super::selector::{EndField, Node, Ref, Resolve, evaluate};
use super::value::Truth;
use super::{Edges, Rules};
use crate::link::Link;
use crate::link::intern::Sid;

/// The endpoint a conjunct of a selection reads alone (`selectors.endpoint_only`), in the order
/// they are judged.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Class {
    Src = 0,
    Dst = 1,
    Rest = 2,
}

fn class_of(node: &Node) -> Class {
    let mut refs = Vec::new();
    node.refs(&mut refs);
    let mut found = None;
    for field in refs {
        let class = match field {
            Ref::Src(EndField::Context | EndField::Exported)
            | Ref::Dst(EndField::Context | EndField::Exported) => return Class::Rest,
            Ref::Src(_) => Class::Src,
            Ref::Dst(_) => Class::Dst,
            _ => return Class::Rest,
        };
        if found.is_some_and(|seen| seen != class) {
            return Class::Rest;
        }
        found = Some(class);
    }
    found.unwrap_or(Class::Rest)
}

/// Whether a ref reads only its endpoint's module.
fn module_level(field: Ref, fields: &[Field]) -> bool {
    let (Ref::Src(end) | Ref::Dst(end)) = field else {
        return false;
    };
    match end {
        EndField::Module
        | EndField::Path
        | EndField::External
        | EndField::Registry
        | EndField::Lifecycle
        | EndField::HandoutZones
        | EndField::Door => true,
        EndField::Field(at, _) => !fields[at].on_symbol,
        _ => false,
    }
}

/// The `and` parts of a node, or the node itself: what is judged one by one.
pub fn parts(node: &Node) -> &[Node] {
    match node {
        Node::And(parts) => parts,
        other => std::slice::from_ref(other),
    }
}

/// Reads every field the node names, for the conflicts those reads meet.
pub fn touch<'a>(node: &Node, env: &impl Resolve<'a>) {
    let mut refs = Vec::new();
    node.refs(&mut refs);
    for field in refs {
        env.resolve(field);
    }
}

/// The parts in order over any environment: false at the first false one, else unknown when one
/// is unknown. With `touching` (`Rules::touch`), every field a part names is read first.
pub fn class_truth<'n, 'a>(
    parts: impl IntoIterator<Item = &'n Node>,
    env: &impl Resolve<'a>,
    touching: bool,
) -> Truth
where
    'n: 'a,
{
    let mut out = Truth::True;
    for part in parts {
        if touching {
            touch(part, env);
        }
        match evaluate(part, env) {
            Truth::False => return Truth::False,
            Truth::Unknown => out = Truth::Unknown,
            Truth::True => {}
        }
    }
    out
}

/// `class_truth` over an edge's environment.
pub fn parts_truth<'n, 'a>(parts: impl IntoIterator<Item = &'n Node>, env: &Env<'a>) -> Truth
where
    'n: 'a,
{
    class_truth(parts, env, env.cx.rules.touch)
}

/// A predicate (`deny`, `allow`, `require`, `unless`, a reach rule's `from`), judged by its `and`
/// parts.
pub fn truth<'a>(node: &'a Node, env: &Env<'a>) -> Truth {
    parts_truth(parts(node), env)
}

/// A rule's selection in the order it is read: the parts that read only `src`, then only `dst`,
/// then the rest. A class of one conjunct is judged by its `and` parts.
pub struct Staged {
    pub parts: Vec<Node>,
    /// How many of the first parts read only `src`.
    pub src_parts: usize,
    /// Whether those parts read only module fields: they are then judged once per source module
    /// of the rule, before any edge (`Rules::read_sources`).
    pub src_module_level: bool,
}

impl Staged {
    pub fn new(conjuncts: &[Node], fields: &[Field]) -> Staged {
        let mut classes: [Vec<&Node>; 3] = Default::default();
        for node in conjuncts {
            classes[class_of(node) as usize].push(node);
        }
        let src = &classes[Class::Src as usize];
        let src_module_level = !src.is_empty()
            && src.iter().all(|node| {
                let mut refs = Vec::new();
                node.refs(&mut refs);
                refs.into_iter().all(|field| module_level(field, fields))
            });
        let mut out = Vec::new();
        let mut src_parts = 0;
        for (at, class) in classes.iter().enumerate() {
            let nodes: Vec<&Node> = match class.as_slice() {
                [node] => parts(node).iter().collect(),
                many => many.to_vec(),
            };
            if at == Class::Src as usize {
                src_parts = nodes.len();
            }
            out.extend(nodes.into_iter().cloned());
        }
        Staged {
            parts: out,
            src_parts,
            src_module_level,
        }
    }

    /// The selection's truth.
    pub fn truth<'a>(&'a self, env: &Env<'a>) -> Truth {
        parts_truth(&self.parts, env)
    }

    /// The parts that read only `src`.
    pub fn src(&self) -> impl Iterator<Item = &Node> {
        self.parts[..self.src_parts].iter()
    }
}

impl Rules {
    /// In a config with labels that conflict only where read: the `src` parts of a rule that
    /// reads only module fields there, judged once per source module of its kinds, edges seen or
    /// not, before any edge.
    pub(super) fn read_sources(&self, link: &mut Link, rule: &Edges) {
        if !self.touch || !rule.staged.src_module_level {
            return;
        }
        let mut modules = Vec::new();
        for kind in self.kind_sids(link, rule) {
            modules.extend(link.source_modules(kind));
        }
        let cx = Cx { link, rules: self };
        for module in modules {
            self.read_source(&cx, rule, module);
        }
    }

    /// In a config with labels that conflict only where read: the `src` parts of an edge with no
    /// destination, read as they would be before any destination.
    pub(super) fn touch_sources<'a>(
        &self,
        cx: &Cx<'a>,
        rule: &'a Edges,
        id: u32,
        src: Endpoint<'a>,
        dsts: &[Endpoint<'a>],
    ) {
        if self.touch && dsts.is_empty() {
            parts_truth(rule.staged.src(), &Env::new(*cx, Some(id), src, src));
        }
    }

    /// The `src` parts judged on one module, for the reads they make.
    pub(super) fn read_source(&self, cx: &Cx, rule: &Edges, module: Sid) {
        let endpoint = cx.module_endpoint(module);
        parts_truth(rule.staged.src(), &Env::new(*cx, None, endpoint, endpoint));
    }
}
