//! Selectors compiled from the plan's syntax trees, and their evaluation
//! (`hoglint.selectors.evaluate`): Kleene logic over values an environment resolves.

use super::plan::{LiteralPlan, NodePlan, OperandPlan};
use super::value::{Truth, Value, all_of, any_of, contained, equals, starts, subset, truthy};
use crate::link::intern::{Interner, Sid};

/// A field of the edge itself (`Env::edge_value`).
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

/// A field of one endpoint (`Env::endpoint_value`, then `Env::read_endpoint`).
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
    /// The zones of what the endpoint's module hands out (`Labeler.handed_zones`).
    HandedZones,
    /// Whether an export's `within` holds the endpoint's module (`Labeler.door`).
    Door,
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
    HandedZones,
    Door,
    /// A bare `subject`: what an entity rule names (`EntityRule.resolver`); elsewhere the module.
    Subject,
    /// A declared field, read from its labels alone.
    Field(usize),
    Unknown,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Ref {
    Edge(EdgeField),
    Src(EndField),
    Dst(EndField),
    /// The module the code named (`surface.*`), whatever endpoint `dst` reads.
    Surface(EndField),
    /// A module the name passes through between the surface and the origin (`via.*`): a
    /// coverage rule's edge cover holds when its selection holds at any one of them.
    Via(EndField),
    Entity(EntityField),
    Unknown,
}

impl Ref {
    /// The declared field the ref reads, on either end or of the entity.
    pub fn field(self) -> Option<usize> {
        match self {
            Ref::Src(EndField::Field(at, _))
            | Ref::Dst(EndField::Field(at, _))
            | Ref::Surface(EndField::Field(at, _))
            | Ref::Via(EndField::Field(at, _))
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

    /// A selector the plan may leave out.
    pub fn optional(&self, plan: &Option<NodePlan>) -> Result<Option<Node>, String> {
        plan.as_ref().map(|plan| self.node(plan)).transpose()
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
            Reading::Entity if path.len() == 1 && path[0] == "subject" => {
                Ref::Entity(EntityField::Subject)
            }
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
            "handed_zones" => EntityField::HandedZones,
            "door" => EntityField::Door,
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
            "surface" => Ref::Surface(self.end_field("surface", name)),
            "via" => Ref::Via(self.end_field("via", name)),
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
            "handed_zones" => EndField::HandedZones,
            "door" => EndField::Door,
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

#[cfg(test)]
mod tests {
    use super::*;

    /// The table the Python selectors are held to as well (`tests/rules/test_selectors.py`).
    const MISSING_SIDE: &str = include_str!("../../../tests/rules/missing_side.txt");

    /// The two sides of one row: `src.left` and `dst.right`.
    struct Sides {
        left: String,
        right: String,
    }

    fn side_value(spec: &str) -> Value<'_> {
        let (kind, rest) = spec.split_once(':').unwrap_or((spec, ""));
        match (kind, rest) {
            ("set", items) => Value::Set(items.split(',').collect()),
            (_, "none") => Value::None,
            (_, "unknown") => Value::Unknown,
            (_, text) => Value::text(text),
        }
    }

    impl<'a> Resolve<'a> for &'a Sides {
        fn resolve(&self, field: Ref) -> Value<'a> {
            let sides: &'a Sides = self;
            match field {
                Ref::Src(_) => side_value(&sides.left),
                _ => side_value(&sides.right),
            }
        }
    }

    fn operand(spec: &str, field: Ref) -> Operand {
        let (kind, rest) = spec.split_once(':').unwrap_or((spec, ""));
        match (kind, rest) {
            ("field" | "set", _) => Operand::Ref(field),
            ("list", items) => Operand::List(items.split(',').map(str::to_string).collect()),
            (_, "none") => Operand::None,
            (_, text) => Operand::Text(text.to_string()),
        }
    }

    fn truth(name: &str) -> Truth {
        match name {
            "true" => Truth::True,
            "false" => Truth::False,
            _ => Truth::Unknown,
        }
    }

    #[test]
    fn a_missing_side_differs_from_every_value_and_equals_another_missing_side() {
        let left_field = Ref::Src(EndField::Field(0, None));
        let right_field = Ref::Dst(EndField::Field(1, None));
        let mut rows = 0;
        for line in MISSING_SIDE.lines() {
            if line.trim().is_empty() || line.starts_with('#') {
                continue;
            }
            let [op, left, right, expected, negated] =
                line.split_whitespace().collect::<Vec<_>>()[..]
            else {
                panic!("a row needs five columns: {line}");
            };
            let left_operand = operand(left, left_field);
            let node = match op {
                "bare" => Node::Bare(left_operand),
                _ => {
                    let op = match op {
                        "==" => Op::Equals,
                        "!=" => Op::NotEquals,
                        "in" => Op::In,
                        "not-in" => Op::NotIn,
                        "startswith" => Op::Starts,
                        "subset_of" => Op::Subset,
                        other => panic!("an operator the table does not know: {other}"),
                    };
                    Node::Compare(op, left_operand, operand(right, right_field))
                }
            };
            let sides = Sides {
                left: left.to_string(),
                right: right.to_string(),
            };
            assert_eq!(evaluate(&node, &&sides), truth(expected), "{line}");
            let not = Node::Not(Box::new(node));
            assert_eq!(evaluate(&not, &&sides), truth(negated), "not {line}");
            rows += 1;
        }
        assert!(rows > 30, "the table has {rows} rows");
    }

    /// The table of `and`, `or` and `not` (`tests/native/test_rule_relations.py` reads it too).
    const CONNECTIVES: &str = include_str!("../../../tests/rules/connectives.txt");

    /// Every ref reads as unknown.
    struct Unknowns;

    impl<'a> Resolve<'a> for Unknowns {
        fn resolve(&self, _field: Ref) -> Value<'a> {
            Value::Unknown
        }
    }

    /// A part with the truth `name`: a bare `true` or `false`, or a bare field nothing sets.
    fn part(name: &str) -> Node {
        match name {
            "true" => Node::Bare(Operand::Bool(true)),
            "false" => Node::Bare(Operand::Bool(false)),
            _ => Node::Bare(Operand::Ref(Ref::Src(EndField::Field(0, None)))),
        }
    }

    #[test]
    fn and_or_and_not_carry_an_unknown_part_as_kleene_logic_does() {
        let mut rows = 0;
        for line in CONNECTIVES.lines() {
            if line.trim().is_empty() || line.starts_with('#') {
                continue;
            }
            let [connective, left, right, expected] =
                line.split_whitespace().collect::<Vec<_>>()[..]
            else {
                panic!("a row needs four columns: {line}");
            };
            let expected = truth(expected);
            match connective {
                "and" => {
                    let parts = vec![part(left), part(right)];
                    let staged = super::super::staged::class_truth(&parts, &Unknowns, false);
                    // A selection's top-level `and` is judged part by part (`Staged`).
                    assert_eq!(staged, expected, "staged {line}");
                    assert_eq!(evaluate(&Node::And(parts), &Unknowns), expected, "{line}");
                }
                "or" => {
                    let parts = vec![part(left), part(right)];
                    assert_eq!(evaluate(&Node::Or(parts), &Unknowns), expected, "{line}");
                }
                "not" => {
                    let not = Node::Not(Box::new(part(left)));
                    assert_eq!(evaluate(&not, &Unknowns), expected, "{line}");
                }
                other => panic!("a connective the table does not know: {other}"),
            }
            rows += 1;
        }
        assert_eq!(rows, 21, "the table has {rows} rows");
    }
}
