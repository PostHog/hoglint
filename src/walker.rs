//! One traversal of a parsed module that collects every row list of an L1 record.

use crate::config::{ExtractConfig, Kind};
use crate::defs::{Bound, DefNode, DefTable};
use crate::facts::flags::{BLOCK, CLASS, FUNC, IF, TC_ELSE, TRY, TYPE_ONLY};
use crate::facts::{
    Assign, Branch, DottedStr, Import, Ref, Site, Tag, TextKey, UseKey, WatchedCall, WatchedKey,
};
use crate::loops::{Sequences, literal_elements, rebinds};
use crate::path::{Frame, climb};
use crate::source::LineIndex;
use crate::strings::{is_dotted_path, is_identifier, is_symbolic, leading_attrs, tag_names};
use crate::value::{
    Encoder, MAX_NODES, StrValue, Value, binop, boolop, callee_text, cmpop, unaryop,
};
use ruff_python_ast::{self as ast, Expr, ExprContext, InterpolatedStringElement, Pattern, Stmt};
use ruff_text_size::{Ranged, TextSize};
use std::collections::{HashMap, HashSet};

/// Kinds whose rows come from expressions. Without any of them the walk skips expressions.
const EXPRESSION_KINDS: [Kind; 9] = [
    Kind::Callees,
    Kind::Calls,
    Kind::Derived,
    Kind::Names,
    Kind::Refs,
    Kind::Strs,
    Kind::Tags,
    Kind::Uses,
    Kind::Watched,
];

/// Occurrence count and first position per aggregate key.
pub type Counts<K> = HashMap<K, (i64, i64, i64)>;

fn count<K: std::hash::Hash + Eq>(table: &mut Counts<K>, key: K, l: i64, c: i64) {
    let entry = table.entry(key).or_insert((0, l, c));
    entry.0 += 1;
    if (l, c) < (entry.1, entry.2) {
        entry.1 = l;
        entry.2 = c;
    }
}

fn is_type_checking_test(test: &Expr) -> bool {
    match test {
        Expr::Name(n) => n.id.as_str() == "TYPE_CHECKING",
        Expr::Attribute(a) => a.attr.as_str() == "TYPE_CHECKING",
        _ => false,
    }
}

fn name_id(expr: &Expr) -> &str {
    match expr {
        Expr::Name(n) => n.id.as_str(),
        _ => "?",
    }
}

/// Where a node sits: its scope, the def whose header holds it (-1 = none), and the flags of
/// the enclosing statements.
#[derive(Clone, Copy)]
struct At {
    scope: i64,
    header: i64,
    flags: u32,
}

impl At {
    fn site(self, (l, c): (i64, i64)) -> Site {
        Site {
            scope: self.scope,
            header: self.header,
            l,
            c,
            flags: self.flags,
        }
    }
}

/// Everything one walk collects. Derived-name candidates still need `derive::Derivation`.
#[derive(Default)]
pub struct Walk {
    pub imports: Vec<Import>,
    pub assigns: Vec<Assign>,
    /// Use sites of import-bound names.
    pub refs: Vec<Ref>,
    /// Loads that become `derived` rows once their name is derived.
    pub candidates: Vec<Ref>,
    pub calls: Vec<WatchedCall>,
    pub strs: Vec<DottedStr>,
    pub tags: Vec<Tag>,
    pub watched: Vec<WatchedKey>,
    pub branches: Vec<Branch>,
    pub uses: Counts<UseKey>,
    pub callees: Counts<TextKey>,
    pub names: Counts<TextKey>,
    /// Statement flags by def index.
    pub def_flags: Vec<u32>,
}

/// The most `calls` rows one call becomes over the values of the loops around it.
const MAX_LOOP_ROWS: usize = 64;

/// A `for` around the code being walked whose variable takes the values of a literal sequence:
/// the scope of the loop, the variable and each value as a shallow value.
struct LoopValues<'a> {
    scope: i64,
    name: &'a str,
    values: Vec<Value>,
}

