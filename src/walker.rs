//! One traversal of a parsed module that emits every row list of an L1 record.
//! Mirrors `FileWalker` in reference_l1.py; the `Frame` stack is its `path`.

use crate::json::J;
use crate::value::{binop, callee_text, cmpop, unaryop, max_nodes, max_symbolic, Encoder, StrValue};
use ruff_python_ast::{self as ast, Expr, ExprContext, InterpolatedStringElement, Pattern, Stmt};
use ruff_text_size::{Ranged, TextSize};
use std::collections::{HashMap, HashSet};

pub const TYPE_ONLY: u32 = 1;
pub const TC_ELSE: u32 = 2;
pub const FUNC: u32 = 4;
pub const CLASS: u32 = 8;
pub const TRY: u32 = 16;
pub const IF: u32 = 32;
pub const BLOCK: u32 = 64;

const WATCHED_CALLEES: [&str; 6] = ["get_model", "patch", "object", "setdefault", "append", "connect"];
const WATCHED_PREFIXES: [&str; 1] = ["register_"];
/// Extra callee names from `--watch-callees`, set once at startup.
static EXTRA_WATCHED: std::sync::OnceLock<Vec<String>> = std::sync::OnceLock::new();

pub fn set_extra_watched(names: Vec<String>) {
    EXTRA_WATCHED.set(names).ok().expect("watched callees set twice");
}

fn is_watched(last: &str) -> bool {
    WATCHED_CALLEES.contains(&last)
        || WATCHED_PREFIXES.iter().any(|prefix| last.starts_with(prefix))
        || EXTRA_WATCHED.get().is_some_and(|extra| extra.iter().any(|n| n == last))
}
const WRAPPER_STEPS: [&str; 4] = ["await", "ifexp:body", "ifexp:else", "star"];

pub type Derived = HashMap<i64, HashSet<String>>;

pub struct LineIndex {
    starts: Vec<u32>,
}

impl LineIndex {
    /// The text is newline-normalized, so `\n` is the only line break.
    pub fn new(text: &str) -> LineIndex {
        let mut starts = vec![0];
        for (i, b) in text.bytes().enumerate() {
            if b == b'\n' {
                starts.push(i as u32 + 1);
            }
        }
        LineIndex { starts }
    }

    /// (1-based line, 0-based byte column).
    pub fn pos(&self, offset: TextSize) -> (i64, i64) {
        let off = offset.to_u32();
        let line = self.starts.partition_point(|&s| s <= off) - 1;
        (line as i64 + 1, (off - self.starts[line]) as i64)
    }

    pub fn line(&self, offset: TextSize) -> i64 {
        self.pos(offset).0
    }
}

// ---- strings -------------------------------------------------------------------------------

fn is_word_char(ch: char) -> bool {
    !ch.is_ascii() || ch.is_ascii_alphanumeric() || ch == '_'
}

fn is_dotted_path(value: &str) -> bool {
    let mut n = 0;
    for part in value.split('.') {
        if part.is_empty() || !part.chars().all(|c| is_word_char(c) || c == '*') {
            return false;
        }
        n += 1;
    }
    n >= 2
}

fn is_camel_name(value: &str) -> bool {
    value.starts_with(|c: char| c.is_ascii_uppercase()) && value.chars().all(is_word_char)
}

fn is_symbolic(value: &str, surrogate: bool) -> bool {
    if value.is_empty() || surrogate || value.chars().count() > max_symbolic() {
        return false;
    }
    is_dotted_path(value) || is_camel_name(value)
}

/// Matches of `<([A-Z]\w*)\b` (Python `re`, Unicode `\w`).
fn tag_names(value: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut rest = value;
    while let Some(at) = rest.find('<') {
        let after = &rest[at + 1..];
        if !after.starts_with(|c: char| c.is_ascii_uppercase()) {
            rest = after;
            continue;
        }
        let end = after.find(|c: char| !(c.is_alphanumeric() || c == '_')).unwrap_or(after.len());
        out.push(&after[..end]);
        rest = &after[end..];
    }
    out
}

fn is_identifier(value: &str) -> bool {
    let mut chars = value.chars();
    matches!(chars.next(), Some(c) if c.is_ascii_alphabetic() || c == '_')
        && chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
}

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

fn is_type_checking_test(test: &Expr) -> bool {
    match test {
        Expr::Name(n) => n.id.as_str() == "TYPE_CHECKING",
        Expr::Attribute(a) => a.attr.as_str() == "TYPE_CHECKING",
        _ => false,
    }
}

pub fn is_stub_body(body: &[Stmt]) -> bool {
    let mut rest = body;
    if let Some(Stmt::Expr(e)) = body.first() {
        if e.value.is_string_literal_expr() {
            rest = &body[1..];
        }
    }
    rest.iter().all(|s| match s {
        Stmt::Pass(_) => true,
        Stmt::Expr(e) => e.value.is_ellipsis_literal_expr(),
        _ => false,
    })
}

fn name_id(expr: &Expr) -> &str {
    match expr {
        Expr::Name(n) => n.id.as_str(),
        _ => "?",
    }
}

// ---- def table -----------------------------------------------------------------------------

