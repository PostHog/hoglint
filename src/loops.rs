//! Loop variables bound over a literal sequence. In `for name in <seq>:`, `<seq>` is a tuple or
//! list literal, or a name the file binds exactly once, to such a literal, in the module or in
//! the scope of the loop. A watched call in the loop's body then gets one `calls` row per value,
//! with the loop variable in its arguments replaced by that value (`Walker::emit_call`).
//!
//! Anything else stays as written: a name bound twice or by anything but that assignment, a
//! list whose attributes the file uses (`X.append(...)` can change it), a starred element, a
//! loop variable the body binds again.

use crate::defs::{DefNode, DefTable};
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{self as ast, Expr, ExprContext, Pattern, Stmt};
use ruff_text_size::Ranged;
use std::collections::{HashMap, HashSet};

/// A literal sequence one assignment binds: the def index of its scope (-1 for the module), the
/// statement's start offset and the elements.
#[derive(Clone, Copy)]
pub struct Sequence<'a> {
    pub scope: i64,
    pub at: u32,
    pub elts: &'a [Expr],
}

/// The elements of a tuple or list literal with no starred element.
pub fn literal_elements(expr: &Expr) -> Option<&[Expr]> {
    let elts = match expr {
        Expr::Tuple(t) => &t.elts[..],
        Expr::List(l) => &l.elts[..],
        _ => return None,
    };
    (!elts.iter().any(Expr::is_starred_expr)).then_some(elts)
}

/// The names of a file that a literal sequence binds, and nothing else.
pub struct Sequences<'a> {
    found: HashMap<&'a str, Sequence<'a>>,
}

impl<'a> Sequences<'a> {
    pub fn of(suite: &'a [Stmt], defs: &DefTable<'a>) -> Sequences<'a> {
        let mut bindings = Bindings::new(defs);
        bindings.visit_body(suite);
        if bindings.star_import {
            return Sequences {
                found: HashMap::new(),
            };
        }
        let found = bindings
            .literal
            .into_iter()
            .filter(|(name, _)| bindings.counts.get(name) == Some(&1))
            .filter(|(name, _)| {
                !(bindings.lists.contains(name) && bindings.attributed.contains(name))
            })
            .collect();
        Sequences { found }
    }

    /// The literal a name stands for in a loop at `scope` that starts at `at`: bound in the
    /// module, or in the loop's own scope before the loop.
    pub fn visible(&self, name: &str, scope: i64, at: u32) -> Option<&'a [Expr]> {
        let found = self.found.get(name)?;
        let visible = if found.scope == scope {
            found.at < at
        } else {
            found.scope == -1
        };
        visible.then_some(found.elts)
    }
}

/// Counts every binding of every name in a file, whatever its scope: assignment and loop
/// targets, `del`, parameters, defs, imports, `global` and `nonlocal`, exception and pattern
/// names, type parameters.
struct Bindings<'a, 'd> {
    defs: &'d DefTable<'a>,
    scopes: Vec<i64>,
    counts: HashMap<&'a str, u32>,
    attributed: HashSet<&'a str>,
    literal: HashMap<&'a str, Sequence<'a>>,
    lists: HashSet<&'a str>,
    star_import: bool,
}

impl<'a, 'd> Bindings<'a, 'd> {
    fn new(defs: &'d DefTable<'a>) -> Bindings<'a, 'd> {
        Bindings {
            defs,
            scopes: Vec::new(),
            counts: HashMap::new(),
            attributed: HashSet::new(),
            literal: HashMap::new(),
            lists: HashSet::new(),
            star_import: false,
        }
    }

    fn bind(&mut self, name: &'a str) {
        *self.counts.entry(name).or_insert(0) += 1;
    }

    fn scope(&self) -> i64 {
        self.scopes.last().copied().unwrap_or(-1)
    }

