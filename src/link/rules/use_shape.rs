//! What a use-shape rule adds to an edge rule (hoglint/rules/edges.py `UseShapePrimitive`): the
//! flow shape that replaces an edge's shape, and the verdict on the shape names.

use super::env::{Cx, Env};
use super::value::Truth;
use crate::link::intern::Sid;

/// What a use-shape rule adds to an edge rule (`UseShapePrimitive`).
pub struct UseShape {
    pub(super) allow: Vec<String>,
    pub(super) deny: Vec<String>,
    /// The interned name of the `flow_shape` attr, with `flow = "def-use"`.
    pub(super) flow: Option<Option<Sid>>,
}

impl UseShape {
    /// `UseShapePrimitive.env`: the flow shape, written `kind` or `kind(detail)`, replaces the
    /// edge's shape.
    pub(super) fn shape<'a>(&self, cx: &Cx<'a>, id: u32) -> Option<(&'a str, &'a str)> {
        let name = (*self.flow.as_ref()?)?;
        let refined = cx.attr(id, Some(name));
        if refined.is_empty() {
            return None;
        }
        let (shape, detail) = refined.split_once('(').unwrap_or((refined, ""));
        Some((shape, detail.strip_suffix(')').unwrap_or(detail)))
    }

    /// `UseShapePrimitive.violation`.
    pub(super) fn violation(&self, env: &Env) -> Truth {
        let Some(id) = env.edge else {
            return Truth::False;
        };
        let (shape, detail) = env.shape_of(id);
        let shape = match shape {
            "" => match env.cx.text(env.cx.link.edges.use_of(id)) {
                "" => "other",
                found => found,
            },
            found => found,
        };
        let detailed = (!detail.is_empty()).then(|| format!("{shape}({detail})"));
        let named = |name: &String| name == shape || detailed.as_ref() == Some(name);
        if !self.allow.is_empty() && !self.allow.iter().any(named) {
            return Truth::True;
        }
        if self.deny.iter().any(named) {
            return Truth::True;
        }
        Truth::False
    }
}
