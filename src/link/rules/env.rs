//! Endpoints and the selector environment of one edge (hoglint/rules/base.py `Endpoint`,
//! `EdgeEnv`, `read_endpoint`), and the label environment a field's domain reads
//! (`Labeler.label_resolver`).

use super::Rules;
use super::conflicts::Conflict;
use super::labels::{Cell, FieldKind};
use super::selector::{EdgeField, EndField, EntityField, Ref, Resolve, evaluate};
use super::value::{Truth, Value, any_of, split_list};
use crate::link::Link;
use crate::link::intern::{EMPTY, Sid};
use crate::link::scope::Res;
use std::borrow::Cow;
use std::cell::Cell as Flag;

/// A rule's view of an edge's destination.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Role {
    Origin,
    Surface,
    Via,
}

/// The dotted name of an endpoint's symbol: none, one interned name, or a module and a name
/// inside it.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Symbol {
    None,
    Name(Sid),
    Inside(Sid, Sid),
}

/// One end of an edge as a rule judges it (`rules.base.Endpoint`).
#[derive(Clone, Copy, Debug)]
pub struct Endpoint<'a> {
    pub module: Option<Sid>,
    /// The module's place among the graph's modules; None outside the graph.
    pub at: Option<u32>,
    pub symbol: Symbol,
    pub path: Option<&'a str>,
    pub external: Option<&'a str>,
    pub unresolved: bool,
    pub scope: &'a str,
}

const UNRESOLVED: Endpoint<'static> = Endpoint {
    module: None,
    at: None,
    symbol: Symbol::None,
    path: None,
    external: None,
    unresolved: true,
    scope: "",
};

/// The link and the run's tables, which every environment reads.
#[derive(Clone, Copy)]
pub struct Cx<'a> {
    pub link: &'a Link,
    pub rules: &'a Rules,
}

fn head(name: &str) -> &str {
    name.split('.').next().unwrap_or(name)
}

/// The first dotted part of a symbol's name: an external endpoint's package.
fn head_owned<'a>(cx: &Cx<'a>, name: Symbol) -> &'a str {
    match name {
        Symbol::None => "",
        Symbol::Name(sid) | Symbol::Inside(sid, _) => head(cx.text(sid)),
    }
}

