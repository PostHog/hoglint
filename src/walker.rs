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
use ruff_python_ast::visitor::{self, Visitor};
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

/// What a module-level or class-body binding identity is, joined over every binding of it
/// (`Walker::survey`): the use reading starts from it.
#[derive(Clone, Debug, Default)]
struct Shape {
    /// Every binding is a display, a comprehension, a literal, a builtin copy, a concatenation
    /// of such, or an alias of a name that is: its dunders are builtin.
    builtin: bool,
    /// Some binding builds a record (a call of a record constructor, `dataclasses.replace`).
    record: bool,
    /// Some binding is a `for` target over a name (of a scope): the identity may be an item
    /// of it.
    item_of: Option<(String, i64)>,
}

/// One binding of an identity, as the inventory reads it (`Walker::survey`).
enum Bind<'a> {
    /// A value whose dunders are builtin: a display, a comprehension, a literal, a builtin copy.
    Same,
    /// A value that is another identity's (an alias, a concatenation): each group lists the
    /// identities one name may be.
    Alias(Vec<Vec<(i64, &'a str)>>),
    /// A record.
    Record,
    /// An item of a name of a scope.
    Item(String, i64),
    /// Anything else.
    Opaque,
}

impl<'a> Bind<'a> {
    fn groups(self) -> Vec<Vec<(i64, &'a str)>> {
        match self {
            Bind::Alias(groups) => groups,
            _ => Vec::new(),
        }
    }
}

/// A module-level or class-body binding identity: (scope, name).
type Identity<'a> = (i64, &'a str);

/// A binding the inventory found: the identity and how it binds.
type Found<'a> = (Identity<'a>, Bind<'a>);

/// What a use starts as (`Walker::start`).
#[derive(Clone, Debug)]
enum Start {
    /// The name's own value; `builtin` as `Shape::builtin`.
    Value { builtin: bool },
    /// A class this file defines: it holds what its body binds.
    Class,
    /// A record: it holds its arguments.
    Record,
    /// An item of `source` (a name of `scope`): it keeps the provenance, so handed on it
    /// escapes for the source.
    Item { source: String, scope: i64 },
    /// An attribute of a class this file defines (a class index) that its body does not bind:
    /// it may be a base's, so every use escapes for the class and its bases of this file.
    Inherited(i64),
}

/// What a use of a name reaches through the attributes of a class this file defines
/// (`Walker::class_attribute`).
enum Chain<'a> {
    /// No class: the name itself.
    Name,
    /// A class.
    Class,
    /// An attribute of a class (its index).
    Attribute(i64, &'a str),
}

/// The iterator builtins T8 reads.
const ITERATORS: [&str; 7] = [
    "builtins.iter",
    "builtins.reversed",
    "builtins.enumerate",
    "builtins.zip",
    "builtins.map",
    "builtins.filter",
    "itertools.chain",
];

/// Where a use takes the value (`Walker::read_use`).
enum Outcome<'a> {
    /// Nothing of it goes on.
    Read,
    /// It escapes.
    Escape,
    /// Bound to module-level or class-body names (comma-separated) as an alias (T2).
    Alias(String),
    /// A record bound to a module-level name holds it, through these constructors (T3, T4).
    Record(String, Vec<String>),
    /// A module-level statement changes it in place through this call (T11): a mutation row.
    Changed(&'a ast::ExprCall),
}

/// Where a value sits in a call.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Position<'a> {
    Arg(usize),
    Keyword(Option<&'a str>),
}

/// The functions of `importlib`, `importlib.util`, `importlib.machinery`, `pkgutil`, `runpy`,
/// `pydoc` and `zipimport` whose documented job is to import or execute a module by name or
/// path, by last name: in a registration point a call of one, on any receiver and through an
/// import alias, or one read as a value, is a module access the reading cannot read (the item
/// reading's rule 2). The configured loaders (`import_module`, `__import__`) keep their literal
/// forms; `reload` of an alias as a statement too.
const ENTRY_POINTS: [&str; 26] = [
    "__import__",
    "import_module",
    "reload",
    "find_spec",
    "module_from_spec",
    "spec_from_file_location",
    "spec_from_loader",
    "exec_module",
    "create_module",
    "load_module",
    "_gcd_import",
    "_load",
    "_find_and_load",
    "_find_and_load_unlocked",
    "_load_unlocked",
    "_exec",
    "resolve_name",
    "walk_packages",
    "get_loader",
    "find_loader",
    "get_importer",
    "run_module",
    "run_path",
    "locate",
    "importfile",
    "safeimport",
];

