//! Rule evaluation over the link (D39, phase N1): labels, kinds and exports of the run, and
//! `EdgeRule.decide` for every candidate edge and each of its destinations, with no grouping.
//!
//! Python hands over a plan (`plan.rs`): the config as data. The evaluator reads the graph's
//! modules, symbols and edges from the link, computes every module's labels once, and judges
//! edges in parallel chunks; the output is the same at any thread count. The Python engine stays
//! the meaning: each function here names the Python code it follows.

pub mod conflicts;
pub mod coverage;
pub mod cycle;
pub mod edges;
pub mod entities;
pub mod env;
pub mod export;
pub mod glob;
pub mod labels;
pub mod plan;
pub mod reach;
pub mod selector;
pub mod signature;
pub mod staged;
pub mod symbols;
pub mod use_shape;
pub mod value;

use self::conflicts::Conflict;
use self::coverage::{Cover, CoverInputs, Required, cover_edges, required};
use self::entities::EntityRule;
use self::env::{Cx, Endpoint, Env, Role};
use self::export::{
    ExportInputs, ExportRow, ExportRule, Shape, Walk, binds_handed, module_scope_imports,
};
use self::labels::{Cell, Domain, Export, Field, FieldKind, Labels};
use self::plan::{
    CoreBasesPlan, EdgesPlan, ModePlan, NodePlan, Plan, RulePlan, SetValue, VerdictPlan,
    check_columns,
};
use self::selector::{Compiler, EdgeField, EndField, Node, Operand, Reading, Ref};
use self::signature::Signature;
use self::staged::{Staged, truth};
use self::symbols::{KindSets, SymbolFacts};
use self::use_shape::UseShape;
use self::value::{Truth, Value, any_of, render};
use super::Link;
use super::intern::{Interner, Sid};
use super::scope::{Res, ctx};
use rayon::prelude::*;
use rustc_hash::{FxHashMap, FxHashSet};
use std::hash::Hash;
use std::sync::{Mutex, OnceLock};

/// What an export module hands out by import, as `handed_zones` reads it.
pub enum Handed {
    /// Its `__all__` cannot be read, or a handed import did not resolve.
    Unknown,
    /// The modules the handed names come from: a graph module with its place, or a namespace
    /// directory with none.
    From(Vec<(Sid, Option<u32>)>),
}

/// The longest dotted prefix of `name` that is a graph module, and its place
/// (`GraphIndex.find_module`).
fn longest_module(
    it: &Interner,
    module_at: &FxHashMap<Sid, u32>,
    name: &str,
) -> Option<(Sid, u32)> {
    let mut head = name;
    loop {
        if let Some(sid) = it.get(head)
            && let Some(&at) = module_at.get(&sid)
        {
            return Some((sid, at));
        }
        head = head.rsplit_once('.')?.0;
    }
}

/// The longest dotted prefix of a first-party name that is a graph module or a directory without
/// `__init__.py` (`directories`: their ids, a directory two graph modules place apart included),
/// with the graph module's place (`Labeler.name_owner`).
fn name_owner(
    it: &Interner,
    module_at: &FxHashMap<Sid, u32>,
    directories: &FxHashSet<Sid>,
    name: &str,
) -> Option<(Sid, Option<u32>)> {
    let mut head = name;
    loop {
        if let Some(sid) = it.get(head) {
            if let Some(&at) = module_at.get(&sid) {
                return Some((sid, Some(at)));
            }
            if directories.contains(&sid) {
                return Some((sid, None));
            }
        }
        head = head.rsplit_once('.')?.0;
    }
}

/// The core shapes a class has by a base or a metaclass (`exports.SHAPE_BASES`), as sets: a
/// lineage is tested against each.
pub struct CoreBases {
    pub abc_metas: Vec<String>,
    pub shapes: Vec<(String, FxHashSet<String>)>,
}

impl CoreBases {
    fn new(plan: &CoreBasesPlan) -> CoreBases {
        CoreBases {
            abc_metas: plan.abc_metas.clone(),
            shapes: plan
                .bases
                .iter()
                .map(|(name, bases)| (name.clone(), bases.iter().cloned().collect()))
                .collect(),
        }
    }
}

/// What a rule decides for one destination of one edge (`rules.base.Judgment`).
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Judgment {
    Clean,
    Unresolved,
    UnknownSelection,
    UnknownViolation,
    Violation,
    /// A signature slot with no usable type.
    UnknownType,
}

