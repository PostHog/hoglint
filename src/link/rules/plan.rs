//! The plan Python hands the evaluator (hoglint/native/plan.py): the config as data. Nothing in
//! it is computed per module or per symbol, except the columns of fields a Python provider sets.

use super::labels::Labels;
use super::selector::Ref;
use serde::Deserialize;

#[derive(Deserialize)]
pub struct Plan {
    /// `len(graph.modules)`: the evaluator finds the same modules in the link.
    pub module_count: usize,
    /// The sha256, in hex, of `graph.modules` joined by newlines: provider columns hold one
    /// label per module in this order.
    pub modules: String,
    pub fields: Vec<FieldPlan>,
    pub assigns: Vec<AssignPlan>,
    pub zones: ZonesPlan,
    pub registries: Vec<RegistryPlan>,
    pub exports: Vec<ExportPlan>,
    pub shapes: Vec<ShapePlan>,
    pub core_bases: CoreBasesPlan,
    pub kinds: Vec<KindPlan>,
    /// Per provider-set field a rule or a domain reads: its label on each module.
    pub provided: Vec<ProvidedPlan>,
    /// The modules, by place, whose provider labels disagree with another source: a conflict
    /// wherever something reads a provided column of the module (`Labeler.label_provided`).
    pub provider_conflicts: Vec<u32>,
    /// Fields whose provider failed: outside the graph's modules they read as unknown.
    pub failed: Vec<String>,
    pub rules: Vec<RulePlan>,
    pub levels: Vec<LevelPlan>,
    /// (row, last row) of each edge a producer repeats: it takes its last row's ordinal.
    pub repeats: Vec<(u32, u32)>,
}

#[derive(Deserialize)]
pub struct FieldPlan {
    pub name: String,
    /// string, id, enum, set, bool, zone, ordered or int.
    #[serde(rename = "type")]
    pub kind: String,
    pub on_symbol: bool,
    pub provided: bool,
    pub domain: Option<DomainPlan>,
}

#[derive(Deserialize)]
pub struct DomainPlan {
    pub everywhere: bool,
    pub globs: Vec<String>,
    pub selector: Option<NodePlan>,
}

/// A value an assignment sets: a string (maybe a `{name}` template) or a bool, with the text a
/// conflict compares (`repr`) for anything else.
#[derive(Deserialize, Clone)]
#[serde(rename_all = "lowercase")]
pub enum SetValue {
    Text(String),
    Bool(bool),
    /// A list, for a set field: its items, strings by the schema.
    Items(Vec<String>),
    /// Any other value: its `str()` and its `repr()`.
    Other(String, String),
}

#[derive(Clone, Deserialize)]
pub struct SymbolMatchPlan {
    pub decorated_by: Vec<String>,
    pub subclass_of: Vec<String>,
    /// The symbols' full dotted names.
    pub names: Vec<String>,
}

#[derive(Deserialize)]
pub struct AssignPlan {
    #[serde(rename = "match")]
    pub matches: Vec<String>,
    pub except_paths: Vec<String>,
    pub except_values: Vec<(String, Vec<String>)>,
    pub only_values: Vec<(String, Vec<String>)>,
    pub sets: Vec<(String, SetValue)>,
    #[serde(rename = "override")]
    pub overrides: bool,
    pub symbols: Option<SymbolMatchPlan>,
}

#[derive(Deserialize)]
pub struct ZonePlan {
    pub name: String,
    pub parent: String,
    pub paths: Vec<String>,
    #[serde(rename = "override")]
    pub overrides: bool,
}

#[derive(Deserialize)]
pub struct ZonesPlan {
    pub within: Vec<String>,
    pub default: String,
    pub zones: Vec<ZonePlan>,
}

#[derive(Deserialize)]
pub struct RegistryPlan {
    pub name: String,
    pub module: String,
    pub loader: String,
    pub lifecycle: String,
}

#[derive(Deserialize)]
pub struct ExportPlan {
    pub name: String,
    /// The export's `[shapes]` policy, by place.
    pub shape: usize,
    pub within: String,
    pub paths: Vec<String>,
    pub subclass_of: Vec<String>,
    /// Only these symbols are members, not their subclasses (`ExportSpec.symbols`).
    pub symbols: Vec<String>,
    pub audience: Option<NodePlan>,
}

#[derive(Deserialize)]
pub struct BehaviorPlan {
    pub kind: String,
    #[serde(rename = "where")]
    pub r#where: Option<NodePlan>,
}

/// A `[shapes]` policy (`ShapePolicy`).
#[derive(Deserialize)]
pub struct ShapePlan {
    pub name: String,
    pub data: Vec<String>,
    pub allow: Vec<String>,
    pub deny: Vec<String>,
    pub behavior: Option<BehaviorPlan>,
    /// `unknown = "incomplete"`.
    pub incomplete: bool,
    /// `signatures = true`: the signatures of what the export hands out are part of it.
    pub signatures: bool,
    /// An entity selector: a symbol it holds for is allowed as it stands.
    pub allow_where: Option<NodePlan>,
    /// An entity selector: an allowed class that is no data shape is walked only where it holds.
    pub walk_where: Option<NodePlan>,
}

