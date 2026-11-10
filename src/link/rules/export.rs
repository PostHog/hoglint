//! The export primitive (hoglint/rules/exports.py `ExportPrimitive`): does a public door hand
//! out a denied shape? Every member of an export is checked, recursively through the types its
//! signatures, fields and bases name, in the order the Python primitive checks them.

use super::Rules;
use super::env::{Cx, EntityEnv};
use super::plan::{ExportRulePlan, ShapePlan};
use super::selector::{Compiler, EntityField, Node, Ref, evaluate};
use super::signature::callable_of;
use super::value::{Truth, Value, render};
use crate::link::Link;
use crate::link::intern::{EMPTY, Sid};
use crate::link::scope::Res;
use rustc_hash::{FxHashMap, FxHashSet};

const ABC_BASES: [&str; 1] = ["abc.ABC"];
const ABC_METAS: [&str; 1] = ["abc.ABCMeta"];
const ENUM_BASES: [&str; 5] = [
    "enum.Enum",
    "enum.StrEnum",
    "enum.IntEnum",
    "enum.Flag",
    "enum.IntFlag",
];
const PROTOCOL_BASES: [&str; 2] = ["typing.Protocol", "typing_extensions.Protocol"];

/// A `[shapes]` policy (`ShapePolicy`).
pub struct Shape {
    data: Vec<String>,
    allow: Vec<String>,
    deny: Vec<String>,
    /// (kind, `where`) of the policy's `behavior`.
    behavior: Option<(String, Option<Node>)>,
    /// `unknown = "incomplete"`.
    incomplete: bool,
}

impl Shape {
    pub fn new(plan: &ShapePlan, entity: &Compiler) -> Result<Shape, String> {
        let behavior = match &plan.behavior {
            None => None,
            Some(found) => Some((
                found.kind.clone(),
                found
                    .r#where
                    .as_ref()
                    .map(|node| entity.node(node))
                    .transpose()?,
            )),
        };
        Ok(Shape {
            data: plan.data.clone(),
            allow: plan.allow.clone(),
            deny: plan.deny.clone(),
            behavior,
            incomplete: plan.incomplete,
        })
    }

    pub fn reads(&self) -> Option<&Node> {
        self.behavior.as_ref().and_then(|(_, found)| found.as_ref())
    }
}

/// One identity column of an export finding, as `ExportPrimitive.resolver` reads it.
#[derive(Clone, Copy)]
enum Column {
    /// `edge.label`: the finding's label.
    Label,
    /// `edge.kind`: "export".
    Kind,
    /// Any other `edge.*`: none.
    NoEdge,
    /// `dst.symbol`: the symbol checked.
    Symbol,
    /// `dst.<name>`: the symbol, read as an entity.
    Dst(EntityField),
    /// `src.module`, `src.symbol`, `src.subject`: the exporting module.
    Module,
    /// Any other `src.<name>`: the exporting module, read as an entity.
    Src(EntityField),
}

/// What an export rule adds to a rule.
pub struct ExportRule {
    exports: Vec<usize>,
    include: u8,
    exclude: u8,
    unknown_types: Vec<String>,
    on_unknown: String,
    on_unresolved: String,
    columns: Vec<Column>,
}

impl ExportRule {
    pub fn new(plan: &ExportRulePlan, identity: &[Vec<String>], entity: &Compiler) -> ExportRule {
        let field = |name: &str| match entity.reference(&[name.to_string()]) {
            Ref::Entity(found) => found,
            _ => EntityField::Unknown,
        };
        let columns = identity
            .iter()
            .map(|path| {
                let name = path.last().map_or("", String::as_str);
                let root = if path.len() == 2 {
                    path[0].as_str()
                } else {
                    ""
                };
                match (root, name) {
                    ("edge", "label") => Column::Label,
                    ("edge", "kind") => Column::Kind,
                    ("edge", _) => Column::NoEdge,
                    ("dst", "symbol") => Column::Symbol,
                    ("dst", _) => Column::Dst(field(name)),
                    (_, "module" | "symbol" | "subject") => Column::Module,
                    _ => Column::Src(field(name)),
                }
            })
            .collect();
        ExportRule {
            exports: plan.exports.clone(),
            include: plan.include,
            exclude: plan.exclude,
            unknown_types: plan.unknown_types.clone(),
            on_unknown: plan.on_unknown.clone(),
            on_unresolved: plan.on_unresolved.clone(),
            columns,
        }
    }