impl Judgment {
    /// The judgment's name, its value in `rules.base.Judgment`.
    pub fn name(self) -> &'static str {
        match self {
            Judgment::Clean => "clean",
            Judgment::Unresolved => "unresolved",
            Judgment::UnknownSelection => "unknown-selection",
            Judgment::UnknownViolation => "unknown-violation",
            Judgment::Violation => "violation",
            Judgment::UnknownType => "unknown-type",
        }
    }

    /// Whether a row of this judgment carries the rule's identity: Python reports any other
    /// judgment with its own code.
    pub fn has_identity(self) -> bool {
        matches!(self, Judgment::Violation | Judgment::UnknownType)
    }
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

/// One compiled rule: its id, and its mode with what that mode reads.
pub struct Rule {
    pub id: String,
    pub kind: RuleKind,
}

/// A rule's mode (`RuleType.NATIVE_MODE`) and what it reads.
pub enum RuleKind {
    /// `EdgeRule.decide` per edge, findings as rows.
    Judge(Judged),
    /// The search of a reach rule.
    Reach(Reach),
    /// The group pairs of a cycle rule.
    Cycle(Cycle),
    /// The whole check of an export rule.
    Export(ExportRule),
    /// The paths a coverage rule needs covered.
    Coverage(Vec<Cover>),
    /// A layout rule's or an entity inventory's entities.
    Entities(EntityRule),
}

/// Which edges a rule over edges sees (`EdgeRule.sees`) and how it selects them.
pub struct Edges {
    kinds: Vec<String>,
    include: u8,
    exclude: u8,
    role: Role,
    /// The selection in the order and parts the Python engine judges it.
    staged: Staged,
}

/// A rule judged per edge: edge, entrypoint, signature, use-shape and edge inventory rules.
pub struct Judged {
    edges: Edges,
    identity: Vec<Ref>,
    unless: Option<Node>,
    /// What a signature rule adds.
    signature: Option<Signature>,
    verdict: Verdict,
    /// Whether a reference edge is left to the import edge of its module with the same surface.
    uses_at_import: bool,
}

/// How a judged rule decides a selected destination (`EdgeRule.violation` and its overrides).
pub enum Verdict {
    /// `deny`, `allow` and `require`, all judged; with none, the selection is the finding.
    Predicates {
        deny: Option<Node>,
        allow: Option<Node>,
        require: Option<Node>,
    },
    /// An entrypoint rule with no predicate: a destination it does not export.
    DefaultAllow,
    UseShape(UseShape),
    /// An inventory over edges: a selected destination is the row.
    Inventory,
}

/// A reach rule (`ReachPrimitive`).
pub struct Reach {
    edges: Edges,
    /// Its `from`.
    start: Option<Node>,
    deny: Option<Node>,
    unless: Option<Node>,
    /// The search goes on through a denied target.
    through_denied: bool,
}

/// A cycle rule (`CyclePrimitive`): its group field on each end.
pub struct Cycle {
    edges: Edges,
    group: (Ref, Ref),
}

impl RuleKind {
    fn mode(&self) -> &'static str {
        match self {
            RuleKind::Judge(_) => "judge",
            RuleKind::Reach(_) => "reach",
            RuleKind::Cycle(_) => "cycle",
            RuleKind::Export(_) => "export",
            RuleKind::Coverage(_) => "coverage",
            RuleKind::Entities(_) => "entities",
        }
    }
}

/// A module's place in a value column where reading the value meets a provider conflict, and
/// in the registry column where no registry holds the module.
pub const NO_PLACE: u32 = u32::MAX;

