//! The export primitive (hoglint/rules/exports.py `ExportPrimitive`): does a public door hand
//! out a denied shape? Every member of an export is checked, recursively through the types its
//! signatures, fields and bases name, in the order the Python primitive checks them.

use super::Rules;
use super::env::{Cx, EntityEnv};
use super::plan::{ExportRulePlan, ShapePlan};
use super::selector::{Compiler, EntityField, Node, Ref, evaluate};
use super::signature::{callable_of, is_private};
use super::value::{Truth, Value, render};
use crate::link::Link;
use crate::link::intern::{EMPTY, Sid};
use crate::link::scope::Res;
use crate::link::surface::HandOut;
use rustc_hash::{FxHashMap, FxHashSet};

/// A `[shapes]` policy (`ShapePolicy`).
pub struct Shape {
    data: Vec<String>,
    allow: Vec<String>,
    deny: Vec<String>,
    /// (kind, `where`) of the policy's `behavior`.
    behavior: Option<(String, Option<Node>)>,
    /// `unknown = "incomplete"`.
    incomplete: bool,
    /// `signatures = true`: an allowed class's methods are part of the contract too.
    signatures: bool,
    /// `allow_where`: a symbol it holds for is allowed as it stands.
    allow_where: Option<Node>,
    /// `walk_where`: where an allowed class that is no data shape is walked.
    walk_where: Option<Node>,
}