impl<'a> Cx<'a> {
    pub fn text(&self, sid: Sid) -> &'a str {
        self.link.core.it.s(sid)
    }

    pub fn symbol_text(&self, symbol: Symbol) -> Option<Cow<'a, str>> {
        match symbol {
            Symbol::None => None,
            Symbol::Name(name) => Some(Cow::Borrowed(self.text(name))),
            Symbol::Inside(module, name) => Some(Cow::Owned(format!(
                "{}.{}",
                self.text(module),
                self.text(name)
            ))),
        }
    }

    /// The graph module of `module`, if it is one.
    pub fn module_at(&self, module: Sid) -> Option<u32> {
        self.rules.module_at.get(&module).copied()
    }

    /// The longest dotted prefix of `name` that is a graph module (`GraphIndex.find_module`).
    pub fn module_of_symbol(&self, name: &str) -> Option<(Sid, u32)> {
        let it = &self.link.core.it;
        let mut head = name;
        loop {
            if let Some(sid) = it.get(head)
                && let Some(at) = self.module_at(sid)
            {
                return Some((sid, at));
            }
            head = head.rsplit_once('.')?.0;
        }
    }

    pub fn path_at(&self, at: Option<u32>) -> Option<&'a str> {
        at.map(|at| self.rules.module_path(self.link, at))
    }

    /// The `src` endpoint of edge `id` (`RuleContext.src_endpoint`).
    pub fn src_endpoint(&self, id: u32) -> Endpoint<'a> {
        let (module, scope) = self.link.site_of(id);
        let at = self.module_at(module);
        let scope_text = self.text(scope);
        let scoped = scope != EMPTY && scope != self.link.settings.module_scope;
        let site_path = self.link.path_of_edge(id);
        let path = if site_path.is_empty() {
            self.path_at(at)
        } else {
            Some(site_path)
        };
        Endpoint {
            module: Some(module),
            at,
            symbol: if scoped {
                Symbol::Inside(module, scope)
            } else {
                Symbol::None
            },
            path,
            external: None,
            unresolved: false,
            scope: if scoped { scope_text } else { "" },
        }
    }

    /// The endpoint a module alone is (`RuleContext.module_endpoint`).
    pub fn module_endpoint(&self, module: Sid) -> Endpoint<'a> {
        let at = self.module_at(module);
        Endpoint {
            module: Some(module),
            at,
            symbol: Symbol::None,
            path: self.path_at(at),
            external: None,
            unresolved: false,
            scope: "",
        }
    }

    /// `RuleContext.new_symbol_endpoint`: the endpoint a dotted name is.
    fn symbol_endpoint(&self, name: Symbol, external: bool) -> Endpoint<'a> {
        let text = self.symbol_text(name).unwrap_or_default();
        let found = self.module_of_symbol(&text);
        if external {
            let module = match found {
                Some((module, _)) => Some(module),
                None => match name {
                    Symbol::Name(sid) => Some(sid),
                    _ => self.link.core.it.get(&text),
                },
            };
            let at = found.map(|(_, at)| at);
            return Endpoint {
                module,
                at,
                symbol: name,
                path: self.path_at(at),
                external: Some(head_owned(self, name)),
                unresolved: false,
                scope: "",
            };
        }
        let Some((module, at)) = found else {
            return Endpoint {
                symbol: name,
                ..UNRESOLVED
            };
        };
        Endpoint {
            module: Some(module),
            at: Some(at),
            symbol: if self.text(module) == text {
                Symbol::None
            } else {
                name
            },
            path: self.path_at(Some(at)),
            external: None,
            unresolved: false,
            scope: "",
        }
    }

    /// The `dst` endpoints of edge `id` as `role` reads them (`RuleContext.dst_endpoints`).
    pub fn dst_endpoints(&self, id: u32, role: Role, out: &mut Vec<Endpoint<'a>>) {
        out.clear();
        let edges = &self.link.edges;
        let (res, surface) = (edges.res_of(id), edges.surface_of(id));
        match role {
            Role::Via => {
                for hop in edges.via_of_id(id) {
                    let name = if hop.s == EMPTY {
                        Symbol::Name(hop.m)
                    } else {
                        Symbol::Inside(hop.m, hop.s)
                    };
                    out.push(self.symbol_endpoint(name, false));
                }
            }
            Role::Surface => {
                out.push(self.symbol_endpoint(Symbol::Name(surface), res == Res::External))
            }
            Role::Origin => {
                let targets = edges.targets_of(id);
                if res == Res::Unresolved || targets.is_empty() {
                    out.push(Endpoint {
                        symbol: Symbol::Name(surface),
                        ..UNRESOLVED
                    });
                    return;
                }
                let external = res == Res::External;
                for target in targets {
                    let at = self.module_at(target.m);
                    let named = if target.s == EMPTY {
                        Symbol::Name(target.m)
                    } else {
                        Symbol::Inside(target.m, target.s)
                    };
                    out.push(Endpoint {
                        module: Some(target.m),
                        at,
                        symbol: if external || target.s != EMPTY {
                            named
                        } else {
                            Symbol::None
                        },
                        path: self.path_at(at),
                        external: external.then(|| head(self.text(target.m))),
                        unresolved: false,
                        scope: "",
                    });
                }
            }
        }
    }

    /// Whether the edge has `src.*`/`dst.*` attrs (`has_endpoint_attrs`).
    pub fn has_endpoint_attrs(&self, id: u32) -> bool {
        let keys = &self.rules.endpoint_keys;
        !keys.is_empty()
            && self
                .link
                .edges
                .attrs_with(id)
                .any(|(key, _)| keys.contains(key))
    }

    pub fn attr(&self, id: u32, name: Option<Sid>) -> &'a str {
        let Some(name) = name else {
            return "";
        };
        self.link
            .edges
            .attrs_with(id)
            .find(|(key, _)| *key == name)
            .map_or("", |(_, value)| self.text(*value))
    }

    pub fn cell(&self, cell: &'a Cell) -> Value<'a> {
        match cell {
            Cell::Unknown => Value::Unknown,
            Cell::None => Value::None,
            Cell::Text(text) => Value::text(text),
            Cell::Bool(flag) => Value::Bool(*flag),
            Cell::Set(items) => Value::Set(items.iter().map(AsRef::as_ref).collect()),
            Cell::Zone(code) => Value::Zone(&self.rules.labels.zones.infos[*code as usize]),
        }
    }

    /// The symbol's kind from [kinds] (`Labeler.kind`).
    pub fn kind(&self, symbol: &str, first_party: bool) -> Value<'a> {
        let rules = self.rules;
        if let Some(kinds) = rules.kind_sets.kinds_of.get(symbol) {
            return match kinds.as_slice() {
                [one] => Value::text(&rules.kind_names[*one]),
                _ => Value::Unknown,
            };
        }
        let without_facts =
            first_party && !rules.kind_names.is_empty() && !rules.has_symbol(self.link, symbol);
        if rules.kind_sets.unknown.contains(symbol) || without_facts {
            return Value::Unknown;
        }
        Value::None
    }

    /// The registry of a graph module, and its lifecycle.
    fn registry(&self, at: Option<u32>, lifecycle: bool) -> Value<'a> {
        let Some(found) = at.and_then(|at| self.rules.labels.registry_of[at as usize]) else {
            return Value::None;
        };
        let registry = &self.rules.labels.registries[found as usize];
        if lifecycle {
            Value::text_or_none(&registry.lifecycle)
        } else {
            Value::text(&registry.name)
        }
    }

    /// A field's value on a module and, for a symbol field, the symbol (`Labeler.field_value`).
    pub fn field_value(
        &self,
        field: usize,
        module: Option<Sid>,
        at: Option<u32>,
        symbol: Option<&str>,
    ) -> Value<'a> {
        let rules = self.rules;
        let spec = &rules.labels.fields[field];
        if !spec.on_symbol {
            if let Some(at) = at {
                rules.read_module_field(field, at);
                return self.cell(&rules.values[field][at as usize]);
            }
            if rules.labels.failed[field] {
                return Value::Unknown;
            }
            return self.domain_value(field, module, at, None);
        }
        let Some(symbol) = symbol else {
            return Value::None;
        };
        if let Some(label) = rules.symbol_label(self.link, symbol, field) {
            return label;
        }
        let fact = rules.symbol_fact(self.link, symbol, &spec.name);
        if !fact.is_empty() {
            return Value::text(fact);
        }
        if rules.labels.failed[field] {
            return Value::Unknown;
        }
        self.domain_value(field, module, at, Some(symbol))
    }

    /// Unknown inside the field's domain, else none (`Labeler.compute_value` with no label).
    pub fn domain_value(
        &self,
        field: usize,
        module: Option<Sid>,
        at: Option<u32>,
        symbol: Option<&str>,
    ) -> Value<'a> {
        let (value, read) = self.domain_value_read(field, module, at, symbol);
        if let (true, Some(at)) = (read, at) {
            self.rules.meet(Conflict::Provided(at));
        }
        value
    }

    /// `domain_value`, and whether the domain read provider labels of the module that are set
    /// two ways.
    pub fn domain_value_read(
        &self,
        field: usize,
        module: Option<Sid>,
        at: Option<u32>,
        symbol: Option<&str>,
    ) -> (Value<'a>, bool) {
        let Some(domain) = &self.rules.labels.fields[field].domain else {
            return (Value::None, false);
        };
        let mut read = false;
        let inside = if domain.everywhere {
            true
        } else if let Some(selector) = &domain.selector {
            let env = LabelEnv {
                cx: *self,
                module,
                at,
                symbol,
                read_conflict: Flag::new(false),
            };
            let inside = evaluate(selector, &env) == Truth::True;
            read = env.read_conflict.get();
            inside
        } else {
            self.path_at(at)
                .is_some_and(|path| domain.globs.iter().any(|glob| glob.is_match(path)))
        };
        (if inside { Value::Unknown } else { Value::None }, read)
    }
}