/// The place of `key` among `distinct`, added there when it is new, so places follow the order
/// keys are first seen.
pub fn place_of<K: Eq + Hash + Clone>(
    places: &mut FxHashMap<K, u32>,
    distinct: &mut Vec<K>,
    key: K,
) -> u32 {
    if let Some(&at) = places.get(&key) {
        return at;
    }
    let at = distinct.len() as u32;
    places.insert(key.clone(), at);
    distinct.push(key);
    at
}

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
    /// Each export's name and `[shapes]` policy, by place.
    pub export_names: Vec<String>,
    pub export_shapes: Vec<usize>,
    pub shapes: Vec<Shape>,
    /// The base classes and metaclasses that make each core shape.
    pub core_bases: CoreBases,
    pub kind_names: Vec<String>,
    pub kind_sets: KindSets,
    facts: SymbolFacts,
    /// Per symbol assignment and field it sets, its candidate.
    symbol_assigns: Vec<usize>,
    pub rules: Vec<Rule>,
    levels: Vec<Level>,
    /// The declared fields' names, for a selector compiled at run time.
    field_names: Vec<String>,
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
    /// The link's edges every export rule reads, gathered for the first one.
    export_inputs: OnceLock<ExportInputs>,
    /// What each export module hands out by import (`Labeler.handed_zones`).
    pub handed: FxHashMap<Sid, Handed>,
    /// The ids of the directories without `__init__.py` an endpoint can name, clashes included.
    pub directory_ids: FxHashSet<Sid>,
    /// The first declared field that holds a zone.
    pub zone_field: Option<usize>,
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

/// A level (`LevelSpec`): its group field and `select`, as entity reads.
pub struct Level {
    field: Ref,
    select: Option<Node>,
}

/// The plan's selectors compiled, before any label is read.
struct Compiled {
    /// The declared fields' names, which a compiled ref reads by place.
    names: Vec<String>,
    fields: Vec<Field>,
    exports: Vec<Export>,
    rules: Vec<Rule>,
    shapes: Vec<Shape>,
    levels: Vec<Level>,
}

impl Compiled {
    fn new(link: &Link, plan: &Plan) -> Result<Compiled, String> {
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
                    selector: entity.optional(&domain.selector)?,
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
            .map(|export| Export::new(export, edge.optional(&export.audience)?))
            .collect::<Result<Vec<_>, String>>()?;
        let rules = plan
            .rules
            .iter()
            .map(|rule| Rules::compile(&edge, &entity, rule, &fields))
            .collect::<Result<Vec<_>, String>>()?;
        let shapes = plan
            .shapes
            .iter()
            .map(|shape| Shape::new(shape, &entity))
            .collect::<Result<Vec<_>, String>>()?;
        let levels = plan
            .levels
            .iter()
            .map(|level| {
                Ok(Level {
                    field: entity.reference(std::slice::from_ref(&level.group_by)),
                    select: entity.optional(&level.select)?,
                })
            })
            .collect::<Result<Vec<_>, String>>()?;
        Ok(Compiled {
            names,
            fields,
            exports,
            rules,
            shapes,
            levels,
        })
    }

    /// The module fields a Python provider sets that anything compiled reads, sorted.
    fn provider_reads(&self) -> Vec<usize> {
        let mut refs = Vec::new();
        for rule in &self.rules {
            refs.extend(rule.refs());
        }
        let audiences = self
            .exports
            .iter()
            .filter_map(|export| export.audience.as_ref());
        let behaviors = self.shapes.iter().flat_map(Shape::reads);
        let domains = self
            .fields
            .iter()
            .filter_map(|field| field.domain.as_ref()?.selector.as_ref());
        let selects = self.levels.iter().filter_map(|level| level.select.as_ref());
        for node in audiences.chain(behaviors).chain(domains).chain(selects) {
            node.refs(&mut refs);
        }
        refs.extend(self.levels.iter().map(|level| level.field));
        let mut out: Vec<usize> = refs
            .into_iter()
            .filter_map(Ref::field)
            .filter(|&field| self.fields[field].provided && !self.fields[field].on_symbol)
            .collect();
        out.sort_unstable();
        out.dedup();
        out
    }
}