pub struct Walker<'a, 'w> {
    src: &'a str,
    lines: &'w LineIndex,
    defs: &'w DefTable<'a>,
    bound: &'w Bound<'a>,
    stored: &'w HashSet<&'a str>,
    sequences: &'w Sequences<'a>,
    loops: Vec<LoopValues<'a>>,
    config: &'w ExtractConfig,
    /// Whether any requested kind needs the expression walk.
    exprs: bool,
    enc: Encoder,
    path: Vec<Frame<'a>>,
    out: Walk,
}

impl<'a, 'w> Walker<'a, 'w> {
    pub fn new(
        src: &'a str,
        lines: &'w LineIndex,
        defs: &'w DefTable<'a>,
        bound: &'w Bound<'a>,
        stored: &'w HashSet<&'a str>,
        sequences: &'w Sequences<'a>,
        config: &'w ExtractConfig,
    ) -> Self {
        let out = Walk {
            def_flags: vec![0; defs.entries.len()],
            ..Walk::default()
        };
        Walker {
            src,
            lines,
            defs,
            bound,
            stored,
            sequences,
            loops: Vec::new(),
            config,
            exprs: config.kinds.any(&EXPRESSION_KINDS),
            enc: Encoder::default(),
            path: Vec::new(),
            out,
        }
    }

    pub fn run(mut self, suite: &'a [Stmt]) -> Walk {
        let module = At {
            scope: -1,
            header: -1,
            flags: 0,
        };
        self.body(suite, module);
        self.out
    }

    fn wants(&self, kind: Kind) -> bool {
        self.config.kinds.has(kind)
    }

    fn wants_any(&self, kinds: &[Kind]) -> bool {
        self.config.kinds.any(kinds)
    }

    /// A value tree under the assignment budget.
    fn value(&mut self, expr: Option<&Expr>, typed: bool) -> Value {
        self.enc.value(expr, typed, MAX_NODES, self.src)
    }

    // ---- traversal ----------------------------------------------------------------------

    fn child(&mut self, frame: Frame<'a>, expr: &'a Expr, at: At) {
        if !self.exprs {
            return;
        }
        self.path.push(frame);
        self.expr(expr, at);
        self.path.pop();
    }

    fn opt_child(&mut self, frame: Frame<'a>, expr: Option<&'a Expr>, at: At) {
        if let Some(e) = expr {
            self.child(frame, e, at);
        }
    }

    fn body(&mut self, stmts: &'a [Stmt], at: At) {
        for stmt in stmts {
            self.stmt(stmt, at);
        }
    }

    fn inner(at: At, flag: u32) -> At {
        At {
            flags: at.flags | flag,
            ..at
        }
    }

