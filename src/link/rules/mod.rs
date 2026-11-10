//! Rule evaluation over the link (D39, phase N1): labels, kinds and exports of the run, and
//! `EdgeRule.decide` for every candidate edge and each of its destinations, with no grouping.
//!
//! Python hands over a plan (`plan.rs`): the config as data. The evaluator reads the graph's
//! modules, symbols and edges from the link, computes every module's labels once, and judges
//! edges in parallel chunks; the output is the same at any thread count. The Python engine stays
//! the meaning: each function here names the Python code it follows.

pub mod conflicts;
pub mod edges;
pub mod env;
pub mod glob;
pub mod labels;
pub mod plan;
pub mod reach;
pub mod selector;
pub mod signature;
pub mod staged;
pub mod symbols;
pub mod value;

use self::conflicts::{Conflict, conflict_key};
use self::env::{Cx, Endpoint, Env, Role};
use self::labels::{Assign, Cell, Domain, Export, Field, FieldKind, Labels};
use self::plan::{Mode, Plan, RulePlan, SetValue};
use self::selector::{Compiler, EdgeField, EndField, Node, Operand, Reading, Ref};
use self::signature::Signature;
use self::staged::{Staged, parts_truth, truth};
use self::symbols::{KindSets, SymbolFacts};
use self::value::{Truth, Value, any_of, render};
use super::Link;
use super::intern::Sid;
use rayon::prelude::*;
use rustc_hash::{FxHashMap, FxHashSet};
use std::sync::Mutex;

/// What a rule decides for one destination of one edge (`rules.base.Judgment`).
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Judgment {
    Clean = 0,
    Unresolved = 1,
    UnknownSelection = 2,
    UnknownViolation = 3,
    Violation = 4,
    /// A signature slot with no usable type.
    UnknownType = 5,
}

/// One destination of one edge a rule does not judge clean: what Python needs to make its
/// finding without judging again.
pub struct Row {
    pub edge: u32,
    /// The destination's place among the edge's destinations.
    pub target: u32,
    pub judgment: Judgment,
    /// The identity columns of a violation or an unknown type, rendered as `rules.base.project`
    /// renders them; empty for any other judgment, which the rule's own code reports.
    pub identity: Vec<String>,
    /// The source module (the finding's owner module) and the destination's module.
    pub src_module: Sid,
    pub dst_module: Option<Sid>,
}

pub struct Rule {
    pub id: String,
    pub mode: Mode,
    kinds: Vec<String>,
    include: u8,
    exclude: u8,
    role: Role,
    selection: Vec<Node>,
    /// The selection in the order and parts the Python engine judges it.
    staged: Staged,
    deny: Option<Node>,
    allow: Option<Node>,
    require: Option<Node>,
    unless: Option<Node>,
    default_allow: bool,
    identity: Vec<Ref>,
    /// A cycle rule's group field on each end.
    group: Option<(Ref, Ref)>,
    /// A reach rule's `from`.
    start: Option<Node>,
    /// What a signature rule adds to an edge rule.
    signature: Option<Signature>,
}

/// One destination a cycle rule cannot leave alone: its edge and place, its judgment
/// (unresolved, unknown selection, or a violation for a pair), and for a pair the two groups.
pub struct PairRow {
    pub edge: u32,
    pub target: u32,
    pub judgment: Judgment,
    pub pair: Option<(String, String)>,
}

/// A module's place in a value column where reading the value meets a provider conflict, and
/// in the registry column where no registry holds the module.
pub const NO_PLACE: u32 = u32::MAX;

/// `dst.exported`, the predicate of an entrypoint rule with none of its own.
const EXPORTED: Node = Node::Bare(Operand::Ref(Ref::Dst(EndField::Exported)));

/// Edges per parallel task.
const CHUNK: usize = 4096;

/// The tables of one run and its compiled rules.
pub struct Rules {
    /// The graph's modules (`graph.modules`): each one's record, and each module's place.
    modules: Vec<Sid>,
    module_files: Vec<u32>,
    pub module_at: FxHashMap<Sid, u32>,
    /// The attr names that start with `src.` or `dst.`: an edge with one overrides its
    /// endpoints' labels.
    pub endpoint_keys: FxHashSet<Sid>,
    pub labels: Labels,
    /// `values[field][module]`: `Labeler.field_value` of each module field on each module.
    pub values: Vec<Vec<Cell>>,
    pub exports: Vec<Export>,
    pub kind_names: Vec<String>,
    pub kind_sets: KindSets,
    facts: SymbolFacts,
    /// Per symbol assignment and field it sets, its candidate.
    symbol_assigns: Vec<usize>,
    pub rules: Vec<Rule>,
    /// Each edge's ordinal, when something reads `edge.ordinal`.
    ordinals: Vec<u32>,
    pool: rayon::ThreadPool,
    /// Whether the config has a label that is a conflict only where it is read: reads then
    /// follow the Python engine's (`staged.rs`).
    pub touch: bool,
    /// (field, module) whose value reads provider labels of the module that are set two ways.
    tainted: FxHashSet<(usize, u32)>,
    /// The conflicts rules met while they ran.
    met: Mutex<FxHashSet<Conflict>>,
}

