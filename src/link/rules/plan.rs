//! The plan Python hands the evaluator (hoglint/native/plan.py): the config as data. Nothing in
//! it is computed per module or per symbol, except the columns of fields a Python provider sets.

use serde::Deserialize;

#[derive(Deserialize)]
pub struct Plan {
    /// `len(graph.modules)`: the evaluator finds the same modules in the link.
    pub module_count: usize,
    pub fields: Vec<FieldPlan>,
    pub assigns: Vec<AssignPlan>,
    pub zones: ZonesPlan,
    pub registries: Vec<RegistryPlan>,
    pub exports: Vec<ExportPlan>,
    pub kinds: Vec<KindPlan>,
    /// Per provider-set field a rule or a domain reads: its label on each module.
    pub provided: Vec<ProvidedPlan>,
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
    /// A list, for a set field: its items as `str()` makes them.
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
    pub within: String,
    pub paths: Vec<String>,
    pub subclass_of: Vec<String>,
    pub audience: Option<NodePlan>,
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
    /// `EdgeRule.decide` per edge, findings as rows (edge and entrypoint rules).
    Judge,
    /// Only the selection: the rule judges in Python.
    Select,
    /// The search of a reach rule.
    Reach,
    /// The package pairs of a cycle rule: what `CyclePrimitive.consider` reads.
    Cycle,
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
    /// Edge fields the primitive's own environment sets: selection skips conjuncts that read them.
    pub redefines: Vec<String>,
    /// A cycle rule's group field (`CyclePrimitive.group_field`).
    pub group: Option<String>,
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
