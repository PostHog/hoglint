//! The plan Python hands the evaluator (hoglint/native/plan.py): the config as data. Nothing in
//! it is computed per module or per symbol, except the columns of fields a Python provider sets.

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
    pub kinds: Vec<KindPlan>,
    /// Per provider-set field a rule or a domain reads: its label on each module.
    pub provided: Vec<ProvidedPlan>,
    /// The modules, by place, whose provider labels disagree with another source: a conflict
    /// wherever something reads a provided column of the module (`Labeler.label_provided`).
    pub provider_conflicts: Vec<u32>,
    /// Fields whose provider failed: outside the graph's modules they read as unknown.
    pub failed: Vec<String>,
    pub rules: Vec<RulePlan>,
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

#[derive(Deserialize)]
pub struct SymbolMatchPlan {
    pub decorated_by: Vec<String>,
    pub subclass_of: Vec<String>,
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
}

/// What an export rule adds (`ExportPrimitive`).
#[derive(Deserialize)]
pub struct ExportRulePlan {
    /// The exports it checks, by place, in order.
    pub exports: Vec<usize>,
    /// The contexts it leaves out: type-only and test unless the rule names its own.
    pub exclude: u8,
    pub unknown_types: Vec<String>,
    pub on_unknown: String,
    pub on_unresolved: String,
}

/// One thing a coverage rule needs covered (`CoverSpec`).
#[derive(Deserialize)]
pub struct CoverPlan {
    /// The rule whose column it reads, or None for an edge selection.
    pub rule: Option<String>,
    /// That column as a ref path, and as the config writes it.
    pub column: Vec<String>,
    pub column_text: String,
    /// The kinds an edge selection pins, sorted; None when it pins none.
    pub kinds: Option<Vec<String>>,
    /// Its conjuncts that read only an edge's kind and contexts, joined, and the others.
    pub by_edge: Option<NodePlan>,
    pub rest: Vec<NodePlan>,
    /// src or dst.
    pub endpoint: String,
    pub text: String,
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

/// How the evaluator treats a rule.
#[derive(Deserialize, Clone, Copy, PartialEq, Eq, Debug)]
#[serde(rename_all = "lowercase")]
pub enum Mode {
    /// `EdgeRule.decide` per edge, findings as rows (edge, entrypoint, signature and use-shape
    /// rules).
    Judge,
    /// The search of a reach rule.
    Reach,
    /// The package pairs of a cycle rule: what `CyclePrimitive.consider` reads.
    Cycle,
    /// The whole check of an export rule.
    Export,
    /// The paths a coverage rule needs covered.
    Coverage,
    /// A layout rule's or an entity inventory's entities.
    Entities,
}

#[derive(Deserialize)]
pub struct RulePlan {
    pub id: String,
    pub mode: Mode,
    /// The kinds in the order the rule reads them; every kind when empty.
    pub kinds: Vec<String>,
    pub include: u8,
    pub exclude: u8,
    /// origin, surface or via.
    pub endpoint: String,
    /// The conjuncts of `from` and `select`, in order.
    pub selection: Vec<NodePlan>,
    pub deny: Option<NodePlan>,
    pub allow: Option<NodePlan>,
    pub require: Option<NodePlan>,
    pub unless: Option<NodePlan>,
    /// An entrypoint rule with no predicate: a violation is a destination it does not export.
    pub default_allow: bool,
    pub identity: Vec<Vec<String>>,
    /// A cycle rule's group field (`CyclePrimitive.group_field`).
    pub group: Option<String>,
    /// A reach rule's `from`: which modules it searches from, read of the module alone.
    pub start: Option<NodePlan>,
    /// A signature rule: it sees public callables only, once per slot, and judges a slot with no
    /// usable type apart (`SignaturePrimitive`).
    pub signature: bool,
    /// The destination symbols a signature rule reads as of unknown type.
    pub unknown_types: Vec<String>,
    pub export: Option<ExportRulePlan>,
    /// A coverage rule's covers.
    pub covers: Vec<CoverPlan>,
    /// What a use-shape rule adds.
    pub use_shape: Option<UseShapePlan>,
    /// An inventory over edges (`classify`): every selected destination is a row, the first
    /// with each identity only.
    pub inventory: bool,
    /// What a layout rule or an entity inventory adds: its selection and `require` are entity
    /// selectors.
    pub entities: Option<EntitiesPlan>,
}

/// A rule over entities (`EntityRule`).
#[derive(Deserialize)]
pub struct EntitiesPlan {
    pub on_symbol: bool,
    /// The kinds a `select` pins, sorted; None when it pins none.
    pub wanted: Option<Vec<String>>,
    /// What a layout finding's message adds after the subject.
    pub suffix: String,
    /// An inventory's `exactly_one` field.
    pub exactly_one: Option<String>,
}

/// What a use-shape rule adds (`UseShapePrimitive`).
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
    pub fn parse(text: &str) -> Result<Plan, String> {
        serde_json::from_str(text)
            .map_err(|error| format!("a rule plan that does not parse: {error}"))
    }
}