fn role_of(name: &str) -> Result<Role, String> {
    match name {
        "origin" => Ok(Role::Origin),
        "surface" => Ok(Role::Surface),
        "via" => Ok(Role::Via),
        other => Err(format!(
            "a rule endpoint the evaluator does not know: {other}"
        )),
    }
}

fn field_kind(name: &str) -> FieldKind {
    match name {
        "set" => FieldKind::Set,
        "zone" => FieldKind::Zone,
        _ => FieldKind::Other,
    }
}

/// Whether a ref reads one of the edge fields a primitive redefines (`shape`, `label`).
fn redefined(field: Ref, names: &[String]) -> bool {
    let name = match field {
        Ref::Edge(EdgeField::Shape) => "shape",
        Ref::Edge(EdgeField::Label) => "label",
        _ => return false,
    };
    names.iter().any(|redefined| redefined == name)
}

impl Rules {
    pub fn new(link: &mut Link, text: &str, threads: usize) -> Result<Rules, String> {
        let plan = Plan::parse(text)?;
        let (modules, module_files) = Self::graph_modules(link);
        if modules.len() != plan.module_count {
            return Err(format!(
                "the link holds {} graph modules, the graph {}",
                modules.len(),
                plan.module_count
            ));
        }
        let facts = SymbolFacts::new(link);
        let kind_sets = facts.kind_sets(link, &plan.kinds);
        let names: Vec<String> = plan.fields.iter().map(|field| field.name.clone()).collect();
        let entity = Compiler {
            fields: &names,
            it: &link.core.it,
            reading: Reading::Entity,
        };
        let edge = Compiler {
            fields: &names,
            it: &link.core.it,
            reading: Reading::Edge,
        };
        let mut fields = Vec::with_capacity(plan.fields.len());
        for field in &plan.fields {
            let domain = match &field.domain {
                None => None,
                Some(domain) => Some(Domain {
                    everywhere: domain.everywhere,
                    globs: domain
                        .globs
                        .iter()
                        .map(|glob| glob::Glob::new(glob))
                        .collect::<Result<_, _>>()?,
                    selector: domain
                        .selector
                        .as_ref()
                        .map(|node| entity.node(node))
                        .transpose()?,
                }),
            };
            fields.push(Field {
                name: field.name.clone(),
                kind: field_kind(&field.kind),
                on_symbol: field.on_symbol,
                provided: field.provided,
                domain,
            });
        }
        let exports = plan
            .exports
            .iter()
            .map(|export| {
                Export::new(
                    export,
                    export
                        .audience
                        .as_ref()
                        .map(|node| edge.node(node))
                        .transpose()?,
                )
            })
            .collect::<Result<Vec<_>, String>>()?;
        let rules = plan
            .rules
            .iter()
            .map(|rule| Self::compile(&edge, rule, &fields))
            .collect::<Result<Vec<_>, String>>()?;
        let it = &link.core.it;
        let module_names: Vec<&str> = modules.iter().map(|module| it.s(*module)).collect();
        if crate::sha256_hex(module_names.join("\n").as_bytes()) != plan.modules {
            return Err(
                "the rule plan lists the graph's modules in another order than the link".into(),
            );
        }
        let paths: Vec<&str> = module_files
            .iter()
            .map(|file| &*link.core.facts.files[*file as usize].path)
            .collect();
        let mut labels = Labels::new(&plan, fields, &paths, &module_names, &exports)?;
        for rule in &rules {
            check_columns(&labels, &format!("rule {}", rule.id), &rule.refs())?;
        }
        for audience in exports.iter().filter_map(|export| export.audience.as_ref()) {
            check_columns(&labels, "an export audience", &refs_of(audience))?;
        }
        for field in &labels.fields {
            let domain = field.domain.as_ref();
            if let Some(selector) = domain.and_then(|domain| domain.selector.as_ref()) {
                let reader = format!("the domain of field {}", field.name);
                check_columns(&labels, &reader, &refs_of(selector))?;
            }
        }
        let symbol_assigns: Vec<usize> = (0..labels.assigns.len())
            .filter(|at| labels.assigns[*at].symbols.is_some())
            .collect();
        for &at in &symbol_assigns {
            let names: Vec<String> = (0..labels.fields.len())
                .filter(|field| labels.fields[*field].kind == FieldKind::Zone)
                .flat_map(|field| labels.assigns[at].sets_of(field))
                .filter_map(|value| match value {
                    SetValue::Text(text) | SetValue::Other(text, _) => Some(text.clone()),
                    SetValue::Bool(flag) => Some(if *flag { "True" } else { "False" }.to_string()),
                    SetValue::Items(_) => None,
                })
                .collect();
            for name in names {
                labels.zones.code(&name);
            }
        }
        let module_at = modules
            .iter()
            .enumerate()
            .map(|(at, module)| (*module, at as u32))
            .collect();
        let reads_ordinal = rules.iter().any(Rule::reads_ordinal)
            || exports.iter().any(|export| {
                export
                    .audience
                    .as_ref()
                    .is_some_and(|node| node.reads(is_ordinal))
            });
        let pool = rayon::ThreadPoolBuilder::new()
            .num_threads(threads)
            .build()
            .map_err(|error| error.to_string())?;
        let endpoint_keys = Self::endpoint_keys(link);
        let mut out = Rules {
            endpoint_keys,
            modules,
            module_files,
            module_at,
            labels,
            values: Vec::new(),
            exports,
            kind_names: plan.kinds.iter().map(|kind| kind.name.clone()).collect(),
            kind_sets,
            facts,
            symbol_assigns,
            rules,
            ordinals: Vec::new(),
            pool,
            touch: false,
            tainted: FxHashSet::default(),
            met: Mutex::new(FxHashSet::default()),
        };
        out.tainted = out.provider_reads();
        out.values = out.module_values(link);
        out.touch = !out.tainted.is_empty() || !out.contested_fields().is_empty();
        if reads_ordinal {
            out.ordinals = ordinals(link, &plan.repeats);
        }
        Ok(out)
    }