impl Rules {
    pub fn new(link: &mut Link, text: &str, threads: usize) -> Result<Rules, String> {
        let plan = Plan::parse(text)?;
        let (modules, module_files) = Self::graph_modules(link);
        let facts = SymbolFacts::new(link);
        let kind_sets = facts.kind_sets(link, &plan.kinds);
        let compiled = Compiled::new(link, &plan)?;
        let read = compiled.provider_reads();
        let Compiled {
            names: field_names,
            fields,
            exports,
            rules,
            shapes,
            levels,
        } = compiled;
        let it = &link.core.it;
        let module_names: Vec<&str> = modules.iter().map(|module| it.s(*module)).collect();
        plan.check_modules(&module_names)?;
        let paths: Vec<&str> = module_files
            .iter()
            .map(|file| &*link.core.facts.files[*file as usize].path)
            .collect();
        let mut labels = Labels::new(&plan, fields, &paths, &module_names, &exports)?;
        // D43 and D54: Python sends the column of every field `reads` names, so this cannot fail.
        if let Some(&field) = read.iter().find(|&&field| !labels.columns[field]) {
            return Err(format!(
                "the rule plan reads field {}, which a provider sets, but holds no column for it",
                labels.fields[field].name
            ));
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
        // A name can stop at a directory without `__init__.py`; each gets an id an endpoint holds.
        let mut directories: Vec<&str> = labels.namespaces.keys().map(AsRef::as_ref).collect();
        directories.extend(labels.namespace_clashes.iter().map(AsRef::as_ref));
        directories.sort_unstable();
        let directory_ids: FxHashSet<Sid> = directories
            .into_iter()
            .map(|name| link.core.it.intern(name))
            .collect();
        let endpoint_keys = Self::endpoint_keys(link);
        let handed = Self::handed(link, &module_at, &directory_ids, &labels);
        let zone_field =
            (0..labels.fields.len()).find(|&field| labels.fields[field].kind == FieldKind::Zone);
        let mut out = Rules {
            endpoint_keys,
            modules,
            module_files,
            module_at,
            labels,
            values: Vec::new(),
            exports,
            export_names: plan
                .exports
                .iter()
                .map(|export| export.name.clone())
                .collect(),
            export_shapes: plan.exports.iter().map(|export| export.shape).collect(),
            shapes,
            core_bases: CoreBases::new(&plan.core_bases),
            kind_names: plan.kinds.iter().map(|kind| kind.name.clone()).collect(),
            kind_sets,
            facts,
            symbol_assigns,
            rules,
            levels,
            field_names,
            ordinals: Vec::new(),
            pool,
            touch: false,
            tainted: FxHashSet::default(),
            met: Mutex::new(FxHashSet::default()),
            export_inputs: OnceLock::new(),
            handed,
            directory_ids,
            zone_field,
        };
        out.tainted = out.provider_reads();
        out.values = out.module_values(link);
        out.touch = !out.tainted.is_empty() || !out.contested_fields().is_empty();
        if reads_ordinal {
            out.ordinals = ordinals(link, &plan.repeats);
        }
        Ok(out)
    }

    /// The names of the module fields a Python provider sets that the plan's compiled selectors,
    /// identities, groups, covers, audiences, shapes, domains and levels read (D54): the columns
    /// the plan must then hold. The plan's own columns are not read.
    pub fn reads(link: &Link, text: &str) -> Result<Vec<String>, String> {
        let plan = Plan::parse(text)?;
        let compiled = Compiled::new(link, &plan)?;
        Ok(compiled
            .provider_reads()
            .into_iter()
            .map(|field| compiled.fields[field].name.clone())
            .collect())
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

    /// What each export module hands out by import, read once (`Labeler.handed_zones`): the
    /// modules its run-time imports of handed names come from. A type-only or test import hands
    /// nothing out, and a third-party name has no zone.
    fn handed(
        link: &mut Link,
        module_at: &FxHashMap<Sid, u32>,
        directories: &FxHashSet<Sid>,
        labels: &Labels,
    ) -> FxHashMap<Sid, Handed> {
        let modules: Vec<(Sid, bool)> = link
            .hand_outs
            .iter()
            .map(|(module, found)| (*module, found.unread))
            .collect();
        let mut out = FxHashMap::default();
        for (module, unread) in modules {
            let handed = if unread {
                Handed::Unknown
            } else {
                Self::handed_from(link, module_at, directories, labels, module)
            };
            out.insert(module, handed);
        }
        out
    }

    /// The modules the run-time imports of an export module's handed names come from, each once,
    /// in first-seen order; unknown when one of those imports did not resolve, or a first-party
    /// name no module answers for.
    fn handed_from(
        link: &mut Link,
        module_at: &FxHashMap<Sid, u32>,
        directories: &FxHashSet<Sid>,
        labels: &Labels,
        module: Sid,
    ) -> Handed {
        let ids = match link.kind_sid("import") {
            Some(kind) => module_scope_imports(link, kind, module),
            None => Vec::new(),
        };
        let found = &link.hand_outs[&module];
        let edges = &link.edges;
        // Each target's name, and whether its import is third party.
        let mut targets: Vec<(String, bool)> = Vec::new();
        for id in ids {
            if edges.seen_ctx(id) & (ctx::TYPE_ONLY | ctx::TEST) != 0 {
                continue;
            }
            if !binds_handed(link, found, id) {
                continue;
            }
            let res = edges.res_of(id);
            if res == Res::Unresolved {
                return Handed::Unknown;
            }
            for target in edges.targets_of(id) {
                targets.push((link.core.ent_text(*target), res == Res::External));
            }
        }
        let mut from = Vec::new();
        let mut seen: FxHashSet<Sid> = FxHashSet::default();
        for (target, external) in targets {
            let Some(owner) =
                Self::handed_owner(&link.core.it, module_at, directories, labels, &target)
            else {
                if external {
                    continue;
                }
                return Handed::Unknown;
            };
            if seen.insert(owner.0) {
                from.push(owner);
            }
        }
        Handed::From(from)
    }

    /// The longest dotted prefix of a handed name that is a graph module or a namespace
    /// directory (`Labeler.handed_owner`), with the graph module's place. None when there is
    /// none, or when a directory two graph modules place apart comes first.
    fn handed_owner(
        it: &Interner,
        module_at: &FxHashMap<Sid, u32>,
        directories: &FxHashSet<Sid>,
        labels: &Labels,
        name: &str,
    ) -> Option<(Sid, Option<u32>)> {
        let owner = name_owner(it, module_at, directories, name)?;
        if owner.1.is_none() && labels.namespace_clashes.contains(it.s(owner.0)) {
            return None;
        }
        Some(owner)
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

    fn compile(
        edge: &Compiler,
        entity: &Compiler,
        plan: &RulePlan,
        fields: &[Field],
    ) -> Result<Rule, String> {
        let edges = |found: &EdgesPlan| -> Result<Edges, String> {
            let selection = edge.nodes(&found.selection)?;
            Ok(Edges {
                kinds: found.kinds.clone(),
                include: found.include,
                exclude: found.exclude,
                role: role_of(&found.endpoint)?,
                staged: Staged::new(&selection, fields),
            })
        };
        let kind = match &plan.mode {
            ModePlan::Judge(found) => RuleKind::Judge(Judged {
                edges: edges(&found.edges)?,
                identity: found
                    .identity
                    .iter()
                    .map(|path| edge.reference(path))
                    .collect(),
                unless: edge.optional(&found.unless)?,
                signature: found.signature.as_ref().map(|signature| Signature {
                    unknown_types: signature.unknown_types.clone(),
                    annotation: edge.it.get("annotation"),
                }),
                verdict: match &found.verdict {
                    VerdictPlan::Predicates {
                        deny,
                        allow,
                        require,
                    } => Verdict::Predicates {
                        deny: edge.optional(deny)?,
                        allow: edge.optional(allow)?,
                        require: edge.optional(require)?,
                    },
                    VerdictPlan::DefaultAllow => Verdict::DefaultAllow,
                    VerdictPlan::UseShape(shapes) => Verdict::UseShape(UseShape {
                        allow: shapes.allow.clone(),
                        deny: shapes.deny.clone(),
                        flow: shapes.flow.then(|| edge.it.get("flow_shape")),
                    }),
                    VerdictPlan::Inventory => Verdict::Inventory,
                },
                uses_at_import: found.uses_at_import,
            }),
            ModePlan::Reach(found) => RuleKind::Reach(Reach {
                edges: edges(&found.edges)?,
                start: edge.optional(&found.start)?,
                deny: edge.optional(&found.deny)?,
                unless: edge.optional(&found.unless)?,
                through_denied: found.through_denied,
            }),
            ModePlan::Cycle(found) => {
                let path = |root: &str| [root.to_string(), found.group.clone()];
                RuleKind::Cycle(Cycle {
                    edges: edges(&found.edges)?,
                    group: (edge.reference(&path("src")), edge.reference(&path("dst"))),
                })
            }
            ModePlan::Export(found) => {
                let start = edge.optional(&found.export.start)?;
                let slot_owners = found
                    .export
                    .slot_owners
                    .iter()
                    .map(|owner| edge.optional(owner))
                    .collect::<Result<Vec<_>, _>>()?;
                RuleKind::Export(ExportRule::new(
                    &found.export,
                    &found.identity,
                    entity,
                    start,
                    slot_owners,
                ))
            }
            ModePlan::Coverage(found) => RuleKind::Coverage(
                found
                    .covers
                    .iter()
                    .map(|cover| Cover::new(cover, edge, fields))
                    .collect::<Result<Vec<_>, String>>()?,
            ),
            ModePlan::Entities(found) => RuleKind::Entities(EntityRule::new(found, entity)?),
        };
        Ok(Rule {
            id: plan.id.clone(),
            kind,
        })
    }

    pub fn module_path<'l>(&self, link: &'l Link, at: u32) -> &'l str {
        &link.core.facts.files[self.module_files[at as usize] as usize].path
    }

    pub fn module_name<'l>(&self, link: &'l Link, at: u32) -> &'l str {
        link.core.it.s(self.modules[at as usize])
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

