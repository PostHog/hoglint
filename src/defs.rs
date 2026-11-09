//! What the walk needs before it starts: every def with its scope id, the names imports
//! bind, and the names anything stores to.

use crate::config::ExtractConfig;
use crate::source::LineIndex;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{self as ast, Expr, ExprContext, Stmt};
use ruff_text_size::TextSize;
use std::collections::{HashMap, HashSet};

#[derive(Clone, Copy)]
pub enum DefNode<'a> {
    Func(&'a ast::StmtFunctionDef),
    Class(&'a ast::StmtClassDef),
}

impl DefNode<'_> {
    /// Ruff's range start: the first decorator, or the keyword when there is none.
    fn start(&self) -> TextSize {
        match self {
            DefNode::Func(f) => f.range.start(),
            DefNode::Class(c) => c.range.start(),
        }
    }

    fn end(&self) -> TextSize {
        match self {
            DefNode::Func(f) => f.range.end(),
            DefNode::Class(c) => c.range.end(),
        }
    }
}

pub struct DefEntry<'a> {
    pub node: DefNode<'a>,
    pub parent: i64,
    pub l: i64,
    pub c: i64,
    pub e: i64,
}

/// Every def / async def / class sorted by keyword position. Index = scope id; -1 = module.
pub struct DefTable<'a> {
    pub entries: Vec<DefEntry<'a>>,
    index: HashMap<u32, usize>,
}

/// An import-bound name: whether it looks like a class and whether it is first-party.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Binding {
    pub class_like: bool,
    pub first_party: bool,
}

/// Bound name -> binding; a name bound twice ORs both flags. Shadowing is ignored.
pub type Bound<'a> = HashMap<&'a str, Binding>;

fn skip_back_blank(src: &[u8], mut at: usize) -> usize {
    while at > 0 && matches!(src[at - 1], b' ' | b'\t' | b'\x0c' | b'\n' | b'\\') {
        at -= 1;
    }
    at
}

/// Offset of the `def` / `async` / `class` keyword (ruff ranges start at the first decorator).
fn keyword_start(src: &str, node: DefNode) -> TextSize {
    let bytes = src.as_bytes();
    let (name, keyword, is_async) = match node {
        DefNode::Func(f) => (f.name.range.start(), "def", f.is_async),
        DefNode::Class(c) => (c.name.range.start(), "class", false),
    };
    let mut at = skip_back_blank(bytes, name.to_usize()) - keyword.len();
    if is_async {
        at = skip_back_blank(bytes, at) - "async".len();
    }
    TextSize::new(at as u32)
}

/// A body that is an optional docstring plus only `pass` and `...`.
pub fn is_stub_body(body: &[Stmt]) -> bool {
    let mut rest = body;
    if let Some(Stmt::Expr(e)) = body.first()
        && e.value.is_string_literal_expr()
    {
        rest = &body[1..];
    }
    rest.iter().all(|s| match s {
        Stmt::Pass(_) => true,
        Stmt::Expr(e) => e.value.is_ellipsis_literal_expr(),
        _ => false,
    })
}

/// Statement-level pass: defs with their owner, and import bindings anywhere in the file.
struct Collector<'a, 'c> {
    config: &'c ExtractConfig,
    found: Vec<(DefNode<'a>, Option<u32>)>,
    bound: Bound<'a>,
}

impl<'a> Collector<'a, '_> {
    fn bind(&mut self, name: &'a str, class_like: bool, first_party: bool) {
        let old = self.bound.entry(name).or_default();
        old.class_like |= class_like;
        old.first_party |= first_party;
    }

    fn body(&mut self, body: &'a [Stmt], owner: Option<u32>) {
        for stmt in body {
            self.stmt(stmt, owner);
        }
    }