/// `Endpoint.label_symbol`: the top-level symbol that carries symbol labels.
fn label_symbol<'s>(module: &str, symbol: &'s str) -> Cow<'s, str> {
    let Some(rest) = symbol
        .strip_prefix(module)
        .and_then(|rest| rest.strip_prefix('.'))
    else {
        return Cow::Borrowed(symbol);
    };
    Cow::Owned(format!("{module}.{}", head(rest)))
}

/// The environment of one edge and one destination (`EdgeEnv`). With no edge it is the
/// environment of two endpoints (`EndpointsEnv`): edge fields are unknown.
pub struct Env<'a> {
    pub cx: Cx<'a>,
    pub edge: Option<u32>,
    pub src: Endpoint<'a>,
    pub dst: Endpoint<'a>,
    /// Whether the edge has `src.*`/`dst.*` attrs, which override its endpoints' labels.
    pub attrs: bool,
    /// (shape, shape detail) a primitive sets for itself in place of the edge's (use-shape's
    /// flow shape).
    pub shape: Option<(&'a str, &'a str)>,
}

impl<'a> Resolve<'a> for Env<'a> {
    fn resolve(&self, field: Ref) -> Value<'a> {
        match field {
            Ref::Edge(field) => match self.edge {
                Some(id) => self.edge_value(id, field),
                None => Value::Unknown,
            },
            Ref::Src(field) => self.endpoint_value(&self.src, field),
            Ref::Dst(field) => self.endpoint_value(&self.dst, field),
            Ref::Entity(_) | Ref::Unknown => Value::Unknown,
        }
    }
}