    /// Whether a symbol (none for a module) belongs to an export past its paths
    /// (`Labeler.find_export_member`): a listed symbol when the export names them, else by class
    /// when it decides by class, else always.
    pub fn export_member(&self, link: &Link, export: &Export, symbol: Option<&str>) -> Truth {
        if !export.symbols.is_empty() {
            let listed =
                symbol.is_some_and(|symbol| export.symbols.iter().any(|name| name == symbol));
            return if listed { Truth::True } else { Truth::False };
        }
        if export.subclass_of.is_empty() {
            return Truth::True;
        }
        match symbol {
            None => Truth::False,
            Some(symbol) => self.member_by_class(link, &export.subclass_of, symbol),
        }
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

    /// The kinds a rule reads as interned names, in its order; every kind when it names none.
    fn kind_sids(&self, link: &Link, rule: &Edges) -> Vec<Sid> {
        if rule.kinds.is_empty() {
            return link.kind_order();
        }
        known_kinds(link, &rule.kinds).unwrap_or_default()
    }

    /// The edges a rule sees (`EdgeRule.sees`), in id order.
    fn candidates(&self, link: &mut Link, rule: &Edges) -> Vec<u32> {
        match known_kinds(link, &rule.kinds) {
            Some(kinds) => link.candidates(&kinds, rule.include, rule.exclude),
            None => Vec::new(),
        }
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

    /// `rows`, which come grouped by edge in id order, with the edges in the order rule `id`
    /// judges them; an edge's rows keep their order.
    fn in_rule_order<T>(
        &self,
        link: &mut Link,
        id: &str,
        rule: &Edges,
        rows: Vec<T>,
        edge_of: impl Fn(&T) -> u32,
    ) -> Result<Vec<T>, String> {
        let mut edges: Vec<u32> = rows.iter().map(&edge_of).collect();
        edges.dedup();
        let kinds = self.kind_sids(link, rule);
        let order = link
            .rule_order(&edges, &kinds)
            .ok_or_else(|| format!("rule {id}: an edge whose kind is not among its kinds"))?;
        Ok(in_order(rows, &edges, &order, edge_of))
    }

    /// Every destination of every candidate edge that rule `index` does not judge clean, in the
    /// order the Python engine judges them.
    pub fn judge(&self, link: &mut Link, index: usize) -> Result<Vec<Row>, String> {
        let (rule_id, rule) = self.rule_as(index, "judge", |kind| match kind {
            RuleKind::Judge(found) => Some(found),
            _ => None,
        })?;
        let edges = &rule.edges;
        self.read_sources(link, edges);
        let mut ids = self.candidates(link, edges);
        if let Some(signature) = &rule.signature {
            ids = signature.seen(link, ids);
        }
        let rows = self.over_candidates(link, &ids, |cx, id, dsts, out| {
            let src = cx.src_endpoint(id);
            cx.dst_endpoints(id, edges.role, dsts);
            self.touch_sources(cx, edges, id, src, dsts);
            for (target, dst) in dsts.iter().enumerate() {
                let mut env = Env::new(*cx, Some(id), src, *dst);
                if let Verdict::UseShape(use_shape) = &rule.verdict {
                    env.shape = use_shape.shape(cx, id);
                }
                let judgment = decide(rule, &env);
                if judgment == Judgment::Clean {
                    continue;
                }
                let identity = if judgment.has_identity() {
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
        let rows = if rule.uses_at_import {
            uses_at_import(link, rows)
        } else {
            rows
        };
        let rows = self.in_rule_order(link, rule_id, edges, rows, |row| row.edge)?;
        if !matches!(rule.verdict, Verdict::Inventory) {
            return Ok(rows);
        }
        // `EdgeInventory.evaluate` keeps the first destination with each identity.
        let mut seen: FxHashSet<Vec<String>> = FxHashSet::default();
        Ok(rows
            .into_iter()
            .filter(|row| row.judgment != Judgment::Violation || seen.insert(row.identity.clone()))
            .collect())
    }

    /// What export rule `index` reports, in the order `ExportPrimitive.evaluate` reports it.
    pub fn export(&self, link: &mut Link, index: usize) -> Result<Vec<ExportRow>, String> {
        let (_, export) = self.rule_as(index, "export", |kind| match kind {
            RuleKind::Export(found) => Some(found),
            _ => None,
        })?;
        let inputs = self
            .export_inputs
            .get_or_init(|| ExportInputs::read(self, link));
        let cx = Cx { link, rules: self };
        Ok(Walk::new(cx, export, inputs).run())
    }

    /// Every name kind `name` holds (`Labeler.kind_members`), sorted.
    pub fn kind_members(&self, name: &str) -> Result<Vec<String>, String> {
        let kind = self
            .kind_names
            .iter()
            .position(|known| known == name)
            .ok_or_else(|| format!("no kind {name}"))?;
        let mut out: Vec<String> = self
            .kind_sets
            .kinds_of
            .iter()
            .filter(|(_, kinds)| kinds.contains(&kind))
            .map(|(member, _)| member.clone())
            .collect();
        out.sort();
        Ok(out)
    }

    /// The paths coverage rule `index` needs covered, from the witnesses Python gathered for its
    /// rule covers (`CoverInputs`; an edge cover's entry is ignored).
    pub fn coverage(
        &self,
        link: &mut Link,
        index: usize,
        mut inputs: CoverInputs,
    ) -> Result<Required, String> {
        let (id, covers) = self.rule_as(index, "coverage", |kind| match kind {
            RuleKind::Coverage(found) => Some(found),
            _ => None,
        })?;
        if inputs.len() != covers.len() {
            return Err(format!(
                "coverage rule {id} has {} covers, the call {}",
                covers.len(),
                inputs.len()
            ));
        }
        for (cover, input) in covers.iter().zip(inputs.iter_mut()) {
            if matches!(cover, Cover::Edges { .. }) {
                *input = Some(cover_edges(link, cover));
            }
        }
        Ok(required(Cx { link, rules: self }, covers, &inputs))
    }

    /// Rule `index`'s id and what its mode reads, when `pick` finds the mode `mode` names.
    fn rule_as<'s, T>(
        &'s self,
        index: usize,
        mode: &str,
        pick: impl Fn(&'s RuleKind) -> Option<&'s T>,
    ) -> Result<(&'s str, &'s T), String> {
        let rule = self
            .rules
            .get(index)
            .ok_or_else(|| format!("no rule {index} in the plan"))?;
        let found = pick(&rule.kind).ok_or_else(|| {
            format!(
                "rule {} is planned as {}, not {mode}",
                rule.id,
                rule.kind.mode()
            )
        })?;
        Ok((&rule.id, found))
    }

    /// A selector Python hands over at run time (its plan tree as JSON), compiled to read an
    /// edge. It may read no field a provider sets: the plan holds no column for it.
    fn selector(&self, link: &Link, text: &str) -> Result<Node, String> {
        let tree: NodePlan = serde_json::from_str(text)
            .map_err(|error| format!("a selector that does not parse: {error}"))?;
        let node = Compiler {
            fields: &self.field_names,
            it: &link.core.it,
            reading: Reading::Edge,
        }
        .node(&tree)?;
        check_columns(
            &self.labels,
            "a selector given at run time",
            &refs_of(&node),
        )?;
        Ok(node)
    }

    /// The edges of `kinds`, kind by kind in graph order, that `selector` does not make false,
    /// each read with its source as both ends (`RuleContext.edges_passing`).
    pub fn passing(
        &self,
        link: &mut Link,
        kinds: &[String],
        selector: &str,
    ) -> Result<Vec<u32>, String> {
        let node = self.selector(link, selector)?;
        let mut out = Vec::new();
        for kind in known_kinds(link, kinds).unwrap_or_default() {
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
}

impl Rule {
    /// Every selector node the rule reads.
    fn nodes(&self) -> Vec<&Node> {
        match &self.kind {
            RuleKind::Judge(rule) => {
                let mut out: Vec<&Node> = rule.edges.staged.parts.iter().collect();
                out.extend(&rule.unless);
                if let Verdict::Predicates {
                    deny,
                    allow,
                    require,
                } = &rule.verdict
                {
                    out.extend([deny, allow, require].into_iter().flatten());
                }
                out
            }
            RuleKind::Reach(rule) => {
                let mut out: Vec<&Node> = rule.edges.staged.parts.iter().collect();
                out.extend(
                    [&rule.start, &rule.deny, &rule.unless]
                        .into_iter()
                        .flatten(),
                );
                out
            }
            RuleKind::Cycle(rule) => rule.edges.staged.parts.iter().collect(),
            RuleKind::Entities(rule) => rule.nodes(),
            RuleKind::Export(rule) => rule.nodes(),
            RuleKind::Coverage(_) => Vec::new(),
        }
    }

    fn reads_ordinal(&self) -> bool {
        self.nodes().into_iter().any(|node| node.reads(is_ordinal))
            || matches!(&self.kind, RuleKind::Judge(rule) if rule.identity.iter().any(|field| is_ordinal(*field)))
    }

    /// Every ref the rule reads: its selectors, identity columns, group field, covers, an
    /// inventory's field and an export rule's identity.
    fn refs(&self) -> Vec<Ref> {
        let mut out = Vec::new();
        for node in self.nodes() {
            node.refs(&mut out);
        }
        match &self.kind {
            RuleKind::Judge(rule) => out.extend(&rule.identity),
            RuleKind::Cycle(rule) => out.extend([rule.group.0, rule.group.1]),
            RuleKind::Coverage(covers) => {
                for cover in covers {
                    cover.refs(&mut out);
                }
            }
            RuleKind::Entities(rule) => rule.refs(&mut out),
            RuleKind::Export(rule) => rule.refs(&mut out),
            RuleKind::Reach(_) => {}
        }
        out
    }
}

fn refs_of(node: &Node) -> Vec<Ref> {
    let mut out = Vec::new();
    node.refs(&mut out);
    out
}

/// The kinds `names` lists that the link has, as interned names, each once, in order. None when
/// it names kinds and the link has none of them, so a reader of them reads no edge; empty when it
/// names none.
fn known_kinds(link: &Link, names: &[String]) -> Option<Vec<Sid>> {
    let mut out: Vec<Sid> = Vec::new();
    for kind in names.iter().filter_map(|name| link.kind_sid(name)) {
        if !out.contains(&kind) {
            out.push(kind);
        }
    }
    (!out.is_empty() || names.is_empty()).then_some(out)
}

/// `EntrypointPrimitive.sees` past what every edge rule sees: `rows` less those of each
/// reference edge whose module has an import edge with the same surface that the rule reports
/// (a row is reported: it is not clean). Keeps the order of `rows`.
fn uses_at_import(link: &Link, rows: Vec<Row>) -> Vec<Row> {
    let (Some(import), Some(reference)) = (link.kind_sid("import"), link.kind_sid("reference"))
    else {
        return rows;
    };
    let edges = &link.edges;
    let reported: FxHashSet<(Sid, Sid)> = rows
        .iter()
        .filter(|row| edges.kind_of(row.edge) == import)
        .map(|row| (link.module_of_edge(row.edge), edges.surface_of(row.edge)))
        .collect();
    rows.into_iter()
        .filter(|row| {
            edges.kind_of(row.edge) != reference
                || !reported.contains(&(link.module_of_edge(row.edge), edges.surface_of(row.edge)))
        })
        .collect()
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
pub fn decide<'a>(rule: &'a Judged, env: &Env<'a>) -> Judgment {
    let selected = rule.edges.staged.truth(env);
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
    let verdict = match &rule.verdict {
        Verdict::Inventory => return Judgment::Violation,
        Verdict::DefaultAllow => truth(&EXPORTED, env).negate(),
        Verdict::UseShape(use_shape) => use_shape.violation(env),
        Verdict::Predicates {
            deny,
            allow,
            require,
        } => {
            let mut parts = Vec::with_capacity(3);
            if let Some(deny) = deny {
                parts.push(truth(deny, env));
            }
            if let Some(allow) = allow {
                parts.push(truth(allow, env).negate());
            }
            if let Some(require) = require {
                parts.push(truth(require, env).negate());
            }
            if parts.is_empty() {
                Truth::True
            } else {
                any_of(parts)
            }
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
