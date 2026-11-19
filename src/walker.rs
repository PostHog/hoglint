//! One traversal of a parsed module that collects every row list of an L1 record.

use crate::config::{ExtractConfig, Kind};
use crate::defs::{Bound, DefNode, DefTable, Patched, patched_names};
use crate::facts::flags::{BLOCK, CLASS, FUNC, IF, TC_ELSE, TRY, TYPE_ONLY};
use crate::facts::{
    Assign, Branch, Computed, DottedStr, Import, Ref, Site, Tag, TextKey, UseKey, WatchedCall,
    WatchedKey,
};
use crate::loops::{Sequences, literal_elements, rebinds};
use crate::path::{Frame, climb, computed_read, getattr_literal, listed};
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

/// What a value may be, relative to the module-level or class-body name a use starts from, as
/// the use reading follows it up (`Walker::follow`). A value that is an item of the name, or
/// holds nothing of it, ends the reading: it is read.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Carried {
    /// The name's value itself.
    Same,
    /// A new value that may hold it: a container, an iterator over it, a copy of a holder, a
    /// record, an instance of a class whose body binds it.
    Holds,
    /// A value that may be it or may hold it: an element or an item taken out of a holder.
    Either,
}

/// One step of the use reading (`Walker::follow`).
enum Step {
    /// Nothing of the value goes on.
    Read,
    /// The value escapes.
    Escape,
    /// The value goes on as this.
    Next(Carried),
}

/// Where a use takes the value it starts from (`Walker::follow`).
enum Outcome {
    /// Nothing of it goes on.
    Read,
    /// Bound to module-level or class-body names (comma-separated), as itself or as a holder of
    /// it, through record constructors when `records` names any.
    Bound {
        targets: String,
        carried: Carried,
        records: Vec<String>,
    },
    /// It escapes.
    Escape,
}

/// Where the value sits in a call (`Walker::argument`).
#[derive(Clone, Copy)]
enum Position<'a> {
    Arg(usize),
    Keyword(Option<&'a str>),
}