impl<'a> Env<'a> {
    pub fn new(cx: Cx<'a>, edge: Option<u32>, src: Endpoint<'a>, dst: Endpoint<'a>) -> Env<'a> {
        let attrs = edge.is_some_and(|id| cx.has_endpoint_attrs(id));
        Env {
            cx,
            edge,
            src,
            dst,
            attrs,
            shape: None,
        }
    }

    /// The edge's (shape, shape detail), or the ones the primitive set (`EdgeEnv.shape`).
    pub fn shape_of(&self, id: u32) -> (&'a str, &'a str) {
        if let Some(shape) = self.shape {
            return shape;
        }
        let (shape, detail) = self.cx.link.edges.shape_of(id);
        (self.cx.text(shape), self.cx.text(detail))
    }

    fn edge_value(&self, id: u32, field: EdgeField) -> Value<'a> {
        let cx = self.cx;
        let edges = &cx.link.edges;
        let text = |sid: Sid| Value::text_or_none(cx.text(sid));
        match field {
            EdgeField::Kind => text(edges.kind_of(id)),
            EdgeField::Shape => Value::text_or_none(self.shape_of(id).0),
            EdgeField::Use => text(edges.use_of(id)),
            EdgeField::Resolution => Value::text(edges.res_of(id).name()),
            EdgeField::Surface => text(edges.surface_of(id)),
            EdgeField::Producer => text(edges.producer_of(id)),
            EdgeField::Context => Value::Contexts(edges.seen_ctx(id)),
            EdgeField::Ordinal => Value::Text(Cow::Owned(cx.rules.ordinal(id).to_string())),
            EdgeField::Label => self.label(id),
            EdgeField::Attr(name) => Value::text_or_none(cx.attr(id, name)),
        }
    }