/// The builtins a callable slot may name (`Walker::callable_slot`): they read their argument.
const READING_BUILTINS: [&str; 12] = [
    "builtins.str",
    "builtins.repr",
    "builtins.len",
    "builtins.bool",
    "builtins.id",
    "builtins.hash",
    "builtins.int",
    "builtins.float",
    "builtins.list",
    "builtins.tuple",
    "builtins.sorted",
    "builtins.type",
];

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
    /// The inventory: what each module-level and class-body binding identity is, by (scope,
    /// name), joined over every binding of it (`survey`).
    identities: HashMap<(i64, &'a str), Shape>,
    /// The variables of the comprehensions around the code being walked that take items of a
    /// module-level or class-body name: (variable, source name, source scope).
    items: Vec<(&'a str, String, i64)>,
    /// The module-level names the inventory resolves to `sys.modules` (`from sys import modules
    /// as m`, `m2 = m`, `M = sys.modules`).
    modules_aliases: HashSet<&'a str>,
    /// The value of the module-level expression statement being walked, for T11's mutation row.
    statement: Option<&'a Expr>,
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
            identities: HashMap::new(),
            items: Vec::new(),
            modules_aliases: HashSet::new(),
            statement: None,
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

    /// The inventory, the use reading's first responsibility: every binding of every name at
    /// module level, in class bodies, and of a module-level name from a class body or a function
    /// that declares it `global`, by every form of the Language Reference's list ("Binding of
    /// names"); each binding identity's shape (`Shape`), joined over its bindings; the names that
    /// stand for `sys.modules`; what the file patches; a first-party star import.
    fn survey(&mut self, suite: &'a [Stmt]) {
        self.patched = patched_names(suite);
        let mut found: Vec<Found<'a>> = Vec::new();
        self.survey_body(suite, -1, &HashSet::new(), &mut found);
        self.settle(found);
    }

    /// The bindings of one module-level or class body (`scope`) and the blocks in it; `global`
    /// holds the names a class body declares global, which it binds at module level.
    fn survey_body(
        &mut self,
        suite: &'a [Stmt],
        scope: i64,
        global: &HashSet<&'a str>,
        found: &mut Vec<Found<'a>>,
    ) {
        for stmt in suite {
            let mut positive: Vec<(&'a str, Bind<'a>)> = Vec::new();
            match stmt {
                Stmt::Assign(a) => {
                    for target in &a.targets {
                        if let Expr::Name(name) = target {
                            positive.push((name.id.as_str(), self.value_bind(&a.value, scope)));
                        }
                    }
                    if let [Expr::Name(name)] = a.targets.as_slice()
                        && scope == -1
                        && self.is_modules(&a.value)
                    {
                        self.modules_aliases.insert(name.id.as_str());
                    }
                }
                Stmt::AnnAssign(a) => {
                    if let (Expr::Name(name), Some(value)) = (&*a.target, a.value.as_deref()) {
                        positive.push((name.id.as_str(), self.value_bind(value, scope)));
                    }
                }
                Stmt::AugAssign(a) => {
                    if let Expr::Name(name) = &*a.target {
                        let keeps = matches!(a.op, ast::Operator::Add | ast::Operator::BitOr)
                            && builtin_literal(&a.value);
                        let bind = if keeps { Bind::Same } else { Bind::Opaque };
                        positive.push((name.id.as_str(), bind));
                    }
                }
                Stmt::For(f) => {
                    let source = self.item_source(&f.iter, scope);
                    let mut names = Vec::new();
                    target_names(&f.target, &mut names);
                    for name in names {
                        let bind = match &source {
                            Some((source, at)) => Bind::Item(source.clone(), *at),
                            None => Bind::Opaque,
                        };
                        positive.push((name, bind));
                    }
                }
                Stmt::ImportFrom(i) => {
                    let module = i.module.as_ref().map(|m| m.as_str()).unwrap_or("");
                    let star = i.names.iter().any(|alias| alias.name.as_str() == "*");
                    if star && (i.level > 0 || self.config.is_first_party(module)) {
                        self.star_first_party = true;
                    }
                }
                Stmt::FunctionDef(f) => self.survey_function(&f.body, &mut Vec::new(), found),
                _ => {}
            }
            let mut bound = Vec::new();
            statement_bindings(stmt, &mut bound);
            for name in bound {
                let bind = match positive.iter().position(|(n, _)| *n == name) {
                    Some(at) => positive.swap_remove(at).1,
                    None => Bind::Opaque,
                };
                let at = if global.contains(name) { -1 } else { scope };
                found.push(((at, name), bind));
            }
            for body in nested_bodies(stmt) {
                self.survey_body(body, scope, global, found);
            }
            if let Stmt::ClassDef(class) = stmt {
                let idx = self.defs.idx(DefNode::Class(class)) as i64;
                let declared = declared_global(&class.body);
                self.survey_body(&class.body, idx, &declared, found);
            }
        }
    }

    /// The module-level names a function body binds under `global`, at any depth: each binding
    /// identity of the module's name becomes one the reading cannot read. `outer` holds the
    /// names the enclosing functions declare global, for a nested function that names them
    /// `nonlocal` (it binds the outer function's name, which is the module's).
    fn survey_function(
        &mut self,
        body: &'a [Stmt],
        outer: &mut Vec<&'a str>,
        found: &mut Vec<Found<'a>>,
    ) {
        let declared = declared_global(body);
        let nonlocal = declared_nonlocal(body);
        let enclosing: HashSet<&'a str> = outer.iter().copied().collect();
        let reaches = |name: &str| {
            declared.contains(name) || (nonlocal.contains(name) && enclosing.contains(name))
        };
        let mut stack: Vec<&'a [Stmt]> = vec![body];
        while let Some(suite) = stack.pop() {
            for stmt in suite {
                let mut bound = Vec::new();
                statement_bindings(stmt, &mut bound);
                for name in bound.into_iter().filter(|name| reaches(name)) {
                    found.push(((-1, name), Bind::Opaque));
                }
                stack.extend(nested_bodies(stmt));
                if let Stmt::FunctionDef(f) = stmt {
                    let before = outer.len();
                    outer.extend(declared.iter().copied());
                    self.survey_function(&f.body, outer, found);
                    outer.truncate(before);
                }
            }
        }
    }

    /// What a binding to `value` in `scope` makes the target (`Bind`).
    fn value_bind(&self, value: &'a Expr, scope: i64) -> Bind<'a> {
        if builtin_literal(value) {
            return Bind::Same;
        }
        match value {
            Expr::Name(name) if !self.bound.contains_key(name.id.as_str()) => {
                Bind::Alias(vec![self.alias_identities(scope, name.id.as_str())])
            }
            Expr::Attribute(_) => match self.class_chain(value) {
                Some(identity) => Bind::Alias(vec![vec![identity]]),
                None => Bind::Opaque,
            },
            Expr::BinOp(binary) if binary.op == ast::Operator::Add => {
                match (
                    self.value_bind(&binary.left, scope),
                    self.value_bind(&binary.right, scope),
                ) {
                    (Bind::Opaque, _) | (_, Bind::Opaque) => Bind::Opaque,
                    (left, right) => Bind::Alias([left.groups(), right.groups()].concat()),
                }
            }
            Expr::Call(call) => {
                let callee = callee_text(call);
                let full = self.resolve(&callee);
                let copy = full.as_deref().is_some_and(|full| {
                    matches!(
                        full,
                        "builtins.list"
                            | "builtins.tuple"
                            | "builtins.set"
                            | "builtins.frozenset"
                            | "builtins.dict"
                            | "builtins.sorted"
                    )
                });
                if copy {
                    Bind::Same
                } else if self.record_callee(&callee) {
                    Bind::Record
                } else {
                    Bind::Opaque
                }
            }
            _ => Bind::Opaque,
        }
    }

    /// The identities a name read in `scope` may be: the class body's own binding and the
    /// module's (a class body reads its own name only once bound, so both may hold).
    fn alias_identities(&self, scope: i64, name: &'a str) -> Vec<(i64, &'a str)> {
        let mut out = vec![(-1, name)];
        if scope != -1 {
            out.push((scope, name));
        }
        out
    }

    /// The class attribute a chain of a class this file defines names (`Reg.WORKFLOWS`,
    /// `Outer.Inner.ATTR`), as the identity (class scope, attribute).
    fn class_chain(&self, value: &'a Expr) -> Option<(i64, &'a str)> {
        let Expr::Attribute(attribute) = value else {
            return None;
        };
        let owner = match &*attribute.value {
            Expr::Name(head) => self.class_in(-1, head.id.as_str())?,
            inner @ Expr::Attribute(_) => {
                let (parent, name) = self.class_chain(inner)?;
                self.class_in(parent, name)?
            }
            _ => return None,
        };
        Some((owner, attribute.attr.as_str()))
    }

    /// The name a `for` or a comprehension takes items of (a name of this module or a class
    /// attribute of a class it defines), directly or through an iterator builtin (T8), and the
    /// scope it is read in.
    fn item_source(&self, iter: &'a Expr, scope: i64) -> Option<(String, i64)> {
        let source = match iter {
            Expr::Call(call) if self.iterator_builtin(call) => call
                .arguments
                .args
                .iter()
                .find_map(|arg| self.item_source(arg, scope))?,
            Expr::Name(name) if !self.bound.contains_key(name.id.as_str()) => {
                (name.id.to_string(), scope)
            }
            Expr::Attribute(_) => {
                let (class, attribute) = self.class_chain(iter)?;
                let name = format!("{}.{attribute}", self.class_path(class));
                (name, -1)
            }
            _ => return None,
        };
        Some(source)
    }

    /// The dotted name of a class this file defines, from the module (`Outer.Inner`).
    fn class_path(&self, mut class: i64) -> String {
        let mut parts = Vec::new();
        while class >= 0 {
            let entry = &self.defs.entries[class as usize];
            if let DefNode::Class(c) = entry.node {
                parts.push(c.name.as_str());
            }
            class = entry.parent;
        }
        parts.reverse();
        parts.join(".")
    }

    /// Joins the bindings into each identity's shape: builtin only when every binding is and
    /// every alias it takes is (the greatest such assignment), a record when any binding builds
    /// one, an item when any binding takes items.
    fn settle(&mut self, found: Vec<Found<'a>>) {
        let mut deps: HashMap<Identity<'a>, Vec<Vec<Identity<'a>>>> = HashMap::new();
        for (identity, bind) in found {
            let shape = self.identities.entry(identity).or_insert(Shape {
                builtin: true,
                ..Shape::default()
            });
            match bind {
                Bind::Same => {}
                Bind::Alias(groups) => deps.entry(identity).or_default().extend(groups),
                Bind::Record => {
                    shape.record = true;
                    shape.builtin = false;
                }
                Bind::Item(source, scope) => {
                    shape.item_of = Some((source, scope));
                    shape.builtin = false;
                }
                Bind::Opaque => shape.builtin = false,
            }
        }
        // An alias is builtin when, for each name it reads, every identity the name may be that
        // the file binds is builtin, and the file binds one.
        loop {
            let mut changed = false;
            for (identity, groups) in &deps {
                let builtin = groups.iter().all(|group| {
                    let shapes: Vec<&Shape> = (group.iter())
                        .filter_map(|dep| self.identities.get(dep))
                        .collect();
                    !shapes.is_empty() && shapes.iter().all(|shape| shape.builtin)
                });
                let shape = self.identities.get_mut(identity).expect("a found identity");
                if shape.builtin && !builtin {
                    shape.builtin = false;
                    changed = true;
                }
            }
            if !changed {
                break;
            }
        }
    }

    /// What one module-level or class-body identity is, read from `scope`: in a class body the
    /// class's own binding joined with the module's (the less safe wins); None when neither
    /// binds it.
    fn shape_of(&self, scope: i64, name: &str) -> Option<Shape> {
        let module = self.identities.get(&(-1, name)).cloned();
        let own = (scope != -1)
            .then(|| self.identities.get(&(scope, name)).cloned())
            .flatten();
        match (own, module) {
            (Some(a), Some(b)) => Some(Shape {
                builtin: a.builtin && b.builtin,
                record: a.record || b.record,
                item_of: a.item_of.or(b.item_of),
            }),
            (a, b) => a.or(b),
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
                self.statement = (at.scope == -1).then_some(&*e.value);
                self.child(Frame::Terminal("expr"), &e.value, at);
                self.statement = None;
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

    /// A `def` or `class` binds its name, decorated or not: a rebinding when the file also
    /// assigns the name somewhere (`emit_rebinding`).
    fn definition(&mut self, name: &ast::Identifier, at: At) {
        let id = name.as_str();
        let global = (self.globals.get(&at.scope)).is_some_and(|names| names.contains(id));
        if self.stored.contains(id) || self.bound.contains_key(id) || global {
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
        let (l, c) = self.lines.pos(alias.range.start());
        // An import in a class body or a function that declares the name `global` binds the
        // module's name.
        if at.scope != -1 && name != Some("*") {
            let full = alias.name.as_str();
            let bound = match &alias.asname {
                Some(asname) => asname.as_str(),
                None if name.is_some() => full,
                None => full.split('.').next().unwrap_or(full),
            };
            self.emit_rebinding(bound, (l, c), at, false);
        }
        if !self.wants(Kind::Imports) {
            return;
        }
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

    /// A module-level call that changes an import-bound name in place (`from x import W` then
    /// `W.append(B)`), as an assignment row: op `()`, the target `name.method`, the call as the
    /// value (limit 1: it is read as its own module binds it). A name this module binds is the use
    /// table's (T11, `emit_change`).
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
        if !MUTATORS.contains(&method.attr.as_str()) || !self.bound.contains_key(name.id.as_str()) {
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
    /// for a store or a delete). A load of a name no import binds starts as what the inventory
    /// says it is (`start`), and the use table reads it (`read_use`): read, bound as an alias
    /// (`name~TARGETS`) or into a record (`name>TARGETS@CALLEES`), changed in place by a
    /// statement whose mutation row the table writes, or escaped (`name`; `name.*` for a class,
    /// or when handed to `setattr` or `delattr`; an item escapes for the name it came from). A
    /// use in an `if TYPE_CHECKING:` branch never runs, so it is none.
    fn emit_use(&mut self, name: &'a ast::ExprName, at: At) {
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
        let Some((reached, used, start)) = self.start(id, at) else {
            return;
        };
        let frames = &self.path[..self.path.len() - used];
        let handed = matches!(frames.last(), Some(Frame::Arg(call, 0))
            if matches!(callee_text(call).as_str(), "setattr" | "delattr"));
        let site = name.range.start();
        let outcome = if handed {
            Outcome::Escape
        } else {
            self.read_use(frames, &start, at, !reached.contains('.'), site)
        };
        let row = match outcome {
            Outcome::Read => return,
            Outcome::Changed(call) => return self.emit_change(call, &reached, at),
            Outcome::Alias(targets) => format!("{reached}~{targets}"),
            Outcome::Record(targets, callees) => {
                format!("{reached}>{targets}@{}", callees.join(","))
            }
            Outcome::Escape => match start {
                Start::Item { source, scope } => {
                    return self.emit_escape(name, At { scope, ..at }, &source);
                }
                Start::Inherited(class) => {
                    for held in self.lineage_names(class) {
                        self.emit_escape(name, At { scope: -1, ..at }, &format!("{held}.*"));
                    }
                    return;
                }
                Start::Class => format!("{reached}.*"),
                _ if handed => format!("{reached}.*"),
                _ => reached,
            },
        };
        // A class's attributes are keys of the module (`Class.ATTR`), whichever scope uses it.
        let at = if row.ends_with(".*") {
            At { scope: -1, ..at }
        } else {
            at
        };
        self.emit_escape(name, at, &row);
    }

    /// What a use of `id` starts as, the name it reaches (`Reg.WORKFLOWS` through a class's
    /// attributes) and how many parent frames that takes: a comprehension's item of a name, a
    /// class this file defines, a class attribute or a name as the inventory says, or an
    /// attribute the class body does not bind (it may be a base's). None for a name the file
    /// does not bind at module level or in a class body: it is no name the link reads.
    fn start(&self, id: &'a str, at: At) -> Option<(String, usize, Start)> {
        let item = self
            .items
            .iter()
            .rev()
            .find(|(variable, _, _)| *variable == id);
        if let Some((_, source, scope)) = item {
            if source.is_empty() {
                return None;
            }
            let source = source.clone();
            return Some((
                id.to_string(),
                0,
                Start::Item {
                    source,
                    scope: *scope,
                },
            ));
        }
        let (reached, used, chain) = self.class_attribute(id);
        let start = match chain {
            Chain::Class => Start::Class,
            Chain::Attribute(class, attribute) => match self.identities.get(&(class, attribute)) {
                Some(shape) => shape_start(shape),
                None => Start::Inherited(class),
            },
            Chain::Name => shape_start(&self.shape_of(at.scope, id)?),
        };
        Some((reached, used, start))
    }

    /// Where a use of a name reaches through the attributes of a class this file defines
    /// (`Reg.WORKFLOWS`, `Outer.Inner.ATTR`, `getattr(Reg, "WORKFLOWS")`), and how many parent
    /// frames that takes. An instance (`Reg()`) is a use of the class.
    fn class_attribute(&self, id: &'a str) -> (String, usize, Chain<'a>) {
        let path = &self.path;
        let Some(mut class) = self.class_in(-1, id) else {
            return (id.to_string(), 0, Chain::Name);
        };
        let mut name = id.to_string();
        let mut used = 0;
        loop {
            let at = path.len() - used;
            let Some(attribute) = at.checked_sub(1).and_then(|i| attribute_of(path[i])) else {
                return (name, used, Chain::Class);
            };
            name = format!("{name}.{attribute}");
            used = path.len() - (at - 1);
            match self.class_in(class, attribute) {
                Some(inner) => class = inner,
                None => return (name, used, Chain::Attribute(class, attribute)),
            }
        }
    }

    /// A class this file defines and the classes of this file it inherits from, by dotted name
    /// (`Inherited`): an attribute its body does not bind may be any of theirs.
    fn lineage_names(&self, class: i64) -> Vec<String> {
        let mut out = Vec::new();
        let mut stack = vec![class];
        while let Some(class) = stack.pop() {
            let name = self.class_path(class);
            if out.contains(&name) {
                continue;
            }
            out.push(name);
            let DefNode::Class(node) = self.defs.entries[class as usize].node else {
                continue;
            };
            let bases = node.arguments.iter().flat_map(|a| a.args.iter());
            for base in bases {
                let found = match base {
                    Expr::Name(name) => self.class_in(-1, name.id.as_str()),
                    Expr::Attribute(_) => (self.class_chain(base))
                        .and_then(|(parent, name)| self.class_in(parent, name)),
                    _ => None,
                };
                stack.extend(found);
            }
        }
        out
    }

    /// A mutation row for a change T11 accepts (`W.append(B)` as a module-level statement): an
    /// assignment row with op `()`, the target `name.method`, the call as the value.
    fn emit_change(&mut self, call: &'a ast::ExprCall, reached: &str, at: At) {
        let Some(value) = self.statement else {
            return;
        };
        let Expr::Attribute(method) = call.func.as_ref() else {
            return;
        };
        let target = Value::Chain(format!("{reached}.{}", method.attr));
        let (l, c) = self.lines.pos(value.range().start());
        let v = self.enc.assigned(Some(value), self.src);
        self.out.assigns.push(Assign {
            ann: None,
            c,
            f: at.flags,
            l,
            op: "()".to_string(),
            s: at.scope,
            t: vec![target],
            v,
        });
    }

    /// A binding of `id` that writes no `assigns` row, or that binds the module's name from a
    /// class body or a function (`global`): an `=id` escape where the name lives, so the reading
    /// takes the name as bound to something it cannot read. Any form counts (the Language
    /// Reference's list, "Binding of names"): a target of any statement, `except ... as`, a
    /// capture pattern, `class`, `def`, an import, a walrus, `del`. An assignment at module level
    /// or in a class body writes a row, and one in a function with `global` to a plain name
    /// writes a module-level row (`emit_global`); any other target shape there, and any binding
    /// in a class body with `global`, binds the module's name.
    fn emit_rebinding(&mut self, id: &str, pos: (i64, i64), at: At, assignment: bool) {
        if !self.wants(Kind::Escapes) {
            return;
        }
        let global =
            at.scope != -1 && (self.globals.get(&at.scope)).is_some_and(|names| names.contains(id));
        let row_scope = self.emits_assign(at.scope);
        let direct = matches!(
            self.path.last(),
            Some(Frame::Terminal(
                "assign-target" | "annassign-target" | "augassign-target"
            ))
        );
        let site = match (global, row_scope) {
            (true, true) => At { scope: -1, ..at },
            (true, false) if !(assignment && direct) => At { scope: -1, ..at },
            (false, true) if !assignment => at,
            _ => return,
        };
        self.out.escapes.push(Tag {
            site: site.site(pos),
            name: format!("={id}"),
        });
    }

    // ---- the use table ----------------------------------------------------------------------
    //
    // The use reading's second responsibility: a use is read only by an entry that accounts for
    // every protocol invocation that can receive the value or a holder of it, every slot of the
    // operation and the binding of its callee. One entry per operation (T1 to T13 of
    // `engine/round7-table.md`); anything else escapes.

    /// Reads a use up through its parent frames (innermost last) by the entry for what it
    /// starts as. `bare`: the use is a name with no dot; `site`: where the use starts, to tell
    /// it from the other operands of an operator.
    fn read_use(
        &self,
        frames: &[Frame<'a>],
        start: &Start,
        at: At,
        bare: bool,
        site: TextSize,
    ) -> Outcome<'a> {
        match start {
            Start::Value { builtin } => self.read_value(frames, *builtin, at, bare, site),
            Start::Item { .. } => self.read_item(frames, at, site),
            Start::Class => self.read_holder(frames, false, at),
            Start::Record => self.read_holder(frames, true, at),
            Start::Inherited(_) => Outcome::Escape,
        }
    }

    /// The name's own value. Every entry that invokes a protocol of it needs it builtin-typed.
    fn read_value(
        &self,
        frames: &[Frame<'a>],
        builtin: bool,
        at: At,
        bare: bool,
        site: TextSize,
    ) -> Outcome<'a> {
        let up = |k: usize| frames.len().checked_sub(k).map(|i| frames[i]);
        let Some(first) = up(1) else {
            return Outcome::Read;
        };
        match first {
            // T1: `[*W]`, `(*W,)`.
            Frame::Step("star") => match up(2) {
                Some(Frame::Step("elt:list" | "elt:tuple")) if builtin => Outcome::Read,
                _ => Outcome::Escape,
            },
            // T2: an alias, also of a conditional's branch.
            Frame::AssignValue(targets) => alias(targets),
            Frame::AnnAssignValue(target) => alias(std::slice::from_ref(target)),
            Frame::Step("ifexp:body" | "ifexp:else") => match up(2) {
                Some(Frame::AssignValue(targets)) => alias(targets),
                Some(Frame::AnnAssignValue(target)) => alias(std::slice::from_ref(target)),
                _ => Outcome::Escape,
            },
            // A bare expression statement evaluates the name only.
            Frame::Terminal("expr") => Outcome::Read,
            // T7: a test, an iteration.
            Frame::Terminal("if-test" | "while-test" | "assert" | "for-iter")
            | Frame::Step("ifexp:test" | "comp:if" | "comp:iter")
            | Frame::Op("unary:", "not")
                if builtin =>
            {
                Outcome::Read
            }
            // T12: an f-string.
            Frame::Step("fstr") if builtin => Outcome::Read,
            Frame::Step("elt:list" | "elt:tuple") => self.in_display(frames, 2, builtin, at),
            Frame::Arg(call, index) => {
                self.value_argument(frames, call, Position::Arg(index), builtin, at)
            }
            Frame::Kw(call, keyword) => {
                self.value_argument(frames, call, Position::Keyword(keyword), builtin, at)
            }
            Frame::SubValue => self.value_element(frames, builtin, at, bare, site),
            Frame::Attr(attribute) => match up(2) {
                Some(Frame::CallFunc(call)) => {
                    self.value_method(frames, attribute, call, builtin, at, bare, site)
                }
                _ => Outcome::Escape,
            },
            // T13 (and `%` of T12).
            Frame::Operand(prefix, op, node) if builtin => {
                read_if(self.operator_reads(prefix, op, node, at, site, true))
            }
            _ => Outcome::Escape,
        }
    }

    /// A value as an element of a list or tuple display, the display at `up(k)`: T3 when the
    /// display is a record's argument, T12 when it is the tuple a `%` formats; else it escapes.
    fn in_display(&self, frames: &[Frame<'a>], k: usize, builtin: bool, at: At) -> Outcome<'a> {
        let up = |k: usize| frames.len().checked_sub(k).map(|i| frames[i]);
        match up(k) {
            Some(Frame::Arg(call, _) | Frame::Kw(call, Some(_))) if self.record_call(call) => {
                self.record(frames, k, at)
            }
            Some(Frame::Operand("bin:", "%", Expr::BinOp(binary)))
                if builtin
                    && binary.left.is_string_literal_expr()
                    && binary.right.is_tuple_expr() =>
            {
                Outcome::Read
            }
            _ => Outcome::Escape,
        }
    }

    /// T3: a record built by a constructor the link confirms (`record_call`) holds its
    /// arguments; an argument may be a list or tuple display of arguments or a nested record,
    /// and the outermost record is bound to one module-level name. `up(k)` is the argument frame
    /// of the first constructor.
    fn record(&self, frames: &[Frame<'a>], mut k: usize, at: At) -> Outcome<'a> {
        let up = |k: usize| frames.len().checked_sub(k).map(|i| frames[i]);
        let mut callees = Vec::new();
        loop {
            let call = match up(k) {
                Some(Frame::Arg(call, _) | Frame::Kw(call, Some(_))) if self.record_call(call) => {
                    call
                }
                _ => return Outcome::Escape,
            };
            callees.push(callee_text(call));
            match up(k + 1) {
                Some(Frame::AssignValue([Expr::Name(target)])) if at.scope == -1 => {
                    return Outcome::Record(target.id.to_string(), callees);
                }
                Some(Frame::Step("elt:list" | "elt:tuple")) => k += 2,
                Some(Frame::Arg(..) | Frame::Kw(..)) => k += 1,
                _ => return Outcome::Escape,
            }
        }
    }

    /// The value handed to a call: a record (T3), `str.format` (T12), a builtin reader by the
    /// slot it sits in (T5, T6) or an iterator consumed in place (T8). Every argument present
    /// must be a slot the entry names; a `*` or `**` argument never is.
    fn value_argument(
        &self,
        frames: &[Frame<'a>],
        call: &'a ast::ExprCall,
        position: Position<'a>,
        builtin: bool,
        at: At,
    ) -> Outcome<'a> {
        // T3: `**X` of a builtin-typed dict hands a record constructor X's items as arguments.
        let callee = callee_text(call);
        let positional = !call.arguments.args.iter().any(Expr::is_starred_expr);
        if position == Position::Keyword(None)
            && builtin
            && positional
            && self.record_callee(&callee)
            && self.resolve(&callee).as_deref() != Some("dataclasses.replace")
        {
            return Outcome::Read;
        }
        if has_star_arguments(call) {
            return Outcome::Escape;
        }
        if self.record_call(call) {
            return self.record(frames, 1, at);
        }
        if !builtin {
            return Outcome::Escape;
        }
        if is_format_call(call) {
            return Outcome::Read;
        }
        let Some(full) = self.resolve(&callee_text(call)) else {
            return Outcome::Escape;
        };
        let args = call.arguments.args.len();
        let no_keywords = call.arguments.keywords.is_empty();
        let only = position == Position::Arg(0) && args == 1 && no_keywords;
        let read = match full.as_str() {
            "builtins.list" | "builtins.tuple" | "builtins.set" | "builtins.frozenset"
            | "builtins.dict" | "builtins.len" | "builtins.bool" | "builtins.id"
            | "builtins.hash" | "builtins.repr" | "builtins.str" | "builtins.any"
            | "builtins.all" | "builtins.min" | "builtins.max" | "builtins.sum" => only,
            "builtins.sorted" => {
                position == Position::Arg(0) && args == 1 && self.sort_keywords(call)
            }
            "builtins.dict.fromkeys" => {
                position == Position::Arg(0) && (1..=2).contains(&args) && no_keywords
            }
            "builtins.print" => matches!(position, Position::Arg(_)) && no_keywords,
            "builtins.isinstance" => {
                position == Position::Arg(0)
                    && args == 2
                    && no_keywords
                    && self.builtin_classes(&call.arguments.args[1])
            }
            full if ITERATORS.contains(&full) => {
                self.iterator_slot(call, full, position) && self.consumed(frames, 2)
            }
            _ => false,
        };
        read_if(read)
    }

    /// T11 and T9: an element of the value. A store or a `del` of it by a module-level
    /// statement on the bare name changes the name in place (the assignment row is the change);
    /// a load is an item.
    fn value_element(
        &self,
        frames: &[Frame<'a>],
        builtin: bool,
        at: At,
        bare: bool,
        site: TextSize,
    ) -> Outcome<'a> {
        let up = |k: usize| frames.len().checked_sub(k).map(|i| frames[i]);
        let statement = builtin && bare && at.scope == -1;
        match up(2) {
            Some(Frame::Terminal("assign-target" | "annassign-target" | "del")) if statement => {
                Outcome::Read
            }
            Some(Frame::Terminal(text)) if text.ends_with("-target") || text == "del" => {
                Outcome::Escape
            }
            _ if builtin => self.read_item(&frames[..frames.len() - 1], at, site),
            _ => Outcome::Escape,
        }
    }

    /// A method of the value: a change by a module-level statement on the bare name (T11, which
    /// writes the mutation row), a reading method (T10), or `.get()`/`.pop()`, whose result is
    /// an item.
    #[allow(clippy::too_many_arguments)]
    fn value_method(
        &self,
        frames: &[Frame<'a>],
        attribute: &str,
        call: &'a ast::ExprCall,
        builtin: bool,
        at: At,
        bare: bool,
        site: TextSize,
    ) -> Outcome<'a> {
        let up = |k: usize| frames.len().checked_sub(k).map(|i| frames[i]);
        if !builtin || has_star_arguments(call) {
            return Outcome::Escape;
        }
        let args = call.arguments.args.len();
        let keywords = !call.arguments.keywords.is_empty();
        let statement = bare
            && at.scope == -1
            && self.statement.is_some()
            && matches!(up(3), Some(Frame::Terminal("expr")));
        match attribute {
            "append" | "add" | "extend" if statement && args == 1 && !keywords => {
                Outcome::Changed(call)
            }
            "insert" if statement && args == 2 && !keywords => Outcome::Changed(call),
            "update" if statement && args <= 1 => Outcome::Changed(call),
            "copy" | "keys" | "values" | "items" | "clear" | "reverse" | "sort"
                if args == 0 && !keywords =>
            {
                Outcome::Read
            }
            "count" | "remove" | "discard" if args == 1 && !keywords => Outcome::Read,
            "index" if (1..=3).contains(&args) && !keywords => Outcome::Read,
            "get" | "pop" if args <= 2 && !keywords => {
                self.read_item(&frames[..frames.len() - 2], at, site)
            }
            _ => Outcome::Escape,
        }
    }

    /// T13 and `%` of T12: `X + Y` with Y a list or tuple display or a builtin-typed name;
    /// `"lit" % X`; a comparison by `==`, `!=`, `in`, `not in` with literals (`value`: or
    /// builtin-typed names), or by `is`/`is not` with anything. `site` tells the use's own
    /// operand from the others.
    fn operator_reads(
        &self,
        prefix: &str,
        op: &str,
        node: &'a Expr,
        at: At,
        site: TextSize,
        value: bool,
    ) -> bool {
        let other = |operand: &Expr| !operand.range().contains(site);
        let known =
            |operand: &Expr| plain_literal(operand) || (value && self.builtin_name(operand, at));
        match (prefix, node) {
            ("bin:", Expr::BinOp(binary)) if op == "+" && value => [&*binary.left, &*binary.right]
                .into_iter()
                .filter(|operand| other(operand))
                .all(|operand| {
                    matches!(operand, Expr::List(_) | Expr::Tuple(_))
                        || self.builtin_name(operand, at)
                }),
            ("bin:", Expr::BinOp(binary)) if op == "%" && value => {
                binary.left.is_string_literal_expr()
            }
            ("cmp:", Expr::Compare(compare)) => {
                let identity =
                    (compare.ops.iter()).all(|op| matches!(op, ast::CmpOp::Is | ast::CmpOp::IsNot));
                let plain = compare.ops.iter().all(|op| {
                    matches!(
                        op,
                        ast::CmpOp::Eq
                            | ast::CmpOp::NotEq
                            | ast::CmpOp::In
                            | ast::CmpOp::NotIn
                            | ast::CmpOp::Is
                            | ast::CmpOp::IsNot
                    )
                });
                let operands = compare.operands.iter();
                identity || (plain && operands.filter(|o| other(o)).all(known))
            }
            _ => false,
        }
    }

    /// An item of a name (a comprehension's or a `for`'s variable over it, an element of it, a
    /// `.get()` or `.pop()` of it). It keeps its provenance: read where nothing can reach the
    /// name from it (an attribute read, a test, formatting, a builtin reader, a comparison, a
    /// display, a binding), else handed on, it escapes for the name.
    fn read_item(&self, frames: &[Frame<'a>], at: At, site: TextSize) -> Outcome<'a> {
        let up = |k: usize| frames.len().checked_sub(k).map(|i| frames[i]);
        let Some(first) = up(1) else {
            return Outcome::Read;
        };
        let read = match first {
            Frame::Attr(_) => !matches!(up(2), Some(Frame::CallFunc(_))),
            Frame::Terminal("if-test" | "while-test" | "assert" | "expr")
            | Frame::Step(
                "ifexp:test" | "comp:if" | "fstr" | "elt:list" | "elt:tuple" | "elt:set"
                | "comp:elt" | "comp:key" | "comp:val" | "dict:key" | "dict:val",
            )
            | Frame::DictVal(_)
            | Frame::Op("unary:", "not")
            | Frame::AssignValue(_)
            | Frame::AnnAssignValue(_) => true,
            Frame::Arg(call, _) => {
                !has_star_arguments(call)
                    && (is_format_call(call) || self.reads_item(call) || self.loader_call(call))
            }
            Frame::Kw(call, Some(_)) => {
                !has_star_arguments(call) && (is_format_call(call) || self.loader_call(call))
            }
            Frame::Operand(prefix, op, node) => {
                self.operator_reads(prefix, op, node, at, site, false)
            }
            _ => false,
        };
        read_if(read)
    }

    /// A class this file defines, or a record: it holds what its body binds or its arguments.
    /// T3 takes either as a record's argument, T4 a record as `dataclasses.replace`'s; a class
    /// as a base runs its creation hooks (limit 1). Any other use escapes.
    fn read_holder(&self, frames: &[Frame<'a>], record: bool, at: At) -> Outcome<'a> {
        let up = |k: usize| frames.len().checked_sub(k).map(|i| frames[i]);
        match up(1) {
            Some(Frame::Arg(call, _) | Frame::Kw(call, Some(_)))
                if !has_star_arguments(call) && self.record_call(call) =>
            {
                self.record(frames, 1, at)
            }
            Some(Frame::Arg(call, 0)) if record && self.replace_call(call) => match up(2) {
                Some(Frame::AssignValue([Expr::Name(target)])) if at.scope == -1 => {
                    Outcome::Record(target.id.to_string(), vec![callee_text(call)])
                }
                _ => Outcome::Escape,
            },
            Some(Frame::Step("elt:list" | "elt:tuple")) => self.in_display(frames, 2, false, at),
            Some(Frame::Base(_)) if !record => Outcome::Read,
            _ => Outcome::Escape,
        }
    }

    /// A call of a record constructor T3 reads: a capitalised first-party class an import binds
    /// (the link confirms it is a bare class), not `dataclasses.replace`, with no `*` or `**`.
    fn record_call(&self, call: &ast::ExprCall) -> bool {
        let callee = callee_text(call);
        !has_star_arguments(call)
            && self.record_callee(&callee)
            && self.resolve(&callee).as_deref() != Some("dataclasses.replace")
    }

    /// T4's call: `dataclasses.replace` (a unique, valid binding) with the record as its only
    /// positional argument and named keywords.
    fn replace_call(&self, call: &ast::ExprCall) -> bool {
        self.resolve(&callee_text(call)).as_deref() == Some("dataclasses.replace")
            && call.arguments.args.len() == 1
            && !has_star_arguments(call)
    }

    /// Whether a call's keywords are `sorted`'s: `reverse`, and `key` as a callable slot.
    fn sort_keywords(&self, call: &ast::ExprCall) -> bool {
        call.arguments.keywords.iter().all(|keyword| {
            match keyword.arg.as_ref().map(|arg| arg.as_str()) {
                Some("reverse") => true,
                Some("key") => self.callable_slot(&keyword.value),
                _ => false,
            }
        })
    }

    /// A callable that receives items (`key=`, `map`'s and `filter`'s function): a lambda, a
    /// builtin that reads its argument, or a function this module defines (its body is limit 1).
    fn callable_slot(&self, expr: &Expr) -> bool {
        match expr {
            Expr::Lambda(_) => true,
            Expr::Name(name) => {
                let id = name.id.as_str();
                let module_def = (self.defs.entries.iter()).any(|entry| {
                    entry.parent == -1
                        && matches!(entry.node, DefNode::Func(f) if f.name.as_str() == id)
                });
                let builtin = (self.resolve(id))
                    .is_some_and(|full| READING_BUILTINS.contains(&full.as_str()));
                builtin || (module_def && !self.stored.contains(id) && !self.bound.contains_key(id))
            }
            _ => false,
        }
    }

    /// `isinstance`'s classes: a builtin type, or a tuple display of builtin types.
    fn builtin_classes(&self, expr: &Expr) -> bool {
        match expr {
            Expr::Tuple(tuple) => tuple.elts.iter().all(|e| self.builtin_classes(e)),
            Expr::Name(name) => (self.resolve(name.id.as_str())).is_some_and(|full| {
                matches!(
                    full.as_str(),
                    "builtins.list"
                        | "builtins.tuple"
                        | "builtins.dict"
                        | "builtins.set"
                        | "builtins.frozenset"
                        | "builtins.str"
                        | "builtins.bytes"
                        | "builtins.int"
                        | "builtins.float"
                        | "builtins.bool"
                        | "builtins.type"
                        | "builtins.object"
                )
            }),
            _ => false,
        }
    }

    /// Whether a call is an iterator builtin (T8).
    fn iterator_builtin(&self, call: &ast::ExprCall) -> bool {
        (self.resolve(&callee_text(call))).is_some_and(|full| ITERATORS.contains(&full.as_str()))
    }

    /// Whether the value sits in a slot an iterator builtin reads it from (T8).
    fn iterator_slot(&self, call: &ast::ExprCall, full: &str, position: Position) -> bool {
        let args = &call.arguments.args;
        let keywords: Vec<&str> = (call.arguments.keywords.iter())
            .filter_map(|k| k.arg.as_ref().map(|arg| arg.as_str()))
            .collect();
        match full {
            "builtins.iter" | "builtins.reversed" => {
                position == Position::Arg(0) && args.len() == 1 && keywords.is_empty()
            }
            "builtins.enumerate" => {
                position == Position::Arg(0)
                    && (1..=2).contains(&args.len())
                    && keywords.iter().all(|k| *k == "start")
            }
            "builtins.zip" => {
                matches!(position, Position::Arg(_)) && keywords.iter().all(|k| *k == "strict")
            }
            "builtins.map" => {
                matches!(position, Position::Arg(i) if i >= 1)
                    && keywords.is_empty()
                    && self.callable_slot(&args[0])
            }
            "builtins.filter" => {
                position == Position::Arg(1)
                    && args.len() == 2
                    && keywords.is_empty()
                    && (args[0].is_none_literal_expr() || self.callable_slot(&args[0]))
            }
            "itertools.chain" => matches!(position, Position::Arg(_)) && keywords.is_empty(),
            _ => false,
        }
    }

    /// Whether the iterator at `up(k)` is consumed in place (T8): the iterable of a `for` or a
    /// comprehension, or the only argument of a builtin copy reader.
    fn consumed(&self, frames: &[Frame<'a>], k: usize) -> bool {
        let up = |k: usize| frames.len().checked_sub(k).map(|i| frames[i]);
        match up(k) {
            Some(Frame::Terminal("for-iter") | Frame::Step("comp:iter")) => true,
            Some(Frame::Arg(reader, 0)) => {
                let full = self.resolve(&callee_text(reader));
                let copy = full.as_deref().is_some_and(|full| {
                    matches!(
                        full,
                        "builtins.list"
                            | "builtins.tuple"
                            | "builtins.set"
                            | "builtins.frozenset"
                            | "builtins.dict"
                    )
                });
                let sorted =
                    full.as_deref() == Some("builtins.sorted") && self.sort_keywords(reader);
                reader.arguments.args.len() == 1
                    && !has_star_arguments(reader)
                    && ((copy && reader.arguments.keywords.is_empty()) || sorted)
            }
            _ => false,
        }
    }

    /// T14: a configured loader (`importlib.import_module`, `__import__`, through a unique, valid
    /// binding or an import alias) takes the item as a module's name: the computed-import rule
    /// reads the loop over the list, and the loader keeps nothing of the item.
    fn loader_call(&self, call: &ast::ExprCall) -> bool {
        (self.resolve(&callee_text(call)))
            .is_some_and(|full| self.config.watch_aliased.contains(&full))
    }

    /// The builtins that read an item handed to them: nothing of it goes on.
    fn reads_item(&self, call: &ast::ExprCall) -> bool {
        let Some(full) = self.resolve(&callee_text(call)) else {
            return false;
        };
        match full.as_str() {
            "builtins.list" | "builtins.tuple" | "builtins.set" | "builtins.frozenset"
            | "builtins.len" | "builtins.bool" | "builtins.id" | "builtins.hash"
            | "builtins.repr" | "builtins.str" | "builtins.print" => {
                call.arguments.keywords.is_empty()
            }
            "builtins.isinstance" => {
                call.arguments.args.len() == 2
                    && call.arguments.keywords.is_empty()
                    && self.builtin_classes(&call.arguments.args[1])
            }
            _ => false,
        }
    }

    /// Whether an operand is a name (or a class attribute) the inventory says is builtin-typed.
    fn builtin_name(&self, expr: &Expr, at: At) -> bool {
        match expr {
            Expr::Name(name) if !self.bound.contains_key(name.id.as_str()) => {
                (self.shape_of(at.scope, name.id.as_str())).is_some_and(|shape| shape.builtin)
            }
            Expr::Attribute(_) => (self.class_chain(expr))
                .and_then(|identity| self.identities.get(&identity))
                .is_some_and(|shape| shape.builtin),
            _ => false,
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
        if self.binds_name(head) || self.patched.heads.contains(head) || self.patched.builtins {
            return None;
        }
        let base = match self.bound.get(head) {
            Some(binding) => binding.origin()?,
            None if self.star_first_party || self.builtins_patched() => return None,
            None => format!("builtins.{head}"),
        };
        let full = match rest {
            Some(rest) => format!("{base}.{rest}"),
            None => base,
        };
        // A module patched under another name (`import copy as c; c.copy = f`) is patched for
        // every name that reaches it.
        let patched = (self.patched.heads.iter())
            .filter_map(|head| self.bound.get(head).and_then(|b| b.origin()))
            .any(|module| full == module || full.starts_with(&format!("{module}.")));
        (!patched).then_some(full)
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
                self.child(Frame::CallFunc(call), &call.func, at);
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
                let items = self.items.len();
                self.comprehension_elements(generators, at.scope);
                self.comprehension_items(generators, at.scope);
                self.child(Frame::Step("comp:elt"), elt, at);
                self.generators(generators, at);
                self.elements.truncate(elements);
                self.items.truncate(items);
            }
            Expr::DictComp(c) => {
                let elements = self.elements.len();
                let items = self.items.len();
                self.comprehension_elements(&c.generators, at.scope);
                self.comprehension_items(&c.generators, at.scope);
                self.opt_child(Frame::Step("comp:key"), c.key.as_deref(), at);
                self.child(Frame::Step("comp:val"), &c.value, at);
                self.generators(&c.generators, at);
                self.elements.truncate(elements);
                self.items.truncate(items);
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

    /// The variables of a comprehension's generators that take items of a module-level or
    /// class-body name (`item_source`): their uses in it are items of that name. A variable over
    /// anything else hides a name of the same spelling.
    fn comprehension_items(&mut self, generators: &'a [ast::Comprehension], scope: i64) {
        if !self.wants(Kind::Escapes) {
            return;
        }
        for generator in generators {
            let mut names = Vec::new();
            target_names(&generator.target, &mut names);
            let source = self.item_source(&generator.iter, scope);
            for name in names {
                let (source, at) = match &source {
                    Some((source, at)) => (source.clone(), *at),
                    None => (String::new(), scope),
                };
                self.items.push((name, source, at));
            }
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
            self.emit_entry_call(call, at, (l, c));
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

    /// Module accesses the item reading cannot read, by what a load needs (the item reading's
    /// rules 1 to 3 in a registration point; elsewhere they reach the module's names):
    /// 1. `sys.modules` by any spelling (`is_modules`: an attribute named `modules` on any
    ///    receiver, a name imported as `modules`, an alias of either) read in a way that may give
    ///    a module: a `computed` row, as no edge names a key of it, and the `*` escape unless it
    ///    is a subscript by a string literal. A read that gives only names or a truth value loads
    ///    nothing (`modules_names_only`);
    /// 2. an import entry point (`ENTRY_POINTS`) read as a value: a `computed` row (`use`); a
    ///    call of one is `emit_entry_call`'s;
    /// 3. a module an `import` binds handed to anything but a literal, non-dunder attribute read
    ///    (`vars(importlib)`, `importlib.__dict__`, a call's argument): a `computed` row (`use`)
    ///    and `*`; `importlib.reload(alias)` as a statement reads the alias's module;
    ///
    /// Also `globals`, `locals` or `vars` read as a value, any use of `__builtins__`, `exec` or
    /// `eval` read as a value.
    fn emit_module_reads(&mut self, expr: &'a Expr, at: At) {
        if !self.wants_any(&[Kind::Computed, Kind::Escapes]) {
            return;
        }
        let pos = self.lines.pos(expr.range().start());
        if let Expr::Subscript(s) = expr
            && self.is_modules(&s.value)
        {
            let sequence = self.sequence_of(&s.slice, at.scope).unwrap_or_default();
            self.push_computed(at, pos, "sys.modules", sequence);
            if !s.slice.is_string_literal_expr() {
                self.emit_everything(pos);
            }
            return;
        }
        let in_subscript = matches!(self.path.last(), Some(Frame::SubValue));
        if self.is_modules(expr) {
            if !in_subscript && !self.modules_names_only() {
                self.push_computed(at, pos, "sys.modules", String::new());
                self.emit_everything(pos);
            }
            return;
        }
        let called = matches!(self.path.last(), Some(Frame::CallFunc(_)));
        if let Some(entry) = self.entry_point(expr)
            && !called
        {
            self.push_computed(at, pos, "use", entry);
            return;
        }
        let Expr::Name(name) = expr else {
            return;
        };
        if name.ctx != ExprContext::Load {
            return;
        }
        let id = name.id.as_str();
        if self.module_object(id) {
            let read = self.path.last().copied().and_then(attribute_of);
            if read.is_none_or(|attribute| attribute.starts_with("__")) && !self.reloaded() {
                let origin = self
                    .bound
                    .get(id)
                    .and_then(|b| b.origin())
                    .unwrap_or_default();
                self.push_computed(at, pos, "use", origin);
                self.emit_everything(pos);
            }
            return;
        }
        if id == "__builtins__" {
            self.push_computed(at, pos, "use", id.to_string());
            self.emit_everything(pos);
            return;
        }
        if called {
            return;
        }
        match self.resolve(id).as_deref() {
            Some("builtins.exec" | "builtins.eval") => {
                self.push_computed(at, pos, "use", id.to_string());
            }
            Some("builtins.globals" | "builtins.locals" | "builtins.vars") => {
                self.emit_everything(pos);
            }
            _ => {}
        }
    }

    /// Rule 2's call: a call of an import entry point (`ENTRY_POINTS`) on any receiver or
    /// through an import alias, that the configured loaders' reading does not take (their
    /// literal and list forms: `import_module`, `__import__`), and that is not `reload` of a
    /// module an import binds (a statement reads it; a result read further is a computed read
    /// of that module): a `computed` row (`use`), and the `*` escape unless every argument is a
    /// string literal.
    fn emit_entry_call(&mut self, call: &'a ast::ExprCall, at: At, pos: (i64, i64)) {
        let Some(entry) = self.entry_point(&call.func) else {
            return;
        };
        if self.config.is_watched_callee(&entry) {
            return;
        }
        let alias = matches!(call.arguments.args.first(), Some(Expr::Name(alias))
            if self.bound.contains_key(alias.id.as_str()));
        if entry == "reload" && alias {
            return;
        }
        let literal = (call.arguments.args.iter()).all(Expr::is_string_literal_expr)
            && (call.arguments.keywords.iter()).all(|k| k.value.is_string_literal_expr());
        self.push_computed(at, pos, "use", entry);
        if !literal {
            self.emit_everything(pos);
        }
    }

    /// The import entry point an expression names (`ENTRY_POINTS`): an attribute of that name
    /// on any receiver, a name an import binds to one (under any alias), or the builtin
    /// `__import__`.
    fn entry_point(&self, expr: &Expr) -> Option<String> {
        let last = match expr {
            Expr::Attribute(attribute) => attribute.attr.as_str(),
            Expr::Name(name) => {
                let id = name.id.as_str();
                match self.bound.get(id) {
                    Some(binding) if !binding.member.is_empty() => binding.member,
                    Some(_) => return None,
                    None if self.resolve(id).as_deref() == Some("builtins.__import__") => id,
                    None => return None,
                }
            }
            _ => return None,
        };
        ENTRY_POINTS.contains(&last).then(|| last.to_string())
    }

    /// Whether a name is a module an `import` statement binds (`import m`, `import m as x`).
    fn module_object(&self, id: &str) -> bool {
        self.bound
            .get(id)
            .is_some_and(|binding| binding.member.is_empty() && !binding.module.is_empty())
    }

    /// Whether the node being walked is the alias `importlib.reload(alias)` reloads as a
    /// statement: a read of that module (`emit_namespace_call`).
    fn reloaded(&self) -> bool {
        let n = self.path.len();
        let Some(Frame::Arg(call, 0)) = self.path.last() else {
            return false;
        };
        let statement = n >= 2 && matches!(self.path[n - 2], Frame::Terminal("expr"));
        statement && self.resolve(&callee_text(call)).as_deref() == Some("importlib.reload")
    }

    /// Whether a read of `sys.modules` (the node being walked) gives only names or a truth value:
    /// `in` or `not in` it, `len`, `bool` or a builtin copy of it (its keys), a test, iteration
    /// over its keys, `.keys()`, and `.get("literal")` compared with `None`.
    fn modules_names_only(&self) -> bool {
        let path = &self.path;
        let n = path.len();
        let frame = |back: usize| n.checked_sub(back).map(|i| path[i]);
        match frame(1) {
            Some(Frame::Operand("cmp:", "in" | "notin", _)) => true,
            Some(Frame::Terminal("if-test" | "while-test" | "assert" | "for-iter")) => true,
            Some(Frame::Step("comp:iter" | "comp:if" | "ifexp:test")) => true,
            Some(Frame::Op("unary:", "not")) => true,
            Some(Frame::Arg(call, 0)) => {
                call.arguments.args.len() == 1
                    && call.arguments.keywords.is_empty()
                    && matches!(
                        self.resolve(&callee_text(call)).as_deref(),
                        Some(
                            "builtins.len"
                                | "builtins.bool"
                                | "builtins.list"
                                | "builtins.tuple"
                                | "builtins.set"
                                | "builtins.frozenset"
                                | "builtins.sorted"
                        )
                    )
            }
            Some(Frame::Attr("keys")) => matches!(frame(2), Some(Frame::CallFunc(_))),
            Some(Frame::Attr("get")) => match (frame(2), frame(3)) {
                (Some(Frame::CallFunc(_)), Some(Frame::Operand("cmp:", _, Expr::Compare(c)))) => {
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

    /// Whether an expression is `sys.modules` by any spelling (rule 1): an attribute named
    /// `modules` on any receiver, `getattr(x, "modules")`, a name an import binds as `modules`,
    /// or a module-level name the inventory resolves to one of these.
    fn is_modules(&self, expr: &Expr) -> bool {
        match expr {
            Expr::Attribute(attribute) => attribute.attr.as_str() == "modules",
            Expr::Name(name) => {
                let id = name.id.as_str();
                self.modules_aliases.contains(id)
                    || self.bound.get(id).is_some_and(|b| b.member == "modules")
            }
            Expr::Call(call) => getattr_literal(call) == Some("modules"),
            _ => false,
        }
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

/// A value whose dunders are builtin on its own: a display, a list, set or dict comprehension,
/// a literal or an f-string.
fn builtin_literal(expr: &Expr) -> bool {
    matches!(
        expr,
        Expr::List(_)
            | Expr::Tuple(_)
            | Expr::Set(_)
            | Expr::Dict(_)
            | Expr::ListComp(_)
            | Expr::SetComp(_)
            | Expr::DictComp(_)
            | Expr::StringLiteral(_)
            | Expr::BytesLiteral(_)
            | Expr::NumberLiteral(_)
            | Expr::BooleanLiteral(_)
            | Expr::NoneLiteral(_)
            | Expr::FString(_)
    )
}

/// A literal a comparison may meet with no first-party code: a string, bytes, a number, a
/// bool, `None` or `...`.
fn plain_literal(expr: &Expr) -> bool {
    matches!(
        expr,
        Expr::StringLiteral(_)
            | Expr::BytesLiteral(_)
            | Expr::NumberLiteral(_)
            | Expr::BooleanLiteral(_)
            | Expr::NoneLiteral(_)
            | Expr::EllipsisLiteral(_)
    )
}

/// Whether a call has a `*` or `**` argument: what it hands on no slot names.
fn has_star_arguments(call: &ast::ExprCall) -> bool {
    call.arguments.args.iter().any(Expr::is_starred_expr)
        || call.arguments.keywords.iter().any(|k| k.arg.is_none())
}

/// The names a target binds: a name, or each name of an unpacking.
fn target_names<'a>(target: &'a Expr, out: &mut Vec<&'a str>) {
    match target {
        Expr::Name(name) => out.push(name.id.as_str()),
        Expr::Tuple(t) => t.elts.iter().for_each(|e| target_names(e, out)),
        Expr::List(l) => l.elts.iter().for_each(|e| target_names(e, out)),
        Expr::Starred(s) => target_names(&s.value, out),
        _ => {}
    }
}

/// The blocks a statement holds that run in its own scope (not a `def` or a `class` body).
fn nested_bodies(stmt: &Stmt) -> Vec<&[Stmt]> {
    match stmt {
        Stmt::If(i) => {
            let mut out = vec![i.body.as_slice()];
            out.extend(i.elif_else_clauses.iter().map(|c| c.body.as_slice()));
            out
        }
        Stmt::For(f) => vec![&f.body, &f.orelse],
        Stmt::While(w) => vec![&w.body, &w.orelse],
        Stmt::With(w) => vec![&w.body],
        Stmt::Try(t) => {
            let mut out = vec![t.body.as_slice()];
            out.extend(t.handlers.iter().map(|h| {
                let ast::ExceptHandler::ExceptHandler(h) = h;
                h.body.as_slice()
            }));
            out.push(&t.orelse);
            out.push(&t.finalbody);
            out
        }
        Stmt::Match(m) => m.cases.iter().map(|c| c.body.as_slice()).collect(),
        _ => Vec::new(),
    }
}

/// The names a body declares `global`, in its own statements and blocks (not nested scopes).
fn declared_global(body: &[Stmt]) -> HashSet<&str> {
    let mut out = HashSet::new();
    let mut stack = vec![body];
    while let Some(suite) = stack.pop() {
        for stmt in suite {
            if let Stmt::Global(g) = stmt {
                out.extend(g.names.iter().map(|n| n.as_str()));
            }
            stack.extend(nested_bodies(stmt));
        }
    }
    out
}

/// The names a body declares `nonlocal`, as `declared_global`.
fn declared_nonlocal(body: &[Stmt]) -> HashSet<&str> {
    let mut out = HashSet::new();
    let mut stack = vec![body];
    while let Some(suite) = stack.pop() {
        for stmt in suite {
            if let Stmt::Nonlocal(n) = stmt {
                out.extend(n.names.iter().map(|n| n.as_str()));
            }
            stack.extend(nested_bodies(stmt));
        }
    }
    out
}

/// Every name one statement binds itself, by every form of the Language Reference's list
/// ("Binding of names"), not counting the statements of its blocks: the names of its targets
/// (an assignment, `for`, `with`, `del`), a walrus anywhere in its expressions (outside a
/// lambda), `except ... as`, capture patterns, imports, `def`, `class` and `type`. An annotation
/// with no value binds nothing.
fn statement_bindings<'a>(stmt: &'a Stmt, out: &mut Vec<&'a str>) {
    let mut names = BoundNames { out: Vec::new() };
    match stmt {
        Stmt::Assign(a) => {
            a.targets.iter().for_each(|t| names.visit_expr(t));
            names.visit_expr(&a.value);
        }
        Stmt::AnnAssign(a) => {
            if let Some(value) = &a.value {
                names.visit_expr(&a.target);
                names.visit_expr(value);
            }
        }
        Stmt::AugAssign(a) => {
            names.visit_expr(&a.target);
            names.visit_expr(&a.value);
        }
        Stmt::For(f) => {
            names.visit_expr(&f.target);
            names.visit_expr(&f.iter);
        }
        Stmt::With(w) => {
            for item in &w.items {
                names.visit_expr(&item.context_expr);
                if let Some(vars) = &item.optional_vars {
                    names.visit_expr(vars);
                }
            }
        }
        Stmt::Delete(d) => d.targets.iter().for_each(|t| names.visit_expr(t)),
        Stmt::Try(t) => {
            for handler in &t.handlers {
                let ast::ExceptHandler::ExceptHandler(h) = handler;
                if let Some(kind) = &h.type_ {
                    names.visit_expr(kind);
                }
                if let Some(name) = &h.name {
                    names.out.push(name.as_str());
                }
            }
        }
        Stmt::Match(m) => {
            names.visit_expr(&m.subject);
            for case in &m.cases {
                names.visit_pattern(&case.pattern);
                if let Some(guard) = &case.guard {
                    names.visit_expr(guard);
                }
            }
        }
        Stmt::Import(i) => {
            for alias in &i.names {
                let name = alias.name.as_str();
                let bound = alias.asname.as_ref().map(|a| a.as_str());
                names
                    .out
                    .push(bound.unwrap_or(name.split('.').next().unwrap_or(name)));
            }
        }
        Stmt::ImportFrom(i) => {
            for alias in &i.names {
                if alias.name.as_str() != "*" {
                    let bound = alias.asname.as_ref().unwrap_or(&alias.name);
                    names.out.push(bound.as_str());
                }
            }
        }
        Stmt::FunctionDef(f) => {
            names.out.push(f.name.as_str());
            f.decorator_list
                .iter()
                .for_each(|d| names.visit_expr(&d.expression));
        }
        Stmt::ClassDef(c) => {
            names.out.push(c.name.as_str());
            c.decorator_list
                .iter()
                .for_each(|d| names.visit_expr(&d.expression));
            if let Some(arguments) = &c.arguments {
                arguments.args.iter().for_each(|a| names.visit_expr(a));
            }
        }
        Stmt::TypeAlias(t) => names.visit_expr(&t.name),
        Stmt::Expr(e) => names.visit_expr(&e.value),
        Stmt::Return(r) => r.value.iter().for_each(|v| names.visit_expr(v)),
        Stmt::If(i) => {
            names.visit_expr(&i.test);
            for clause in &i.elif_else_clauses {
                clause.test.iter().for_each(|t| names.visit_expr(t));
            }
        }
        Stmt::While(w) => names.visit_expr(&w.test),
        Stmt::Assert(a) => {
            names.visit_expr(&a.test);
            a.msg.iter().for_each(|m| names.visit_expr(m));
        }
        Stmt::Raise(r) => {
            r.exc.iter().for_each(|e| names.visit_expr(e));
            r.cause.iter().for_each(|c| names.visit_expr(c));
        }
        _ => {}
    }
    out.extend(names.out);
}

/// The names stored or deleted in an expression, and the targets of a walrus in it, outside a
/// lambda and a comprehension's own targets (`statement_bindings`).
struct BoundNames<'a> {
    out: Vec<&'a str>,
}

impl<'a> Visitor<'a> for BoundNames<'a> {
    fn visit_expr(&mut self, expr: &'a Expr) {
        match expr {
            Expr::Name(name) if matches!(name.ctx, ExprContext::Store | ExprContext::Del) => {
                self.out.push(name.id.as_str());
            }
            Expr::Lambda(_) => return,
            Expr::ListComp(ast::ExprListComp {
                elt, generators, ..
            })
            | Expr::SetComp(ast::ExprSetComp {
                elt, generators, ..
            })
            | Expr::Generator(ast::ExprGenerator {
                elt, generators, ..
            }) => {
                self.visit_expr(elt);
                self.generators(generators);
                return;
            }
            Expr::DictComp(c) => {
                if let Some(key) = &c.key {
                    self.visit_expr(key);
                }
                self.visit_expr(&c.value);
                self.generators(&c.generators);
                return;
            }
            _ => {}
        }
        visitor::walk_expr(self, expr);
    }

    fn visit_pattern(&mut self, pattern: &'a Pattern) {
        match pattern {
            Pattern::MatchAs(p) => p.name.iter().for_each(|n| self.out.push(n.as_str())),
            Pattern::MatchStar(p) => p.name.iter().for_each(|n| self.out.push(n.as_str())),
            Pattern::MatchMapping(p) => p.rest.iter().for_each(|n| self.out.push(n.as_str())),
            _ => {}
        }
        visitor::walk_pattern(self, pattern);
    }
}

impl<'a> BoundNames<'a> {
    /// A comprehension's iterables and conditions (a walrus there binds the enclosing scope);
    /// its targets are its own.
    fn generators(&mut self, generators: &'a [ast::Comprehension]) {
        for generator in generators {
            self.visit_expr(&generator.iter);
            generator.ifs.iter().for_each(|i| self.visit_expr(i));
        }
    }
}

/// What a use of an identity starts as (`Walker::start`): a record holds, an item keeps its
/// provenance, else the value itself.
fn shape_start(shape: &Shape) -> Start {
    if shape.record {
        return Start::Record;
    }
    if let Some((source, scope)) = &shape.item_of {
        return Start::Item {
            source: source.clone(),
            scope: *scope,
        };
    }
    Start::Value {
        builtin: shape.builtin,
    }
}

/// T2: the value bound to names (comma-separated) is an alias of it; any other target escapes.
fn alias<'a>(targets: &[Expr]) -> Outcome<'a> {
    let names: Option<Vec<&str>> = (targets.iter())
        .map(|target| match target {
            Expr::Name(name) => Some(name.id.as_str()),
            _ => None,
        })
        .collect();
    match names {
        Some(names) => Outcome::Alias(names.join(",")),
        None => Outcome::Escape,
    }
}

/// Read when an entry accepts the site, else it escapes.
fn read_if<'a>(read: bool) -> Outcome<'a> {
    if read { Outcome::Read } else { Outcome::Escape }
}

/// `"...".format(...)` on a string literal (T12).
fn is_format_call(call: &ast::ExprCall) -> bool {
    matches!(call.func.as_ref(), Expr::Attribute(method)
        if method.attr.as_str() == "format" && method.value.is_string_literal_expr())
}