/// The base classes and metaclasses that make each core shape (`exports.core_shape_bases`): the
/// Python lists are the only copy.
#[derive(Clone, Deserialize)]
pub struct CoreBasesPlan {
    pub abc: Vec<String>,
    pub abc_metas: Vec<String>,
    #[serde(rename = "enum")]
    pub enum_: Vec<String>,
    pub protocol: Vec<String>,
    pub exception: Vec<String>,
}

/// What an export rule checks (`ExportPrimitive`).
#[derive(Deserialize)]
pub struct ExportRulePlan {
    /// The exports it checks, by place, in order.
    pub exports: Vec<usize>,
    /// The contexts it reads, and the ones it leaves out: type-only and test unless the rule
    /// names its own.
    pub include: u8,
    pub exclude: u8,
    pub unknown_types: Vec<String>,
    pub on_unknown: String,
    pub on_unresolved: String,
    /// Its `from`: which export modules it judges, read of the module alone.
    pub start: Option<NodePlan>,
    /// With `slots = "shape"`, where each signature rule sees a member's slots (None: in every
    /// module); there only a class's shape is judged. Empty: every slot is judged in full.
    pub slot_owners: Vec<Option<NodePlan>>,
}

/// One thing a coverage rule needs covered (`CoverSpec`).
#[derive(Deserialize)]
pub struct CoverPlan {
    /// The rule whose column it reads, or None for an edge selection.
    pub rule: Option<String>,
    /// That column as a ref path.
    pub column: Vec<String>,
    /// The kinds an edge selection pins, sorted; None when it pins none.
    pub kinds: Option<Vec<String>>,
    /// Its conjuncts that read only an edge's kind and contexts, joined, and the others.
    pub by_edge: Option<NodePlan>,
    pub rest: Vec<NodePlan>,
    /// src or dst.
    pub endpoint: String,
}

#[derive(Deserialize)]
pub struct KindPlan {
    pub name: String,
    pub subclass_of: Vec<String>,
    pub decorated_by: Vec<String>,
    pub abstract_: Option<bool>,
    pub decl: String,
}

/// A label value, for a provider column: `none` where the provider sets nothing.
#[derive(Deserialize, Clone)]
#[serde(rename_all = "lowercase")]
pub enum ValuePlan {
    None,
    Text(String),
    Bool(bool),
    Set(Vec<String>),
    Zone(String),
}

#[derive(Deserialize)]
pub struct ProvidedPlan {
    pub field: String,
    /// `Labeler.label_only` of each module in graph order: the label the provider gives it.
    pub labels: Vec<ValuePlan>,
}

/// A level (`LevelSpec`): the field it groups by and its `select`, both entity reads.
#[derive(Deserialize)]
pub struct LevelPlan {
    pub group_by: String,
    pub select: Option<NodePlan>,
}

/// One rule of the plan: its id, and its mode with what that mode reads.
#[derive(Deserialize)]
pub struct RulePlan {
    pub id: String,
    #[serde(flatten)]
    pub mode: ModePlan,
}

/// How the evaluator treats a rule, as the rule's Python class states it (`RuleType.NATIVE_MODE`).
#[derive(Deserialize)]
#[serde(tag = "mode", rename_all = "lowercase")]
pub enum ModePlan {
    /// `EdgeRule.decide` per edge, findings as rows (edge, entrypoint, signature, use-shape and
    /// edge inventory rules).
    Judge(JudgePlan),
    /// The search of a reach rule.
    Reach(ReachPlan),
    /// The group pairs of a cycle rule: what `CyclePrimitive.consider` reads.
    Cycle(CyclePlan),
    /// The whole check of an export rule.
    Export(ExportModePlan),
    /// The paths a coverage rule needs covered.
    Coverage(CoveragePlan),
    /// A layout rule's or an entity inventory's entities.
    Entities(EntitiesRulePlan),
}

/// Which edges a rule over edges sees and how it selects them.
#[derive(Deserialize)]
pub struct EdgesPlan {
    /// The kinds in the order the rule reads them; every kind when empty.
    pub kinds: Vec<String>,
    pub include: u8,
    pub exclude: u8,
    /// origin, surface or via.
    pub endpoint: String,
    /// The conjuncts of `from` and `select` (of `select` alone for a reach rule), in order.
    pub selection: Vec<NodePlan>,
}

#[derive(Deserialize)]
pub struct JudgePlan {
    pub edges: EdgesPlan,
    pub identity: Vec<Vec<String>>,
    pub unless: Option<NodePlan>,
    /// A signature rule: it sees public callables only, once per slot, and judges a slot with no
    /// usable type apart (`SignaturePrimitive`).
    pub signature: Option<SignaturePlan>,
    pub verdict: VerdictPlan,
    /// An entrypoint rule that judges a later use of an imported name at its import
    /// (`EntrypointPrimitive.sees`).
    pub uses_at_import: bool,
}

