//! The walk's parent stack, and the `chain` / `up` use-site context read from it
//! (schema.md section 6).

use crate::value::callee_text;
use ruff_python_ast::{self as ast, Expr};

/// One parent of the node being visited, and the slot the node sits in.
#[derive(Clone, Copy)]
pub enum Frame<'a> {
    Attr(&'a str),
    CallFunc,
    SubValue,
    Arg(&'a ast::ExprCall, usize),
    Kw(&'a ast::ExprCall, Option<&'a str>),
    /// A non-terminal step with fixed text.
    Step(&'static str),
    /// A non-terminal operator step: a prefix (`bin:`, `unary:`, `cmp:`, `bool:`) and the operator.
    Op(&'static str, &'static str),
    DictVal(&'a str),
    Walrus(&'a str),
    /// A terminal step with fixed text: a statement or header slot.
    Terminal(&'static str),
    AssignValue(&'a [Expr]),
    AnnAssignValue(&'a Expr),
    AugAssignValue(&'static str, &'a Expr),
    Base(usize),
    ClassKw(Option<&'a str>),
    ParamAnn(&'a str),
    ParamDefault(&'a str),
    TypeAliasValue(&'a str),
}

/// Text of an assignment target in a step: the name, `.attr`, `[]`, `()`, `*` or `?`.
fn target_text(target: &Expr) -> String {
    match target {
        Expr::Name(n) => n.id.to_string(),
        Expr::Attribute(a) => format!(".{}", a.attr.as_str()),
        Expr::Subscript(_) => "[]".to_string(),
        Expr::Tuple(_) | Expr::List(_) => "()".to_string(),
        Expr::Starred(_) => "*".to_string(),
        _ => "?".to_string(),
    }
}

impl Frame<'_> {
    /// (step text, terminal).
    fn step(&self) -> (String, bool) {
        match self {
            Frame::Attr(a) => (format!(".{a}"), false),
            Frame::CallFunc => ("()".to_string(), false),
            Frame::SubValue => ("[]".to_string(), false),
            Frame::Arg(call, i) => (format!("arg:{i}:{}", callee_text(call)), false),
            Frame::Kw(call, name) => (
                format!("kw:{}:{}", name.unwrap_or("**"), callee_text(call)),
                false,
            ),
            Frame::Step(s) => (s.to_string(), false),
            Frame::Op(prefix, op) => (format!("{prefix}{op}"), false),
            Frame::DictVal(k) => (format!("dict:val:{k}"), false),
            Frame::Walrus(n) => (format!("walrus:{n}"), false),
            Frame::Terminal(s) => (s.to_string(), true),
            Frame::AssignValue(targets) => {
                let names: Vec<String> = targets.iter().map(target_text).collect();
                (format!("assign:{}", names.join(",")), true)
            }
            Frame::AnnAssignValue(t) => (format!("annassign:{}", target_text(t)), true),
            Frame::AugAssignValue(op, t) => (format!("augassign:{op}:{}", target_text(t)), true),
            Frame::Base(i) => (format!("base:{i}"), true),
            Frame::ClassKw(name) => (format!("classkw:{}", name.unwrap_or("**")), true),
            Frame::ParamAnn(n) => (format!("param-ann:{n}"), true),
            Frame::ParamDefault(n) => (format!("param-default:{n}"), true),
            Frame::TypeAliasValue(n) => (format!("typealias:{n}"), true),
        }
    }
}

/// Use-site context of the node on top of `path`: the trailer chain directly above it
/// (`.objects.filter()`), then the space-joined escape steps up to the first terminal step.
pub fn climb(path: &[Frame]) -> (String, String) {
    let mut chain = String::new();
    let mut i = path.len();
    while i > 0 {
        match path[i - 1] {
            Frame::Attr(a) => {
                chain.push('.');
                chain.push_str(a);
            }
            Frame::CallFunc => chain.push_str("()"),
            Frame::SubValue => chain.push_str("[]"),
            _ => break,
        }
        i -= 1;
    }
    let mut up: Vec<String> = Vec::new();
    while i > 0 {
        let (step, terminal) = path[i - 1].step();
        up.push(step);
        if terminal {
            break;
        }
        i -= 1;
    }
    (chain, up.join(" "))
}