    /// Every attr name of any edge, refinements included, that starts with `src.` or `dst.`.
    fn endpoint_keys(link: &Link) -> FxHashSet<Sid> {
        let edges = &link.edges;
        let refined = edges
            .refined
            .values()
            .flat_map(|refinement| refinement.attrs.iter());
        let keys: FxHashSet<Sid> = edges
            .attrs
            .iter()
            .chain(refined)
            .map(|(key, _)| *key)
            .collect();
        let it = &link.core.it;
        keys.into_iter()
            .filter(|key| {
                let name = it.s(*key);
                name.starts_with("src.") || name.starts_with("dst.")
            })
            .collect()
    }

    /// `graph.modules`: the modules with a file of the repo, in the module index's order.
    fn graph_modules(link: &Link) -> (Vec<Sid>, Vec<u32>) {
        let index = &link.core.index;
        let mut modules = Vec::new();
        let mut files = Vec::new();
        for module in &index.modules {
            let file = index.path_of[module];
            if !index.site[file as usize] {
                modules.push(*module);
                files.push(file);
            }
        }
        (modules, files)
    }

    fn compile(compiler: &Compiler, plan: &RulePlan, fields: &[Field]) -> Result<Rule, String> {
        let selection = compiler.nodes(&plan.selection)?;
        let redefined: Vec<bool> = selection
            .iter()
            .map(|node| node.reads(|field| redefined(field, &plan.redefines)))
            .collect();
        let staged = Staged::new(&selection, &redefined, fields);
        let optional = |node: &Option<plan::NodePlan>| {
            node.as_ref().map(|node| compiler.node(node)).transpose()
        };
        Ok(Rule {
            id: plan.id.clone(),
            mode: plan.mode,
            kinds: plan.kinds.clone(),
            include: plan.include,
            exclude: plan.exclude,
            role: role_of(&plan.endpoint)?,
            selection,
            staged,
            deny: optional(&plan.deny)?,
            allow: optional(&plan.allow)?,
            require: optional(&plan.require)?,
            unless: optional(&plan.unless)?,
            default_allow: plan.default_allow,
            identity: plan
                .identity
                .iter()
                .map(|path| compiler.reference(path))
                .collect(),
            start: optional(&plan.start)?,
            signature: plan.signature.then(|| Signature {
                unknown_types: plan.unknown_types.clone(),
                annotation: compiler.it.get("annotation"),
            }),
            group: plan.group.as_ref().map(|name| {
                let path = |root: &str| [root.to_string(), name.clone()];
                (
                    compiler.reference(&path("src")),
                    compiler.reference(&path("dst")),
                )
            }),
        })
    }