    fn literal(&mut self, stmt: &'a Stmt, target: &'a Expr, value: &'a Expr) {
        let (Expr::Name(name), Some(elts)) = (target, literal_elements(value)) else {
            return;
        };
        let at = stmt.range().start().to_u32();
        let sequence = Sequence {
            scope: self.scope(),
            at,
            elts,
        };
        self.literal.insert(name.id.as_str(), sequence);
        if value.is_list_expr() {
            self.lists.insert(name.id.as_str());
        }
    }

    fn def_body(&mut self, node: DefNode<'a>, name: &'a str, stmt: &'a Stmt) {
        self.bind(name);
        self.scopes.push(self.defs.idx(node) as i64);
        visitor::walk_stmt(self, stmt);
        self.scopes.pop();
    }
}

impl<'a> Visitor<'a> for Bindings<'a, '_> {
    fn visit_stmt(&mut self, stmt: &'a Stmt) {
        match stmt {
            Stmt::FunctionDef(f) => return self.def_body(DefNode::Func(f), f.name.as_str(), stmt),
            Stmt::ClassDef(c) => return self.def_body(DefNode::Class(c), c.name.as_str(), stmt),
            Stmt::Assign(a) if a.targets.len() == 1 => self.literal(stmt, &a.targets[0], &a.value),
            Stmt::AnnAssign(a) => {
                if let Some(value) = &a.value {
                    self.literal(stmt, &a.target, value);
                }
            }
            Stmt::Global(g) => g.names.iter().for_each(|n| self.bind(n.as_str())),
            Stmt::Nonlocal(n) => n.names.iter().for_each(|n| self.bind(n.as_str())),
            _ => {}
        }
        visitor::walk_stmt(self, stmt);
    }

    fn visit_expr(&mut self, expr: &'a Expr) {
        match expr {
            Expr::Name(n) if matches!(n.ctx, ExprContext::Store | ExprContext::Del) => {
                self.bind(n.id.as_str())
            }
            Expr::Attribute(a) => {
                if let Expr::Name(n) = &*a.value {
                    self.attributed.insert(n.id.as_str());
                }
            }
            _ => {}
        }
        visitor::walk_expr(self, expr);
    }

    fn visit_parameter(&mut self, parameter: &'a ast::Parameter) {
        self.bind(parameter.name.as_str());
        visitor::walk_parameter(self, parameter);
    }

    fn visit_alias(&mut self, alias: &'a ast::Alias) {
        let name = alias.name.as_str();
        if name == "*" {
            self.star_import = true;
            return;
        }
        match &alias.asname {
            Some(asname) => self.bind(asname.as_str()),
            None => self.bind(name.split('.').next().unwrap_or(name)),
        }
    }

    fn visit_except_handler(&mut self, handler: &'a ast::ExceptHandler) {
        let ast::ExceptHandler::ExceptHandler(h) = handler;
        if let Some(name) = &h.name {
            self.bind(name.as_str());
        }
        visitor::walk_except_handler(self, handler);
    }

    fn visit_pattern(&mut self, pattern: &'a Pattern) {
        match pattern {
            Pattern::MatchAs(p) => p.name.iter().for_each(|n| self.bind(n.as_str())),
            Pattern::MatchStar(p) => p.name.iter().for_each(|n| self.bind(n.as_str())),
            Pattern::MatchMapping(p) => p.rest.iter().for_each(|n| self.bind(n.as_str())),
            _ => {}
        }
        visitor::walk_pattern(self, pattern);
    }

    fn visit_type_param(&mut self, type_param: &'a ast::TypeParam) {
        self.bind(type_param.name().as_str());
        visitor::walk_type_param(self, type_param);
    }
}

/// Whether a loop body binds `name` again, anywhere in it.
pub fn rebinds<'a>(body: &'a [Stmt], name: &str, defs: &DefTable<'a>) -> bool {
    let mut bindings = Bindings::new(defs);
    bindings.visit_body(body);
    bindings.star_import || bindings.counts.contains_key(name)
}