    /// The fields its identity columns read of a module or a symbol.
    pub fn refs(&self, out: &mut Vec<Ref>) {
        for column in &self.columns {
            if let Column::Src(field) | Column::Dst(field) = column {
                out.push(Ref::Entity(*field));
            }
        }
    }
}

/// What an export rule reports, in the order the Python primitive reports it, as data: the
/// rule's Python class words every message and reason (`ExportPrimitive`).
pub enum ExportRow {
    Finding {
        symbol: String,
        module: String,
        label: String,
        trail: Vec<String>,
        edge: Option<u32>,
        ledgerable: bool,
        unknown: bool,
        identity: Vec<String>,
        /// The module the symbol belongs to, or the symbol.
        subject: String,
    },
    /// A symbol the rule cannot tell about, reported as incomplete: why, by its code
    /// (`exports.UNKNOWN_WHY`), and the export's name or the slot the reason names.
    Unknown {
        symbol: String,
        module: String,
        trail: Vec<String>,
        why: &'static str,
        detail: String,
    },
    /// An import that did not resolve, reported as incomplete.
    Unresolved { surface: String, module: String },
}

/// The link's edges an export rule reads, gathered while the link can still build its indexes.
pub struct ExportInputs {
    /// (module, path) of every graph module an export covers, per export, in module name order.
    covered: Vec<Vec<(Sid, u32)>>,
    /// Module-scope import edges per covered module.
    imports: FxHashMap<Sid, Vec<u32>>,
    /// (callable, edge) of every `exposes` edge, per module.
    exposes: FxHashMap<Sid, Vec<(Sid, u32)>>,
    /// Every symbol name, sorted, for `TopLevel`.
    symbols: Vec<String>,
}

impl ExportInputs {
    pub fn read(rules: &Rules, link: &mut Link) -> ExportInputs {
        let it = &link.core.it;
        let mut order: Vec<u32> = (0..rules.modules.len() as u32).collect();
        order.sort_by(|a, b| {
            it.s(rules.modules[*a as usize])
                .cmp(it.s(rules.modules[*b as usize]))
        });
        let mut covered = vec![Vec::new(); rules.exports.len()];
        for &at in &order {
            let bits = rules.labels.exports_of[at as usize];
            for (export, found) in covered.iter_mut().enumerate() {
                if bits & (1 << export) != 0 {
                    found.push((rules.modules[at as usize], at));
                }
            }
        }
        let module_scope = link.settings.module_scope;
        let mut imports = FxHashMap::default();
        if let Some(kind) = link.kind_sid("import") {
            for module in covered.iter().flatten().map(|(module, _)| *module) {
                if imports.contains_key(&module) {
                    continue;
                }
                let ids: Vec<u32> = link
                    .ids_from(kind, module)
                    .into_iter()
                    .filter(|&id| {
                        let scope = link.site_of(id).1;
                        scope == module_scope || scope == EMPTY
                    })
                    .collect();
                imports.insert(module, ids);
            }
        }
        let mut exposes = FxHashMap::default();
        if let Some(kind) = link.kind_sid("exposes") {
            let callable = link.core.it.get("callable");
            for module in link.source_modules(kind) {
                let ids = link.ids_from(kind, module);
                let named = ids
                    .into_iter()
                    .map(|id| (callable_of(link, id, callable), id))
                    .collect();
                exposes.insert(module, named);
            }
        }
        let it = &link.core.it;
        let mut symbols: Vec<String> = super::symbols::SymbolFacts::all_symbols(link)
            .into_iter()
            .map(|sid| it.s(sid).to_string())
            .collect();
        symbols.sort();
        ExportInputs {
            covered,
            imports,
            exposes,
            symbols,
        }
    }
}