    pub fn module_path<'l>(&self, link: &'l Link, at: u32) -> &'l str {
        &link.core.facts.files[self.module_files[at as usize] as usize].path
    }

    pub fn module_name<'l>(&self, link: &'l Link, at: u32) -> &'l str {
        link.core.it.s(self.modules[at as usize])
    }

    /// (field, module) of every provider-set module field on a module whose provider labels
    /// are set two ways: reading any of them reads all of the module's (`Labeler.label_provided`).
    fn provider_reads(&self) -> FxHashSet<(usize, u32)> {
        let labels = &self.labels;
        let mut out = FxHashSet::default();
        for (at, _) in labels
            .provider_conflicts
            .iter()
            .enumerate()
            .filter(|(_, set)| **set)
        {
            for (field, spec) in labels.fields.iter().enumerate() {
                if spec.provided && !spec.on_symbol {
                    out.insert((field, at as u32));
                }
            }
        }
        out
    }

    /// `values` of every module field (`Labeler.compute_value`): the label, else unknown where
    /// the provider failed, else what the domain says. A value whose domain read provider labels
    /// that are set two ways is tainted: reading it meets that conflict, as in Python, where the
    /// domain is read only when the value is.
    fn module_values(&mut self, link: &Link) -> Vec<Vec<Cell>> {
        let cx = Cx { link, rules: self };
        let count = self.modules.len();
        let mut out = Vec::with_capacity(self.labels.fields.len());
        let mut tainted = Vec::new();
        for (field, spec) in self.labels.fields.iter().enumerate() {
            if spec.on_symbol {
                out.push(Vec::new());
                continue;
            }
            let column = (0..count as u32)
                .map(|at| match &self.labels.labels[field][at as usize] {
                    Some(label) => label.clone(),
                    None if self.labels.failed[field] => Cell::Unknown,
                    None => {
                        let module = Some(self.modules[at as usize]);
                        let (value, read) = cx.domain_value_read(field, module, Some(at), None);
                        if read {
                            tainted.push((field, at));
                        }
                        match value {
                            Value::Unknown => Cell::Unknown,
                            _ => Cell::None,
                        }
                    }
                })
                .collect();
            out.push(column);
        }
        self.tainted.extend(tainted);
        out
    }

    pub fn ordinal(&self, id: u32) -> u32 {
        self.ordinals.get(id as usize).copied().unwrap_or(0)
    }

    pub fn has_symbol(&self, link: &Link, name: &str) -> bool {
        SymbolFacts::has(link, name)
    }

    pub fn symbol_decl<'l>(&self, link: &'l Link, name: &str) -> &'l str {
        SymbolFacts::decl(link, name)
    }

    pub fn symbol_fact<'l>(&self, link: &'l Link, name: &str, key: &str) -> &'l str {
        SymbolFacts::fact(link, name, key)
    }

    /// Whether a symbol belongs to an export that decides by class (`Labeler.find_export_member`
    /// past the path test): a listed base itself or a class that inherits one; unknown when a
    /// base on the way up did not resolve.
    pub fn member_by_class(&self, link: &Link, bases: &[String], symbol: &str) -> Truth {
        if bases.iter().any(|base| base == symbol) {
            return Truth::True;
        }
        let (ancestors, complete) = self.facts.ancestors(link, symbol);
        if bases.iter().any(|base| ancestors.contains(base.as_str())) {
            return Truth::True;
        }
        if complete {
            Truth::False
        } else {
            Truth::Unknown
        }
    }

    /// The label symbol assignments give a symbol's field (`Labeler.label_symbol`), as a value.
    /// Their values are fixed: a symbol assignment fills no template.
    pub fn symbol_label<'a>(
        &'a self,
        link: &Link,
        symbol: &str,
        field: usize,
    ) -> Option<Value<'a>> {
        if self.symbol_assigns.is_empty() || symbol.is_empty() {
            return None;
        }
        let cx = Cx { link, rules: self };
        let path = cx
            .module_of_symbol(symbol)
            .map(|(_, at)| self.module_path(link, at));
        let mut candidates: Vec<(&'a SetValue, bool)> = Vec::new();
        for &at in &self.symbol_assigns {
            let assign = &self.labels.assigns[at];
            if self.symbol_matches(link, assign, symbol, path) {
                candidates.extend(assign.sets_of(field).map(|value| (value, assign.overrides)));
            }
        }
        if candidates.is_empty() {
            return None;
        }
        let spec = &self.labels.fields[field];
        if spec.kind == FieldKind::Set {
            let mut items: Vec<&'a str> = Vec::new();
            for (value, _) in &candidates {
                match value {
                    SetValue::Items(found) => items.extend(found.iter().map(String::as_str)),
                    SetValue::Text(text) | SetValue::Other(text, _) => items.push(text),
                    SetValue::Bool(flag) => items.push(if *flag { "True" } else { "False" }),
                }
            }
            items.sort_unstable();
            items.dedup();
            return Some(Value::Set(items));
        }
        let overriding: Vec<&SetValue> = candidates
            .iter()
            .filter(|(_, overrides)| *overrides)
            .map(|(value, _)| *value)
            .collect();
        let pool: Vec<&SetValue> = if overriding.is_empty() {
            candidates.iter().map(|(value, _)| *value).collect()
        } else {
            overriding
        };
        if pool
            .iter()
            .any(|value| conflict_key(value) != conflict_key(pool[0]))
        {
            self.meet(Conflict::Symbol(symbol.to_string(), field));
            return None;
        }
        let text: &'a str = match pool[0] {
            SetValue::Bool(flag) if spec.kind != FieldKind::Zone => {
                return Some(Value::Bool(*flag));
            }
            SetValue::Bool(flag) => {
                if *flag {
                    "True"
                } else {
                    "False"
                }
            }
            SetValue::Text(text) | SetValue::Other(text, _) => text,
            SetValue::Items(_) => return Some(Value::Unknown),
        };
        if spec.kind == FieldKind::Zone {
            let code = self.labels.zones.code_of(text)?;
            return Some(Value::Zone(&self.labels.zones.infos[code as usize]));
        }
        Some(Value::text(text))
    }

    /// `Labeler.symbol_matches`.
    pub fn symbol_matches(
        &self,
        link: &Link,
        assign: &Assign,
        symbol: &str,
        path: Option<&str>,
    ) -> bool {
        let Some((decorated_by, subclass_of)) = &assign.symbols else {
            return false;
        };
        if !assign.allows(path) {
            return false;
        }
        if !decorated_by.is_empty() {
            let decorators = self.facts.decorators(link, symbol);
            if !decorated_by
                .iter()
                .any(|name| decorators.contains(&name.as_str()))
            {
                return false;
            }
        }
        if !subclass_of.is_empty() {
            let (ancestors, _) = self.facts.ancestors(link, symbol);
            if !subclass_of
                .iter()
                .any(|name| ancestors.contains(name.as_str()))
            {
                return false;
            }
        }
        true
    }

    /// The kinds a rule reads as interned names, in its order; every kind when it names none.
    fn kind_sids(&self, link: &Link, rule: &Rule) -> Vec<Sid> {
        if rule.kinds.is_empty() {
            return link.kind_order();
        }
        rule.kinds
            .iter()
            .filter_map(|kind| link.kind_sid(kind))
            .collect()
    }

    /// The edges a rule sees (`EdgeRule.sees`), in id order.
    fn candidates(&self, link: &mut Link, rule: &Rule) -> Vec<u32> {
        let kinds: Vec<Sid> = rule
            .kinds
            .iter()
            .filter_map(|kind| link.kind_sid(kind))
            .collect();
        if kinds.is_empty() && !rule.kinds.is_empty() {
            return Vec::new();
        }
        link.candidates(&kinds, rule.include, rule.exclude)
    }

    /// Runs `judge` over every candidate of a rule, chunk by chunk on the pool, and returns what
    /// each chunk found in id order.
    fn over_candidates<'a, T: Send>(
        &'a self,
        link: &'a Link,
        ids: &[u32],
        judge: impl Fn(&Cx<'a>, u32, &mut Vec<Endpoint<'a>>, &mut Vec<T>) + Sync,
    ) -> Vec<T> {
        let cx = Cx { link, rules: self };
        let chunks: Vec<&[u32]> = ids.chunks(CHUNK).collect();
        let found: Vec<Vec<T>> = self.pool.install(|| {
            chunks
                .par_iter()
                .map(|chunk| {
                    let mut dsts = Vec::with_capacity(2);
                    let mut out = Vec::new();
                    for &id in *chunk {
                        judge(&cx, id, &mut dsts, &mut out);
                    }
                    out
                })
                .collect()
        });
        found.into_iter().flatten().collect()
    }

    /// Positions into `edges` (distinct ids) in the order the rule judges its edges.
    fn order(&self, link: &mut Link, rule: &Rule, edges: &[u32]) -> Result<Vec<u32>, String> {
        let kinds = self.kind_sids(link, rule);
        link.rule_order(edges, &kinds).ok_or_else(|| {
            format!(
                "rule {}: an edge whose kind is not among its kinds",
                rule.id
            )
        })
    }

    /// Every destination of every candidate edge that rule `index` does not judge clean, in the
    /// order the Python engine judges them.
    pub fn judge(&self, link: &mut Link, index: usize) -> Result<Vec<Row>, String> {
        let rule = self.rule(index, Mode::Judge)?;
        self.read_sources(link, rule);
        let mut ids = self.candidates(link, rule);
        if let Some(signature) = &rule.signature {
            ids = signature.seen(link, ids);
        }
        let rows = self.over_candidates(link, &ids, |cx, id, dsts, out| {
            let src = cx.src_endpoint(id);
            cx.dst_endpoints(id, rule.role, dsts);
            self.touch_sources(cx, rule, id, src, dsts);
            for (target, dst) in dsts.iter().enumerate() {
                let env = Env::new(*cx, Some(id), src, *dst);
                let judgment = decide(rule, &env);
                if judgment == Judgment::Clean {
                    continue;
                }
                let identity = if matches!(judgment, Judgment::Violation | Judgment::UnknownType) {
                    rule.identity
                        .iter()
                        .map(|field| render(&evaluate_ref(*field, &env)))
                        .collect()
                } else {
                    Vec::new()
                };
                out.push(Row {
                    edge: id,
                    target: target as u32,
                    judgment,
                    identity,
                    src_module: src.module.unwrap_or_default(),
                    dst_module: dst.module,
                });
            }
        });
        let mut edges: Vec<u32> = rows.iter().map(|row| row.edge).collect();
        edges.dedup();
        let order = self.order(link, rule, &edges)?;
        Ok(in_order(rows, &edges, &order, |row| row.edge))
    }

    /// The candidate edges of rule `index` whose selection is not false for some destination,
    /// in the order the Python engine judges them. Only conjuncts that read nothing the
    /// primitive's own environment sets can drop an edge.
    pub fn select(&self, link: &mut Link, index: usize) -> Result<Vec<u32>, String> {
        let rule = self.rule(index, Mode::Select)?;
        self.read_sources(link, rule);
        let ids = self.candidates(link, rule);
        let kept = self.over_candidates(link, &ids, |cx, id, dsts, out| {
            let src = cx.src_endpoint(id);
            cx.dst_endpoints(id, rule.role, dsts);
            self.touch_sources(cx, rule, id, src, dsts);
            let mut selected = false;
            for dst in dsts.iter() {
                let env = Env::new(*cx, Some(id), src, *dst);
                selected |= rule.staged.truth(&env, true) != Truth::False;
                // Python reads every destination of an edge before it judges any.
                if selected && !self.touch {
                    break;
                }
            }
            if selected {
                out.push(id);
            }
        });
        let order = self.order(link, rule, &kept)?;
        Ok(order.into_iter().map(|at| kept[at as usize]).collect())
    }

    /// What a cycle rule makes of every candidate destination (`CyclePrimitive.consider`), in
    /// the order the Python engine judges them: unresolved ones, ones whose selection is
    /// unknown, and the ones whose two groups are known and differ, with the groups.
    pub fn pairs(&self, link: &mut Link, index: usize) -> Result<Vec<PairRow>, String> {
        let rule = self.rule(index, Mode::Cycle)?;
        let Some((src_group, dst_group)) = rule.group else {
            return Err(format!("cycle rule {} has no group field", rule.id));
        };
        self.read_sources(link, rule);
        let ids = self.candidates(link, rule);
        let rows = self.over_candidates(link, &ids, |cx, id, dsts, out| {
            let src = cx.src_endpoint(id);
            cx.dst_endpoints(id, rule.role, dsts);
            self.touch_sources(cx, rule, id, src, dsts);
            for (target, dst) in dsts.iter().enumerate() {
                let env = Env::new(*cx, Some(id), src, *dst);
                let selected = rule.staged.truth(&env, false);
                let judgment = match selected {
                    Truth::False => continue,
                    _ if dst.unresolved => Judgment::Unresolved,
                    Truth::Unknown => Judgment::UnknownSelection,
                    Truth::True => Judgment::Violation,
                };
                let mut pair = None;
                if judgment == Judgment::Violation {
                    let (from, to) = (evaluate_ref(src_group, &env), evaluate_ref(dst_group, &env));
                    let known = |value: &Value| !matches!(value, Value::Unknown | Value::None);
                    if !known(&from) || !known(&to) {
                        continue;
                    }
                    // Most edges stay inside their group: same text, nothing to render.
                    if let (Value::Text(left), Value::Text(right)) = (&from, &to)
                        && left == right
                    {
                        continue;
                    }
                    let (from, to) = (render(&from), render(&to));
                    if from == to {
                        continue;
                    }
                    pair = Some((from, to));
                }
                out.push(PairRow {
                    edge: id,
                    target: target as u32,
                    judgment,
                    pair,
                });
            }
        });
        let mut edges: Vec<u32> = rows.iter().map(|row| row.edge).collect();
        edges.dedup();
        let order = self.order(link, rule, &edges)?;
        Ok(in_order(rows, &edges, &order, |row| row.edge))
    }

    /// In a config with labels that conflict only where read: the `src` parts of a rule that
    /// reads only module fields there, judged once per source module of its kinds, edges seen or
    /// not, as `StagedSelection.module_false` judges them before any edge.
    fn read_sources(&self, link: &mut Link, rule: &Rule) {
        if !self.touch || !rule.staged.src_module_level {
            return;
        }
        let mut modules = Vec::new();
        for kind in self.kind_sids(link, rule) {
            modules.extend(link.source_modules(kind));
        }
        let cx = Cx { link, rules: self };
        for module in modules {
            self.read_source(&cx, rule, module);
        }
    }

    /// In a config with labels that conflict only where read: the `src` parts of an edge with no
    /// destination, which `EdgeRule.edge_envs` reads before it reads any destination.
    fn touch_sources<'a>(
        &self,
        cx: &Cx<'a>,
        rule: &'a Rule,
        id: u32,
        src: Endpoint<'a>,
        dsts: &[Endpoint<'a>],
    ) {
        if self.touch && dsts.is_empty() {
            parts_truth(rule.staged.src(), &Env::new(*cx, Some(id), src, src));
        }
    }

    /// `StagedSelection.module_false` of one module, for the reads it makes.
    fn read_source(&self, cx: &Cx, rule: &Rule, module: Sid) {
        let endpoint = cx.module_endpoint(module);
        parts_truth(rule.staged.src(), &Env::new(*cx, None, endpoint, endpoint));
    }

    fn rule(&self, index: usize, mode: Mode) -> Result<&Rule, String> {
        let rule = self
            .rules
            .get(index)
            .ok_or_else(|| format!("no rule {index} in the plan"))?;
        if rule.mode != mode {
            return Err(format!(
                "rule {} is planned as {:?}, not {mode:?}",
                rule.id, rule.mode
            ));
        }
        Ok(rule)
    }

    /// The edges of `kinds`, kind by kind in graph order, that `selector` does not make false,
    /// each read with its source as both ends (`RuleContext.edges_passing`).
    pub fn passing(
        &self,
        link: &mut Link,
        kinds: &[String],
        selector: &str,
    ) -> Result<Vec<u32>, String> {
        let tree: plan::NodePlan = serde_json::from_str(selector)
            .map_err(|error| format!("a selector that does not parse: {error}"))?;
        let names: Vec<String> = self
            .labels
            .fields
            .iter()
            .map(|field| field.name.clone())
            .collect();
        let node = Compiler {
            fields: &names,
            it: &link.core.it,
            reading: Reading::Edge,
        }
        .node(&tree)?;
        check_columns(&self.labels, "a coverage prefilter", &refs_of(&node))?;
        let mut sids: Vec<Sid> = Vec::new();
        for kind in kinds.iter().filter_map(|kind| link.kind_sid(kind)) {
            if !sids.contains(&kind) {
                sids.push(kind);
            }
        }
        let mut out = Vec::new();
        for kind in sids {
            let ids = link.ids_of_kind(kind);
            out.extend(self.over_candidates(link, &ids, |cx, id, _, kept| {
                let src = cx.src_endpoint(id);
                if selector::evaluate(&node, &Env::new(*cx, Some(id), src, src)) != Truth::False {
                    kept.push(id);
                }
            }));
        }
        Ok(out)
    }

    /// A module field's value on every graph module (`Labeler.field_value`), for Python to read
    /// instead of labelling each module again: the distinct values, and each module's place
    /// among them. A module whose value reads provider labels set two ways has `NO_PLACE`:
    /// Python reads that one itself, to meet the conflict where the Python engine meets it.
    pub fn value_column(&self, name: &str) -> Result<(Vec<&Cell>, Vec<u32>), String> {
        let field = self
            .labels
            .fields
            .iter()
            .position(|field| field.name == name && !field.on_symbol)
            .ok_or_else(|| format!("no module field {name}"))?;
        let mut distinct: Vec<&Cell> = Vec::new();
        let mut places: FxHashMap<&Cell, u32> = FxHashMap::default();
        let mut codes = Vec::with_capacity(self.values[field].len());
        for (at, cell) in self.values[field].iter().enumerate() {
            if self.tainted.contains(&(field, at as u32)) {
                codes.push(NO_PLACE);
                continue;
            }
            let place = *places.entry(cell).or_insert_with(|| {
                distinct.push(cell);
                distinct.len() as u32 - 1
            });
            codes.push(place);
        }
        Ok((distinct, codes))
    }

    /// Each graph module's registry, by its place in the plan's registries; `NO_PLACE` where
    /// none holds the module.
    pub fn registry_column(&self) -> Vec<u32> {
        self.labels
            .registry_of
            .iter()
            .map(|found| found.map_or(NO_PLACE, u32::from))
            .collect()
    }

    /// One field's value on every graph module, rendered as `render_value` does, with zones as
    /// `name|chain`: what the label proof compares.
    pub fn module_column(&self, link: &Link, name: &str) -> Result<Vec<String>, String> {
        let cx = Cx { link, rules: self };
        if name == "registry" || name == "lifecycle" {
            let lifecycle = name == "lifecycle";
            return Ok((0..self.modules.len() as u32)
                .map(|at| {
                    let found = self.labels.registry_of[at as usize];
                    match found {
                        None => "-".to_string(),
                        Some(registry) if lifecycle => {
                            let text = &self.labels.registries[registry as usize].lifecycle;
                            if text.is_empty() {
                                "-".to_string()
                            } else {
                                text.clone()
                            }
                        }
                        Some(registry) => self.labels.registries[registry as usize].name.clone(),
                    }
                })
                .collect());
        }
        let field = self
            .labels
            .fields
            .iter()
            .position(|field| field.name == name)
            .ok_or_else(|| format!("no field {name}"))?;
        Ok(self.values[field]
            .iter()
            .map(|cell| match cx.cell(cell) {
                Value::Zone(zone) => format!("{}|{}", zone.name, zone.chain.join(",")),
                value => render(&value),
            })
            .collect())
    }
}

