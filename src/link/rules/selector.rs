//! Selectors compiled from the plan's syntax trees, and their evaluation
//! (`hoglint.selectors.evaluate`): Kleene logic over values an environment resolves.

use super::plan::{LiteralPlan, NodePlan, OperandPlan};
use super::value::{Truth, Value, all_of, any_of, contained, equals, starts, subset, truthy};
use crate::link::intern::{Interner, Sid};

/// A field of the edge itself (`EdgeEnv.edge_value`).
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum EdgeField {
    Kind,
    Shape,
    Use,
    Label,
    Resolution,
    Surface,
    Producer,
    Context,
    Ordinal,
    /// An attr by name; None when no edge has an attr of that name.
    Attr(Option<Sid>),
}

/// A field of one endpoint (`EdgeEnv.endpoint_value`, then `read_endpoint`).
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum EndField {
    Symbol,
    Module,
    Path,
    Scope,
    Name,
    External,
    Kind,
    Subject,
    Decl,
    Registry,
    Lifecycle,
    Context,
    Exported,
    /// A declared field, and the edge attr (`src.<name>` or `dst.<name>`) that overrides it.
    Field(usize, Option<Sid>),
    /// A name no field declares: only an edge attr gives it a value.
    Undeclared(Option<Sid>),
}

/// What an entity selector (a field's domain) reads by its last name (`Labeler.resolver`).
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum EntityField {
    Module,
    Symbol,
    Path,
    Name,
    Decl,
    Kind,
    Registry,
    Lifecycle,
    /// A declared field, read from its labels alone.
    Field(usize),
    Unknown,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Ref {
    Edge(EdgeField),
    Src(EndField),
    Dst(EndField),
    Entity(EntityField),
    Unknown,
}

impl Ref {
    /// The declared field the ref reads, on either end or of the entity.
    pub fn field(self) -> Option<usize> {
        match self {
            Ref::Src(EndField::Field(at, _))
            | Ref::Dst(EndField::Field(at, _))
            | Ref::Entity(EntityField::Field(at)) => Some(at),
            _ => None,
        }
    }
}

#[derive(Clone, Debug)]
pub enum Operand {
    Ref(Ref),
    None,
    Bool(bool),
    Text(String),
    List(Vec<String>),
}

#[derive(Clone, Copy, Debug)]
pub enum Op {
    Equals,
    NotEquals,
    In,
    NotIn,
    Starts,
    Subset,
}

#[derive(Clone, Debug)]
pub enum Node {
    And(Vec<Node>),
    Or(Vec<Node>),
    Not(Box<Node>),
    Bare(Operand),
    Compare(Op, Operand, Operand),
}

/// Resolves the refs of a selector for one environment.
pub trait Resolve<'a> {
    fn resolve(&self, field: Ref) -> Value<'a>;
}

fn value<'a>(operand: &'a Operand, env: &impl Resolve<'a>) -> Value<'a> {
    match operand {
        Operand::Ref(field) => env.resolve(*field),
        Operand::None => Value::None,
        Operand::Bool(flag) => Value::Bool(*flag),
        Operand::Text(text) => Value::text(text),
        Operand::List(items) => Value::List(items),
    }
}

pub fn evaluate<'a>(node: &'a Node, env: &impl Resolve<'a>) -> Truth {
    match node {
        Node::And(parts) => all_of(parts.iter().map(|part| evaluate(part, env))),
        Node::Or(parts) => any_of(parts.iter().map(|part| evaluate(part, env))),
        Node::Not(inner) => evaluate(inner, env).negate(),
        Node::Bare(operand) => truthy(&value(operand, env)),
        Node::Compare(op, left, right) => {
            let (left, right) = (value(left, env), value(right, env));
            match op {
                Op::Equals => equals(&left, &right),
                Op::NotEquals => equals(&left, &right).negate(),
                Op::In => contained(&left, &right),
                Op::NotIn => contained(&left, &right).negate(),
                Op::Starts => starts(&left, &right),
                Op::Subset => subset(&left, &right),
            }
        }
    }
}

impl Node {
    /// Every ref the node reads, in order.
    pub fn refs(&self, out: &mut Vec<Ref>) {
        let operand = |operand: &Operand, out: &mut Vec<Ref>| {
            if let Operand::Ref(field) = operand {
                out.push(*field);
            }
        };
        match self {
            Node::And(parts) | Node::Or(parts) => parts.iter().for_each(|part| part.refs(out)),
            Node::Not(inner) => inner.refs(out),
            Node::Bare(single) => operand(single, out),
            Node::Compare(_, left, right) => {
                operand(left, out);
                operand(right, out);
            }
        }
    }

    pub fn reads(&self, test: impl Fn(Ref) -> bool) -> bool {
        let mut found = Vec::new();
        self.refs(&mut found);
        found.into_iter().any(test)
    }
}