impl Shape {
    pub fn new(plan: &ShapePlan, entity: &Compiler) -> Result<Shape, String> {
        let behavior = match &plan.behavior {
            None => None,
            Some(found) => Some((found.kind.clone(), entity.optional(&found.r#where)?)),
        };
        Ok(Shape {
            data: plan.data.clone(),
            allow: plan.allow.clone(),
            deny: plan.deny.clone(),
            behavior,
            incomplete: plan.incomplete,
            signatures: plan.signatures,
            allow_where: entity.optional(&plan.allow_where)?,
            walk_where: entity.optional(&plan.walk_where)?,
        })
    }

    /// The entity selectors the policy reads: its behavior's `where`, `allow_where`, `walk_where`.
    pub fn reads(&self) -> Vec<&Node> {
        let behavior = self.behavior.as_ref().and_then(|(_, found)| found.as_ref());
        behavior
            .into_iter()
            .chain(&self.allow_where)
            .chain(&self.walk_where)
            .collect()
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
    /// Its `from`: the export modules it judges, read of the module alone.
    pub start: Option<Node>,
    /// `leave_slots_to`: the modules whose rows each named signature rule reads; a slot whose row
    /// one of them reads is judged by its class's shape only. None for a rule that reads them in
    /// every module. Empty when the rule judges every slot in full.
    slot_owners: Vec<Option<Node>>,
    include: u8,
    exclude: u8,
    unknown_types: Vec<String>,
    on_unknown: String,
    on_unresolved: String,
    columns: Vec<Column>,
}

impl ExportRule {
    pub fn new(
        plan: &ExportRulePlan,
        identity: &[Vec<String>],
        entity: &Compiler,
        start: Option<Node>,
        slot_owners: Vec<Option<Node>>,
    ) -> ExportRule {
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
            start,
            slot_owners,
            include: plan.include,
            exclude: plan.exclude,
            unknown_types: plan.unknown_types.clone(),
            on_unknown: plan.on_unknown.clone(),
            on_unresolved: plan.on_unresolved.clone(),
            columns,
        }
    }

    /// The selectors it compiles: its `from` and its slot owners.
    pub fn nodes(&self) -> Vec<&Node> {
        self.start
            .iter()
            .chain(self.slot_owners.iter().flatten())
            .collect()
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

/// The module-scope `kind` edges of a module: the imports that can bind a name it hands out.
pub fn module_scope_imports(link: &mut Link, kind: Sid, module: Sid) -> Vec<u32> {
    let module_scope = link.settings.module_scope;
    link.ids_from(kind, module)
        .into_iter()
        .filter(|&id| {
            let scope = link.site_of(id).1;
            scope == module_scope || scope == EMPTY
        })
        .collect()
}

/// Whether import edge `id` binds a name a hand-out holds (`Graph.hand_outs`).
pub fn binds_handed(link: &Link, found: &HandOut, id: u32) -> bool {
    let binds = link.core.it.get("binds");
    link.edges
        .attrs_with(id)
        .any(|&(key, value)| Some(key) == binds && found.handed.contains(&value))
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
        let mut imports = FxHashMap::default();
        if let Some(kind) = link.kind_sid("import") {
            for module in covered.iter().flatten().map(|(module, _)| *module) {
                if imports.contains_key(&module) {
                    continue;
                }
                imports.insert(module, module_scope_imports(link, kind, module));
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
    /// `allow_where` and `walk_where` per (shape, symbol): many export modules name one type.
    allowed: FxHashMap<(usize, String), bool>,
    walked: FxHashMap<(usize, String), bool>,
    exposes_by_name: FxHashMap<Sid, FxHashMap<String, Vec<u32>>>,
    top_level: FxHashMap<Sid, Vec<String>>,
    slots_seen: FxHashMap<String, bool>,
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
            allowed: FxHashMap::default(),
            walked: FxHashMap::default(),
            exposes_by_name: FxHashMap::default(),
            top_level: FxHashMap::default(),
            slots_seen: FxHashMap::default(),
        }
    }

    pub fn run(mut self) -> Vec<ExportRow> {
        let rules = self.cx.rules;
        for &export in &self.rule.exports {
            let shape = rules.export_shapes[export];
            for &(module, _) in &self.inputs.covered[export] {
                if !self.judges(module) {
                    continue;
                }
                let module_name = self.cx.text(module).to_string();
                for (symbol, edge) in self.exported_symbols(module) {
                    let member =
                        rules.export_member(self.cx.link, &rules.exports[export], Some(&symbol));
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
                            self.check(&symbol, &module_name, shape, edge, &trail, false);
                        }
                    }
                }
            }
        }
        self.rows
    }

    /// `ExportPrimitive.judges`: whether the rule judges what a module exports, by its `from`
    /// read of the module alone. A module it cannot tell about is judged.
    fn judges(&self, module: Sid) -> bool {
        let Some(start) = &self.rule.start else {
            return true;
        };
        evaluate(start, &self.cx.module_env(module)) != Truth::False
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

    /// Whether a module hands out what an import edge binds (`Graph.hand_outs`).
    fn hands_out(&self, module: Sid, id: u32) -> bool {
        let link = self.cx.link;
        let Some(found) = link.hand_outs.get(&module) else {
            return false;
        };
        found.unread || binds_handed(link, found, id)
    }

    /// `ExportPrimitive.exported_symbols`: top-level symbols, then the imports the module hands
    /// out; an import that did not resolve is reported on the way.
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
            if !self.hands_out(module, id) {
                continue;
            }
            let edges = &self.cx.link.edges;
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
        let in_lineage = |bases: &FxHashSet<String>| {
            bases.contains(symbol) || ancestors.iter().any(|name| bases.contains(*name))
        };
        let core = &rules.core_bases;
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
        for (name, bases) in &core.shapes {
            if in_lineage(bases) {
                names.insert(name.clone());
            }
        }
        let metaclass = rules.symbol_fact(link, symbol, "metaclass");
        if core.abc_metas.iter().any(|meta| meta == metaclass) {
            names.insert("abc".into());
        }
        (names, complete && !kind_unknown)
    }

    /// `ExportPrimitive.symbol_truth`: a `[shapes]` entity selector, read of a symbol in the
    /// module that defines it.
    fn symbol_truth(&self, node: &Node, symbol: &str) -> Truth {
        let owner = self.cx.module_of_symbol(symbol);
        evaluate(node, &EntityEnv::of(self.cx, owner, Some(symbol), None))
    }

    /// `ExportPrimitive.walks`: whether what an allowed class's members name is judged. Where
    /// `walk_where` cannot be told, the class is walked.
    fn walks(&mut self, symbol: &str, shapes: &FxHashSet<String>, shape: usize) -> bool {
        let policy = &self.cx.rules.shapes[shape];
        let dataclass_like = shapes.contains("dataclass") || shapes.contains("frozen-dataclass");
        if dataclass_like && policy.data.iter().any(|name| shapes.contains(name)) {
            return true;
        }
        if !(policy.signatures || dataclass_like) {
            return false;
        }
        self.walk_holds(symbol, shape)
    }

    /// `ExportPrimitive.walk_holds`: whether `walk_where` holds for a class, or cannot be told.
    fn walk_holds(&mut self, symbol: &str, shape: usize) -> bool {
        let Some(node) = &self.cx.rules.shapes[shape].walk_where else {
            return true;
        };
        let key = (shape, symbol.to_string());
        if let Some(&found) = self.walked.get(&key) {
            return found;
        }
        let found = self.symbol_truth(node, symbol) != Truth::False;
        self.walked.insert(key, found);
        found
    }

    /// `ExportPrimitive.ancestor_walked`: whether the member rows of an ancestor of a walked
    /// class are read: always the fields of a data shape, else where `walk_where` holds for it.
    fn ancestor_walked(&mut self, ancestor: &str, shape: usize) -> bool {
        let (shapes, _) = self.recognize(ancestor);
        let policy = &self.cx.rules.shapes[shape];
        let dataclass_like = shapes.contains("dataclass") || shapes.contains("frozen-dataclass");
        if dataclass_like && policy.data.iter().any(|name| shapes.contains(name)) {
            return true;
        }
        self.walk_holds(ancestor, shape)
    }

    /// `ExportPrimitive.allowed_as_is`: whether the policy's `allow_where` holds for a symbol.
    fn allowed_as_is(&mut self, symbol: &str, shape: usize) -> bool {
        let rules = self.cx.rules;
        let Some(node) = &rules.shapes[shape].allow_where else {
            return false;
        };
        let key = (shape, symbol.to_string());
        if let Some(&found) = self.allowed.get(&key) {
            return found;
        }
        let found = self.symbol_truth(node, symbol) == Truth::True;
        self.allowed.insert(key, found);
        found
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
            if self.symbol_truth(node, &candidate) == Truth::True {
                return true;
            }
        }
        false
    }

    /// `ExportPrimitive.check`. A check of the shape alone (`shape_only`) that finds a denied kind
    /// or an unknown type marks nothing: a full check may still report it.
    fn check(
        &mut self,
        symbol: &str,
        module: &str,
        shape: usize,
        edge: Option<u32>,
        trail: &[String],
        shape_only: bool,
    ) {
        let visit = (module.to_string(), shape, symbol.to_string());
        if self.visited.contains(&visit) {
            return;
        }
        let rules = self.cx.rules;
        let policy = &rules.shapes[shape];
        if self.allowed_as_is(symbol, shape) {
            self.visited.insert(visit);
            return;
        }
        let (shapes, complete) = self.recognize(symbol);
        let mut denied: Vec<&String> = policy
            .deny
            .iter()
            .filter(|name| shapes.contains(*name))
            .collect();
        denied.sort();
        let unknown_type = self.rule.unknown_types.iter().any(|name| name == symbol);
        if shape_only && (!denied.is_empty() || unknown_type) {
            return;
        }
        self.visited.insert(visit);
        if let Some(first) = denied.first() {
            let label = format!("export-denied({first})");
            self.finding(symbol, module, &label, trail, edge, true, false);
            return;
        }
        if unknown_type {
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
            if self.walks(symbol, &shapes, shape) {
                self.check_members(symbol, module, shape, trail, edge);
            }
            return;
        }
        if shapes.contains("function") {
            self.check_members(symbol, module, shape, trail, edge);
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

    /// `ExportPrimitive.member_handles`: each row with the module and qualname of the class or
    /// callable it belongs to; of the ancestors only those the policy walks.
    fn member_edges(
        &mut self,
        symbol: &str,
        include_bases: bool,
        shape: usize,
    ) -> Vec<(u32, Sid, String)> {
        let mut lineage = vec![symbol.to_string()];
        if include_bases {
            let (ancestors, _) = self.cx.rules.facts.ancestors(self.cx.link, symbol);
            let mut sorted: Vec<String> = ancestors.into_iter().map(str::to_string).collect();
            sorted.sort();
            for ancestor in sorted {
                if self.ancestor_walked(&ancestor, shape) {
                    lineage.push(ancestor);
                }
            }
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
            for id in self.exposes_under(owner, &qualname) {
                out.push((id, owner, qualname.clone()));
            }
        }
        out
    }

    /// `ExportPrimitive.holds_copy`: whether a module holds an exposes row of a callable's slot.
    fn holds_copy(&mut self, module: Sid, callable_name: &str, slot: &str) -> bool {
        let link = self.cx.link;
        let callable = link.core.it.get("callable");
        let slot_attr = link.core.it.get("slot");
        self.exposes_under(module, callable_name)
            .into_iter()
            .any(|id| {
                self.cx.attr(id, callable) == callable_name && self.cx.attr(id, slot_attr) == slot
            })
    }

    /// `ExportPrimitive.leaves_slot`: whether a slot is left to a signature rule, a public
    /// callable's slot whose row that rule reads.
    fn leaves_slot(
        &mut self,
        id: u32,
        owner: Sid,
        qualname: &str,
        module: &str,
        bound: &str,
    ) -> bool {
        let link = self.cx.link;
        let row_callable = self.cx.attr(id, link.core.it.get("callable"));
        if is_private(row_callable) {
            return false;
        }
        let owner_name = self.cx.text(owner).to_string();
        if self.signature_sees(&owner_name) {
            return true;
        }
        if bound.is_empty() || !self.signature_sees(module) {
            return false;
        }
        let Some(module_sid) = link.core.it.get(module) else {
            return false;
        };
        let copy = format!("{bound}{}", &row_callable[qualname.len()..]);
        let slot = self.cx.attr(id, link.core.it.get("slot"));
        self.holds_copy(module_sid, &copy, slot)
    }

    /// `ExportPrimitive.signature_sees`: whether a signature rule sees the slots of a module's
    /// public callables. A module a rule cannot tell about is not seen.
    fn signature_sees(&mut self, module: &str) -> bool {
        if let Some(&seen) = self.slots_seen.get(module) {
            return seen;
        }
        let seen = match self.cx.link.core.it.get(module) {
            None => false,
            Some(sid) => {
                let env = self.cx.module_env(sid);
                self.rule.slot_owners.iter().any(|owner| match owner {
                    None => true,
                    Some(node) => evaluate(node, &env) == Truth::True,
                })
            }
        };
        self.slots_seen.insert(module.to_string(), seen);
        seen
    }

    /// `ExportPrimitive.check_members`; `edge` is the import the module hands the symbol out
    /// by, if any.
    fn check_members(
        &mut self,
        symbol: &str,
        module: &str,
        shape: usize,
        trail: &[String],
        edge: Option<u32>,
    ) {
        let link = self.cx.link;
        let bound = match edge {
            Some(id) if trail.len() == 1 => self.cx.attr(id, link.core.it.get("binds")),
            _ => "",
        };
        let include_bases = self.cx.rules.symbol_decl(link, symbol) == "class";
        let annotation = link.core.it.get("annotation");
        let slot = link.core.it.get("slot");
        let alias = link.core.it.get("alias");
        for (id, owner, qualname) in self.member_edges(symbol, include_bases, shape) {
            if self.cx.attr(id, alias) == "expanded" {
                continue;
            }
            let shape_only = self.leaves_slot(id, owner, &qualname, module, bound);
            if self.cx.attr(id, annotation) == "missing" {
                if !shape_only {
                    let why = ("no-annotation", self.cx.attr(id, slot));
                    self.unknown(symbol, module, shape, trail, Some(id), why);
                }
                continue;
            }
            let edges = &link.edges;
            if edges.res_of(id) == Res::Unresolved {
                if !shape_only {
                    self.unresolved(id, module);
                }
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
                    self.check(&name, module, shape, Some(id), &deeper, shape_only);
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