impl Rule {
    fn nodes(&self) -> impl Iterator<Item = &Node> {
        self.selection.iter().chain(
            [&self.deny, &self.allow, &self.require, &self.unless]
                .into_iter()
                .flatten(),
        )
    }

    fn reads_ordinal(&self) -> bool {
        self.nodes().any(|node| node.reads(is_ordinal))
            || self.identity.iter().any(|field| is_ordinal(*field))
    }

    /// Every ref the rule reads: its selectors, `from`, identity columns and group field.
    fn refs(&self) -> Vec<Ref> {
        let mut out = Vec::new();
        for node in self.nodes().chain(&self.start) {
            node.refs(&mut out);
        }
        out.extend(&self.identity);
        if let Some((src, dst)) = self.group {
            out.extend([src, dst]);
        }
        out
    }
}

fn refs_of(node: &Node) -> Vec<Ref> {
    let mut out = Vec::new();
    node.refs(&mut out);
    out
}

/// D43: a reader of a module field a Python provider sets needs that field's column in the
/// plan. Without it the evaluator would read no label at all, a clean answer nothing checks.
fn check_columns(labels: &Labels, reader: &str, refs: &[Ref]) -> Result<(), String> {
    for field in refs.iter().filter_map(|field| field.field()) {
        let spec = &labels.fields[field];
        if spec.provided && !spec.on_symbol && !labels.columns[field] {
            return Err(format!(
                "{reader} reads field {}, which a provider sets, but the rule plan holds no \
                 column for it",
                spec.name
            ));
        }
    }
    Ok(())
}