/// Which resolver a selector is written for.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Reading {
    /// `src.*`, `dst.*` and `edge.*`: rules and export audiences.
    Edge,
    /// Bare entity names: field domains.
    Entity,
}

/// Compiles plan trees against the declared fields and the link's interned names.
pub struct Compiler<'c> {
    pub fields: &'c [String],
    pub it: &'c Interner,
    pub reading: Reading,
}

impl Compiler<'_> {
    pub fn node(&self, plan: &NodePlan) -> Result<Node, String> {
        Ok(match plan {
            NodePlan::And(parts) => Node::And(self.nodes(parts)?),
            NodePlan::Or(parts) => Node::Or(self.nodes(parts)?),
            NodePlan::Not(inner) => Node::Not(Box::new(self.node(inner)?)),
            NodePlan::Bare(operand) => Node::Bare(self.operand(operand)),
            NodePlan::Cmp(op, left, right) => {
                let op = match op.as_str() {
                    "==" => Op::Equals,
                    "!=" => Op::NotEquals,
                    "in" => Op::In,
                    "not in" => Op::NotIn,
                    "startswith" => Op::Starts,
                    "subset_of" => Op::Subset,
                    other => {
                        return Err(format!(
                            "a selector operator the evaluator does not know: {other}"
                        ));
                    }
                };
                Node::Compare(op, self.operand(left), self.operand(right))
            }
        })
    }

    pub fn nodes(&self, plans: &[NodePlan]) -> Result<Vec<Node>, String> {
        plans.iter().map(|plan| self.node(plan)).collect()
    }

    fn operand(&self, plan: &OperandPlan) -> Operand {
        match plan {
            OperandPlan::Ref(path) => Operand::Ref(self.reference(path)),
            OperandPlan::Lit(None) => Operand::None,
            OperandPlan::Lit(Some(LiteralPlan::Bool(flag))) => Operand::Bool(*flag),
            OperandPlan::Lit(Some(LiteralPlan::Text(text))) => Operand::Text(text.clone()),
            OperandPlan::List(items) => Operand::List(items.clone()),
        }
    }

    fn field(&self, name: &str) -> Option<usize> {
        self.fields.iter().position(|field| field == name)
    }

    pub fn reference(&self, path: &[String]) -> Ref {
        match self.reading {
            Reading::Edge => self.edge_reference(path),
            Reading::Entity => Ref::Entity(
                path.last()
                    .map_or(EntityField::Unknown, |name| self.entity(name)),
            ),
        }
    }

    fn entity(&self, name: &str) -> EntityField {
        match name {
            "module" | "subject" => EntityField::Module,
            "symbol" => EntityField::Symbol,
            "path" => EntityField::Path,
            "name" => EntityField::Name,
            "decl" => EntityField::Decl,
            "kind" => EntityField::Kind,
            "registry" => EntityField::Registry,
            "lifecycle" => EntityField::Lifecycle,
            other => self
                .field(other)
                .map_or(EntityField::Unknown, EntityField::Field),
        }
    }

    fn edge_reference(&self, path: &[String]) -> Ref {
        let [root, name] = path else {
            return Ref::Unknown;
        };
        let name = name.as_str();
        match root.as_str() {
            "edge" => Ref::Edge(self.edge_field(name)),
            "src" => Ref::Src(self.end_field("src", name)),
            "dst" => Ref::Dst(self.end_field("dst", name)),
            _ => Ref::Unknown,
        }
    }

    fn edge_field(&self, name: &str) -> EdgeField {
        match name {
            "kind" => EdgeField::Kind,
            "shape" => EdgeField::Shape,
            "use" => EdgeField::Use,
            "label" => EdgeField::Label,
            "resolution" => EdgeField::Resolution,
            "surface" => EdgeField::Surface,
            "producer" => EdgeField::Producer,
            "context" => EdgeField::Context,
            "ordinal" => EdgeField::Ordinal,
            other => EdgeField::Attr(self.it.get(other)),
        }
    }

    fn end_field(&self, root: &str, name: &str) -> EndField {
        match name {
            "symbol" => EndField::Symbol,
            "module" => EndField::Module,
            "path" => EndField::Path,
            "scope" => EndField::Scope,
            "name" => EndField::Name,
            "external" => EndField::External,
            "kind" => EndField::Kind,
            "subject" => EndField::Subject,
            "decl" => EndField::Decl,
            "registry" => EndField::Registry,
            "lifecycle" => EndField::Lifecycle,
            "context" => EndField::Context,
            "exported" => EndField::Exported,
            other => {
                let attr = self.it.get(&format!("{root}.{other}"));
                match self.field(other) {
                    Some(at) => EndField::Field(at, attr),
                    None => EndField::Undeclared(attr),
                }
            }
        }
    }
}