    fn stmt(&mut self, stmt: &'a Stmt, owner: Option<u32>) {
        match stmt {
            Stmt::FunctionDef(f) => {
                self.found.push((DefNode::Func(f), owner));
                self.body(&f.body, Some(f.range.start().to_u32()));
            }
            Stmt::ClassDef(c) => {
                self.found.push((DefNode::Class(c), owner));
                self.body(&c.body, Some(c.range.start().to_u32()));
            }
            Stmt::Import(i) => {
                for alias in &i.names {
                    let name = alias.name.as_str();
                    let head = name.split('.').next().unwrap_or("");
                    let bound = alias.asname.as_ref().map(|a| a.as_str()).unwrap_or(head);
                    let first_party = self.config.is_first_party(name);
                    self.bind(bound, false, first_party);
                }
            }
            Stmt::ImportFrom(i) => {
                let module = i.module.as_ref().map(|m| m.as_str()).unwrap_or("");
                let first_party = i.level > 0 || self.config.is_first_party(module);
                for alias in &i.names {
                    let name = alias.name.as_str();
                    if name == "*" {
                        continue;
                    }
                    let bound = alias.asname.as_ref().map(|a| a.as_str()).unwrap_or(name);
                    let class_like = name.starts_with(|c: char| c.is_ascii_uppercase());
                    self.bind(bound, class_like, first_party);
                }
            }
            Stmt::If(i) => {
                self.body(&i.body, owner);
                for clause in &i.elif_else_clauses {
                    self.body(&clause.body, owner);
                }
            }
            Stmt::For(f) => {
                self.body(&f.body, owner);
                self.body(&f.orelse, owner);
            }
            Stmt::While(w) => {
                self.body(&w.body, owner);
                self.body(&w.orelse, owner);
            }
            Stmt::With(w) => self.body(&w.body, owner),
            Stmt::Try(t) => {
                self.body(&t.body, owner);
                for h in &t.handlers {
                    let ast::ExceptHandler::ExceptHandler(h) = h;
                    self.body(&h.body, owner);
                }
                self.body(&t.orelse, owner);
                self.body(&t.finalbody, owner);
            }
            Stmt::Match(m) => {
                for case in &m.cases {
                    self.body(&case.body, owner);
                }
            }
            _ => {}
        }
    }
}

impl<'a> DefTable<'a> {
    pub fn build(
        suite: &'a [Stmt],
        src: &str,
        lines: &LineIndex,
        config: &ExtractConfig,
    ) -> (DefTable<'a>, Bound<'a>) {
        let mut collector = Collector {
            config,
            found: Vec::new(),
            bound: HashMap::new(),
        };
        collector.body(suite, None);
        let mut rows: Vec<(DefNode<'a>, Option<u32>, (i64, i64))> = collector
            .found
            .iter()
            .map(|(node, owner)| (*node, *owner, lines.pos(keyword_start(src, *node))))
            .collect();
        rows.sort_by_key(|row| row.2);
        let index: HashMap<u32, usize> = rows
            .iter()
            .enumerate()
            .map(|(i, row)| (row.0.start().to_u32(), i))
            .collect();
        let entries = rows
            .iter()
            .map(|(node, owner, (l, c))| DefEntry {
                node: *node,
                parent: owner.map(|o| index[&o] as i64).unwrap_or(-1),
                l: *l,
                c: *c,
                e: lines.line(node.end()),
            })
            .collect();
        (DefTable { entries, index }, collector.bound)
    }

    pub fn idx(&self, node: DefNode) -> usize {
        self.index[&node.start().to_u32()]
    }

    pub fn is_class(&self, idx: i64) -> bool {
        idx >= 0 && matches!(self.entries[idx as usize].node, DefNode::Class(_))
    }

    /// `idx`, then each enclosing def, then -1.
    pub fn ancestry(&self, mut idx: i64) -> Vec<i64> {
        let mut out = vec![idx];
        while idx != -1 {
            idx = self.entries[idx as usize].parent;
            out.push(idx);
        }
        out
    }
}

/// Every name the file stores to anywhere. A local can only be derived from a tracked use
/// when it is assigned, so loads of other names never need a use-site row.
#[derive(Default)]
pub struct StoredNames<'a> {
    pub names: HashSet<&'a str>,
}

impl<'a> StoredNames<'a> {
    pub fn collect(suite: &'a [Stmt]) -> HashSet<&'a str> {
        let mut collector = StoredNames::default();
        collector.visit_body(suite);
        collector.names
    }
}

impl<'a> Visitor<'a> for StoredNames<'a> {
    fn visit_expr(&mut self, expr: &'a Expr) {
        if let Expr::Name(name) = expr
            && name.ctx == ExprContext::Store
        {
            self.names.insert(name.id.as_str());
        }
        visitor::walk_expr(self, expr);
    }
}