    /// `EdgeEnv.label`.
    fn label(&self, id: u32) -> Value<'a> {
        let cx = self.cx;
        let edges = &cx.link.edges;
        let kind = cx.text(edges.kind_of(id));
        if self.dst.unresolved {
            return Value::Text(Cow::Owned(format!("unresolved-{kind}")));
        }
        let (shape, detail) = self.shape_of(id);
        if shape.is_empty() {
            return Value::text_or_none(kind);
        }
        if detail.is_empty() {
            return Value::text(shape);
        }
        Value::Text(Cow::Owned(format!("{shape}({detail})")))
    }

    fn endpoint_value(&self, endpoint: &Endpoint<'a>, field: EndField) -> Value<'a> {
        if let Some(id) = self.edge {
            match field {
                EndField::Context => return Value::Contexts(self.cx.link.edges.seen_ctx(id)),
                EndField::Exported if endpoint.unresolved => return Value::Unknown,
                EndField::Exported => return self.exported(endpoint),
                _ => {}
            }
        }
        self.read_endpoint(endpoint, field)
    }

    /// `read_endpoint`: the endpoint answers what it knows better than its module, then labels.
    fn read_endpoint(&self, endpoint: &Endpoint<'a>, field: EndField) -> Value<'a> {
        let cx = self.cx;
        if field == EndField::Symbol {
            return match cx
                .symbol_text(endpoint.symbol)
                .filter(|symbol| !symbol.is_empty())
            {
                Some(symbol) => Value::Text(symbol),
                None => match endpoint.module.map(|module| cx.text(module)) {
                    Some(module) if !module.is_empty() => Value::text(module),
                    _ => Value::Unknown,
                },
            };
        }
        if endpoint.unresolved {
            return Value::Unknown;
        }
        let module = endpoint.module.map(|module| cx.text(module));
        match field {
            EndField::Module | EndField::Subject => module.map_or(Value::None, Value::text_or_none),
            EndField::Path => endpoint.path.map_or(Value::None, Value::text_or_none),
            EndField::Scope => Value::text_or_none(endpoint.scope),
            EndField::Name => match cx.symbol_text(endpoint.symbol) {
                Some(symbol) if !symbol.is_empty() => Value::Text(last_name(symbol)),
                _ => match module {
                    Some(module) if !module.is_empty() => {
                        Value::text(module.rsplit('.').next().unwrap_or(""))
                    }
                    _ => Value::None,
                },
            },
            EndField::External => endpoint.external.map_or(Value::None, Value::text_or_none),
            EndField::Kind => match cx.symbol_text(endpoint.symbol) {
                Some(symbol) => cx.kind(&symbol, endpoint.external.is_none()),
                None => Value::None,
            },
            EndField::Decl => match cx.symbol_text(endpoint.symbol) {
                Some(symbol) => Value::text_or_none(cx.rules.symbol_decl(cx.link, &symbol)),
                None => Value::None,
            },
            EndField::Registry => cx.registry(endpoint.at, false),
            EndField::Lifecycle => cx.registry(endpoint.at, true),
            EndField::Field(at, attr) => self.declared(endpoint, at, attr),
            EndField::Undeclared(attr) => match self.own_attr(attr) {
                "" => Value::Unknown,
                raw => Value::text(raw),
            },
            // Read through `read_endpoint`, these name no declared field (`EndpointsEnv`).
            EndField::Context | EndField::Exported => Value::Unknown,
            EndField::Symbol => unreachable!("answered above"),
        }
    }

    fn own_attr(&self, attr: Option<Sid>) -> &'a str {
        match self.edge {
            Some(id) if self.attrs => self.cx.attr(id, attr),
            _ => "",
        }
    }

    fn declared(&self, endpoint: &Endpoint<'a>, field: usize, attr: Option<Sid>) -> Value<'a> {
        let cx = self.cx;
        let spec = &cx.rules.labels.fields[field];
        let raw = self.own_attr(attr);
        if !raw.is_empty() {
            return if spec.kind == FieldKind::Set {
                Value::Set(split_list(raw))
            } else {
                Value::text(raw)
            };
        }
        if endpoint.external.is_some() {
            return Value::None;
        }
        let symbol = match (spec.on_symbol, cx.symbol_text(endpoint.symbol)) {
            (true, Some(symbol)) => Some(match endpoint.module {
                Some(module) => label_symbol(cx.text(module), &symbol).into_owned(),
                None => symbol.into_owned(),
            }),
            _ => None,
        };
        cx.field_value(field, endpoint.module, endpoint.at, symbol.as_deref())
    }

    /// `EdgeEnv.exported`, uncached: each export that holds the endpoint, with its audience
    /// read through this environment (`Labeler.compute_exported`).
    fn exported(&self, endpoint: &Endpoint<'a>) -> Value<'a> {
        if endpoint.external.is_some() {
            return Value::None;
        }
        let cx = self.cx;
        let Some(at) = endpoint.at else {
            return Value::Unknown;
        };
        let bits = cx.rules.labels.exports_of[at as usize];
        let symbol = cx.symbol_text(endpoint.symbol);
        let mut results = Vec::new();
        for (index, export) in cx.rules.exports.iter().enumerate() {
            if bits & (1 << index) == 0 {
                continue;
            }
            let mut truth = if export.subclass_of.is_empty() {
                Truth::True
            } else {
                match &symbol {
                    None => Truth::False,
                    Some(symbol) => cx
                        .rules
                        .member_by_class(cx.link, &export.subclass_of, symbol),
                }
            };
            if truth == Truth::False {
                continue;
            }
            if truth == Truth::True
                && let Some(audience) = &export.audience
            {
                truth = evaluate(audience, self);
            }
            results.push(truth);
        }
        match any_of(results) {
            Truth::Unknown => Value::Unknown,
            truth => Value::Bool(truth == Truth::True),
        }
    }
}

fn last_name(text: Cow<'_, str>) -> Cow<'_, str> {
    match text {
        Cow::Borrowed(text) => Cow::Borrowed(text.rsplit('.').next().unwrap_or("")),
        Cow::Owned(text) => Cow::Owned(text.rsplit('.').next().unwrap_or("").to_string()),
    }
}

/// What a field's domain reads (`Labeler.label_resolver`): builtins of the entity, and the
/// labels of other fields, with no domain.
pub struct LabelEnv<'a, 's> {
    pub cx: Cx<'a>,
    pub module: Option<Sid>,
    pub at: Option<u32>,
    pub symbol: Option<&'s str>,
    /// Whether it read provider labels of its module that are set two ways.
    pub read_conflict: Flag<bool>,
}

impl<'a> Resolve<'a> for LabelEnv<'a, '_> {
    fn resolve(&self, field: Ref) -> Value<'a> {
        let cx = self.cx;
        let Ref::Entity(field) = field else {
            return Value::Unknown;
        };
        let module = self.module.map(|module| cx.text(module));
        let owned = |text: &str| Value::Text(Cow::Owned(text.to_string()));
        match field {
            EntityField::Module | EntityField::Subject => {
                module.map_or(Value::None, Value::text_or_none)
            }
            EntityField::Symbol => self.symbol.map_or(Value::None, |symbol| {
                if symbol.is_empty() {
                    Value::None
                } else {
                    owned(symbol)
                }
            }),
            EntityField::Path => cx.path_at(self.at).map_or(Value::None, Value::text_or_none),
            EntityField::Name => match self.symbol.filter(|symbol| !symbol.is_empty()).or(module) {
                Some(target) if !target.is_empty() => {
                    owned(target.rsplit('.').next().unwrap_or(""))
                }
                _ => Value::None,
            },
            EntityField::Decl => match self.symbol {
                Some(symbol) => Value::text_or_none(cx.rules.symbol_decl(cx.link, symbol)),
                None => Value::None,
            },
            EntityField::Kind => match self.symbol {
                Some(symbol) if !symbol.is_empty() => cx.kind(symbol, true),
                _ => Value::None,
            },
            EntityField::Registry => cx.registry(self.at, false),
            EntityField::Lifecycle => cx.registry(self.at, true),
            EntityField::Field(field) => self.label_only(field),
            EntityField::Unknown => Value::Unknown,
        }
    }
}

impl<'a> LabelEnv<'a, '_> {
    /// A field as its labels say, without the domain (`Labeler.label_only`).
    fn label_only(&self, field: usize) -> Value<'a> {
        let cx = self.cx;
        let rules = cx.rules;
        if rules.labels.fields[field].on_symbol {
            return match self.symbol.filter(|symbol| !symbol.is_empty()) {
                Some(symbol) => rules
                    .symbol_label(cx.link, symbol, field)
                    .unwrap_or(Value::None),
                None => Value::None,
            };
        }
        let Some(at) = self.at else {
            return Value::None;
        };
        let labels = &rules.labels;
        if labels.fields[field].provided && labels.provider_conflicts[at as usize] {
            self.read_conflict.set(true);
        }
        match &labels.labels[field][at as usize] {
            Some(label) => cx.cell(label),
            None => Value::None,
        }
    }
}