#[derive(Clone, Copy)]
pub enum DefNode<'a> {
    Func(&'a ast::StmtFunctionDef),
    Class(&'a ast::StmtClassDef),
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

/// Bound name -> (class-like, first-party); a name bound twice ORs both flags.
pub type Bound<'a> = HashMap<&'a str, (bool, bool)>;

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

struct Collector<'a, 'b> {
    src: &'a str,
    first_party: &'b [String],
    found: Vec<(DefNode<'a>, Option<u32>)>,
    bound: Bound<'a>,
}

impl<'a, 'b> Collector<'a, 'b> {
    fn add(&mut self, name: &'a str, class_like: bool, local: bool) {
        let old = self.bound.entry(name).or_insert((false, false));
        old.0 |= class_like;
        old.1 |= local;
    }

    fn is_first_party(&self, module: &str) -> bool {
        let head = module.split('.').next().unwrap_or("");
        self.first_party.iter().any(|r| r == head)
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
                    let local = self.is_first_party(name);
                    self.add(bound, false, local);
                }
            }
            Stmt::ImportFrom(i) => {
                let module = i.module.as_ref().map(|m| m.as_str()).unwrap_or("");
                let local = i.level > 0 || self.is_first_party(module);
                for alias in &i.names {
                    let name = alias.name.as_str();
                    if name == "*" {
                        continue;
                    }
                    let bound = alias.asname.as_ref().map(|a| a.as_str()).unwrap_or(name);
                    self.add(bound, name.starts_with(|c: char| c.is_ascii_uppercase()), local);
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
    pub fn build(suite: &'a [Stmt], src: &'a str, lines: &LineIndex, first_party: &[String]) -> (DefTable<'a>, Bound<'a>) {
        let mut col = Collector { src, first_party, found: Vec::new(), bound: HashMap::new() };
        col.body(suite, None);
        let mut rows: Vec<(DefNode<'a>, Option<u32>, i64, i64, u32)> = col
            .found
            .iter()
            .map(|(node, owner)| {
                let (l, c) = lines.pos(keyword_start(col.src, *node));
                let start = match node {
                    DefNode::Func(f) => f.range.start().to_u32(),
                    DefNode::Class(c) => c.range.start().to_u32(),
                };
                (*node, *owner, l, c, start)
            })
            .collect();
        rows.sort_by_key(|r| (r.2, r.3));
        let index: HashMap<u32, usize> = rows.iter().enumerate().map(|(i, r)| (r.4, i)).collect();
        let entries = rows
            .iter()
            .map(|(node, owner, l, c, _)| {
                let end = match node {
                    DefNode::Func(f) => f.range.end(),
                    DefNode::Class(c) => c.range.end(),
                };
                DefEntry {
                    node: *node,
                    parent: owner.map(|o| index[&o] as i64).unwrap_or(-1),
                    l: *l,
                    c: *c,
                    e: lines.line(end),
                }
            })
            .collect();
        (DefTable { entries, index }, col.bound)
    }

    fn idx(&self, start: TextSize) -> usize {
        self.index[&start.to_u32()]
    }

    pub fn is_class(&self, idx: i64) -> bool {
        idx >= 0 && matches!(self.entries[idx as usize].node, DefNode::Class(_))
    }

    fn ancestry(&self, mut idx: i64) -> Vec<i64> {
        let mut out = vec![idx];
        while idx != -1 {
            idx = self.entries[idx as usize].parent;
            out.push(idx);
        }
        out
    }
}

// ---- traversal -----------------------------------------------------------------------------

/// One `path` entry: the parent of the node being visited and the slot it sits in.
#[derive(Clone, Copy)]
enum Frame<'a> {
    Attr(&'a str),
    CallFunc,
    SubValue,
    Arg(&'a ast::ExprCall, usize),
    Kw(&'a ast::ExprCall, Option<&'a str>),
    Nt(&'static str),
    DictVal(&'a str),
    Walrus(&'a str),
    T(&'static str),
    AssignV(&'a [Expr]),
    AnnAssignV(&'a Expr),
    AugAssignV(&'static str, &'a Expr),
    Base(usize),
    ClassKw(Option<&'a str>),
    ParamAnn(&'a str),
    ParamDefault(&'a str),
    TypeAliasV(&'a str),
}

impl Frame<'_> {
    /// (step text, terminal) as in `FileWalker.step`.
    fn step(&self) -> (String, bool) {
        match self {
            Frame::Attr(a) => (format!(".{a}"), false),
            Frame::CallFunc => ("()".to_string(), false),
            Frame::SubValue => ("[]".to_string(), false),
            Frame::Arg(call, i) => (format!("arg:{i}:{}", callee_text(call)), false),
            Frame::Kw(call, name) => (format!("kw:{}:{}", name.unwrap_or("**"), callee_text(call)), false),
            Frame::Nt(s) => (s.to_string(), false),
            Frame::DictVal(k) => (format!("dict:val:{k}"), false),
            Frame::Walrus(n) => (format!("walrus:{n}"), false),
            Frame::T(s) => (s.to_string(), true),
            Frame::AssignV(targets) => {
                let names: Vec<String> = targets.iter().map(target_text).collect();
                (format!("assign:{}", names.join(",")), true)
            }
            Frame::AnnAssignV(t) => (format!("annassign:{}", target_text(t)), true),
            Frame::AugAssignV(op, t) => (format!("augassign:{op}:{}", target_text(t)), true),
            Frame::Base(i) => (format!("base:{i}"), true),
            Frame::ClassKw(name) => (format!("classkw:{}", name.unwrap_or("**")), true),
            Frame::ParamAnn(n) => (format!("param-ann:{n}"), true),
            Frame::ParamDefault(n) => (format!("param-default:{n}"), true),
            Frame::TypeAliasV(n) => (format!("typealias:{n}"), true),
        }
    }
}

pub struct Row {
    pub l: i64,
    pub c: i64,
    pub json: String,
}

impl Row {
    fn new(l: i64, c: i64, value: J) -> Row {
        Row { l, c, json: value.to_json() }
    }
}

/// Aggregate table: key JSON prefix -> (count, first line, first col).
type Agg = HashMap<String, (i64, i64, i64)>;

fn aggregate(table: &mut Agg, key: String, l: i64, c: i64) {
    let entry = table.entry(key).or_insert((0, l, c));
    entry.0 += 1;
    if (l, c) < (entry.1, entry.2) {
        entry.1 = l;
        entry.2 = c;
    }
}

fn key_prefix(items: Vec<J>) -> String {
    let mut json = J::Arr(items).to_json();
    json.pop();
    json
}

pub struct Walker<'a, 'b> {
    src: &'a str,
    lines: &'b LineIndex,
    pub defs: &'b DefTable<'a>,
    bound: &'b Bound<'a>,
    derived: &'b Derived,
    pub enc: Encoder,
    path: Vec<Frame<'a>>,
    pub imports: Vec<Row>,
    pub assigns: Vec<Row>,
    pub refs: Vec<Row>,
    pub calls: Vec<Row>,
    pub strs: Vec<Row>,
    pub tags: Vec<Row>,
    pub watched: Vec<Row>,
    pub branches: Vec<Row>,
    pub uses: Agg,
    pub callees: Agg,
    pub names: Agg,
    pub def_flags: Vec<u32>,
    pub new_derived: Derived,
}

impl<'a, 'b> Walker<'a, 'b> {
    pub fn new(src: &'a str, lines: &'b LineIndex, defs: &'b DefTable<'a>, bound: &'b Bound<'a>, derived: &'b Derived) -> Self {
        Walker {
            src,
            lines,
            defs,
            bound,
            derived,
            enc: Encoder::new(),
            path: Vec::new(),
            imports: Vec::new(),
            assigns: Vec::new(),
            refs: Vec::new(),
            calls: Vec::new(),
            strs: Vec::new(),
            tags: Vec::new(),
            watched: Vec::new(),
            branches: Vec::new(),
            uses: HashMap::new(),
            callees: HashMap::new(),
            names: HashMap::new(),
            def_flags: vec![0; defs.entries.len()],
            new_derived: HashMap::new(),
        }
    }

    pub fn run(&mut self, suite: &'a [Stmt]) {
        self.body(suite, -1, 0);
    }

    fn child(&mut self, frame: Frame<'a>, expr: &'a Expr, scope: i64, header: i64) {
        self.path.push(frame);
        self.expr(expr, scope, header);
        self.path.pop();
    }

    fn opt_child(&mut self, frame: Frame<'a>, expr: Option<&'a Expr>, scope: i64, header: i64) {
        if let Some(e) = expr {
            self.child(frame, e, scope, header);
        }
    }

    fn body(&mut self, stmts: &'a [Stmt], scope: i64, flags: u32) {
        for stmt in stmts {
            self.stmt(stmt, scope, flags);
        }
    }

    fn assign_scope(&self, scope: i64) -> bool {
        scope == -1 || self.defs.is_class(scope)
    }

    fn stmt(&mut self, stmt: &'a Stmt, scope: i64, flags: u32) {
        let h = -1;
        match stmt {
            Stmt::FunctionDef(f) => self.visit_def(DefNode::Func(f), scope, flags),
            Stmt::ClassDef(c) => self.visit_def(DefNode::Class(c), scope, flags),
            Stmt::Import(i) => {
                for alias in &i.names {
                    let (l, c) = self.lines.pos(alias.range.start());
                    let row = J::Arr(vec![
                        J::Int(scope),
                        J::Int(l),
                        J::Int(c),
                        J::Int(flags as i64),
                        J::s(alias.name.as_str()),
                        J::Int(0),
                        J::Null,
                        J::opt_s(alias.asname.as_ref().map(|a| a.as_str())),
                    ]);
                    self.imports.push(Row::new(l, c, row));
                }
            }
            Stmt::ImportFrom(i) => {
                for alias in &i.names {
                    let (l, c) = self.lines.pos(alias.range.start());
                    let row = J::Arr(vec![
                        J::Int(scope),
                        J::Int(l),
                        J::Int(c),
                        J::Int(flags as i64),
                        J::opt_s(i.module.as_ref().map(|m| m.as_str())),
                        J::Int(i.level as i64),
                        J::s(alias.name.as_str()),
                        J::opt_s(alias.asname.as_ref().map(|a| a.as_str())),
                    ]);
                    self.imports.push(Row::new(l, c, row));
                }
            }
            Stmt::Assign(a) => {
                if self.assign_scope(scope) {
                    let t = a.targets.iter().map(|t| self.enc.value(Some(t), false, max_nodes(), self.src)).collect();
                    let v = self.enc.value(Some(&a.value), false, max_nodes(), self.src);
                    self.emit_assign(stmt, scope, flags, "=", t, None, v);
                }
                for t in &a.targets {
                    self.child(Frame::T("assign-target"), t, scope, h);
                }
                self.child(Frame::AssignV(&a.targets), &a.value, scope, h);
            }
            Stmt::AnnAssign(a) => {
                if self.assign_scope(scope) {
                    let t = vec![self.enc.value(Some(&a.target), false, max_nodes(), self.src)];
                    let ann = self.enc.value(Some(&a.annotation), true, max_nodes(), self.src);
                    let v = self.enc.value(a.value.as_deref(), false, max_nodes(), self.src);
                    self.emit_assign(stmt, scope, flags, ":", t, Some(ann), v);
                }
                self.child(Frame::T("annassign-target"), &a.target, scope, h);
                self.child(Frame::T("annotation"), &a.annotation, scope, h);
                self.opt_child(Frame::AnnAssignV(&a.target), a.value.as_deref(), scope, h);
            }
            Stmt::AugAssign(a) => {
                let op = binop(a.op);
                if self.assign_scope(scope) {
                    let t = vec![self.enc.value(Some(&a.target), false, max_nodes(), self.src)];
                    let v = self.enc.value(Some(&a.value), false, max_nodes(), self.src);
                    let op_text: &'static str = aug_op(op);
                    self.emit_assign(stmt, scope, flags, op_text, t, None, v);
                }
                self.child(Frame::T("augassign-target"), &a.target, scope, h);
                self.child(Frame::AugAssignV(op, &a.target), &a.value, scope, h);
            }
            Stmt::TypeAlias(a) => {
                if self.assign_scope(scope) {
                    let t = vec![self.enc.value(Some(&a.name), false, max_nodes(), self.src)];
                    let v = self.enc.value(Some(&a.value), true, max_nodes(), self.src);
                    self.emit_assign(stmt, scope, flags, "type", t, None, v);
                }
                self.child(Frame::T("typealias-target"), &a.name, scope, h);
                if let Some(tp) = &a.type_params {
                    self.type_params(tp, scope, h);
                }
                self.child(Frame::TypeAliasV(name_id(&a.name)), &a.value, scope, h);
            }
            Stmt::Expr(e) => self.child(Frame::T("expr"), &e.value, scope, h),
            Stmt::Return(r) => self.opt_child(Frame::T("return"), r.value.as_deref(), scope, h),
            Stmt::Delete(d) => {
                for t in &d.targets {
                    self.child(Frame::T("del"), t, scope, h);
                }
            }
            Stmt::If(i) => self.visit_if(&i.test, &i.body, i.range.start(), &i.elif_else_clauses, scope, flags),
            Stmt::For(f) => {
                self.child(Frame::T("for-target"), &f.target, scope, h);
                self.child(Frame::T("for-iter"), &f.iter, scope, h);
                self.body(&f.body, scope, flags | BLOCK);
                self.body(&f.orelse, scope, flags | BLOCK);
            }
            Stmt::While(w) => {
                self.child(Frame::T("while-test"), &w.test, scope, h);
                self.body(&w.body, scope, flags | BLOCK);
                self.body(&w.orelse, scope, flags | BLOCK);
            }
            Stmt::With(w) => {
                for item in &w.items {
                    self.child(Frame::T("with"), &item.context_expr, scope, h);
                    self.opt_child(Frame::T("with-target"), item.optional_vars.as_deref(), scope, h);
                }
                self.body(&w.body, scope, flags | BLOCK);
            }
            Stmt::Try(t) => {
                let inner = flags | TRY;
                self.body(&t.body, scope, inner);
                for handler in &t.handlers {
                    let ast::ExceptHandler::ExceptHandler(handler) = handler;
                    self.opt_child(Frame::T("except"), handler.type_.as_deref(), scope, h);
                    self.body(&handler.body, scope, inner);
                }
                self.body(&t.orelse, scope, inner);
                self.body(&t.finalbody, scope, inner);
            }
            Stmt::Match(m) => {
                self.child(Frame::T("match"), &m.subject, scope, h);
                for case in &m.cases {
                    self.pattern(&case.pattern, scope);
                    self.opt_child(Frame::T("case-guard"), case.guard.as_deref(), scope, h);
                    self.body(&case.body, scope, flags | BLOCK);
                }
            }
            Stmt::Raise(r) => {
                self.opt_child(Frame::T("raise"), r.exc.as_deref(), scope, h);
                self.opt_child(Frame::T("raise-from"), r.cause.as_deref(), scope, h);
            }
            Stmt::Assert(a) => {
                self.child(Frame::T("assert"), &a.test, scope, h);
                self.opt_child(Frame::T("assert-msg"), a.msg.as_deref(), scope, h);
            }
            _ => {}
        }
    }

    fn pattern(&mut self, pattern: &'a Pattern, scope: i64) {
        match pattern {
            Pattern::MatchValue(p) => self.child(Frame::T("case"), &p.value, scope, -1),
            Pattern::MatchSequence(p) => {
                for sub in &p.patterns {
                    self.pattern(sub, scope);
                }
            }
            Pattern::MatchMapping(p) => {
                for key in &p.keys {
                    self.child(Frame::T("case"), key, scope, -1);
                }
                for sub in &p.patterns {
                    self.pattern(sub, scope);
                }
            }
            Pattern::MatchClass(p) => {
                self.child(Frame::T("case"), &p.cls, scope, -1);
                for sub in &p.arguments.patterns {
                    self.pattern(sub, scope);
                }
                for kw in &p.arguments.keywords {
                    self.pattern(&kw.pattern, scope);
                }
            }
            Pattern::MatchAs(p) => {
                if let Some(sub) = &p.pattern {
                    self.pattern(sub, scope);
                }
            }
            Pattern::MatchOr(p) => {
                for sub in &p.patterns {
                    self.pattern(sub, scope);
                }
            }
            Pattern::MatchSingleton(_) | Pattern::MatchStar(_) => {}
        }
    }

    fn type_params(&mut self, tp: &'a ast::TypeParams, scope: i64, header: i64) {
        for param in &tp.type_params {
            match param {
                ast::TypeParam::TypeVar(t) => {
                    self.opt_child(Frame::T("typeparam"), t.bound.as_deref(), scope, header);
                    self.opt_child(Frame::T("typeparam"), t.default.as_deref(), scope, header);
                }
                ast::TypeParam::ParamSpec(t) => self.opt_child(Frame::T("typeparam"), t.default.as_deref(), scope, header),
                ast::TypeParam::TypeVarTuple(t) => {
                    self.opt_child(Frame::T("typeparam"), t.default.as_deref(), scope, header)
                }
            }
        }
    }

    /// An `if` and its `elif` chain read as CPython's nested `If` in `orelse`.
    fn visit_if(
        &mut self,
        test: &'a Expr,
        body: &'a [Stmt],
        start: TextSize,
        rest: &'a [ast::ElifElseClause],
        scope: i64,
        flags: u32,
    ) {
        if let Expr::Compare(cmp) = test {
            if cmp.operands[0].is_name_expr() {
                let (l, c) = self.lines.pos(start);
                let body_end = self.lines.line(body.last().map(|s| s.range().end()).unwrap_or(start));
                let v = self.enc.value(Some(test), false, max_nodes(), self.src);
                let row = J::Arr(vec![J::Int(scope), J::Int(-1), J::Int(l), J::Int(c), J::Int(body_end), v]);
                self.branches.push(Row::new(l, c, row));
            }
        }
        self.child(Frame::T("if-test"), test, scope, -1);
        let tc = is_type_checking_test(test);
        self.body(body, scope, flags | if tc { TYPE_ONLY } else { IF });
        let else_flags = flags | if tc { TC_ELSE } else { IF };
        let Some(first) = rest.first() else {
            return;
        };
        match &first.test {
            Some(t) => self.visit_if(t, &first.body, first.range.start(), &rest[1..], scope, else_flags),
            None => self.body(&first.body, scope, else_flags),
        }
    }

    fn visit_def(&mut self, node: DefNode<'a>, scope: i64, flags: u32) {
        let start = match node {
            DefNode::Func(f) => f.range.start(),
            DefNode::Class(c) => c.range.start(),
        };
        let idx = self.defs.idx(start);
        self.def_flags[idx] = flags;
        let h = idx as i64;
        match node {
            DefNode::Func(f) => {
                for d in &f.decorator_list {
                    self.child(Frame::T("decorator"), &d.expression, scope, h);
                }
                if let Some(tp) = &f.type_params {
                    self.type_params(tp, scope, h);
                }
                let p = &f.parameters;
                for param in p.posonlyargs.iter().chain(p.args.iter()).chain(p.kwonlyargs.iter()) {
                    let name = param.parameter.name.as_str();
                    self.opt_child(Frame::ParamAnn(name), param.parameter.annotation.as_deref(), scope, h);
                    self.opt_child(Frame::ParamDefault(name), param.default.as_deref(), scope, h);
                }
                for param in p.vararg.iter().chain(p.kwarg.iter()) {
                    self.opt_child(Frame::ParamAnn(param.name.as_str()), param.annotation.as_deref(), scope, h);
                }
                self.opt_child(Frame::T("returns"), f.returns.as_deref(), scope, h);
                self.body(&f.body, h, flags | FUNC);
            }
            DefNode::Class(c) => {
                for d in &c.decorator_list {
                    self.child(Frame::T("decorator"), &d.expression, scope, h);
                }
                if let Some(tp) = &c.type_params {
                    self.type_params(tp, scope, h);
                }
                if let Some(args) = &c.arguments {
                    for (i, base) in args.args.iter().enumerate() {
                        self.child(Frame::Base(i), base, scope, h);
                    }
                    for kw in args.keywords.iter() {
                        self.child(Frame::ClassKw(kw.arg.as_ref().map(|a| a.as_str())), &kw.value, scope, h);
                    }
                }
                self.body(&c.body, h, flags | CLASS);
            }
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn emit_assign(&mut self, stmt: &Stmt, scope: i64, flags: u32, op: &'static str, t: Vec<J>, ann: Option<J>, v: J) {
        let (l, c) = self.lines.pos(stmt.range().start());
        let mut pairs = vec![("op", J::s(op)), ("t", J::Arr(t)), ("s", J::Int(scope)), ("l", J::Int(l)), ("c", J::Int(c))];
        if let Some(ann) = ann {
            pairs.push(("ann", ann));
        }
        if flags != 0 {
            pairs.push(("f", J::Int(flags as i64)));
        }
        if !v.is_null() {
            pairs.push(("v", v));
        }
        self.assigns.push(Row::new(l, c, J::obj(pairs)));
    }

    fn expr(&mut self, expr: &'a Expr, scope: i64, header: i64) {
        let h = header;
        match expr {
            Expr::Call(call) => {
                self.emit_call(call, scope, header);
                self.child(Frame::CallFunc, &call.func, scope, h);
                for (i, arg) in call.arguments.args.iter().enumerate() {
                    self.child(Frame::Arg(call, i), arg, scope, h);
                }
                for kw in call.arguments.keywords.iter() {
                    self.child(Frame::Kw(call, kw.arg.as_ref().map(|a| a.as_str())), &kw.value, scope, h);
                }
            }
            Expr::StringLiteral(s) => self.emit_string(s, scope, header),
            Expr::Name(n) => self.emit_ref(n, scope, header),
            Expr::Dict(d) => {
                self.emit_watched(d, scope, header);
                for item in d.items.iter() {
                    if let Some(key) = &item.key {
                        self.child(Frame::Nt("dict:key"), key, scope, h);
                    }
                    let frame = match &item.key {
                        None => Frame::Nt("dict:**"),
                        Some(Expr::StringLiteral(k)) if is_identifier(k.value.to_str()) => Frame::DictVal(k.value.to_str()),
                        Some(_) => Frame::Nt("dict:val"),
                    };
                    self.child(frame, &item.value, scope, h);
                }
            }
            Expr::BoolOp(b) => {
                let step = if b.op == ast::BoolOp::And { "bool:and" } else { "bool:or" };
                for v in &b.values {
                    self.child(Frame::Nt(step), v, scope, h);
                }
            }
            Expr::Named(n) => {
                self.child(Frame::Nt("walrus-target"), &n.target, scope, h);
                self.child(Frame::Walrus(name_id(&n.target)), &n.value, scope, h);
            }
            Expr::BinOp(b) => {
                let step = bin_step(binop(b.op));
                self.child(Frame::Nt(step), &b.left, scope, h);
                self.child(Frame::Nt(step), &b.right, scope, h);
            }
            Expr::UnaryOp(u) => self.child(Frame::Nt(unary_step(unaryop(u.op))), &u.operand, scope, h),
            Expr::Lambda(l) => {
                if let Some(p) = &l.parameters {
                    for param in p.posonlyargs.iter().chain(p.args.iter()).chain(p.kwonlyargs.iter()) {
                        self.opt_child(Frame::Nt("lambda:default"), param.default.as_deref(), scope, h);
                    }
                }
                self.child(Frame::Nt("lambda"), &l.body, scope, h);
            }
            Expr::If(i) => {
                self.child(Frame::Nt("ifexp:test"), &i.test, scope, h);
                self.child(Frame::Nt("ifexp:body"), &i.body, scope, h);
                self.child(Frame::Nt("ifexp:else"), &i.orelse, scope, h);
            }
            Expr::Set(s) => self.elts(&s.elts, "elt:set", scope, h),
            Expr::List(l) => self.elts(&l.elts, "elt:list", scope, h),
            Expr::Tuple(t) => self.elts(&t.elts, "elt:tuple", scope, h),
            Expr::ListComp(c) => {
                self.child(Frame::Nt("comp:elt"), &c.elt, scope, h);
                self.generators(&c.generators, scope, h);
            }
            Expr::SetComp(c) => {
                self.child(Frame::Nt("comp:elt"), &c.elt, scope, h);
                self.generators(&c.generators, scope, h);
            }
            Expr::Generator(c) => {
                self.child(Frame::Nt("comp:elt"), &c.elt, scope, h);
                self.generators(&c.generators, scope, h);
            }
            Expr::DictComp(c) => {
                self.opt_child(Frame::Nt("comp:key"), c.key.as_deref(), scope, h);
                self.child(Frame::Nt("comp:val"), &c.value, scope, h);
                self.generators(&c.generators, scope, h);
            }
            Expr::Await(a) => self.child(Frame::Nt("await"), &a.value, scope, h),
            Expr::Yield(y) => self.opt_child(Frame::Nt("yield"), y.value.as_deref(), scope, h),
            Expr::YieldFrom(y) => self.child(Frame::Nt("yieldfrom"), &y.value, scope, h),
            Expr::Compare(c) => {
                self.child(Frame::Nt("cmp:left"), &c.operands[0], scope, h);
                for (op, operand) in c.ops.iter().zip(c.operands[1..].iter()) {
                    self.child(Frame::Nt(cmp_step(cmpop(*op))), operand, scope, h);
                }
            }
            Expr::FString(f) => {
                for part in f.value.iter() {
                    if let ast::FStringPartRef::FString(fs) = part {
                        self.interpolations(&fs.elements, "fstr", scope, h);
                    }
                }
            }
            Expr::TString(t) => {
                for ts in t.value.iter() {
                    self.interpolations(&ts.elements, "tstr", scope, h);
                }
            }
            Expr::Attribute(a) => self.child(Frame::Attr(a.attr.as_str()), &a.value, scope, h),
            Expr::Subscript(s) => {
                self.child(Frame::SubValue, &s.value, scope, h);
                self.child(Frame::Nt("slice"), &s.slice, scope, h);
            }
            Expr::Starred(s) => self.child(Frame::Nt("star"), &s.value, scope, h),
            Expr::Slice(s) => {
                self.opt_child(Frame::Nt("slice-part"), s.lower.as_deref(), scope, h);
                self.opt_child(Frame::Nt("slice-part"), s.upper.as_deref(), scope, h);
                self.opt_child(Frame::Nt("slice-part"), s.step.as_deref(), scope, h);
            }
            _ => {}
        }
    }

    fn elts(&mut self, elts: &'a [Expr], step: &'static str, scope: i64, h: i64) {
        for e in elts {
            self.child(Frame::Nt(step), e, scope, h);
        }
    }

    fn generators(&mut self, gens: &'a [ast::Comprehension], scope: i64, h: i64) {
        for g in gens {
            self.child(Frame::Nt("comp:target"), &g.target, scope, h);
            self.child(Frame::Nt("comp:iter"), &g.iter, scope, h);
            for cond in &g.ifs {
                self.child(Frame::Nt("comp:if"), cond, scope, h);
            }
        }
    }

    /// A format spec is a JoinedStr in CPython: nested fields step `fstr`. An f-string spec slot
    /// is transparent; a t-string `Interpolation.format_spec` slot adds its own `tstr` step.
    fn interpolations(&mut self, elements: &'a [InterpolatedStringElement], step: &'static str, scope: i64, h: i64) {
        for el in elements {
            let InterpolatedStringElement::Interpolation(i) = el else {
                continue;
            };
            self.child(Frame::Nt(step), &i.expression, scope, h);
            let Some(spec) = &i.format_spec else {
                continue;
            };
            if step == "tstr" {
                self.path.push(Frame::Nt("tstr"));
                self.interpolations(&spec.elements, "fstr", scope, h);
                self.path.pop();
            } else {
                self.interpolations(&spec.elements, "fstr", scope, h);
            }
        }
    }

    // ---- emitters ------------------------------------------------------------------------

    fn emit_call(&mut self, call: &'a ast::ExprCall, scope: i64, header: i64) {
        let callee = callee_text(call);
        let (l, c) = self.lines.pos(call.range().start());
        aggregate(&mut self.callees, key_prefix(vec![J::Int(scope), J::Int(header), J::s(&callee)]), l, c);
        let last = callee.rsplit('.').next().unwrap_or("");
        if !is_watched(last) {
            return;
        }
        let args = call.arguments.args.iter().map(|a| self.enc.shallow(a, self.src)).collect();
        let kws = call
            .arguments
            .keywords
            .iter()
            .map(|k| J::Arr(vec![J::opt_s(k.arg.as_ref().map(|a| a.as_str())), self.enc.shallow(&k.value, self.src)]))
            .collect();
        let row = J::Arr(vec![J::Int(scope), J::Int(header), J::Int(l), J::Int(c), J::Str(callee), J::Arr(args), J::Arr(kws)]);
        self.calls.push(Row::new(l, c, row));
    }

    fn emit_string(&mut self, lit: &'a ast::ExprStringLiteral, scope: i64, header: i64) {
        let v = StrValue::of(lit, self.src);
        let value = v.text.as_str();
        let (l, c) = self.lines.pos(lit.range().start());
        if value.contains('<') {
            for name in tag_names(value) {
                let row = J::Arr(vec![J::Int(scope), J::Int(header), J::Int(l), J::Int(c), J::s(name)]);
                self.tags.push(Row::new(l, c, row));
            }
        }
        if !value.contains('.') && !value.starts_with(|ch: char| ch.is_ascii_uppercase()) {
            return;
        }
        let parts = if v.parts.len() > 1 { Some(&v.parts) } else { None };
        let symbolic = is_symbolic(value, v.surrogate);
        let mut dotted = symbolic && is_dotted_path(value);
        if let Some(parts) = parts {
            dotted |= parts.iter().any(|(p, s)| is_symbolic(p, *s) && is_dotted_path(p));
        }
        if !dotted {
            if symbolic {
                aggregate(&mut self.names, key_prefix(vec![J::Int(scope), J::Int(header), J::s(value)]), l, c);
            }
            return;
        }
        let (chain, up) = self.climb();
        let mut row = vec![
            J::Int(scope),
            J::Int(header),
            J::Int(l),
            J::Int(c),
            if symbolic { J::s(value) } else { J::Null },
            J::Str(chain),
            J::Str(up),
        ];
        if let Some(parts) = parts {
            row.push(J::Arr(parts.iter().map(|(p, s)| if is_symbolic(p, *s) { J::s(p) } else { J::Null }).collect()));
        }
        self.strs.push(Row::new(l, c, J::Arr(row)));
    }

    fn deriving_scope(&self, name: &str, scope: i64) -> Option<i64> {
        for idx in self.defs.ancestry(scope) {
            if self.defs.is_class(idx) {
                continue;
            }
            if self.derived.get(&idx).is_some_and(|names| names.contains(name)) {
                return Some(idx);
            }
        }
        None
    }

    fn emit_ref(&mut self, name: &'a ast::ExprName, scope: i64, header: i64) {
        let id = name.id.as_str();
        let binding = self.bound.get(id).copied();
        let mut origin = None;
        if binding.is_none() {
            if name.ctx != ExprContext::Load {
                return;
            }
            origin = self.deriving_scope(id, scope);
            if origin.is_none() {
                return;
            }
        }
        let (l, c) = self.lines.pos(name.range.start());
        let (chain, up) = self.climb();
        if let (None, Some((class_like, first_party))) = (origin, binding) {
            let attr_upper = chain.starts_with('.') && chain[1..].starts_with(|ch: char| ch.is_ascii_uppercase());
            if !(first_party && (class_like || attr_upper)) {
                let attrs = leading_attrs(&chain);
                let key = key_prefix(vec![J::Int(scope), J::Int(header), J::s(id), J::s(attrs)]);
                aggregate(&mut self.uses, key, l, c);
                return;
            }
        }
        let mut row = vec![J::Int(scope), J::Int(header), J::Int(l), J::Int(c), J::s(id), J::Str(chain), J::Str(up.clone())];
        if let Some(o) = origin {
            row.push(J::Int(o));
        }
        self.refs.push(Row::new(l, c, J::Arr(row)));
        self.note_derivation(scope, &up);
    }

    fn note_derivation(&mut self, scope: i64, up: &str) {
        if self.defs.is_class(scope) {
            return;
        }
        let mut steps = up.split_whitespace().skip_while(|s| WRAPPER_STEPS.contains(s));
        let Some(first) = steps.next() else {
            return;
        };
        let (kind, rest) = first.split_once(':').unwrap_or((first, ""));
        if !matches!(kind, "assign" | "annassign" | "walrus") {
            return;
        }
        let names: Vec<&str> = rest.split(',').collect();
        if names.iter().all(|n| !n.is_empty() && !n.starts_with(['.', '[', '(', '*', '?'])) {
            let set = self.new_derived.entry(scope).or_default();
            for n in names {
                set.insert(n.to_string());
            }
        }
    }

    fn emit_watched(&mut self, dict: &'a ast::ExprDict, scope: i64, header: i64) {
        for item in dict.items.iter() {
            let Some(Expr::StringLiteral(key)) = &item.key else {
                continue;
            };
            let key_text = key.value.to_str();
            if key_text != "kind" {
                continue;
            }
            let (chain, up) = self.climb();
            let (l, c) = self.lines.pos(item.value.range().start());
            let row = J::Arr(vec![
                J::Int(scope),
                J::Int(header),
                J::Int(l),
                J::Int(c),
                J::s(key_text),
                self.enc.shallow(&item.value, self.src),
                J::Str(chain),
                J::Str(up),
            ]);
            self.watched.push(Row::new(l, c, row));
        }
    }

    /// Trailer chain above the current node, then the escape steps up to the statement or header slot.
    fn climb(&self) -> (String, String) {
        let mut chain = String::new();
        let mut i = self.path.len();
        while i > 0 {
            match self.path[i - 1] {
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
            let (step, terminal) = self.path[i - 1].step();
            up.push(step);
            if terminal {
                break;
            }
            i -= 1;
        }
        (chain, up.join(" "))
    }
}

/// `(\.\w+)*` at the start of a chain.
fn leading_attrs(chain: &str) -> &str {
    let bytes = chain.as_bytes();
    let mut end = 0;
    let mut i = 0;
    while i < bytes.len() && bytes[i] == b'.' {
        let rest = &chain[i + 1..];
        let len = rest.find(|c: char| !(c.is_alphanumeric() || c == '_')).unwrap_or(rest.len());
        if len == 0 {
            break;
        }
        i += 1 + len;
        end = i;
    }
    &chain[..end]
}

fn aug_op(op: &str) -> &'static str {
    match op {
        "+" => "+=",
        "-" => "-=",
        "*" => "*=",
        "@" => "@=",
        "/" => "/=",
        "//" => "//=",
        "%" => "%=",
        "**" => "**=",
        "<<" => "<<=",
        ">>" => ">>=",
        "|" => "|=",
        "^" => "^=",
        _ => "&=",
    }
}

fn bin_step(op: &str) -> &'static str {
    match op {
        "+" => "bin:+",
        "-" => "bin:-",
        "*" => "bin:*",
        "@" => "bin:@",
        "/" => "bin:/",
        "//" => "bin://",
        "%" => "bin:%",
        "**" => "bin:**",
        "<<" => "bin:<<",
        ">>" => "bin:>>",
        "|" => "bin:|",
        "^" => "bin:^",
        _ => "bin:&",
    }
}

fn unary_step(op: &str) -> &'static str {
    match op {
        "not" => "unary:not",
        "-" => "unary:-",
        "+" => "unary:+",
        _ => "unary:~",
    }
}

fn cmp_step(op: &str) -> &'static str {
    match op {
        "==" => "cmp:==",
        "!=" => "cmp:!=",
        "<" => "cmp:<",
        "<=" => "cmp:<=",
        ">" => "cmp:>",
        ">=" => "cmp:>=",
        "is" => "cmp:is",
        "isnot" => "cmp:isnot",
        "in" => "cmp:in",
        _ => "cmp:notin",
    }
}