/// Shape names of a symbol, and whether the facts were enough to be sure.
type Shapes = (FxHashSet<String>, bool);

/// One run of an export rule (`ExportPrimitive.evaluate`).
pub struct Walk<'a> {
    cx: Cx<'a>,
    rule: &'a ExportRule,
    inputs: &'a ExportInputs,
    rows: Vec<ExportRow>,
    reported: FxHashSet<(String, String, String)>,
    visited: FxHashSet<(String, usize, String)>,
    shapes: FxHashMap<String, Shapes>,
    exposes_by_name: FxHashMap<Sid, FxHashMap<String, Vec<u32>>>,
    top_level: FxHashMap<Sid, Vec<String>>,
}

impl<'a> Walk<'a> {
    pub fn new(cx: Cx<'a>, rule: &'a ExportRule, inputs: &'a ExportInputs) -> Walk<'a> {
        Walk {
            cx,
            rule,
            inputs,
            rows: Vec::new(),
            reported: FxHashSet::default(),
            visited: FxHashSet::default(),
            shapes: FxHashMap::default(),
            exposes_by_name: FxHashMap::default(),
            top_level: FxHashMap::default(),
        }
    }

    pub fn run(mut self) -> Vec<ExportRow> {
        let rules = self.cx.rules;
        for &export in &self.rule.exports {
            let shape = rules.export_shapes[export];
            for &(module, _) in &self.inputs.covered[export] {
                let module_name = self.cx.text(module).to_string();
                for (symbol, edge) in self.exported_symbols(module) {
                    let bases = &rules.exports[export].subclass_of;
                    let member = if bases.is_empty() {
                        Truth::True
                    } else {
                        rules.member_by_class(self.cx.link, bases, &symbol)
                    };
                    match member {
                        Truth::False => {}
                        Truth::Unknown => {
                            let trail = vec![symbol.clone()];
                            let name = &rules.export_names[export];
                            let why = ("not-member", name.as_str());
                            self.unknown(&symbol, &module_name, shape, &trail, edge, why);
                        }
                        Truth::True => {
                            let trail = vec![symbol.clone()];
                            self.check(&symbol, &module_name, shape, edge, &trail);
                        }
                    }
                }
            }
        }
        self.rows
    }

    /// `TopLevel.of`: the module's public top-level symbols, sorted.
    fn top_level(&mut self, module: Sid) -> Vec<String> {
        if let Some(found) = self.top_level.get(&module) {
            return found.clone();
        }
        let name = self.cx.text(module);
        let prefix = format!("{name}.");
        let symbols = &self.inputs.symbols;
        let start = symbols.partition_point(|symbol| symbol.as_str() < prefix.as_str());
        let mut found = Vec::new();
        for symbol in &symbols[start..] {
            let Some(rest) = symbol.strip_prefix(&prefix) else {
                break;
            };
            if rest.is_empty() || rest.contains('.') || rest.starts_with('_') {
                continue;
            }
            if self.cx.module_of_symbol(symbol).map(|(found, _)| found) == Some(module) {
                found.push(symbol.clone());
            }
        }
        self.top_level.insert(module, found.clone());
        found
    }

    /// `ExportPrimitive.exported_symbols`: top-level symbols, then what module-scope imports
    /// bring in; an import that did not resolve is reported on the way.
    fn exported_symbols(&mut self, module: Sid) -> Vec<(String, Option<u32>)> {
        let mut out: Vec<(String, Option<u32>)> = self
            .top_level(module)
            .into_iter()
            .map(|symbol| (symbol, None))
            .collect();
        let module_name = self.cx.text(module).to_string();
        let imports = self
            .inputs
            .imports
            .get(&module)
            .cloned()
            .unwrap_or_default();
        for id in imports {
            let edges = &self.cx.link.edges;
            let ctx = edges.seen_ctx(id);
            let include = self.rule.include;
            if (include != 0 && ctx & include == 0) || ctx & self.rule.exclude != 0 {
                continue;
            }
            let targets = edges.targets_of(id);
            if edges.res_of(id) == Res::Unresolved || targets.is_empty() {
                self.unresolved(id, &module_name);
                continue;
            }
            for target in targets {
                if target.s != EMPTY {
                    out.push((
                        format!("{}.{}", self.cx.text(target.m), self.cx.text(target.s)),
                        Some(id),
                    ));
                }
            }
        }
        out
    }

    fn recognize(&mut self, symbol: &str) -> Shapes {
        if let Some(found) = self.shapes.get(symbol) {
            return found.clone();
        }
        let found = self.find_shapes(symbol);
        self.shapes.insert(symbol.to_string(), found.clone());
        found
    }

    /// `ExportPrimitive.find_shapes`.
    fn find_shapes(&self, symbol: &str) -> Shapes {
        let cx = self.cx;
        let rules = cx.rules;
        let link = cx.link;
        let mut names: FxHashSet<String> = FxHashSet::default();
        let has = rules.has_symbol(link, symbol);
        let kind = cx.kind(symbol, has);
        let kind_unknown = kind.is_unknown();
        if let Value::Text(text) = &kind {
            names.insert(text.to_string());
        }
        if !has {
            return (names, !kind_unknown);
        }
        let (ancestors, complete) = rules.facts.ancestors(link, symbol);
        let in_lineage = |bases: &[&str]| {
            bases
                .iter()
                .any(|base| *base == symbol || ancestors.contains(base))
        };
        match rules.symbol_decl(link, symbol) {
            "function" => {
                names.insert("function".into());
            }
            "assignment" => {
                names.insert("constant".into());
            }
            "class" => {
                names.insert("class".into());
            }
            _ => {}
        }
        if rules.facts.is_dataclass(link, symbol) {
            names.insert("dataclass".into());
            if rules.facts.is_frozen(link, symbol) {
                names.insert("frozen-dataclass".into());
            }
        }
        let metaclass = rules.symbol_fact(link, symbol, "metaclass");
        if in_lineage(&ABC_BASES) || ABC_METAS.contains(&metaclass) {
            names.insert("abc".into());
        }
        if in_lineage(&ENUM_BASES) {
            names.insert("enum".into());
        }
        if in_lineage(&PROTOCOL_BASES) {
            names.insert("protocol".into());
        }
        (names, complete && !kind_unknown)
    }

    fn behavior_ok(&mut self, symbol: &str, shape: usize) -> bool {
        let rules = self.cx.rules;
        let Some((kind, found)) = &rules.shapes[shape].behavior else {
            return false;
        };
        let (ancestors, _) = rules.facts.ancestors(self.cx.link, symbol);
        let mut lineage: Vec<String> = ancestors.into_iter().map(str::to_string).collect();
        lineage.push(symbol.to_string());
        lineage.sort();
        lineage.dedup();
        for candidate in lineage {
            let (shapes, _) = self.recognize(&candidate);
            if !shapes.contains(kind) {
                continue;
            }
            let Some(node) = found else {
                return true;
            };
            let owner = self.cx.module_of_symbol(&candidate);
            let env = EntityEnv::of(self.cx, owner, Some(&candidate), None);
            if evaluate(node, &env) == Truth::True {
                return true;
            }
        }
        false
    }

    /// `ExportPrimitive.check`.
    fn check(
        &mut self,
        symbol: &str,
        module: &str,
        shape: usize,
        edge: Option<u32>,
        trail: &[String],
    ) {
        if !self
            .visited
            .insert((module.to_string(), shape, symbol.to_string()))
        {
            return;
        }
        let rules = self.cx.rules;
        let policy = &rules.shapes[shape];
        let (shapes, complete) = self.recognize(symbol);
        let mut denied: Vec<&String> = policy
            .deny
            .iter()
            .filter(|name| shapes.contains(*name))
            .collect();
        denied.sort();
        if let Some(first) = denied.first() {
            let label = format!("export-denied({first})");
            self.finding(symbol, module, &label, trail, edge, true, false);
            return;
        }
        if self.rule.unknown_types.iter().any(|name| name == symbol) {
            self.unknown(symbol, module, shape, trail, edge, ("unknown-type", ""));
            return;
        }
        if !rules.has_symbol(self.cx.link, symbol) {
            if !complete {
                self.unknown(symbol, module, shape, trail, edge, ("no-facts", ""));
            }
            return;
        }
        if shapes.contains("class") {
            let listed = policy
                .data
                .iter()
                .chain(&policy.allow)
                .any(|name| shapes.contains(name));
            let allowed = listed || self.behavior_ok(symbol, shape);
            if !allowed {
                if !complete {
                    self.unknown(symbol, module, shape, trail, edge, ("bases", ""));
                    return;
                }
                self.finding(
                    symbol,
                    module,
                    "export-shape(class)",
                    trail,
                    edge,
                    true,
                    false,
                );
                return;
            }
            if shapes.contains("dataclass") || shapes.contains("frozen-dataclass") {
                self.check_members(symbol, module, shape, trail);
            }
            return;
        }
        if shapes.contains("function") {
            self.check_members(symbol, module, shape, trail);
        }
    }

    /// `ExportPrimitive.exposes_under`.
    fn exposes_under(&mut self, module: Sid, qualname: &str) -> Vec<u32> {
        if !self.exposes_by_name.contains_key(&module) {
            let mut found: FxHashMap<String, Vec<u32>> = FxHashMap::default();
            for &(callable, id) in self
                .inputs
                .exposes
                .get(&module)
                .map_or(&[][..], Vec::as_slice)
            {
                let parts: Vec<&str> = self.cx.text(callable).split('.').collect();
                for end in 1..=parts.len() {
                    found.entry(parts[..end].join(".")).or_default().push(id);
                }
            }
            self.exposes_by_name.insert(module, found);
        }
        self.exposes_by_name[&module]
            .get(qualname)
            .cloned()
            .unwrap_or_default()
    }

    /// `ExportPrimitive.member_handles`.
    fn member_edges(&mut self, symbol: &str, include_bases: bool) -> Vec<u32> {
        let mut lineage = vec![symbol.to_string()];
        if include_bases {
            let (ancestors, _) = self.cx.rules.facts.ancestors(self.cx.link, symbol);
            let mut sorted: Vec<String> = ancestors.into_iter().map(str::to_string).collect();
            sorted.sort();
            lineage.extend(sorted);
        }
        let mut out = Vec::new();
        for target in lineage {
            let Some((owner, _)) = self.cx.module_of_symbol(&target) else {
                continue;
            };
            let owner_name = self.cx.text(owner);
            if owner_name == target {
                continue;
            }
            let qualname = target[owner_name.len() + 1..].to_string();
            out.extend(self.exposes_under(owner, &qualname));
        }
        out
    }

    /// `ExportPrimitive.check_members`.
    fn check_members(&mut self, symbol: &str, module: &str, shape: usize, trail: &[String]) {
        let link = self.cx.link;
        let include_bases = self.cx.rules.symbol_decl(link, symbol) == "class";
        let annotation = link.core.it.get("annotation");
        let slot = link.core.it.get("slot");
        for id in self.member_edges(symbol, include_bases) {
            if self.cx.attr(id, annotation) == "missing" {
                let why = ("no-annotation", self.cx.attr(id, slot));
                self.unknown(symbol, module, shape, trail, Some(id), why);
                continue;
            }
            let edges = &link.edges;
            if edges.res_of(id) == Res::Unresolved {
                self.unresolved(id, module);
                continue;
            }
            let targets = edges.targets_of(id);
            let names: Vec<String> = if targets.is_empty() {
                vec![self.cx.text(edges.surface_of(id)).to_string()]
            } else {
                targets
                    .iter()
                    .map(|target| match target.s {
                        EMPTY => self.cx.text(target.m).to_string(),
                        name => format!("{}.{}", self.cx.text(target.m), self.cx.text(name)),
                    })
                    .collect()
            };
            for name in names {
                if !name.is_empty() && !trail.contains(&name) {
                    let mut deeper = trail.to_vec();
                    deeper.push(name.clone());
                    self.check(&name, module, shape, Some(id), &deeper);
                }
            }
        }
    }

    /// One identity column (`ExportPrimitive.resolver`), rendered.
    fn column(&self, column: Column, symbol: &str, module: &str, label: &str) -> String {
        let cx = self.cx;
        let entity = |field: EntityField, owner: Option<(Sid, u32)>, symbol: Option<&str>| {
            let env = EntityEnv::of(cx, owner, symbol, None);
            render(&super::selector::Resolve::resolve(&env, Ref::Entity(field)))
        };
        match column {
            Column::Label => label.to_string(),
            Column::Kind => "export".to_string(),
            Column::NoEdge => render(&Value::None),
            Column::Symbol => symbol.to_string(),
            Column::Dst(field) => entity(field, cx.module_of_symbol(symbol), Some(symbol)),
            Column::Module => module.to_string(),
            Column::Src(field) => entity(field, cx.module_named(module), None),
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn finding(
        &mut self,
        symbol: &str,
        module: &str,
        label: &str,
        trail: &[String],
        edge: Option<u32>,
        ledgerable: bool,
        unknown: bool,
    ) {
        let key = (symbol.to_string(), module.to_string(), label.to_string());
        if !self.reported.insert(key) {
            return;
        }
        let identity = self
            .rule
            .columns
            .iter()
            .map(|column| self.column(*column, symbol, module, label))
            .collect();
        let subject = match self.cx.module_of_symbol(symbol) {
            Some((owner, _)) => self.cx.text(owner).to_string(),
            None => symbol.to_string(),
        };
        self.rows.push(ExportRow::Finding {
            symbol: symbol.to_string(),
            module: module.to_string(),
            label: label.to_string(),
            trail: trail.to_vec(),
            edge,
            ledgerable,
            unknown,
            identity,
            subject,
        });
    }

    /// `ExportPrimitive.unknown`; `why` is the reason's code and the name it holds.
    fn unknown(
        &mut self,
        symbol: &str,
        module: &str,
        shape: usize,
        trail: &[String],
        edge: Option<u32>,
        why: (&'static str, &str),
    ) {
        let incomplete =
            self.cx.rules.shapes[shape].incomplete || self.rule.on_unknown == "incomplete";
        if incomplete {
            self.rows.push(ExportRow::Unknown {
                symbol: symbol.to_string(),
                module: module.to_string(),
                trail: trail.to_vec(),
                why: why.0,
                detail: why.1.to_string(),
            });
            return;
        }
        let ledgerable = self.rule.on_unknown == "finding";
        self.finding(
            symbol,
            module,
            "export-unknown",
            trail,
            edge,
            ledgerable,
            true,
        );
    }

    /// `ExportPrimitive.unresolved`.
    fn unresolved(&mut self, id: u32, module: &str) {
        let edges = &self.cx.link.edges;
        let surface = self.cx.text(edges.surface_of(id)).to_string();
        if self.rule.on_unresolved == "incomplete" {
            self.rows.push(ExportRow::Unresolved {
                surface,
                module: module.to_string(),
            });
            return;
        }
        let ledgerable = self.rule.on_unresolved == "finding";
        let label = format!("unresolved-{}", self.cx.text(edges.kind_of(id)));
        let trail = vec![surface.clone()];
        self.finding(&surface, module, &label, &trail, Some(id), ledgerable, true);
    }
}