/// What an entity selector reads (`Labeler.entity_resolver`): builtins of the entity, and
/// declared fields with their domain, as `Labeler.field_value` reads them.
pub struct EntityEnv<'a, 's> {
    pub cx: Cx<'a>,
    pub module: Option<Sid>,
    pub at: Option<u32>,
    pub symbol: Option<&'s str>,
    /// What a bare `subject` reads, when an entity rule names one (`EntityRule.resolver`).
    pub subject: Option<&'s str>,
}

impl<'a> Resolve<'a> for EntityEnv<'a, '_> {
    fn resolve(&self, field: Ref) -> Value<'a> {
        let Ref::Entity(field) = field else {
            return Value::Unknown;
        };
        let label = LabelEnv {
            cx: self.cx,
            module: self.module,
            at: self.at,
            symbol: self.symbol,
            read_conflict: Flag::new(false),
        };
        match field {
            EntityField::Subject if self.subject.is_some() => {
                Value::Text(Cow::Owned(self.subject.unwrap_or_default().to_string()))
            }
            EntityField::Field(at) => {
                let symbol = self.symbol.filter(|symbol| !symbol.is_empty());
                self.cx.field_value(at, self.module, self.at, symbol)
            }
            other => label.resolve(Ref::Entity(other)),
        }
    }
}
