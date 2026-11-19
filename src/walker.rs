//! One traversal of a parsed module that collects every row list of an L1 record.

use crate::config::{ExtractConfig, Kind};
use crate::defs::{Bound, DefNode, DefTable};
use crate::facts::flags::{BLOCK, CLASS, FUNC, IF, TC_ELSE, TRY, TYPE_ONLY};
use crate::facts::{
    Assign, Branch, Computed, DottedStr, Import, Ref, Site, Tag, TextKey, UseKey, WatchedCall,
    WatchedKey,
};
use crate::loops::{Sequences, literal_elements, rebinds};
use crate::path::{Frame, climb, computed_read, listed};
use crate::source::LineIndex;
use crate::strings::{is_dotted_path, is_identifier, is_symbolic, leading_attrs, tag_names};
use crate::value::{
    Encoder, Keyword, MAX_NODES, StrValue, Value, binop, boolop, callee_text, cmpop, keyword_name,
    unaryop,
};
use ruff_python_ast::{self as ast, Expr, ExprContext, InterpolatedStringElement, Pattern, Stmt};
use ruff_text_size::{Ranged, TextSize};
use std::collections::{HashMap, HashSet};

/// Kinds whose rows come from expressions. Without any of them the walk skips expressions.
const EXPRESSION_KINDS: [Kind; 12] = [
    Kind::Callees,
    Kind::Calls,
    Kind::Computed,
    Kind::Derived,
    Kind::Escapes,
    Kind::Listed,
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

/// The methods that change a list, set or dict in place, read at module level as rows that add
/// to a name's items (`emit_mutation`).
const MUTATORS: [&str; 5] = ["append", "extend", "insert", "update", "add"];

/// Calls handed a name that read it and change no value they are handed, by the callee's last
/// name: copies and the read-only builtins.
const READING_CALLEES: [&str; 27] = [
    "list",
    "tuple",
    "set",
    "frozenset",
    "sorted",
    "reversed",
    "len",
    "iter",
    "enumerate",
    "zip",
    "map",
    "filter",
    "any",
    "all",
    "sum",
    "min",
    "max",
    "isinstance",
    "issubclass",
    "print",
    "repr",
    "str",
    "bool",
    "chain",
    "replace",
    "copy",
    "deepcopy",
];

/// Methods of a list, set or dict that read it, or only take items out of it.
const READING_METHODS: [&str; 13] = [
    "copy", "values", "keys", "items", "get", "index", "count", "remove", "pop", "clear",
    "discard", "sort", "reverse",
];

/// Whether the item reading understands a load of a name with this use-site context (`climb`'s
/// chain and escape steps): one that cannot change what the name holds, or one it reads as a
/// change (a module-level `name.append(x)` statement, a subscript store). Anything else, such
/// as handing the name to a call, an attribute store, or another method, is not understood.
fn understood_use(chain: &str, up: &str, module_level: bool) -> bool {
    let first = up.split(' ').next().unwrap_or_default();
    if chain.is_empty() {
        if let Some(call) = first.strip_prefix("arg:") {
            let callee = call.split_once(':').map_or("", |(_, callee)| callee);
            let last = callee.rsplit('.').next().unwrap_or_default();
            return READING_CALLEES.contains(&last);
        }
        let parts = [
            "elt:",
            "star",
            "dict:",
            "bin:",
            "bool:",
            "ifexp:",
            "cmp:",
            "unary:",
            "assign:",
            "annassign:",
            "augassign:",
            "comp:",
            "slice",
            "await",
            "kw:**:",
        ];
        let terminals = [
            "expr",
            "for-iter",
            "if-test",
            "while-test",
            "assert",
            "assert-msg",
            "return",
            "case",
            "match",
            "annotation",
            "returns",
            "except",
            "raise",
        ];
        let headers = ["base:", "classkw:", "param-ann:"];
        if headers.iter().any(|header| first.starts_with(header)) {
            return true;
        }
        return parts.iter().any(|part| first.starts_with(part)) || terminals.contains(&first);
    }
    if chain.starts_with("()") {
        return true;
    }
    if chain == "[]" {
        return !first.starts_with("augassign-target");
    }
    let Some(attrs) = chain.strip_prefix('.') else {
        return false;
    };
    match attrs.split_once("()") {
        // One method called on the name.
        Some((method, "")) if !method.contains('.') => {
            let statement = module_level && first == "expr";
            READING_METHODS.contains(&method) || (statement && MUTATORS.contains(&method))
        }
        Some(_) => false,
        // An attribute read, not a store.
        None => !attrs.contains('[') && !first.ends_with("-target") && first != "del",
    }
}

/// What a use the item reading does not understand makes unread: the name itself when the code
/// hands it on or calls another method of it (`W.setdefault(...)`); `name.ATTR` when it changes
/// an attribute of it (`Reg.WORKFLOWS = ...`, `Reg.WORKFLOWS.append(B)`); `name.*`, every
/// attribute, when it is handed to `setattr` or `delattr`.
fn escaped_name(id: &str, chain: &str, first: &str) -> String {
    let callee = first.strip_prefix("arg:0:").unwrap_or_default();
    if chain.is_empty() && matches!(callee, "setattr" | "delattr") {
        return format!("{id}.*");
    }
    let Some(attrs) = chain.strip_prefix('.') else {
        return id.to_string();
    };
    let attribute = attrs.split(['.', '(', '[']).next().unwrap_or_default();
    let called = attrs[attribute.len()..] == *"()";
    if attribute.is_empty() || called {
        return id.to_string();
    }
    format!("{id}.{attribute}")
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
    pub computed: Vec<Computed>,
    pub strs: Vec<DottedStr>,
    pub tags: Vec<Tag>,
    pub watched: Vec<WatchedKey>,
    pub escapes: Vec<Tag>,
    pub branches: Vec<Branch>,
    pub uses: Counts<UseKey>,
    pub callees: Counts<TextKey>,
    pub names: Counts<TextKey>,
    /// Every load of an import-bound name, by name, and the loads of it that sit in a literal.
    pub loads: Counts<TextKey>,
    pub listed: Counts<TextKey>,
    /// Statement flags by def index.
    pub def_flags: Vec<u32>,
    /// The functional `TypedDict`s the walk read as class declarations.
    pub typed_dicts: Vec<TypedDictClass>,
}

/// A functional `TypedDict` (`Name = TypedDict("Name", {"key": type})`): the class it declares,
/// as the class form would, with one annotated field per key. Where and how it is written (its
/// assignment's scope, flags and lines), the callee as its base and the call's keywords.
pub struct TypedDictClass {
    pub scope: i64,
    pub flags: u32,
    pub l: i64,
    pub c: i64,
    pub e: i64,
    pub name: String,
    pub base: Value,
    pub kw: Vec<Keyword>,
    /// Each field: its key, its type, and the key's line and column.
    pub fields: Vec<(String, Value, i64, i64)>,
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

/// A variable a `for`, a comprehension or a lambda around the code being walked binds, in its
/// scope, and what it takes (`Computed`).
struct ElementOf<'a> {
    scope: i64,
    variable: &'a str,
    from: Element<'a>,
}

#[derive(Clone, Copy)]
enum Element<'a> {
    /// Each item of the name a `for` or a comprehension loops over.
    Of(&'a str),
    /// Each string of a list or tuple of string literals in the loop's header.
    Strings,
    /// Anything else: a value the walk cannot name.
    Other,
}

pub struct Walker<'a, 'w> {
    src: &'a str,
    lines: &'w LineIndex,
    defs: &'w DefTable<'a>,
    bound: &'w Bound<'a>,
    stored: &'w HashSet<&'a str>,
    sequences: &'w Sequences<'a>,
    loops: Vec<LoopValues<'a>>,
    /// The variables of the loops, comprehensions and lambdas around the code being walked.
    elements: Vec<ElementOf<'a>>,
    /// The names each function declares `global`, by its scope (`emit_global`).
    globals: HashMap<i64, HashSet<String>>,
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
            elements: Vec::new(),
            globals: HashMap::new(),
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

    /// A functional `TypedDict` where the walk records assignments, read as the class it declares:
    /// one target name, a callee whose last name is `TypedDict`, and fields that are a dict literal
    /// of string keys with no dot. Any other form stays an assignment.
    fn typed_dict(&mut self, a: &'a ast::StmtAssign, at: At) -> Option<TypedDictClass> {
        if !self.emits_assign(at.scope) || !self.wants(Kind::Defs) {
            return None;
        }
        let [Expr::Name(target)] = a.targets.as_slice() else {
            return None;
        };
        let Expr::Call(call) = &*a.value else {
            return None;
        };
        let last = match &*call.func {
            Expr::Name(name) => name.id.as_str(),
            Expr::Attribute(attr) => attr.attr.as_str(),
            _ => return None,
        };
        let [_, Expr::Dict(dict)] = &*call.arguments.args else {
            return None;
        };
        if last != "TypedDict" {
            return None;
        }
        let mut fields = Vec::new();
        for item in dict.items.iter() {
            let Some(Expr::StringLiteral(key)) = &item.key else {
                return None;
            };
            let key = key.value.to_str();
            if key.contains('.') {
                return None;
            }
            let (l, c) = self.lines.pos(item.key.as_ref()?.range().start());
            let ann = self.value(Some(&item.value), true);
            fields.push((key.to_string(), ann, l, c));
        }
        let mut kw = Vec::new();
        for k in call.arguments.keywords.iter() {
            kw.push(Keyword(keyword_name(k), self.value(Some(&k.value), false)));
        }
        let (l, c) = self.lines.pos(a.range.start());
        Some(TypedDictClass {
            scope: at.scope,
            flags: at.flags,
            l,
            c,
            e: self.lines.line(a.range.end()),
            name: target.id.to_string(),
            base: self.value(Some(&call.func), false),
            kw,
            fields,
        })
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
                if let Some(declared) = self.typed_dict(a, at) {
                    self.out.typed_dicts.push(declared);
                } else if self.emits_assign(at.scope) {
                    let t = a
                        .targets
                        .iter()
                        .map(|t| self.value(Some(t), false))
                        .collect();
                    let v = self.enc.assigned(Some(&a.value), self.src);
                    self.emit_assign(stmt, at, "=".to_string(), t, None, v);
                } else {
                    let targets: Vec<&Expr> = a.targets.iter().collect();
                    self.emit_global(stmt, at, &targets, Some(&a.value));
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
                    let v = self.enc.assigned(a.value.as_deref(), self.src);
                    self.emit_assign(stmt, at, ":".to_string(), t, Some(ann), v);
                } else if a.value.is_some() {
                    self.emit_global(stmt, at, &[&*a.target], a.value.as_deref());
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
                    let v = self.enc.assigned(Some(&a.value), self.src);
                    self.emit_assign(stmt, at, format!("{op}="), t, None, v);
                } else {
                    self.emit_global(stmt, at, &[&*a.target], Some(&a.value));
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
            Stmt::Expr(e) => {
                self.emit_mutation(stmt, &e.value, at);
                self.child(Frame::Terminal("expr"), &e.value, at)
            }
            Stmt::Global(g) => {
                let names = self.globals.entry(at.scope).or_default();
                names.extend(g.names.iter().map(|name| name.to_string()));
            }
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
                let elements = self.elements.len();
                self.loop_elements(&f.target, &f.iter, at.scope, &f.body);
                self.body(&f.body, Self::inner(at, BLOCK));
                self.elements.truncate(elements);
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

    /// A module-level call that changes a module-level name in place (`WORKFLOWS.append(B)`),
    /// as an assignment row: op `()`, the target `name.method`, the call as the value. Only a
    /// reader of a name's items reads it; the others read `=`, `:` and `+=` rows.
    fn emit_mutation(&mut self, stmt: &Stmt, value: &Expr, at: At) {
        if at.scope != -1 || !self.emits_assign(at.scope) {
            return;
        }
        let Expr::Call(call) = value else {
            return;
        };
        let Expr::Attribute(method) = call.func.as_ref() else {
            return;
        };
        let Expr::Name(name) = method.value.as_ref() else {
            return;
        };
        if !MUTATORS.contains(&method.attr.as_str()) {
            return;
        }
        let target = Value::Chain(format!("{}.{}", name.id, method.attr));
        let v = self.enc.assigned(Some(value), self.src);
        self.emit_assign(stmt, at, "()".to_string(), vec![target], None, v);
    }

    /// An assignment in a function to a name the function declares `global`: a module-level row
    /// with op `global`, the statement's flags and the assigned value, so the reading knows what
    /// the function may bind the name to.
    fn emit_global(&mut self, stmt: &Stmt, at: At, targets: &[&Expr], value: Option<&Expr>) {
        if at.scope == -1 || self.emits_assign(at.scope) || !self.wants(Kind::Assigns) {
            return;
        }
        let Some(names) = self.globals.get(&at.scope) else {
            return;
        };
        let bound: Vec<String> = targets
            .iter()
            .filter_map(|target| match target {
                Expr::Name(name) if names.contains(name.id.as_str()) => Some(name.id.to_string()),
                _ => None,
            })
            .collect();
        if bound.is_empty() {
            return;
        }
        let v = self.enc.assigned(value, self.src);
        let module = At { scope: -1, ..at };
        for name in bound {
            let target = Value::Chain(name);
            self.emit_assign(
                stmt,
                module,
                "global".to_string(),
                vec![target],
                None,
                v.clone(),
            );
        }
    }

    /// An `escapes` row: a name the item reading cannot read at the name node's site.
    fn emit_escape(&mut self, name: &ast::ExprName, at: At, escaped: &str) {
        let (l, c) = self.lines.pos(name.range.start());
        self.out.escapes.push(Tag {
            site: at.site((l, c)),
            name: escaped.to_string(),
        });
    }

    /// The `escapes` rows of a name node: a load at module level or in a class body, of a name
    /// no import binds, in a position the item reading does not understand (`understood_use`);
    /// `globals()` and `sys.modules[__name__]`, which reach every name of the module (`*`); and a
    /// store of a name a function declares `global` that is no assignment (a `for` target, say),
    /// sited at module level.
    fn emit_use(&mut self, name: &ast::ExprName, at: At) {
        if !self.wants(Kind::Escapes) {
            return;
        }
        let id = name.id.as_str();
        let (chain, up) = climb(&self.path);
        let first = up.split(' ').next().unwrap_or_default();
        if name.ctx != ExprContext::Load {
            let global = at.scope != -1
                && !self.emits_assign(at.scope)
                && self
                    .globals
                    .get(&at.scope)
                    .is_some_and(|names| names.contains(id));
            let assigned = matches!(
                first,
                "assign-target" | "annassign-target" | "augassign-target"
            );
            if global && !assigned {
                let module = At { scope: -1, ..at };
                self.emit_escape(name, module, id);
            }
            return;
        }
        if !self.emits_assign(at.scope) {
            return;
        }
        let everything =
            (id == "globals" && chain == "()") || (id == "__name__" && first == "slice");
        if everything {
            self.emit_escape(name, at, "*");
            return;
        }
        if id.starts_with("__")
            || self.bound.contains_key(id)
            || understood_use(&chain, &up, at.scope == -1)
        {
            return;
        }
        self.emit_escape(name, at, &escaped_name(id, &chain, first));
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
                let elements = self.elements.len();
                for param in l.parameters.iter().flat_map(|p| p.iter()) {
                    self.elements.push(ElementOf {
                        scope: at.scope,
                        variable: param.name().as_str(),
                        from: Element::Other,
                    });
                }
                self.child(Frame::Step("lambda"), &l.body, at);
                self.elements.truncate(elements);
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
                let elements = self.elements.len();
                self.comprehension_elements(generators, at.scope);
                self.child(Frame::Step("comp:elt"), elt, at);
                self.generators(generators, at);
                self.elements.truncate(elements);
            }
            Expr::DictComp(c) => {
                let elements = self.elements.len();
                self.comprehension_elements(&c.generators, at.scope);
                self.opt_child(Frame::Step("comp:key"), c.key.as_deref(), at);
                self.child(Frame::Step("comp:val"), &c.value, at);
                self.generators(&c.generators, at);
                self.elements.truncate(elements);
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
                self.emit_module_key(s, at);
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

    /// What the variables of a `for` take (`ElementOf`): a body that binds the variable again
    /// makes it a value the walk cannot name.
    fn loop_elements(&mut self, target: &'a Expr, iter: &'a Expr, scope: i64, body: &'a [Stmt]) {
        if !self.wants(Kind::Computed) {
            return;
        }
        let from = match target {
            Expr::Name(variable) if !rebinds(body, variable.id.as_str(), self.defs) => {
                self.element_from(iter, scope)
            }
            _ => Element::Other,
        };
        self.push_elements(target, scope, from);
    }

    /// What the variables of each generator of a comprehension take, the generators in order, so
    /// one that loops over an earlier one's variable takes a value the walk cannot name.
    fn comprehension_elements(&mut self, generators: &'a [ast::Comprehension], scope: i64) {
        if !self.wants(Kind::Computed) {
            return;
        }
        for generator in generators {
            let from = self.element_from(&generator.iter, scope);
            self.push_elements(&generator.target, scope, from);
        }
    }

    /// Each name a loop target binds, taking `from` when the target is one plain name.
    fn push_elements(&mut self, target: &'a Expr, scope: i64, from: Element<'a>) {
        match target {
            Expr::Name(variable) => self.elements.push(ElementOf {
                scope,
                variable: variable.id.as_str(),
                from,
            }),
            Expr::Tuple(t) => t
                .elts
                .iter()
                .for_each(|e| self.push_elements(e, scope, Element::Other)),
            Expr::List(l) => l
                .elts
                .iter()
                .for_each(|e| self.push_elements(e, scope, Element::Other)),
            Expr::Starred(s) => self.push_elements(&s.value, scope, Element::Other),
            _ => {}
        }
    }

    /// What a loop over `iter` hands its variable: each item of a name no loop around binds, each
    /// string of a literal of string literals, else a value the walk cannot name.
    fn element_from(&self, iter: &'a Expr, scope: i64) -> Element<'a> {
        if let Expr::Name(name) = iter {
            let name = name.id.as_str();
            let looped = self
                .elements
                .iter()
                .any(|e| e.scope == scope && e.variable == name);
            return if looped {
                Element::Other
            } else {
                Element::Of(name)
            };
        }
        match literal_elements(iter) {
            Some(elts) if elts.iter().all(Expr::is_string_literal_expr) => Element::Strings,
            _ => Element::Other,
        }
    }

    /// Whether the def at `scope` binds `name` itself (a parameter, or any binding in its body),
    /// so a load of it there reads no module-level name.
    fn binds_locally(&self, scope: i64, name: &str) -> bool {
        if scope < 0 {
            return false;
        }
        match self.defs.entries[scope as usize].node {
            DefNode::Func(f) => {
                let parameter = f.parameters.iter().any(|p| p.name().as_str() == name);
                parameter || rebinds(&f.body, name, self.defs)
            }
            DefNode::Class(c) => rebinds(&c.body, name, self.defs),
        }
    }

    /// The name a value is an element of (`Computed`): the variable of the innermost loop or
    /// comprehension that binds it, over a module-level name, or `name[...]`; empty when it is
    /// none. None for an element of a literal of string literals.
    fn sequence_of(&self, value: &'a Expr, scope: i64) -> Option<String> {
        let sequence = match value {
            Expr::Name(variable) => {
                let variable = variable.id.as_str();
                let found = self
                    .elements
                    .iter()
                    .rev()
                    .find(|e| e.scope == scope && e.variable == variable);
                match found.map(|e| e.from) {
                    Some(Element::Of(name)) => name,
                    Some(Element::Strings) => return None,
                    _ => return Some(String::new()),
                }
            }
            Expr::Subscript(s) => match &*s.value {
                Expr::Name(name) => name.id.as_str(),
                _ => return Some(String::new()),
            },
            _ => return Some(String::new()),
        };
        if self.binds_locally(scope, sequence) {
            return Some(String::new());
        }
        Some(sequence.to_string())
    }

    // ---- emitters -------------------------------------------------------------------------

    /// A `computed` row for a module named by `value` at `pos`, when it is no string literal and
    /// no element of a literal of string literals.
    fn emit_computed(
        &mut self,
        value: Option<&'a Expr>,
        form: &'static str,
        at: At,
        pos: (i64, i64),
    ) {
        if value.is_some_and(Expr::is_string_literal_expr) {
            return;
        }
        let sequence = match value {
            Some(value) => self.sequence_of(value, at.scope),
            None => Some(String::new()),
        };
        if let Some(sequence) = sequence {
            self.out.computed.push(Computed {
                site: at.site(pos),
                form,
                sequence,
            });
        }
    }

    /// `sys.modules[key]`, read by any key: no edge names a key of `sys.modules`, so it names a
    /// module the link cannot read, whatever the key.
    fn emit_module_key(&mut self, s: &'a ast::ExprSubscript, at: At) {
        if !self.wants(Kind::Computed) || s.ctx != ExprContext::Load {
            return;
        }
        let Expr::Attribute(attribute) = &*s.value else {
            return;
        };
        let sys = matches!(&*attribute.value, Expr::Name(name) if name.id.as_str() == "sys");
        if !sys || attribute.attr.as_str() != "modules" {
            return;
        }
        let pos = self.lines.pos(s.range().start());
        let sequence = self.sequence_of(&s.slice, at.scope).unwrap_or_default();
        self.out.computed.push(Computed {
            site: at.site(pos),
            form: "sys.modules",
            sequence,
        });
    }

    fn emit_call(&mut self, call: &'a ast::ExprCall, at: At) {
        let (want_callees, want_calls) = (self.wants(Kind::Callees), self.wants(Kind::Calls));
        let want_computed = self.wants(Kind::Computed);
        if !want_callees && !want_calls && !want_computed {
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
        let hook = self.config.is_hook(last)
            || (!callee.contains('.')
                && self
                    .bound
                    .get(callee.as_str())
                    .is_some_and(|binding| binding.hook_alias));
        let watched = self.config.is_watched_callee(last);
        if watched && want_computed {
            self.emit_computed(module_argument(call), "call", at, (l, c));
        }
        if !want_calls || !(watched || hook) {
            return;
        }
        let row = WatchedCall {
            site: at.site((l, c)),
            args: self.enc.shallow_args(call, self.src, hook),
            kws: self.enc.shallow_keywords(call, self.src, hook),
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
        self.emit_use(name, at);
        if !self.wants_any(&[Kind::Refs, Kind::Derived, Kind::Uses, Kind::Listed]) {
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
        if self.wants(Kind::Listed) {
            self.count_load(id, chain.is_empty() && listed(&up), l, c);
        }
        let attr_upper = chain
            .strip_prefix('.')
            .is_some_and(|rest| rest.starts_with(|ch: char| ch.is_ascii_uppercase()));
        if !(binding.first_party && (binding.class_like || attr_upper)) {
            if !self.wants(Kind::Uses) {
                return;
            }
            // `getattr(alias, NAME)` and `vars(alias)` read an attribute no one can name: `.?`.
            let attrs = if chain.is_empty() && computed_read(&self.path) {
                ".?"
            } else {
                leading_attrs(&chain)
            };
            let key = UseKey {
                scope: at.scope,
                header: at.header,
                root: id.to_string(),
                attrs: attrs.to_string(),
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

    /// One load of an import-bound name, for the `listed` rows: a load that is the name itself and
    /// sits in a literal counts as listed.
    fn count_load(&mut self, name: &str, in_literal: bool, l: i64, c: i64) {
        let key = TextKey {
            scope: -1,
            header: -1,
            text: name.to_string(),
        };
        if in_literal {
            count(&mut self.out.listed, key.clone(), l, c);
        }
        count(&mut self.out.loads, key, l, c);
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

/// The argument a loader takes the module's name from: the first positional one, else `name=`.
fn module_argument(call: &ast::ExprCall) -> Option<&Expr> {
    if let Some(first) = call.arguments.args.first() {
        return Some(first);
    }
    let keyword = call
        .arguments
        .keywords
        .iter()
        .find(|k| k.arg.as_ref().is_some_and(|name| name.as_str() == "name"));
    keyword.map(|k| &k.value)
}
