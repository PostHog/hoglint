//! What a method assigns to attributes of its first parameter (`self.name = value`): the facts
//! the link types an instance attribute from (`Def::attrs`).
//!
//! A binding whose value the method does not spell gets an opaque value, so the link can tell
//! there is one: unpacking (`self.a, b = ...`), `+=`, a loop or `with` target, and an assignment
//! in a function the method defines, which runs at some other time.

use crate::facts::SelfAttr;
use crate::source::LineIndex;
use crate::value::{Encoder, MAX_HEADER_NODES, Value};
use ruff_python_ast::{self as ast, Expr, Stmt};
use ruff_text_size::Ranged;

/// The opaque value of a binding the method does not spell.
const UNSPELLED: &str = "unspelled";

/// The attribute assignments on the first positional parameter of `f`, in its body and the
/// blocks in it.
pub fn self_attrs(f: &ast::StmtFunctionDef, src: &str, lines: &LineIndex) -> Vec<SelfAttr> {
    let p = &f.parameters;
    let Some(first) = p.posonlyargs.iter().chain(p.args.iter()).next() else {
        return Vec::new();
    };
    let mut found = SelfAttrs {
        owner: first.parameter.name.as_str(),
        src,
        lines,
        enc: Encoder::default(),
        out: Vec::new(),
    };
    found.body(&f.body, false);
    found.out
}

struct SelfAttrs<'s> {
    owner: &'s str,
    src: &'s str,
    lines: &'s LineIndex,
    enc: Encoder,
    out: Vec<SelfAttr>,
}

impl SelfAttrs<'_> {
    /// `name` of an `<owner>.name` target.
    fn name_of<'x>(&self, target: &'x Expr) -> Option<&'x str> {
        let Expr::Attribute(a) = target else {
            return None;
        };
        let Expr::Name(n) = &*a.value else {
            return None;
        };
        (n.id.as_str() == self.owner).then_some(a.attr.as_str())
    }

    fn push(
        &mut self,
        stmt: &Stmt,
        name: &str,
        value: Option<&Expr>,
        annotation: Option<&Expr>,
        later: bool,
    ) {
        let value = if later {
            Value::opaque(UNSPELLED)
        } else {
            self.enc.value(value, false, MAX_HEADER_NODES, self.src)
        };
        let annotation = self.enc.value(annotation, true, MAX_HEADER_NODES, self.src);
        self.out.push(SelfAttr {
            l: self.lines.line(stmt.range().start()),
            name: name.to_string(),
            value,
            annotation,
        });
    }

    /// Every `<owner>.name` inside a target the method binds with a value it does not spell.
    fn unspelled(&mut self, stmt: &Stmt, target: &Expr) {
        if let Some(name) = self.name_of(target) {
            self.push(stmt, name, None, None, true);
            return;
        }
        match target {
            Expr::Tuple(t) => t.elts.iter().for_each(|e| self.unspelled(stmt, e)),
            Expr::List(l) => l.elts.iter().for_each(|e| self.unspelled(stmt, e)),
            Expr::Starred(s) => self.unspelled(stmt, &s.value),
            _ => {}
        }
    }

    /// Whether a nested function takes the owner's name as a parameter of its own.
    fn shadows(&self, f: &ast::StmtFunctionDef) -> bool {
        f.parameters.iter().any(|p| p.name().as_str() == self.owner)
    }

    fn body(&mut self, body: &[Stmt], later: bool) {
        for stmt in body {
            match stmt {
                Stmt::Assign(a) => {
                    for target in &a.targets {
                        match self.name_of(target) {
                            Some(name) => self.push(stmt, name, Some(&a.value), None, later),
                            None => self.unspelled(stmt, target),
                        }
                    }
                }
                Stmt::AnnAssign(a) => {
                    if let Some(name) = self.name_of(&a.target) {
                        self.push(stmt, name, a.value.as_deref(), Some(&a.annotation), later);
                    }
                }
                Stmt::AugAssign(a) => self.unspelled(stmt, &a.target),
                Stmt::For(f) => {
                    self.unspelled(stmt, &f.target);
                    self.body(&f.body, later);
                    self.body(&f.orelse, later);
                }
                Stmt::While(w) => {
                    self.body(&w.body, later);
                    self.body(&w.orelse, later);
                }
                Stmt::If(i) => {
                    self.body(&i.body, later);
                    for clause in &i.elif_else_clauses {
                        self.body(&clause.body, later);
                    }
                }
                Stmt::With(w) => {
                    for item in &w.items {
                        if let Some(target) = &item.optional_vars {
                            self.unspelled(stmt, target);
                        }
                    }
                    self.body(&w.body, later);
                }
                Stmt::Try(t) => {
                    self.body(&t.body, later);
                    for handler in &t.handlers {
                        let ast::ExceptHandler::ExceptHandler(h) = handler;
                        self.body(&h.body, later);
                    }
                    self.body(&t.orelse, later);
                    self.body(&t.finalbody, later);
                }
                Stmt::Match(m) => {
                    for case in &m.cases {
                        self.body(&case.body, later);
                    }
                }
                Stmt::FunctionDef(f) if !self.shadows(f) => self.body(&f.body, true),
                _ => {}
            }
        }
    }
}