    /// The values a `for` binds its variable to, when it is a plain name the body does not bind
    /// again and the iterable is a literal sequence (`loops.rs`).
    fn loop_values(&self, f: &'a ast::StmtFor, at: At) -> Option<LoopValues<'a>> {
        if !self.wants(Kind::Calls) {
            return None;
        }
        let Expr::Name(target) = &*f.target else {
            return None;
        };
        let elts = match &*f.iter {
            Expr::Name(seq) => {
                let start = f.range.start().to_u32();
                self.sequences.visible(seq.id.as_str(), at.scope, start)?
            }
            iter => literal_elements(iter)?,
        };
        let name = target.id.as_str();
        if elts.is_empty() || rebinds(&f.body, name, self.defs) {
            return None;
        }
        let values = elts.iter().map(|e| self.enc.shallow(e, self.src)).collect();
        Some(LoopValues {
            scope: at.scope,
            name,
            values,
        })
    }

    /// Whether the node being walked runs later than the statement around it: in a lambda or
    /// a comprehension.
    fn deferred(&self) -> bool {
        self.path.iter().any(|frame| {
            matches!(frame, Frame::Step(step) if *step == "lambda" || step.starts_with("comp:"))
        })
    }

    /// `row` once per combination of the values of the loops around it whose variable it
    /// passes as a whole argument; `row` alone when there is none, or too many combinations.
    fn loop_rows(&self, row: WatchedCall, scope: i64) -> Vec<WatchedCall> {
        if self.loops.is_empty() || self.deferred() {
            return vec![row];
        }
        let passes = |name: &str| {
            let chain = |v: &Value| matches!(v, Value::Chain(c) if c == name);
            row.args.iter().any(chain) || row.kws.iter().any(|k| chain(&k.1))
        };
        let used: Vec<&LoopValues> = self
            .loops
            .iter()
            .filter(|l| l.scope == scope && passes(l.name))
            .collect();
        let combinations = used.iter().map(|l| l.values.len()).product::<usize>();
        if used.is_empty() || combinations > MAX_LOOP_ROWS {
            return vec![row];
        }
        let mut rows = vec![row];
        for bound in used {
            let swap = |v: &mut Value, value: &Value| {
                if matches!(v, Value::Chain(c) if c == bound.name) {
                    *v = value.clone();
                }
            };
            rows = rows
                .iter()
                .flat_map(|row| {
                    bound.values.iter().map(move |value| {
                        let mut one = row.clone();
                        one.args.iter_mut().for_each(|v| swap(v, value));
                        one.kws.iter_mut().for_each(|k| swap(&mut k.1, value));
                        one
                    })
                })
                .collect();
        }
        rows
    }

    /// Assignments get a row at module and class scope only.
    fn emits_assign(&self, scope: i64) -> bool {
        self.wants(Kind::Assigns) && (scope == -1 || self.defs.is_class(scope))
    }

    fn stmt(&mut self, stmt: &'a Stmt, at: At) {
        match stmt {
            Stmt::FunctionDef(f) => self.visit_def(DefNode::Func(f), at),
            Stmt::ClassDef(c) => self.visit_def(DefNode::Class(c), at),
            Stmt::Import(i) => {
                for alias in &i.names {
                    self.push_import(alias, at, Some(alias.name.as_str()), 0, None);
                }
            }
            Stmt::ImportFrom(i) => {
                let module = i.module.as_ref().map(|m| m.as_str());
                for alias in &i.names {
                    let name = Some(alias.name.as_str());
                    self.push_import(alias, at, module, i.level, name);
                }
            }
            Stmt::Assign(a) => {
                if self.emits_assign(at.scope) {
                    let t = a
                        .targets
                        .iter()
                        .map(|t| self.value(Some(t), false))
                        .collect();
                    let v = self.value(Some(&a.value), false);
                    self.emit_assign(stmt, at, "=".to_string(), t, None, v);
                }
                for t in &a.targets {
                    self.child(Frame::Terminal("assign-target"), t, at);
                }
                self.child(Frame::AssignValue(&a.targets), &a.value, at);
            }
            Stmt::AnnAssign(a) => {
                if self.emits_assign(at.scope) {
                    let t = vec![self.value(Some(&a.target), false)];
                    let ann = self.value(Some(&a.annotation), true);
                    let v = self.value(a.value.as_deref(), false);
                    self.emit_assign(stmt, at, ":".to_string(), t, Some(ann), v);
                }
                self.child(Frame::Terminal("annassign-target"), &a.target, at);
                self.child(Frame::Terminal("annotation"), &a.annotation, at);
                let value = a.value.as_deref();
                self.opt_child(Frame::AnnAssignValue(&a.target), value, at);
            }
            Stmt::AugAssign(a) => {
                let op = binop(a.op);
                if self.emits_assign(at.scope) {
                    let t = vec![self.value(Some(&a.target), false)];
                    let v = self.value(Some(&a.value), false);
                    self.emit_assign(stmt, at, format!("{op}="), t, None, v);
                }
                self.child(Frame::Terminal("augassign-target"), &a.target, at);
                self.child(Frame::AugAssignValue(op, &a.target), &a.value, at);
            }
            Stmt::TypeAlias(a) => {
                if self.emits_assign(at.scope) {
                    let t = vec![self.value(Some(&a.name), false)];
                    let v = self.value(Some(&a.value), true);
                    self.emit_assign(stmt, at, "type".to_string(), t, None, v);
                }
                self.child(Frame::Terminal("typealias-target"), &a.name, at);
                if let Some(tp) = &a.type_params {
                    self.type_params(tp, at);
                }
                self.child(Frame::TypeAliasValue(name_id(&a.name)), &a.value, at);
            }
            Stmt::Expr(e) => self.child(Frame::Terminal("expr"), &e.value, at),
            Stmt::Return(r) => self.opt_child(Frame::Terminal("return"), r.value.as_deref(), at),
            Stmt::Delete(d) => {
                for t in &d.targets {
                    self.child(Frame::Terminal("del"), t, at);
                }
            }
            Stmt::If(i) => {
                self.visit_if(&i.test, &i.body, i.range.start(), &i.elif_else_clauses, at)
            }
            Stmt::For(f) => {
                self.child(Frame::Terminal("for-target"), &f.target, at);
                self.child(Frame::Terminal("for-iter"), &f.iter, at);
                let bound = self.loop_values(f, at);
                let pushed = bound.is_some();
                self.loops.extend(bound);
                self.body(&f.body, Self::inner(at, BLOCK));
                if pushed {
                    self.loops.pop();
                }
                self.body(&f.orelse, Self::inner(at, BLOCK));
            }
            Stmt::While(w) => {
                self.child(Frame::Terminal("while-test"), &w.test, at);
                self.body(&w.body, Self::inner(at, BLOCK));
                self.body(&w.orelse, Self::inner(at, BLOCK));
            }
            Stmt::With(w) => {
                for item in &w.items {
                    self.child(Frame::Terminal("with"), &item.context_expr, at);
                    let target = item.optional_vars.as_deref();
                    self.opt_child(Frame::Terminal("with-target"), target, at);
                }
                self.body(&w.body, Self::inner(at, BLOCK));
            }
            Stmt::Try(t) => {
                let inner = Self::inner(at, TRY);
                self.body(&t.body, inner);
                for handler in &t.handlers {
                    let ast::ExceptHandler::ExceptHandler(handler) = handler;
                    self.opt_child(Frame::Terminal("except"), handler.type_.as_deref(), inner);
                    self.body(&handler.body, inner);
                }
                self.body(&t.orelse, inner);
                self.body(&t.finalbody, inner);
            }
            Stmt::Match(m) => {
                self.child(Frame::Terminal("match"), &m.subject, at);
                let case_at = Self::inner(at, BLOCK);
                for case in &m.cases {
                    self.pattern(&case.pattern, case_at);
                    let guard = case.guard.as_deref();
                    self.opt_child(Frame::Terminal("case-guard"), guard, case_at);
                    self.body(&case.body, case_at);
                }
            }
            Stmt::Raise(r) => {
                self.opt_child(Frame::Terminal("raise"), r.exc.as_deref(), at);
                self.opt_child(Frame::Terminal("raise-from"), r.cause.as_deref(), at);
            }
            Stmt::Assert(a) => {
                self.child(Frame::Terminal("assert"), &a.test, at);
                self.opt_child(Frame::Terminal("assert-msg"), a.msg.as_deref(), at);
            }
            _ => {}
        }
    }

    fn pattern(&mut self, pattern: &'a Pattern, at: At) {
        match pattern {
            Pattern::MatchValue(p) => self.child(Frame::Terminal("case"), &p.value, at),
            Pattern::MatchSequence(p) => {
                for sub in &p.patterns {
                    self.pattern(sub, at);
                }
            }
            Pattern::MatchMapping(p) => {
                for key in &p.keys {
                    self.child(Frame::Terminal("case"), key, at);
                }
                for sub in &p.patterns {
                    self.pattern(sub, at);
                }
            }
            Pattern::MatchClass(p) => {
                self.child(Frame::Terminal("case"), &p.cls, at);
                for sub in &p.arguments.patterns {
                    self.pattern(sub, at);
                }
                for kw in &p.arguments.keywords {
                    self.pattern(&kw.pattern, at);
                }
            }
            Pattern::MatchAs(p) => {
                if let Some(sub) = &p.pattern {
                    self.pattern(sub, at);
                }
            }
            Pattern::MatchOr(p) => {
                for sub in &p.patterns {
                    self.pattern(sub, at);
                }
            }
            Pattern::MatchSingleton(_) | Pattern::MatchStar(_) => {}
        }
    }

    fn type_params(&mut self, tp: &'a ast::TypeParams, at: At) {
        let step = Frame::Terminal("typeparam");
        for param in &tp.type_params {
            match param {
                ast::TypeParam::TypeVar(t) => {
                    self.opt_child(step, t.bound.as_deref(), at);
                    self.opt_child(step, t.default.as_deref(), at);
                }
                ast::TypeParam::ParamSpec(t) => self.opt_child(step, t.default.as_deref(), at),
                ast::TypeParam::TypeVarTuple(t) => self.opt_child(step, t.default.as_deref(), at),
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
        at: At,
    ) {
        if let Expr::Compare(cmp) = test
            && cmp.operands[0].is_name_expr()
            && self.wants(Kind::Branches)
        {
            let (l, c) = self.lines.pos(start);
            let last = body.last().map(|s| s.range().end()).unwrap_or(start);
            let test_value = self.value(Some(test), false);
            self.out.branches.push(Branch {
                site: at.site((l, c)),
                body_end: self.lines.line(last),
                test: test_value,
            });
        }
        self.child(Frame::Terminal("if-test"), test, at);
        let type_checking = is_type_checking_test(test);
        self.body(
            body,
            Self::inner(at, if type_checking { TYPE_ONLY } else { IF }),
        );
        let else_at = Self::inner(at, if type_checking { TC_ELSE } else { IF });
        let Some(first) = rest.first() else {
            return;
        };
        match &first.test {
            Some(t) => self.visit_if(t, &first.body, first.range.start(), &rest[1..], else_at),
            None => self.body(&first.body, else_at),
        }
    }

    /// Decorators, bases, class keywords, annotations, defaults and type params belong to the
    /// enclosing scope; the def is their header.
    fn visit_def(&mut self, node: DefNode<'a>, at: At) {
        let idx = self.defs.idx(node);
        self.out.def_flags[idx] = at.flags;
        let header = At {
            header: idx as i64,
            ..at
        };
        for d in node.decorators() {
            self.child(Frame::Terminal("decorator"), &d.expression, header);
        }
        if let Some(tp) = node.type_params() {
            self.type_params(tp, header);
        }
        let flag = match node {
            DefNode::Func(f) => {
                for param in f.parameters.iter() {
                    let name = param.name().as_str();
                    self.opt_child(Frame::ParamAnn(name), param.annotation(), header);
                    self.opt_child(Frame::ParamDefault(name), param.default(), header);
                }
                self.opt_child(Frame::Terminal("returns"), f.returns.as_deref(), header);
                FUNC
            }
            DefNode::Class(c) => {
                if let Some(args) = &c.arguments {
                    for (i, base) in args.args.iter().enumerate() {
                        self.child(Frame::Base(i), base, header);
                    }
                    for kw in args.keywords.iter() {
                        let name = kw.arg.as_ref().map(|a| a.as_str());
                        self.child(Frame::ClassKw(name), &kw.value, header);
                    }
                }
                CLASS
            }
        };
        let body_at = At {
            scope: idx as i64,
            header: -1,
            flags: at.flags | flag,
        };
        self.body(node.body(), body_at);
    }

    fn push_import(
        &mut self,
        alias: &ast::Alias,
        at: At,
        module: Option<&str>,
        level: u32,
        name: Option<&str>,
    ) {
        if !self.wants(Kind::Imports) {
            return;
        }
        let (l, c) = self.lines.pos(alias.range.start());
        self.out.imports.push(Import {
            scope: at.scope,
            l,
            c,
            flags: at.flags,
            module: module.map(str::to_string),
            level,
            name: name.map(str::to_string),
            asname: alias.asname.as_ref().map(|a| a.to_string()),
        });
    }

    fn emit_assign(
        &mut self,
        stmt: &Stmt,
        at: At,
        op: String,
        t: Vec<Value>,
        ann: Option<Value>,
        v: Value,
    ) {
        let (l, c) = self.lines.pos(stmt.range().start());
        self.out.assigns.push(Assign {
            ann,
            c,
            f: at.flags,
            l,
            op,
            s: at.scope,
            t,
            v,
        });
    }

    fn expr(&mut self, expr: &'a Expr, at: At) {
        match expr {
            Expr::Call(call) => {
                self.emit_call(call, at);
                self.child(Frame::CallFunc, &call.func, at);
                for (i, arg) in call.arguments.args.iter().enumerate() {
                    self.child(Frame::Arg(call, i), arg, at);
                }
                for kw in call.arguments.keywords.iter() {
                    let name = kw.arg.as_ref().map(|a| a.as_str());
                    self.child(Frame::Kw(call, name), &kw.value, at);
                }
            }
            Expr::StringLiteral(s) => self.emit_string(s, at),
            Expr::Name(n) => self.emit_ref(n, at),
            Expr::Dict(d) => {
                self.emit_watched(d, at);
                for item in d.items.iter() {
                    if let Some(key) = &item.key {
                        self.child(Frame::Step("dict:key"), key, at);
                    }
                    let frame = match &item.key {
                        None => Frame::Step("dict:**"),
                        Some(Expr::StringLiteral(k)) if is_identifier(k.value.to_str()) => {
                            Frame::DictVal(k.value.to_str())
                        }
                        Some(_) => Frame::Step("dict:val"),
                    };
                    self.child(frame, &item.value, at);
                }
            }
            Expr::BoolOp(b) => {
                for v in &b.values {
                    self.child(Frame::Op("bool:", boolop(b.op)), v, at);
                }
            }
            Expr::Named(n) => {
                self.child(Frame::Step("walrus-target"), &n.target, at);
                self.child(Frame::Walrus(name_id(&n.target)), &n.value, at);
            }
            Expr::BinOp(b) => {
                let step = Frame::Op("bin:", binop(b.op));
                self.child(step, &b.left, at);
                self.child(step, &b.right, at);
            }
            Expr::UnaryOp(u) => self.child(Frame::Op("unary:", unaryop(u.op)), &u.operand, at),
            Expr::Lambda(l) => {
                for param in l.parameters.iter().flat_map(|p| p.iter()) {
                    self.opt_child(Frame::Step("lambda:default"), param.default(), at);
                }
                self.child(Frame::Step("lambda"), &l.body, at);
            }
            Expr::If(i) => {
                self.child(Frame::Step("ifexp:test"), &i.test, at);
                self.child(Frame::Step("ifexp:body"), &i.body, at);
                self.child(Frame::Step("ifexp:else"), &i.orelse, at);
            }
            Expr::Set(s) => self.elts(&s.elts, "elt:set", at),
            Expr::List(l) => self.elts(&l.elts, "elt:list", at),
            Expr::Tuple(t) => self.elts(&t.elts, "elt:tuple", at),
            Expr::ListComp(ast::ExprListComp {
                elt, generators, ..
            })
            | Expr::SetComp(ast::ExprSetComp {
                elt, generators, ..
            })
            | Expr::Generator(ast::ExprGenerator {
                elt, generators, ..
            }) => {
                self.child(Frame::Step("comp:elt"), elt, at);
                self.generators(generators, at);
            }
            Expr::DictComp(c) => {
                self.opt_child(Frame::Step("comp:key"), c.key.as_deref(), at);
                self.child(Frame::Step("comp:val"), &c.value, at);
                self.generators(&c.generators, at);
            }
            Expr::Await(a) => self.child(Frame::Step("await"), &a.value, at),
            Expr::Yield(y) => self.opt_child(Frame::Step("yield"), y.value.as_deref(), at),
            Expr::YieldFrom(y) => self.child(Frame::Step("yieldfrom"), &y.value, at),
            Expr::Compare(c) => {
                self.child(Frame::Step("cmp:left"), &c.operands[0], at);
                for (op, operand) in c.ops.iter().zip(c.operands[1..].iter()) {
                    self.child(Frame::Op("cmp:", cmpop(*op)), operand, at);
                }
            }
            Expr::FString(f) => {
                for part in f.value.iter() {
                    if let ast::FStringPartRef::FString(fs) = part {
                        self.interpolations(&fs.elements, "fstr", at);
                    }
                }
            }
            Expr::TString(t) => {
                for ts in t.value.iter() {
                    self.interpolations(&ts.elements, "tstr", at);
                }
            }
            Expr::Attribute(a) => self.child(Frame::Attr(a.attr.as_str()), &a.value, at),
            Expr::Subscript(s) => {
                self.child(Frame::SubValue, &s.value, at);
                self.child(Frame::Step("slice"), &s.slice, at);
            }
            Expr::Starred(s) => self.child(Frame::Step("star"), &s.value, at),
            Expr::Slice(s) => {
                self.opt_child(Frame::Step("slice-part"), s.lower.as_deref(), at);
                self.opt_child(Frame::Step("slice-part"), s.upper.as_deref(), at);
                self.opt_child(Frame::Step("slice-part"), s.step.as_deref(), at);
            }
            _ => {}
        }
    }

    fn elts(&mut self, elts: &'a [Expr], step: &'static str, at: At) {
        for e in elts {
            self.child(Frame::Step(step), e, at);
        }
    }

    fn generators(&mut self, gens: &'a [ast::Comprehension], at: At) {
        for g in gens {
            self.child(Frame::Step("comp:target"), &g.target, at);
            self.child(Frame::Step("comp:iter"), &g.iter, at);
            for cond in &g.ifs {
                self.child(Frame::Step("comp:if"), cond, at);
            }
        }
    }

    /// A format spec is a JoinedStr in CPython: nested fields step `fstr`. An f-string spec slot
    /// is transparent; a t-string `Interpolation.format_spec` slot adds its own `tstr` step.
    fn interpolations(
        &mut self,
        elements: &'a [InterpolatedStringElement],
        step: &'static str,
        at: At,
    ) {
        for el in elements {
            let InterpolatedStringElement::Interpolation(i) = el else {
                continue;
            };
            self.child(Frame::Step(step), &i.expression, at);
            let Some(spec) = &i.format_spec else {
                continue;
            };
            if step == "tstr" {
                self.path.push(Frame::Step("tstr"));
                self.interpolations(&spec.elements, "fstr", at);
                self.path.pop();
            } else {
                self.interpolations(&spec.elements, "fstr", at);
            }
        }
    }

    // ---- emitters -------------------------------------------------------------------------

    fn emit_call(&mut self, call: &'a ast::ExprCall, at: At) {
        let (want_callees, want_calls) = (self.wants(Kind::Callees), self.wants(Kind::Calls));
        if !want_callees && !want_calls {
            return;
        }
        let callee = callee_text(call);
        let (l, c) = self.lines.pos(call.range().start());
        if want_callees {
            let key = TextKey {
                scope: at.scope,
                header: at.header,
                text: callee.clone(),
            };
            count(&mut self.out.callees, key, l, c);
        }
        let last = callee.rsplit('.').next().unwrap_or("");
        if !want_calls || !self.config.is_watched_callee(last) {
            return;
        }
        let row = WatchedCall {
            site: at.site((l, c)),
            args: self.enc.shallow_args(call, self.src),
            kws: self.enc.shallow_keywords(call, self.src),
            callee,
        };
        let rows = self.loop_rows(row, at.scope);
        self.out.calls.extend(rows);
    }

    fn emit_string(&mut self, lit: &'a ast::ExprStringLiteral, at: At) {
        if !self.wants_any(&[Kind::Strs, Kind::Names, Kind::Tags]) {
            return;
        }
        let v = StrValue::of(lit, self.src);
        let value = v.text.as_str();
        let (l, c) = self.lines.pos(lit.range().start());
        if value.contains('<') && self.wants(Kind::Tags) {
            for name in tag_names(value) {
                self.out.tags.push(Tag {
                    site: at.site((l, c)),
                    name: name.to_string(),
                });
            }
        }
        if !value.contains('.') && !value.starts_with(|ch: char| ch.is_ascii_uppercase()) {
            return;
        }
        let parts = if v.parts.len() > 1 {
            Some(&v.parts)
        } else {
            None
        };
        let symbolic = is_symbolic(value, v.surrogate);
        let mut dotted = symbolic && is_dotted_path(value);
        if let Some(parts) = parts {
            dotted |= parts
                .iter()
                .any(|(p, s)| is_symbolic(p, *s) && is_dotted_path(p));
        }
        if !dotted {
            if symbolic && self.wants(Kind::Names) {
                let key = TextKey {
                    scope: at.scope,
                    header: at.header,
                    text: value.to_string(),
                };
                count(&mut self.out.names, key, l, c);
            }
            return;
        }
        if !self.wants(Kind::Strs) {
            return;
        }
        let (chain, up) = climb(&self.path);
        let parts = parts.map(|parts| {
            parts
                .iter()
                .map(|(p, s)| is_symbolic(p, *s).then(|| p.clone()))
                .collect()
        });
        self.out.strs.push(DottedStr {
            site: at.site((l, c)),
            value: symbolic.then(|| value.to_string()),
            chain,
            up,
            parts,
        });
    }

    /// An import-bound name is a `refs` row when it is first-party and class-like, else a
    /// `uses` count. Another load may be a derived local: it becomes a candidate. `derived`
    /// needs the `refs` rows too: they seed the derivation.
    fn emit_ref(&mut self, name: &'a ast::ExprName, at: At) {
        if !self.wants_any(&[Kind::Refs, Kind::Derived, Kind::Uses]) {
            return;
        }
        let id = name.id.as_str();
        let (l, c) = self.lines.pos(name.range.start());
        let Some(binding) = self.bound.get(id).copied() else {
            if name.ctx == ExprContext::Load
                && self.wants(Kind::Derived)
                && self.stored.contains(id)
            {
                let (chain, up) = climb(&self.path);
                self.out.candidates.push(Ref {
                    site: at.site((l, c)),
                    root: id.to_string(),
                    chain,
                    up,
                    origin: None,
                });
            }
            return;
        };
        let (chain, up) = climb(&self.path);
        let attr_upper = chain
            .strip_prefix('.')
            .is_some_and(|rest| rest.starts_with(|ch: char| ch.is_ascii_uppercase()));
        if !(binding.first_party && (binding.class_like || attr_upper)) {
            if !self.wants(Kind::Uses) {
                return;
            }
            let key = UseKey {
                scope: at.scope,
                header: at.header,
                root: id.to_string(),
                attrs: leading_attrs(&chain).to_string(),
            };
            count(&mut self.out.uses, key, l, c);
            return;
        }
        if !self.wants_any(&[Kind::Refs, Kind::Derived]) {
            return;
        }
        self.out.refs.push(Ref {
            site: at.site((l, c)),
            root: id.to_string(),
            chain,
            up,
            origin: None,
        });
    }

    fn emit_watched(&mut self, dict: &'a ast::ExprDict, at: At) {
        if !self.wants(Kind::Watched) || self.config.watch_keys.is_empty() {
            return;
        }
        for item in dict.items.iter() {
            let Some(Expr::StringLiteral(key)) = &item.key else {
                continue;
            };
            let key_text = key.value.to_str();
            if !self
                .config
                .watch_keys
                .iter()
                .any(|watched| watched == key_text)
            {
                continue;
            }
            let (chain, up) = climb(&self.path);
            let position = self.lines.pos(item.value.range().start());
            self.out.watched.push(WatchedKey {
                site: at.site(position),
                key: key_text.to_string(),
                value: self.enc.shallow(&item.value, self.src),
                chain,
                up,
            });
        }
    }
}