#[derive(Deserialize)]
pub struct SignaturePlan {
    /// The destination symbols it reads as of unknown type.
    pub unknown_types: Vec<String>,
}

/// How a judged rule decides a selected destination (`EdgeRule.violation` and its overrides).
// Decoded once per rule: the size of the largest variant costs nothing.
#[allow(clippy::large_enum_variant)]
#[derive(Deserialize)]
#[serde(tag = "kind", rename_all = "kebab-case")]
pub enum VerdictPlan {
    /// `deny`, `allow` and `require`, all judged; with none, the selection is the finding.
    Predicates {
        deny: Option<NodePlan>,
        allow: Option<NodePlan>,
        require: Option<NodePlan>,
    },
    /// An entrypoint rule with no predicate: a violation is a destination it does not export.
    DefaultAllow,
    /// A use-shape rule's shapes (`UseShapePrimitive`).
    UseShape(UseShapePlan),
    /// An inventory over edges (`classify`): every selected destination is a row, the first with
    /// each identity only.
    Inventory,
}

#[derive(Deserialize)]
pub struct ReachPlan {
    pub edges: EdgesPlan,
    /// Its `from`: which modules it searches from, read of the module alone.
    pub start: Option<NodePlan>,
    pub deny: Option<NodePlan>,
    pub unless: Option<NodePlan>,
}

#[derive(Deserialize)]
pub struct CyclePlan {
    pub edges: EdgesPlan,
    /// The field whose values are the nodes of its graph (`CyclePrimitive.group_field`).
    pub group: String,
}

#[derive(Deserialize)]
pub struct ExportModePlan {
    pub identity: Vec<Vec<String>>,
    pub export: ExportRulePlan,
}

#[derive(Deserialize)]
pub struct CoveragePlan {
    pub covers: Vec<CoverPlan>,
}

/// A layout rule or an entity inventory: its selection and `require` are entity selectors.
#[derive(Deserialize)]
pub struct EntitiesRulePlan {
    pub selection: Vec<NodePlan>,
    pub require: Option<NodePlan>,
    pub identity: Vec<Vec<String>>,
    pub entities: EntitiesPlan,
}

/// A rule over entities (`EntityRule`).
#[derive(Deserialize)]
pub struct EntitiesPlan {
    pub on_symbol: bool,
    /// The kinds a `select` pins, sorted; None when it pins none.
    pub wanted: Option<Vec<String>>,
    /// An inventory's `exactly_one` field.
    pub exactly_one: Option<String>,
}

/// A use-shape rule's shapes (`UseShapePrimitive`).
#[derive(Deserialize)]
pub struct UseShapePlan {
    pub allow: Vec<String>,
    pub deny: Vec<String>,
    /// `flow = "def-use"`: the edge's `flow_shape` attr replaces its shape.
    pub flow: bool,
}

#[derive(Deserialize, Clone)]
#[serde(rename_all = "lowercase")]
pub enum NodePlan {
    And(Vec<NodePlan>),
    Or(Vec<NodePlan>),
    Not(Box<NodePlan>),
    Bare(OperandPlan),
    Cmp(String, OperandPlan, OperandPlan),
}

#[derive(Deserialize, Clone)]
#[serde(rename_all = "lowercase")]
pub enum OperandPlan {
    Ref(Vec<String>),
    /// `none` (None), `true`/`false`, or a string or number as `str()` writes it.
    Lit(Option<LiteralPlan>),
    /// A list literal's items as `str()` writes them.
    List(Vec<String>),
}

#[derive(Deserialize, Clone)]
#[serde(untagged)]
pub enum LiteralPlan {
    Bool(bool),
    Text(String),
}

impl Plan {
    /// The plan is for the link's graph: its modules, `names` in the link's order, are the ones
    /// the plan's columns list, in that order.
    pub fn check_modules(&self, names: &[&str]) -> Result<(), String> {
        if names.len() != self.module_count {
            return Err(format!(
                "the link holds {} graph modules, the graph {}",
                names.len(),
                self.module_count
            ));
        }
        if crate::sha256_hex(names.join("\n").as_bytes()) != self.modules {
            return Err(
                "the rule plan lists the graph's modules in another order than the link".into(),
            );
        }
        Ok(())
    }

    pub fn parse(text: &str) -> Result<Plan, String> {
        serde_json::from_str(text)
            .map_err(|error| format!("a rule plan that does not parse: {error}"))
    }
}

/// D43: a reader of a module field a Python provider sets needs that field's column in the
/// plan. Without it the evaluator would read no label at all, a clean answer nothing checks.
pub(crate) fn check_columns(labels: &Labels, reader: &str, refs: &[Ref]) -> Result<(), String> {
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