/// The modules whose objects the use reading tracks: they load modules or reach a module's
/// names. One handed to anything but a plain attribute read escapes.
const TRACKED_MODULES: [&str; 4] = ["sys", "importlib", "importlib.util", "builtins"];

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
    /// The names, by scope, a binding the walk has passed makes a holder of another name's
    /// value (`_L = [W]`, `_M = list(_L)`, a record, a class whose body binds it): their uses
    /// start as holders.
    holders: HashMap<(i64, String), Carried>,
    /// The module-level names every binding of which is a plain value: no call in it.
    plain: HashSet<&'a str>,
    /// What the file patches (`patched_names`).
    patched: Patched<'a>,
    /// Whether the file star-imports a first-party module, which may bind any bare name.
    star_first_party: bool,
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
            holders: HashMap::new(),
            plain: HashSet::new(),
            patched: Patched::default(),
            star_first_party: false,
            globals: HashMap::new(),
            config,
            exprs: config.kinds.any(&EXPRESSION_KINDS),
            enc: Encoder::default(),
            path: Vec::new(),
            out,
        }
    }

    pub fn run(mut self, suite: &'a [Stmt]) -> Walk {
        if self.wants_any(&[Kind::Escapes, Kind::Computed]) {
            self.survey(suite);
        }
        let module = At {
            scope: -1,
            header: -1,
            flags: 0,
        };
        self.body(suite, module);
        self.out
    }

    /// What the use reading needs to know of the whole file before it walks: the names module
    /// level and class bodies bind only to plain values, what the file patches, and a
    /// first-party star import.
    fn survey(&mut self, suite: &'a [Stmt]) {
        let mut verdicts: HashMap<&'a str, bool> = HashMap::new();
        self.survey_body(suite, &mut verdicts);
        self.plain = (verdicts.into_iter())
            .filter(|(name, plain)| *plain && !self.bound.contains_key(name))
            .map(|(name, _)| name)
            .collect();
        self.patched = patched_names(suite);
    }

    /// The plain verdict of each name a body binds (`survey`), class bodies in it too.
    fn survey_body(&mut self, suite: &'a [Stmt], verdicts: &mut HashMap<&'a str, bool>) {
        for stmt in suite {
            let (targets, value): (Vec<&'a Expr>, Option<&'a Expr>) = match stmt {
                Stmt::Assign(a) => (a.targets.iter().collect(), Some(&a.value)),
                Stmt::AnnAssign(a) => (vec![&a.target], a.value.as_deref()),
                Stmt::ImportFrom(i) => {
                    let module = i.module.as_ref().map(|m| m.as_str()).unwrap_or("");
                    let star = i.names.iter().any(|alias| alias.name.as_str() == "*");
                    if star && (i.level > 0 || self.config.is_first_party(module)) {
                        self.star_first_party = true;
                    }
                    continue;
                }
                Stmt::ClassDef(class) => {
                    self.survey_body(&class.body, verdicts);
                    continue;
                }
                _ => continue,
            };
            for target in targets {
                if let Expr::Name(name) = target {
                    let plain = value.is_some_and(plain_value);
                    *verdicts.entry(name.id.as_str()).or_insert(true) &= plain;
                }
            }
        }
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
            Stmt::FunctionDef(f) => {
                self.definition(&f.name, at);
                self.visit_def(DefNode::Func(f), at)
            }
            Stmt::ClassDef(c) => {
                self.definition(&c.name, at);
                self.visit_def(DefNode::Class(c), at)
            }
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
                    if let Some(name) = &handler.name {
                        let pos = self.lines.pos(name.range.start());
                        self.emit_rebinding(name.as_str(), pos, inner, false);
                    }
                    self.body(&handler.body, inner);
                }
                self.body(&t.orelse, inner);
                self.body(&t.finalbody, inner);
            }
            Stmt::Match(m) => {
                self.child(Frame::Subject(m), &m.subject, at);
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

    /// A `def` or `class` binds its name, decorated or not: a rebinding when the file also
    /// assigns the name somewhere (`emit_rebinding`).
    fn definition(&mut self, name: &ast::Identifier, at: At) {
        if self.stored.contains(name.as_str()) {
            let pos = self.lines.pos(name.range.start());
            self.emit_rebinding(name.as_str(), pos, at, false);
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
                self.capture(p.rest.as_ref(), at);
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
                self.capture(p.name.as_ref(), at);
                if let Some(sub) = &p.pattern {
                    self.pattern(sub, at);
                }
            }
            Pattern::MatchOr(p) => {
                for sub in &p.patterns {
                    self.pattern(sub, at);
                }
            }
            Pattern::MatchStar(p) => self.capture(p.name.as_ref(), at),
            Pattern::MatchSingleton(_) => {}
        }
    }

    /// A name a capture pattern binds (`case W:`, `*rest`, `**rest`).
    fn capture(&mut self, name: Option<&ast::Identifier>, at: At) {
        if let Some(name) = name {
            let pos = self.lines.pos(name.range.start());
            self.emit_rebinding(name.as_str(), pos, at, false);
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

    /// An `escapes` row of `*` at `pos`: code that reaches every name of the module, so any of
    /// them may be changed or bound again.
    fn emit_everything(&mut self, pos: (i64, i64)) {
        if !self.wants(Kind::Escapes) {
            return;
        }
        let module = At {
            scope: -1,
            header: -1,
            flags: 0,
        };
        self.out.escapes.push(Tag {
            site: module.site(pos),
            name: "*".to_string(),
        });
    }

    /// The `escapes` rows of a name node at module level or in a class body (`emit_rebinding`
    /// for a store or a delete). A load of a name no import binds is followed up through the
    /// expression and the statement around it (`follow`): read, bound to names as an alias or a
    /// holder (`name~TARGETS`, `name^TARGETS`, `name>TARGETS@CALLEES` through record
    /// constructors; a class this file defines so too, as a holder of what its body binds), or
    /// escaped (`name`; `name.*` for a class, or when handed to `setattr` or `delattr`). A use in
    /// an `if TYPE_CHECKING:` branch never runs, so it is none.
    fn emit_use(&mut self, name: &ast::ExprName, at: At) {
        if !self.wants(Kind::Escapes) {
            return;
        }
        let id = name.id.as_str();
        let pos = self.lines.pos(name.range.start());
        if name.ctx != ExprContext::Load {
            let (_, up) = climb(&self.path);
            let steps: Vec<&str> = up.split(' ').collect();
            if steps.contains(&"comp:target") {
                return;
            }
            let assignment = steps.iter().any(|step| {
                matches!(
                    *step,
                    "assign-target" | "annassign-target" | "augassign-target" | "typealias-target"
                )
            });
            self.emit_rebinding(id, pos, at, assignment);
            return;
        }
        if at.flags & TYPE_ONLY != 0 || !self.emits_assign(at.scope) || self.bound.contains_key(id)
        {
            return;
        }
        let (reached, used) = self.class_attribute(id);
        let frames = &self.path[..self.path.len() - used];
        let class = used == 0 && self.class_in(-1, id).is_some();
        let handed = matches!(frames.last(), Some(Frame::Arg(call, 0))
            if matches!(callee_text(call).as_str(), "setattr" | "delattr"));
        let start =
            (self.holders.get(&(at.scope, reached.clone())).copied()).unwrap_or(Carried::Same);
        let outcome = self.follow(frames, start, at, !reached.contains('.'));
        let row = match outcome {
            Outcome::Read => return,
            Outcome::Escape if handed || class => format!("{reached}.*"),
            _ if handed => format!("{reached}.*"),
            Outcome::Escape => reached,
            Outcome::Bound {
                targets,
                carried,
                records,
            } => {
                let held = if records.is_empty() {
                    carried
                } else {
                    Carried::Holds
                };
                if held != Carried::Same {
                    for target in targets.split(',') {
                        self.holders.insert((at.scope, target.to_string()), held);
                    }
                }
                self.class_holds(at.scope);
                match (records.is_empty(), carried) {
                    (false, _) => format!("{reached}>{targets}@{}", records.join(",")),
                    (true, Carried::Holds) => format!("{reached}^{targets}"),
                    (true, Carried::Same | Carried::Either) => format!("{reached}~{targets}"),
                }
            }
        };
        self.emit_escape(name, at, &row);
    }

    /// A class body that binds another name's value makes the class a holder: an instance or the
    /// class handed on reaches it.
    fn class_holds(&mut self, scope: i64) {
        if scope < 0 {
            return;
        }
        let entry = &self.defs.entries[scope as usize];
        if let DefNode::Class(class) = entry.node {
            let holder = (entry.parent, class.name.to_string());
            self.holders.insert(holder, Carried::Holds);
        }
    }

    /// A binding of `id` that writes no `assigns` row, or that binds the module's name from a
    /// class body or a function (`global`): an `=id` escape where the name lives, so the reading
    /// takes the name as bound to something it cannot read. Any form counts (the Language
    /// Reference's list, "Binding of names"): a target of any statement, `except ... as`, a
    /// capture pattern, `class`, `def`, a walrus, `del`. An assignment at module level or in a
    /// class body writes a row, and one in a function with `global` writes a module-level row
    /// (`emit_global`); one in a class body with `global` binds the module's name.
    fn emit_rebinding(&mut self, id: &str, pos: (i64, i64), at: At, assignment: bool) {
        if !self.wants(Kind::Escapes) {
            return;
        }
        let global =
            at.scope != -1 && (self.globals.get(&at.scope)).is_some_and(|names| names.contains(id));
        let row_scope = self.emits_assign(at.scope);
        let site = match (global, row_scope) {
            (true, true) => At { scope: -1, ..at },
            (true, false) if !assignment => At { scope: -1, ..at },
            (false, true) if !assignment => at,
            _ => return,
        };
        self.out.escapes.push(Tag {
            site: site.site(pos),
            name: format!("={id}"),
        });
    }

    /// The name a use of a class this file defines reaches through its attributes
    /// (`Reg.WORKFLOWS`, `Outer.Inner.ATTR`), also read off an instance (`Reg().WORKFLOWS`), and
    /// how many parent frames that takes; the name itself and 0 for anything else.
    fn class_attribute(&self, id: &str) -> (String, usize) {
        let path = &self.path;
        let mut name = id.to_string();
        let mut used = 0;
        let mut class = self.class_in(-1, id);
        while let Some(index) = class {
            let mut at = path.len() - used;
            if at > 0 && matches!(path[at - 1], Frame::CallFunc) {
                at -= 1;
            }
            let Some(attribute) = at.checked_sub(1).and_then(|i| attribute_of(path[i])) else {
                break;
            };
            if at >= 2 && matches!(path[at - 2], Frame::CallFunc) {
                break;
            }
            name = format!("{name}.{attribute}");
            used = path.len() - (at - 1);
            class = self.class_in(index, attribute);
        }
        (name, used)
    }

    /// Follows a value up through the parent frames of a use (innermost last), from what it
    /// starts as (`start`), and says where it goes. Each step the reading understands maps what
    /// the value may be (`Carried`); the first one it does not understand escapes. `bare`: the
    /// use is a module-level or class-body name with no dot, whose changes the walk writes as
    /// rows (`W.append(B)`, `W[k] = v` at module level).
    fn follow(&self, frames: &[Frame<'a>], start: Carried, at: At, bare: bool) -> Outcome {
        let mut carried = start;
        let mut records: Vec<String> = Vec::new();
        let mut first = true;
        let mut i = frames.len();
        while i > 0 {
            i -= 1;
            let outer = i.checked_sub(1).map(|j| frames[j]);
            let attribute = attribute_of(frames[i]);
            let step = match frames[i] {
                _ if attribute.is_some() && !matches!(outer, Some(Frame::CallFunc)) => {
                    // An attribute read hands a bound method or a field on.
                    Step::Escape
                }
                _ if attribute.is_some() => {
                    i -= 1;
                    let before = i.checked_sub(1).map(|j| frames[j]);
                    let statement = first
                        && bare
                        && at.scope == -1
                        && matches!(before, Some(Frame::Terminal("expr")));
                    method(carried, attribute.unwrap_or_default(), statement)
                }
                Frame::Attr(_) => Step::Escape,
                Frame::CallFunc => match carried {
                    Carried::Same => Step::Read,
                    Carried::Holds => Step::Next(Carried::Holds),
                    Carried::Either => Step::Escape,
                },
                Frame::SubValue => element(outer, carried, first && bare && at.scope == -1),
                Frame::Arg(call, index) => {
                    self.argument(call, Position::Arg(index), carried, &mut records)
                }
                Frame::Kw(call, name) => {
                    self.argument(call, Position::Keyword(name), carried, &mut records)
                }
                Frame::Op("bool:", _) => Step::Next(carried),
                Frame::Op(..) => Step::Read,
                Frame::Operand(prefix, op, node) => self.operand(prefix, op, node, carried),
                Frame::Step(text) => step(text, carried),
                Frame::DictVal(_) => Step::Next(Carried::Holds),
                Frame::Walrus(_) => Step::Escape,
                Frame::Subject(statement) => {
                    let cases = statement.cases.iter();
                    if cases
                        .map(|case| &case.pattern)
                        .all(|pattern| self.plain_pattern(pattern))
                    {
                        Step::Read
                    } else {
                        Step::Escape
                    }
                }
                Frame::Terminal(text) => terminal(text, carried),
                Frame::AssignValue(targets) => return bound(targets, carried, records),
                Frame::AnnAssignValue(target) => {
                    return bound(std::slice::from_ref(target), carried, records);
                }
                Frame::AugAssignValue(_, target) => match carried {
                    // `X += W` adds W's items; `X += [W]` makes X hold W.
                    Carried::Same => Step::Read,
                    Carried::Holds | Carried::Either => {
                        return bound(std::slice::from_ref(target), Carried::Holds, records);
                    }
                },
                Frame::Base(_) => Step::Read,
                // `type T = W`, a class keyword, a default and a parameter's annotation hand W on.
                Frame::TypeAliasValue(_)
                | Frame::ClassKw(_)
                | Frame::ParamDefault(_)
                | Frame::ParamAnn(_) => Step::Escape,
            };
            match step {
                Step::Read => return Outcome::Read,
                Step::Escape => return Outcome::Escape,
                Step::Next(next) => carried = next,
            }
            first = false;
        }
        Outcome::Read
    }

    /// The value handed to a call at `position`. `"...".format(W)` formats it. A reader
    /// (`reader`) maps it by position; a record constructor (`record_callee`) holds it, for the
    /// link to confirm the record is a class; any other call escapes.
    fn argument(
        &self,
        call: &ast::ExprCall,
        position: Position<'a>,
        carried: Carried,
        records: &mut Vec<String>,
    ) -> Step {
        if let Expr::Attribute(method) = &*call.func
            && method.attr.as_str() == "format"
            && is_text_literal(&method.value)
        {
            return Step::Read;
        }
        if matches!(position, Position::Keyword(None)) {
            // `f(**W)` hands W's items on; a holder's may be W.
            return match carried {
                Carried::Same => Step::Read,
                Carried::Holds | Carried::Either => Step::Escape,
            };
        }
        let callee = callee_text(call);
        if let Some(full) = self.resolve(&callee)
            && let Some(step) = self.reader(&full, call, position, carried)
        {
            return step;
        }
        if self.record_callee(&callee) {
            records.push(callee);
            return Step::Next(Carried::Holds);
        }
        Step::Escape
    }

    /// What a builtin or standard-library reader gives for the value at `position`, by the
    /// callee's full name (`resolve`); None for any other callee.
    /// - a copy (`list`, `tuple`, `set`, `frozenset`, `sorted`, `copy.copy`): of the value its
    ///   items (read), of a holder a holder;
    /// - a count, a test, formatting or serialising (`len`, `bool`, `any`, `all`, `repr`,
    ///   `str`, `id`, `hash`, `print`, `json.dumps`, `pprint`, `collections.Counter`,
    ///   `dict.fromkeys`, `copy.deepcopy`): read; `isinstance` and `issubclass` only with plain
    ///   classes (`plain_operand`);
    /// - an iterator (`iter`, `reversed`, `enumerate`, `zip`, `map`, `filter`, `itertools.*`): a
    ///   holder;
    /// - `min`, `max`, `sum`, `next` of the value alone: an item, of a holder possibly the value;
    ///   with a second positional argument, a `default=` or a start, the result may be any
    ///   argument, so it carries what the argument carries.
    fn reader(
        &self,
        full: &str,
        call: &ast::ExprCall,
        position: Position<'a>,
        carried: Carried,
    ) -> Option<Step> {
        let positional = call.arguments.args.len();
        let first = matches!(position, Position::Arg(0));
        let item = match carried {
            Carried::Same => Step::Read,
            Carried::Holds | Carried::Either => Step::Next(Carried::Either),
        };
        let copy = match carried {
            Carried::Same => Step::Read,
            Carried::Holds | Carried::Either => Step::Next(Carried::Holds),
        };
        let start = (call.arguments.keywords.iter()).any(|keyword| {
            keyword
                .arg
                .as_ref()
                .is_some_and(|arg| arg.as_str() == "start")
        });
        Some(match full {
            "builtins.list" | "builtins.tuple" | "builtins.set" | "builtins.frozenset"
            | "builtins.sorted" | "builtins.dict" | "copy.copy"
                if first =>
            {
                copy
            }
            "copy.deepcopy"
            | "builtins.len"
            | "builtins.bool"
            | "builtins.any"
            | "builtins.all"
            | "builtins.repr"
            | "builtins.str"
            | "builtins.id"
            | "builtins.hash"
            | "json.dumps"
            | "pprint.pformat"
            | "collections.Counter"
            | "builtins.dict.fromkeys"
                if first =>
            {
                Step::Read
            }
            "builtins.print" | "pprint.pprint" if matches!(position, Position::Arg(_)) => {
                Step::Read
            }
            "builtins.isinstance" | "builtins.issubclass" if first => {
                let classes = call.arguments.args.iter().skip(1);
                let keywords = call.arguments.keywords.iter().map(|k| &k.value);
                if classes
                    .chain(keywords)
                    .all(|c| self.plain_operand(c, false))
                {
                    Step::Read
                } else {
                    Step::Escape
                }
            }
            "builtins.iter" | "builtins.reversed" | "builtins.enumerate" | "builtins.zip"
            | "builtins.map" | "builtins.filter" => Step::Next(Carried::Holds),
            full if full.starts_with("itertools.") => Step::Next(Carried::Holds),
            "builtins.min" | "builtins.max" => match position {
                Position::Keyword(Some("default")) => Step::Next(carried),
                Position::Keyword(_) => Step::Escape,
                Position::Arg(0) if positional == 1 => item,
                Position::Arg(_) => Step::Next(carried),
            },
            "builtins.sum" => match position {
                Position::Arg(0) if positional == 1 && !start => item,
                _ => Step::Next(carried),
            },
            "builtins.next" => match position {
                Position::Arg(0) => item,
                _ => Step::Next(carried),
            },
            _ => return None,
        })
    }

    /// The value as an operand of a binary operation, a comparison or a subscript's index. Any
    /// operand that is not plain (`plain_operand`) may run first-party code with it (a dunder),
    /// so it escapes. `"..." % W` formats; an operation on the value makes a new one, and on a
    /// holder (`[W] + X`) a holder.
    fn operand(&self, prefix: &str, op: &str, node: &Expr, carried: Carried) -> Step {
        if let Expr::BinOp(binary) = node
            && op == "%"
            && is_text_literal(&binary.left)
        {
            return Step::Read;
        }
        let operands: Vec<&Expr> = match node {
            Expr::BinOp(binary) => vec![&binary.left, &binary.right],
            Expr::Compare(compare) => compare.operands.iter().collect(),
            Expr::Subscript(subscript) => vec![&subscript.value],
            _ => Vec::new(),
        };
        // `in` compares each element of a container with the value itself.
        let membership = matches!(node, Expr::Compare(compare)
            if compare.ops.iter().any(|op| matches!(op, ast::CmpOp::In | ast::CmpOp::NotIn)));
        if !operands
            .into_iter()
            .all(|operand| self.plain_operand(operand, membership))
        {
            return Step::Escape;
        }
        match (prefix, carried) {
            ("bin:", Carried::Holds | Carried::Either) => Step::Next(Carried::Holds),
            _ => Step::Read,
        }
    }

    /// Whether an operand runs no first-party code when a value meets it: a literal, a builtin or
    /// a name an import from outside the first party binds (and attributes of it), a name the
    /// module or a class body binds only to plain values (`plain`) and such a class attribute, a
    /// loop's variable over a module-level name (an item), a subscript of such, a call of a
    /// builtin on such, and a display. A display's elements meet only the value's items, so any
    /// name may be one; under `in` (`membership`) they meet the value itself and must be plain.
    fn plain_operand(&self, expr: &Expr, membership: bool) -> bool {
        let element = |e: &Expr| {
            self.plain_operand(e, membership)
                || (!membership && matches!(e, Expr::Name(_) | Expr::Attribute(_)))
        };
        match expr {
            Expr::StringLiteral(_)
            | Expr::BytesLiteral(_)
            | Expr::NumberLiteral(_)
            | Expr::BooleanLiteral(_)
            | Expr::NoneLiteral(_)
            | Expr::EllipsisLiteral(_)
            | Expr::FString(_) => true,
            Expr::List(l) => l.elts.iter().all(element),
            Expr::Tuple(t) => t.elts.iter().all(element),
            Expr::Set(s) => s.elts.iter().all(element),
            Expr::Dict(d) => (d.items.iter())
                .all(|item| item.key.as_ref().is_none_or(element) && element(&item.value)),
            Expr::Starred(s) => self.plain_operand(&s.value, membership),
            Expr::Subscript(s) => self.plain_operand(&s.value, membership),
            Expr::UnaryOp(u) => self.plain_operand(&u.operand, membership),
            Expr::Name(name) => {
                let id = name.id.as_str();
                self.plain.contains(id)
                    || self.outside(id)
                    || (self.elements.iter())
                        .any(|e| e.variable == id && matches!(e.from, Element::Of(_)))
            }
            Expr::Attribute(attribute) => match &*attribute.value {
                Expr::Name(head) if self.outside(head.id.as_str()) => true,
                Expr::Name(head) => {
                    self.class_in(-1, head.id.as_str()).is_some()
                        && self.plain.contains(attribute.attr.as_str())
                }
                _ => false,
            },
            Expr::Call(call) => {
                let builtin = self.resolve(&callee_text(call));
                builtin.is_some_and(|full| full.starts_with("builtins."))
                    && call
                        .arguments
                        .args
                        .iter()
                        .all(|a| self.plain_operand(a, membership))
            }
            _ => false,
        }
    }

    /// Whether a match pattern compares the subject with plain values only (`plain_operand`): a
    /// class pattern's class and a value pattern's value may run first-party code with it.
    fn plain_pattern(&self, pattern: &Pattern) -> bool {
        match pattern {
            Pattern::MatchValue(p) => self.plain_operand(&p.value, false),
            Pattern::MatchSingleton(_) | Pattern::MatchStar(_) => true,
            Pattern::MatchSequence(p) => p.patterns.iter().all(|p| self.plain_pattern(p)),
            Pattern::MatchMapping(p) => {
                p.keys.iter().all(|key| self.plain_operand(key, false))
                    && p.patterns.iter().all(|p| self.plain_pattern(p))
            }
            Pattern::MatchClass(p) => {
                self.plain_operand(&p.cls, false)
                    && p.arguments.patterns.iter().all(|p| self.plain_pattern(p))
                    && (p.arguments.keywords.iter()).all(|k| self.plain_pattern(&k.pattern))
            }
            Pattern::MatchAs(p) => p.pattern.as_deref().is_none_or(|p| self.plain_pattern(p)),
            Pattern::MatchOr(p) => p.patterns.iter().all(|p| self.plain_pattern(p)),
        }
    }

    /// Whether a name resolves outside the first party: a builtin, or what an import from a
    /// module that is not first-party binds.
    fn outside(&self, name: &str) -> bool {
        match self.bound.get(name) {
            Some(binding) => !binding.first_party && self.resolve(name).is_some(),
            None => self
                .resolve(name)
                .is_some_and(|full| full.starts_with("builtins.")),
        }
    }

    /// The full name a dotted name of the code stands for: through the import that binds its
    /// head (`import sys as s`: `s.modules` is `sys.modules`), or a builtin's (`builtins.len`)
    /// when the file binds the head nowhere. None when the binding is not unique or not still
    /// valid: the file binds the head itself too (`class copy`, `def copy`), patches an attribute
    /// of it (`copy.copy = f`), patches builtins (`builtins.sorted = f`, `__builtins__`), or
    /// star-imports a first-party module that may bind a bare name; or for a relative import.
    fn resolve(&self, dotted: &str) -> Option<String> {
        let (head, rest) = match dotted.split_once('.') {
            Some((head, rest)) => (head, Some(rest)),
            None => (dotted, None),
        };
        if self.binds_name(head) || self.patched.heads.contains(head) {
            return None;
        }
        let base = match self.bound.get(head) {
            Some(binding) => binding.origin()?,
            None if self.star_first_party || self.builtins_patched() => return None,
            None => format!("builtins.{head}"),
        };
        Some(match rest {
            Some(rest) => format!("{base}.{rest}"),
            None => base,
        })
    }

    /// Whether the file patches builtins: it uses `__builtins__`, or stores an attribute of a
    /// name an import binds to `builtins`.
    fn builtins_patched(&self) -> bool {
        self.patched.builtins
            || (self.patched.heads.iter()).any(|head| {
                self.bound.get(head).and_then(|b| b.origin()).as_deref() == Some("builtins")
            })
    }

    /// Whether the file binds a name itself anywhere: a store, a def or a class.
    fn binds_name(&self, name: &str) -> bool {
        self.stored.contains(name)
            || self.defs.entries.iter().any(|entry| match entry.node {
                DefNode::Func(f) => f.name.as_str() == name,
                DefNode::Class(c) => c.name.as_str() == name,
            })
    }

    /// Whether a callee may build a record: a capitalised first-party name an import binds
    /// (`QueueBinding(...)`, `registration.QueueBinding(...)`), or `dataclasses.replace`. The link
    /// confirms the name is a class (`escapes` rows name the callees).
    fn record_callee(&self, callee: &str) -> bool {
        if self.resolve(callee).as_deref() == Some("dataclasses.replace") {
            return true;
        }
        let head = callee.split('.').next().unwrap_or_default();
        let last = callee.rsplit('.').next().unwrap_or_default();
        // A unique, still-valid binding: no other binding or patch of the head in the file. A
        // relative import is first-party too, though `resolve` gives it no full name.
        let first_party = self
            .bound
            .get(head)
            .is_some_and(|binding| binding.first_party);
        let valid = !self.binds_name(head) && !self.patched.heads.contains(head);
        first_party && valid && last.starts_with(|c: char| c.is_ascii_uppercase())
    }

    /// The def index of the class named `name` directly in the scope `parent` (-1: the module).
    fn class_in(&self, parent: i64, name: &str) -> Option<i64> {
        (self.defs.entries.iter().enumerate()).find_map(|(index, entry)| match entry.node {
            DefNode::Class(c) if entry.parent == parent && c.name.as_str() == name => {
                Some(index as i64)
            }
            _ => None,
        })
    }

    fn expr(&mut self, expr: &'a Expr, at: At) {
        self.emit_module_reads(expr, at);
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
                let step = Frame::Operand("bin:", binop(b.op), expr);
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
                self.child(Frame::Operand("cmp:", "left", expr), &c.operands[0], at);
                for (op, operand) in c.ops.iter().zip(c.operands[1..].iter()) {
                    self.child(Frame::Operand("cmp:", cmpop(*op), expr), operand, at);
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
                self.child(Frame::Operand("slice", "", expr), &s.slice, at);
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

    fn emit_call(&mut self, call: &'a ast::ExprCall, at: At) {
        let (want_callees, want_calls) = (self.wants(Kind::Callees), self.wants(Kind::Calls));
        let want_computed = self.wants(Kind::Computed);
        let want_escapes = self.wants(Kind::Escapes);
        if !want_callees && !want_calls && !want_computed && !want_escapes {
            return;
        }
        let callee = callee_text(call);
        let (l, c) = self.lines.pos(call.range().start());
        if want_computed || want_escapes {
            self.emit_namespace_call(call, &callee, at, (l, c));
        }
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
        // A loader reached through an import alias (`from importlib import import_module as
        // load`) is the loader.
        let aliased = !self.config.watch_aliased.is_empty()
            && (self.resolve(&callee))
                .is_some_and(|full| self.config.watch_aliased.contains(&full));
        let watched = self.config.is_watched_callee(last) || aliased;
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

    /// A call that reaches the module's names or loads code the link cannot read:
    /// - `globals()`, and `locals()` or `vars()` with no argument, at module level or in a class
    ///   body: the `*` escape;
    /// - `exec` and `eval`, anywhere: the `*` escape and a `computed` row (`use`);
    /// - `importlib.reload(alias)` of a module an import binds, as a statement: a `computed` row
    ///   (`reload`) that names the alias, so the link reads it as a read of that module.
    fn emit_namespace_call(
        &mut self,
        call: &'a ast::ExprCall,
        callee: &str,
        at: At,
        pos: (i64, i64),
    ) {
        let Some(full) = self.resolve(callee) else {
            return;
        };
        let bare = call.arguments.args.is_empty() && call.arguments.keywords.is_empty();
        let namespace = self.emits_assign(at.scope);
        match full.as_str() {
            "builtins.globals" if namespace => self.emit_everything(pos),
            "builtins.locals" | "builtins.vars" if namespace && bare => self.emit_everything(pos),
            "builtins.exec" | "builtins.eval" => {
                self.emit_everything(pos);
                let name = full.trim_start_matches("builtins.").to_string();
                self.push_computed(at, pos, "use", name);
            }
            "importlib.reload" => {
                // The reloaded module is the alias's own; a result read further (`.WORKFLOWS`)
                // names nothing the link reads.
                let statement = matches!(self.path.last(), Some(Frame::Terminal("expr")));
                if let Some(Expr::Name(alias)) = call.arguments.args.first()
                    && self.bound.contains_key(alias.id.as_str())
                    && statement
                {
                    self.push_computed(at, pos, "reload", alias.id.to_string());
                }
            }
            _ => {}
        }
    }

    /// A `computed` row at `pos`, when the configuration asks for them.
    fn push_computed(&mut self, at: At, pos: (i64, i64), form: &'static str, sequence: String) {
        if !self.wants(Kind::Computed) {
            return;
        }
        self.out.computed.push(Computed {
            site: at.site(pos),
            form,
            sequence,
        });
    }

    /// Reads of a module's machinery:
    /// - `sys.modules` (resolved through the imports and the paths to `sys`: `import sys as s`,
    ///   `from sys import modules`, `importlib.sys`, `os.sys`, `__import__("sys")`) read in a way
    ///   that may give a module: a `computed` row, as no edge names a key of it, and the `*`
    ///   escape unless it is a subscript by a string literal (it may be this module). A read
    ///   that gives only names or a truth value loads nothing (`modules_names_only`);
    /// - a tracked module (`TRACKED_MODULES`) handed to anything but a plain attribute read
    ///   (`vars(importlib)`, `importlib.__dict__`, a call): a `computed` row (`use`) and `*`;
    /// - a builtin loader or `exec`/`eval` read as a value (`load = __import__`): a `computed`
    ///   row (`use`); `globals`, `locals` or `vars` read as a value, and any use of
    ///   `__builtins__`: `*` (and a `use` row for `__builtins__`).
    fn emit_module_reads(&mut self, expr: &'a Expr, at: At) {
        if !self.wants_any(&[Kind::Computed, Kind::Escapes]) {
            return;
        }
        let pos = self.lines.pos(expr.range().start());
        if let Expr::Subscript(s) = expr
            && self.names_modules(&s.value)
        {
            let sequence = self.sequence_of(&s.slice, at.scope).unwrap_or_default();
            self.push_computed(at, pos, "sys.modules", sequence);
            if !s.slice.is_string_literal_expr() {
                self.emit_everything(pos);
            }
            return;
        }
        let in_subscript = matches!(self.path.last(), Some(Frame::SubValue));
        let module = self.module_path(expr);
        if module.as_deref() == Some("sys.modules") {
            if !in_subscript && !self.modules_names_only() {
                self.push_computed(at, pos, "sys.modules", String::new());
                self.emit_everything(pos);
            }
            return;
        }
        if let Some(module) = module.filter(|module| TRACKED_MODULES.contains(&module.as_str())) {
            let read = self.path.last().copied().and_then(attribute_of);
            if read.is_none_or(|attribute| attribute.starts_with("__")) {
                self.push_computed(at, pos, "use", module);
                self.emit_everything(pos);
            }
            return;
        }
        let Expr::Name(name) = expr else {
            return;
        };
        if name.ctx != ExprContext::Load {
            return;
        }
        let id = name.id.as_str();
        if id == "__builtins__" {
            self.push_computed(at, pos, "use", id.to_string());
            self.emit_everything(pos);
            return;
        }
        if matches!(self.path.last(), Some(Frame::CallFunc)) {
            return;
        }
        match self.resolve(id).as_deref() {
            Some("builtins.__import__" | "builtins.exec" | "builtins.eval") => {
                self.push_computed(at, pos, "use", id.to_string());
            }
            Some("builtins.globals" | "builtins.locals" | "builtins.vars") => {
                self.emit_everything(pos);
            }
            _ => {}
        }
    }

    /// Whether a read of `sys.modules` (the node being walked) gives only names or a truth value:
    /// `in` or `not in` it, `len` or `bool` of it, a test, iteration over its keys, `.keys()`, and
    /// `.get("literal")` compared with `None`.
    fn modules_names_only(&self) -> bool {
        let path = &self.path;
        let n = path.len();
        let frame = |back: usize| n.checked_sub(back).map(|i| path[i]);
        match frame(1) {
            Some(Frame::Operand("cmp:", "in" | "notin", _)) => true,
            Some(Frame::Terminal("if-test" | "while-test" | "assert" | "for-iter")) => true,
            Some(Frame::Step("comp:iter" | "comp:if" | "ifexp:test")) => true,
            Some(Frame::Op("unary:", "not")) => true,
            Some(Frame::Arg(call, 0)) => matches!(
                self.resolve(&callee_text(call)).as_deref(),
                Some("builtins.len" | "builtins.bool")
            ),
            Some(Frame::Attr("keys")) => matches!(frame(2), Some(Frame::CallFunc)),
            Some(Frame::Attr("get")) => match (frame(2), frame(3)) {
                (Some(Frame::CallFunc), Some(Frame::Operand("cmp:", _, Expr::Compare(c)))) => {
                    let presence =
                        (c.ops.iter()).all(|op| matches!(op, ast::CmpOp::Is | ast::CmpOp::IsNot));
                    let none = c.operands.iter().any(Expr::is_none_literal_expr);
                    let literal = c.operands.iter().any(|operand| {
                        matches!(operand, Expr::Call(call)
                            if call.arguments.keywords.is_empty()
                                && matches!(call.arguments.args.as_ref(), [Expr::StringLiteral(_)]))
                    });
                    presence && none && literal
                }
                _ => false,
            },
            _ => false,
        }
    }

    /// The module or attribute path an expression names: resolved through the imports
    /// (`resolve`), through `getattr(x, "name")`, and through `__import__("m")` or
    /// `importlib.import_module("m")` of a literal; the standard library's attribute paths to
    /// `sys` (`importlib.sys`, `os.sys`) read as `sys`.
    fn module_path(&self, expr: &Expr) -> Option<String> {
        let path = match expr {
            Expr::Name(name) => self.resolve(name.id.as_str())?,
            Expr::Attribute(attribute) => {
                let value = self.module_path(&attribute.value)?;
                format!("{value}.{}", attribute.attr.as_str())
            }
            Expr::Call(call) => match getattr_literal(call) {
                Some(attribute) => {
                    format!("{}.{attribute}", self.module_path(&call.arguments.args[0])?)
                }
                None => {
                    let callee = self.resolve(&callee_text(call))?;
                    let loader = matches!(
                        callee.as_str(),
                        "builtins.__import__" | "importlib.import_module"
                    );
                    match call.arguments.args.first() {
                        Some(Expr::StringLiteral(literal)) if loader => literal.value.to_string(),
                        _ => return None,
                    }
                }
            },
            _ => return None,
        };
        for alias in ["importlib.sys", "os.sys"] {
            if let Some(rest) = path.strip_prefix(alias)
                && (rest.is_empty() || rest.starts_with('.'))
            {
                return Some(format!("sys{rest}"));
            }
        }
        Some(path)
    }

    /// Whether an expression is `sys.modules` (`module_path`).
    fn names_modules(&self, expr: &Expr) -> bool {
        self.module_path(expr).as_deref() == Some("sys.modules")
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

/// The attribute a parent frame reads of the value: `.name`, or `getattr(value, "name")`.
fn attribute_of<'f>(frame: Frame<'f>) -> Option<&'f str> {
    match frame {
        Frame::Attr(attribute) => Some(attribute),
        Frame::Arg(call, 0) => getattr_literal(call),
        _ => None,
    }
}

/// A string literal or an f-string.
fn is_text_literal(expr: &Expr) -> bool {
    matches!(expr, Expr::StringLiteral(_) | Expr::FString(_))
}

/// Whether a module-level value holds no call, so binding it runs no first-party code: literals,
/// names, attributes, displays and operations of such.
fn plain_value(expr: &Expr) -> bool {
    match expr {
        Expr::StringLiteral(_)
        | Expr::BytesLiteral(_)
        | Expr::NumberLiteral(_)
        | Expr::BooleanLiteral(_)
        | Expr::NoneLiteral(_)
        | Expr::EllipsisLiteral(_)
        | Expr::Name(_)
        | Expr::Attribute(_) => true,
        Expr::List(l) => l.elts.iter().all(plain_value),
        Expr::Tuple(t) => t.elts.iter().all(plain_value),
        Expr::Set(s) => s.elts.iter().all(plain_value),
        Expr::Dict(d) => (d.items.iter())
            .all(|item| item.key.as_ref().is_none_or(plain_value) && plain_value(&item.value)),
        Expr::Starred(s) => plain_value(&s.value),
        Expr::BinOp(b) => plain_value(&b.left) && plain_value(&b.right),
        Expr::BoolOp(b) => b.values.iter().all(plain_value),
        Expr::UnaryOp(u) => plain_value(&u.operand),
        _ => false,
    }
}

/// A method called on the value. A changing method (`append`) is read as a module-level
/// statement on a bare name, which the walk writes as a row. Of the value itself, a reading
/// method reads (`copy`, `count`, `get`, `pop`, `values`, ...; `remove`, `sort` and the like
/// only take items out or reorder them). Of a holder, a copy or a view still holds, an item taken
/// out (`get`, `pop`) may be the value or hold it, and a count reads. Any other method escapes.
fn method(carried: Carried, attribute: &str, statement: bool) -> Step {
    if statement && MUTATORS.contains(&attribute) {
        return Step::Read;
    }
    let holder = matches!(carried, Carried::Holds | Carried::Either);
    match (holder, attribute) {
        (_, "count" | "index") => Step::Read,
        (
            false,
            "copy" | "values" | "keys" | "items" | "get" | "pop" | "remove" | "clear" | "discard"
            | "sort" | "reverse",
        ) => Step::Read,
        (true, "copy" | "values" | "items") => Step::Next(Carried::Holds),
        (true, "get" | "pop") => Step::Next(Carried::Either),
        (true, "keys" | "remove" | "clear" | "discard" | "sort" | "reverse") => Step::Read,
        _ => Step::Escape,
    }
}

/// An element or a slice of the value (`W[0]`, `W[1:]`): of the value itself an item, read; of
/// a holder, possibly the value or another holder of it. A store into it (`W[k] = v`) is a change the reading reads as a
/// row on a bare name at module level (`statement`); one into a holder's slot changes the holder
/// only; any other store (`+=`, a `for` target) escapes.
fn element(outer: Option<Frame>, carried: Carried, statement: bool) -> Step {
    match outer {
        Some(Frame::Terminal("assign-target" | "annassign-target")) => {
            if carried == Carried::Holds || (carried == Carried::Same && statement) {
                Step::Read
            } else {
                Step::Escape
            }
        }
        Some(Frame::Terminal("del")) => Step::Read,
        Some(Frame::Terminal(text)) if text.ends_with("-target") => Step::Escape,
        _ => match carried {
            Carried::Same => Step::Read,
            Carried::Holds | Carried::Either => Step::Next(Carried::Either),
        },
    }
}

/// A fixed step: unpacking (`[*W]`, `{**W}`) copies the value's items and a holder's; a display
/// or a comprehension's element holds it; a conditional's branch may be it; a test, an index's
/// bound and formatting read; iterating a holder may give the value; anything else (a lambda,
/// an await, a yield, a walrus) escapes.
fn step(text: &str, carried: Carried) -> Step {
    match text {
        "star" | "dict:**" => match carried {
            Carried::Same => Step::Read,
            Carried::Holds | Carried::Either => Step::Next(Carried::Holds),
        },
        text if text.starts_with("elt:") || text.starts_with("dict:") => Step::Next(Carried::Holds),
        "comp:elt" | "comp:key" | "comp:val" => Step::Next(Carried::Holds),
        "ifexp:body" | "ifexp:else" => Step::Next(carried),
        "ifexp:test" | "comp:if" | "fstr" | "tstr" | "slice-part" => Step::Read,
        "comp:iter" => match carried {
            Carried::Same => Step::Read,
            Carried::Holds | Carried::Either => Step::Escape,
        },
        _ => Step::Escape,
    }
}

/// A statement or header slot: a bare statement, a test, an `except` class, a decorator, a
/// pattern and `del` read; iterating the value gives its items, a holder's may be it; anything
/// else escapes, annotations and an assert message too (they keep the object).
fn terminal(text: &str, carried: Carried) -> Step {
    match text {
        "expr" | "if-test" | "while-test" | "assert" | "case-guard" | "except" | "decorator"
        | "case" | "del" => Step::Read,
        "for-iter" => match carried {
            Carried::Same => Step::Read,
            Carried::Holds | Carried::Either => Step::Escape,
        },
        _ => Step::Escape,
    }
}

/// The binding an assignment makes of the value: names it binds take it as an alias or a holder
/// (the link reads the row), unpacking the value itself gives items (read), and anything else
/// escapes (an attribute or a subscript store, a holder unpacked: the link cannot pair it).
fn bound(targets: &[Expr], carried: Carried, records: Vec<String>) -> Outcome {
    let names: Option<Vec<&str>> = (targets.iter())
        .map(|target| match target {
            Expr::Name(name) => Some(name.id.as_str()),
            _ => None,
        })
        .collect();
    if let Some(names) = names {
        return Outcome::Bound {
            targets: names.join(","),
            carried,
            records,
        };
    }
    let unpacked = (targets.iter()).all(|t| matches!(t, Expr::Tuple(_) | Expr::List(_)));
    if unpacked && carried == Carried::Same && records.is_empty() {
        return Outcome::Read;
    }
    Outcome::Escape
}