/// `rows`, grouped by edge in id order (`edges` lists each edge once), with the edges put in
/// `order` (positions into `edges`); an edge's rows keep their order.
fn in_order<T>(rows: Vec<T>, edges: &[u32], order: &[u32], edge_of: impl Fn(&T) -> u32) -> Vec<T> {
    let mut starts = Vec::with_capacity(edges.len() + 1);
    let mut at = 0;
    for &edge in edges {
        starts.push(at);
        while at < rows.len() && edge_of(&rows[at]) == edge {
            at += 1;
        }
    }
    starts.push(rows.len());
    let mut slots: Vec<Option<T>> = rows.into_iter().map(Some).collect();
    let mut out = Vec::with_capacity(slots.len());
    for &position in order {
        let position = position as usize;
        for slot in &mut slots[starts[position]..starts[position + 1]] {
            out.extend(slot.take());
        }
    }
    out
}

fn is_ordinal(field: Ref) -> bool {
    field == Ref::Edge(EdgeField::Ordinal)
}

fn evaluate_ref<'a>(field: Ref, env: &Env<'a>) -> Value<'a> {
    selector::Resolve::resolve(env, field)
}

/// `EdgeRule.decide` for one environment, and `SignaturePrimitive.decide` for a signature rule.
/// `deny`, `allow` and `require` are all judged, as `EdgeRule.violation` judges them.
pub fn decide<'a>(rule: &'a Rule, env: &Env<'a>) -> Judgment {
    let selected = rule.staged.truth(env, false);
    if selected == Truth::False {
        return Judgment::Clean;
    }
    if let (Some(signature), Some(id)) = (&rule.signature, env.edge)
        && !env.dst.unresolved
        && signature.unknown_type(&env.cx, id, &env.dst)
    {
        return Judgment::UnknownType;
    }
    if env.dst.unresolved {
        return Judgment::Unresolved;
    }
    if selected == Truth::Unknown {
        return Judgment::UnknownSelection;
    }
    let verdict = if rule.default_allow {
        truth(&EXPORTED, env).negate()
    } else {
        let mut parts = Vec::with_capacity(3);
        if let Some(deny) = &rule.deny {
            parts.push(truth(deny, env));
        }
        if let Some(allow) = &rule.allow {
            parts.push(truth(allow, env).negate());
        }
        if let Some(require) = &rule.require {
            parts.push(truth(require, env).negate());
        }
        if parts.is_empty() {
            Truth::True
        } else {
            any_of(parts)
        }
    };
    if verdict == Truth::False {
        return Judgment::Clean;
    }
    if let Some(unless) = &rule.unless
        && truth(unless, env) == Truth::True
    {
        return Judgment::Clean;
    }
    if verdict == Truth::Unknown {
        Judgment::UnknownViolation
    } else {
        Judgment::Violation
    }
}

/// Each edge's place among the edges with the same module, scope, kind and surface, in graph
/// order (`GraphIndex.ordinals`); an edge a producer repeats takes its last row's place.
fn ordinals(link: &Link, repeats: &[(u32, u32)]) -> Vec<u32> {
    let edges = &link.edges;
    let mut counters: FxHashMap<(Sid, Sid, Sid, Sid), u32> = FxHashMap::default();
    let mut out = Vec::with_capacity(edges.len());
    for id in 0..edges.len() as u32 {
        let (module, scope) = link.site_of(id);
        let counter = counters
            .entry((module, scope, edges.kind_of(id), edges.surface_of(id)))
            .or_insert(0);
        out.push(*counter);
        *counter += 1;
    }
    for &(row, last) in repeats {
        if let (Some(&place), true) = (out.get(last as usize), (row as usize) < out.len()) {
            out[row as usize] = place;
        }
    }
    out
}
