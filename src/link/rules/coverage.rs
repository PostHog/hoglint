//! The paths a coverage rule needs covered: another rule's column read off its findings'
//! witnesses, the endpoints of an edge selection (or the modules on a name's path to its origin),
//! and the modules or the locations an entity selection keeps.
//! Whether an outside input covers each path stays with the coverage adapter, a Python plugin.

use super::env::{Cx, Endpoint, EntityEnv, Env, Role, Symbol};
use super::labels::Field;
use super::plan::CoverPlan;
use super::selector::{Compiler, Node, Ref, Resolve, evaluate};
use super::staged::{Staged, class_truth, parts_truth};
use super::value::{Truth, Value, all_of, render};
use crate::link::Link;
use crate::link::intern::Sid;
use rustc_hash::{FxHashMap, FxHashSet};

/// One thing a coverage rule needs covered (`CoverSpec`), compiled.
pub enum Cover {
    /// A column of another rule's findings, read off each witness with its first destination.
    Rule { column: Ref },
    /// The `src`, `dst` or `surface` paths of the edges a selection keeps.
    Edges {
        /// The kinds the selection pins, sorted; every edge when it pins none.
        kinds: Option<Vec<String>>,
        /// The conjuncts that read only an edge's kind and contexts.
        by_edge: Option<Node>,
        /// The other conjuncts, staged as an edge rule's selection is.
        rest: Staged,
        src: bool,
        /// The destination is the one the edge names (a patch target's module), not its origin.
        surface: bool,
        /// Every module on the name's path: the one the code names, those it passes through, and
        /// its origin (`: chain`).
        chain: bool,
        /// The selection reads `via.*`: it holds when it holds at one module the name passes
        /// through between the surface and the origin.
        via: bool,
        /// The module that builds the sequence the origin names and the modules that define its
        /// items (`: items`).
        items: bool,
    },
    /// The files of the modules an entity selection keeps, in its `and` parts. With `location`,
    /// the place each such module stands in instead (`location_of`). With `registered`, what the
    /// attribute its registry's loader reads there registers, as `: items` reads a list.
    Modules {
        selection: Vec<Node>,
        location: bool,
        registered: bool,
    },
}

impl Cover {
    pub fn new(
        plan: &CoverPlan,
        edge: &Compiler,
        entity: &Compiler,
        fields: &[Field],
    ) -> Result<Cover, String> {
        if plan.rule.is_some() {
            return Ok(Cover::Rule {
                column: edge.reference(&plan.column),
            });
        }
        if ["module", "location", "registered"].contains(&plan.endpoint.as_str()) {
            return Ok(Cover::Modules {
                selection: entity.nodes(&plan.rest)?,
                location: plan.endpoint == "location",
                registered: plan.endpoint == "registered",
            });
        }
        let rest = edge.nodes(&plan.rest)?;
        let mut refs = Vec::new();
        for node in &rest {
            node.refs(&mut refs);
        }
        let via = refs.iter().any(|field| matches!(field, Ref::Via(_)));
        Ok(Cover::Edges {
            kinds: plan.kinds.clone(),
            by_edge: plan
                .by_edge
                .as_ref()
                .map(|node| edge.node(node))
                .transpose()?,
            rest: Staged::new(&rest, fields),
            src: plan.endpoint == "src",
            surface: plan.endpoint == "surface",
            chain: plan.endpoint == "chain",
            via,
            items: plan.endpoint == "items",
        })
    }

    pub fn refs(&self, out: &mut Vec<Ref>) {
        match self {
            Cover::Rule { column, .. } => out.push(*column),
            Cover::Edges { by_edge, rest, .. } => {
                for node in by_edge.iter().chain(rest.parts.iter()) {
                    node.refs(out);
                }
            }
            Cover::Modules { selection, .. } => {
                for node in selection {
                    node.refs(out);
                }
            }
        }
    }
}

/// What a coverage rule's covers need, as data the rule's Python class words
/// (`CoveragePrimitive`): every path with the cover that names it first (a location cover
/// last) and the module that needs it, sorted by path; and in order, what it cannot tell, (what,
/// cover, source module, surface): "not-run" for a cover whose rule did not run, "unplaced" for
/// an edge it cannot place, "unknown-selection" for a module an entity cover cannot tell it keeps,
/// "unread-items" for a sequence with an item it cannot read (the surface is the sequence),
/// "unread-core-items" for one in a list no group owns that a registration point registers,
/// "computed-load" for a module a registration point names by a value the link cannot read
/// exactly (the surface is that place, in words).
#[derive(Default)]
pub struct Required {
    pub paths: Vec<(String, u32, Option<String>)>,
    pub incomplete: Vec<(&'static str, u32, Option<String>, String)>,
}

/// The edge ids each cover reads: the witnesses of the named rule's findings, in order, or None
/// when that rule did not run; every edge of the cover's kinds for an edge selection.
pub type CoverInputs = Vec<Option<Vec<u32>>>;

/// What the covers need, over the inputs Python gathered (`CoverInputs`).
pub fn required(cx: Cx, covers: &[Cover], inputs: &CoverInputs) -> Required {
    let mut out = Required::default();
    let mut found: FxHashMap<String, (u32, Option<String>)> = FxHashMap::default();
    let mut dsts: Vec<Endpoint> = Vec::with_capacity(2);
    let mut hops: Vec<Endpoint> = Vec::new();
    let mut named: Vec<Endpoint> = Vec::with_capacity(1);
    let mut surfaces: Vec<Endpoint> = Vec::with_capacity(1);
    // A location cover's rows go in last: a directory another cover needs file by file (an empty
    // directory without `__init__.py`) keeps that need.
    let mut locations: Vec<(String, u32, String)> = Vec::new();
    for (at, (cover, ids)) in covers.iter().zip(inputs).enumerate() {
        let at = at as u32;
        match cover {
            Cover::Rule { column } => {
                let Some(ids) = ids else {
                    out.incomplete.push(("not-run", at, None, String::new()));
                    continue;
                };
                for &id in ids {
                    let src = cx.src_endpoint(id);
                    cx.dst_endpoints(id, Role::Origin, &mut dsts);
                    let dst = dsts[0];
                    let value = Env::new(cx, Some(id), src, dst).resolve(*column);
                    if !matches!(value, Value::Unknown | Value::None) {
                        found.entry(render(&value)).or_insert((at, None));
                        continue;
                    }
                    let files = match column {
                        Ref::Dst(_) => cx.namespace_files(&dst),
                        _ => None,
                    };
                    if let Some(files) = files {
                        for path in files {
                            found.entry(path).or_insert((at, None));
                        }
                    } else if matches!(value, Value::Unknown) || cx.namespace_clash(&dst) {
                        out.incomplete.push(unplaced(cx, at, &src, id));
                    }
                }
            }
            Cover::Edges {
                by_edge,
                rest,
                src: from_src,
                surface,
                chain,
                via,
                items,
                ..
            } => {
                let role = if *surface {
                    Role::Surface
                } else {
                    Role::Origin
                };
                for &id in ids.as_deref().unwrap_or_default() {
                    let src = cx.src_endpoint(id);
                    let edge_truth = match by_edge {
                        Some(node) => evaluate(node, &Env::new(cx, Some(id), src, src)),
                        None => Truth::True,
                    };
                    if edge_truth == Truth::False {
                        continue;
                    }
                    cx.dst_endpoints(id, role, &mut dsts);
                    if *via || *chain {
                        cx.dst_endpoints(id, Role::Via, &mut hops);
                    }
                    if *chain {
                        cx.dst_endpoints(id, Role::Surface, &mut named);
                    }
                    for dst in dsts.iter() {
                        let mut dst = dst;
                        // An origin the link cannot name, inside a module of the graph (a name
                        // only a module's `__getattr__` serves), is judged as that module's.
                        if *items && dst.unresolved {
                            cx.dst_endpoints(id, Role::Surface, &mut surfaces);
                            if let Some(inside) = surfaces.first().filter(|end| end.at.is_some()) {
                                dst = inside;
                            }
                        }
                        let held = if *via {
                            via_truth(cx, rest, id, src, *dst, &hops)
                        } else {
                            rest.truth(&Env::new(cx, Some(id), src, *dst))
                        };
                        let truth = all_of([edge_truth, held]);
                        if truth == Truth::False {
                            // Only a source the selection keeps registers what a list holds.
                            let env = Env::new(cx, Some(id), src, *dst);
                            if !*items || parts_truth(rest.src(), &env) != Truth::True {
                                continue;
                            }
                            match registered(cx, id, &src, dst) {
                                Registered::List(list) => {
                                    let mut through = Through {
                                        cx,
                                        rest,
                                        edge_truth,
                                        id,
                                        src,
                                        at,
                                        found: &mut found,
                                        out: &mut out,
                                    };
                                    through.lists(list);
                                }
                                // What a registration point registers from a module no group
                                // owns, which the link cannot read, may be any product's list.
                                Registered::Unread(module, named) => {
                                    let row = ("unread-core-items", at, Some(module), named);
                                    out.incomplete.push(row);
                                }
                                Registered::Nothing => {}
                            }
                            continue;
                        }
                        if truth == Truth::Unknown {
                            out.incomplete.push(unplaced(cx, at, &src, id));
                            continue;
                        }
                        if *items {
                            match registered(cx, id, &src, dst) {
                                Registered::List(list) => {
                                    sequence_files(cx, list, at, &mut found, &mut out);
                                }
                                Registered::Unread(module, named) => {
                                    out.incomplete
                                        .push(("unread-items", at, Some(module), named));
                                }
                                Registered::Nothing => {}
                            }
                            continue;
                        }
                        let endpoint = if *from_src { &src } else { dst };
                        let mut need = Need {
                            cx,
                            at,
                            id,
                            src: &src,
                            found: &mut found,
                            out: &mut out,
                        };
                        need.endpoint(endpoint);
                        if *chain {
                            for hop in named.iter().chain(&hops) {
                                need.endpoint(hop);
                            }
                        }
                    }
                }
                if *items {
                    computed_loads(cx, rest, at, &mut out);
                }
            }
            Cover::Modules {
                selection,
                location,
                registered,
            } => {
                let mut kept: Vec<Truth> = Vec::with_capacity(cx.rules.modules.len());
                for (place, &module) in cx.rules.modules.iter().enumerate() {
                    let env = EntityEnv::of(cx, Some((module, place as u32)), None, None);
                    let truth = class_truth(selection, &env, cx.rules.touch);
                    if truth == Truth::Unknown {
                        let name = Some(cx.text(module).to_string());
                        out.incomplete
                            .push(("unknown-selection", at, name, String::new()));
                    }
                    kept.push(truth);
                }
                let mut directories = FxHashMap::default();
                for (place, &module) in cx.rules.modules.iter().enumerate() {
                    let place = place as u32;
                    if kept[place as usize] != Truth::True {
                        continue;
                    }
                    if *location {
                        let (path, name) =
                            location_of(cx, selection, &kept, &mut directories, module, place);
                        locations.push((path, at, name));
                        continue;
                    }
                    if *registered {
                        if let Some(attribute) = registered_attribute(cx, place) {
                            sequence_files(cx, (module, attribute), at, &mut found, &mut out);
                        }
                        continue;
                    }
                    let path = cx.rules.module_path(cx.link, place).to_string();
                    let name = Some(cx.text(module).to_string());
                    found.entry(path).or_insert((at, name));
                }
            }
        }
    }
    for (path, at, name) in locations {
        found.entry(path).or_insert((at, Some(name)));
    }
    let mut paths: Vec<(String, u32, Option<String>)> = found
        .into_iter()
        .map(|(path, (cover, module))| (path, cover, module))
        .collect();
    paths.sort();
    out.paths = paths;
    out
}

/// Where an edge cover puts what one endpoint of a kept edge needs: its file, the files of a
/// namespace package, or an "unplaced" row when it cannot tell.
struct Need<'n, 'a> {
    cx: Cx<'a>,
    at: u32,
    id: u32,
    src: &'n Endpoint<'a>,
    found: &'n mut FxHashMap<String, (u32, Option<String>)>,
    out: &'n mut Required,
}

impl<'a> Need<'_, 'a> {
    fn endpoint(&mut self, endpoint: &Endpoint<'a>) {
        let cx = self.cx;
        if endpoint.unresolved {
            self.out
                .incomplete
                .push(unplaced(cx, self.at, self.src, self.id));
            return;
        }
        if endpoint.external.is_some() {
            return;
        }
        if let Some(path) = endpoint.path.filter(|path| !path.is_empty()) {
            let owner = endpoint.module.map(|module| cx.text(module).to_string());
            self.found
                .entry(path.to_string())
                .or_insert((self.at, owner));
            return;
        }
        let Some(files) = cx.namespace_files(endpoint) else {
            self.out
                .incomplete
                .push(unplaced(cx, self.at, self.src, self.id));
            return;
        };
        for path in files {
            self.found.entry(path).or_insert((self.at, None));
        }
    }
}

/// A module a registration point (a source the selection keeps) names by a value the link
/// cannot read exactly (`Link::computed_loads`) may be any product's: no group owns it, so the
/// unknown is everyone's (a problem of the answer). A source the selection may keep counts.
fn computed_loads(cx: Cx, rest: &Staged, at: u32, out: &mut Required) {
    for (module, words) in &cx.link.computed_loads {
        let src = cx.module_endpoint(*module);
        let env = Env::new(cx, None, src, src);
        if parts_truth(rest.src(), &env) == Truth::False {
            continue;
        }
        let row = (
            "computed-load",
            at,
            Some(cx.text(*module).to_string()),
            words.clone(),
        );
        out.incomplete.push(row);
    }
}

/// The attribute the registry of the module at `place` reads, when the module has a registry and
/// the snapshot has the attribute's name.
fn registered_attribute(cx: Cx, place: u32) -> Option<Sid> {
    let registry = cx.rules.labels.registry_of[place as usize]?;
    let attribute = &cx.rules.labels.registries[registry as usize].attribute;
    cx.link.core.it.get(attribute)
}

/// What a registration point registers at one destination of an edge (`registered`).
enum Registered {
    /// The list the link holds for (module, key), or a name that is no list.
    List((Sid, Sid)),
    /// Something the link cannot read, in this module, named so.
    Unread(String, String),
    /// Nothing an item cover reads: a module the code names no attribute of.
    Nothing,
}

/// What edge `id` registers at destination `dst`:
/// - a name: the list the link holds for it, or for the class attribute the edge's chain reads
///   next (`Class.ATTR`, `Outer.Inner.ATTR`); when the origin holds no list (a one-class
///   container resolves to the class), the list of the name the code names or a name it passes
///   on the way; an attribute read off an instance (`Class().ATTR`) is unread, as what the
///   instance holds can differ; a name iterated (`[f.value for f in Flows]`) that the link holds
///   no list for and that no assignment binds is unread;
/// - a name the link cannot resolve inside a module of the graph (only a module `__getattr__`
///   serves it): unread;
/// - a whole module the source uses other than by `module.NAME` (handed to a call, read through
///   `__dict__`, `getattr(module, NAME)`, `vars(module)`): unread;
/// - a whole module whose path the code hands to a call or a subscript (`import_module("m")`,
///   `sys.modules["m"]`): the attribute read next, else unread.
fn registered(cx: Cx, id: u32, src: &Endpoint, dst: &Endpoint) -> Registered {
    match dst.symbol {
        Symbol::Inside(module, name) => match read_list(cx, id, (module, name)) {
            Registered::List(list) if !cx.link.sequences.contains_key(&list) => {
                passed_list(cx, id, list)
            }
            found => found,
        },
        Symbol::Name(surface) if dst.external.is_none() => match dst.module {
            Some(module) => {
                Registered::Unread(cx.text(module).to_string(), cx.text(surface).to_string())
            }
            None => Registered::Nothing,
        },
        Symbol::None => match (src.module, dst.module) {
            (Some(reader), Some(module)) if cx.link.computed_reads.contains(&(reader, module)) => {
                let text = cx.text(module).to_string();
                Registered::Unread(text.clone(), format!("{text}.?"))
            }
            (_, Some(module)) => loaded_list(cx, id, module),
            _ => Registered::Nothing,
        },
        _ => Registered::Nothing,
    }
}

/// When the origin `list` of edge `id` holds no list: the list of a name the code names or passes
/// on the way to it, when the link holds one; else, for an iterated name no assignment binds,
/// unread; else the origin.
fn passed_list(cx: Cx, id: u32, list: (Sid, Sid)) -> Registered {
    let mut names = Vec::new();
    cx.dst_endpoints(id, Role::Via, &mut names);
    let mut surfaces = Vec::new();
    cx.dst_endpoints(id, Role::Surface, &mut surfaces);
    for endpoint in surfaces.iter().chain(&names) {
        let (Some(module), Some(text)) = (endpoint.module, cx.symbol_text(endpoint.symbol)) else {
            continue;
        };
        let Some(name) = text
            .strip_prefix(cx.text(module))
            .and_then(|rest| rest.strip_prefix('.'))
        else {
            continue;
        };
        if let Some(name) = cx.link.core.it.get(name)
            && cx.link.sequences.contains_key(&(module, name))
        {
            return Registered::List((module, name));
        }
    }
    let iterated = cx.text(cx.link.edges.use_of(id)) == "iterate";
    if iterated && !cx.link.bound.contains(&list) {
        let named = format!("{}.{}", cx.text(list.0), cx.text(list.1));
        return Registered::Unread(cx.text(list.0).to_string(), named);
    }
    Registered::List(list)
}

/// The list edge `id` reads at its origin `name`: the class attribute its chain reads next
/// (`Class.ATTR`; nested, `Outer.Inner.ATTR`) when the link holds one, else the name. An
/// attribute read off an instance (`Class().ATTR`) is unread.
fn read_list(cx: Cx, id: u32, name: (Sid, Sid)) -> Registered {
    let chain = cx.attr(id, cx.link.core.it.get("chain"));
    let (instance, path) = match chain.strip_prefix("()") {
        Some(path) => (true, path),
        None => (false, chain),
    };
    let Some(rest) = path.strip_prefix('.') else {
        return Registered::List(name);
    };
    if instance {
        // An attribute the class body binds or defines (a property), read off an instance; a
        // method called on one is a call, and any other attribute reads as the class.
        let attribute = rest.split(['.', '(', '[']).next().unwrap_or_default();
        let called = rest[attribute.len()..].starts_with("()");
        let key = format!("{}.{attribute}", cx.text(name.1));
        let known = cx.link.core.it.get(&key).is_some_and(|key| {
            cx.link.sequences.contains_key(&(name.0, key))
                || cx.link.defined.contains(&(name.0, key))
        });
        if known && !called {
            let named = format!("{}.{}{chain}", cx.text(name.0), cx.text(name.1));
            return Registered::Unread(cx.text(name.0).to_string(), named);
        }
        return Registered::List(name);
    }
    let mut key = cx.text(name.1).to_string();
    let mut found = None;
    for part in rest.split('.') {
        let plain = part.split(['(', '[']).next().unwrap_or_default();
        if plain.is_empty() {
            break;
        }
        key = format!("{key}.{plain}");
        if let Some(sid) = cx.link.core.it.get(&key)
            && cx.link.sequences.contains_key(&(name.0, sid))
        {
            found = Some(sid);
        }
        if plain.len() != part.len() {
            break;
        }
    }
    match found {
        Some(sid) => Registered::List((name.0, sid)),
        None => Registered::List(name),
    }
}

/// What a dotted-path edge to a whole module registers when the code hands the module's path to
/// a call or a subscript (`up` starts `arg:N:` or `slice`): something loads the module there
/// (`import_module("m")`, `__import__("m")`, `sys.modules["m"]`), so what it registers is the
/// attribute the code reads right after (`.WORKFLOWS`), else unread. A path in any other place
/// (a list of locations, say) registers nothing an item cover reads.
fn loaded_list(cx: Cx, id: u32, module: Sid) -> Registered {
    let up = cx.attr(id, cx.link.core.it.get("up"));
    let mut steps = up.split(' ');
    let loaded = steps
        .next()
        .is_some_and(|step| step.starts_with("arg:") || step == "slice");
    if !loaded {
        return Registered::Nothing;
    }
    let attribute = steps
        .next()
        .and_then(|step| step.strip_prefix('.'))
        .map(|step| step.split(['.', '(', '[']).next().unwrap_or_default())
        .filter(|name| !name.is_empty());
    let text = cx.text(module).to_string();
    match attribute.and_then(|name| cx.link.core.it.get(name)) {
        Some(name) => Registered::List((module, name)),
        None => match attribute {
            Some(name) => Registered::Unread(text.clone(), format!("{text}.{name}")),
            None => Registered::Unread(text.clone(), text),
        },
    }
}

/// The files of the module that builds the sequence `list` names and of the modules that define
/// its items, through each item that is itself such a sequence. A name that is no sequence needs
/// nothing.
fn sequence_files(
    cx: Cx,
    list: (Sid, Sid),
    at: u32,
    found: &mut FxHashMap<String, (u32, Option<String>)>,
    out: &mut Required,
) {
    let mut need = |module: Sid| {
        // The module that builds the list decides what core registers, as an item's module
        // does. A list outside the graph (library code) needs no file.
        if let Some(place) = cx.module_at(module) {
            let path = cx.rules.module_path(cx.link, place).to_string();
            found
                .entry(path)
                .or_insert((at, Some(cx.text(module).to_string())));
        }
    };
    let mut stack = vec![list];
    let mut seen = FxHashSet::default();
    while let Some(list) = stack.pop() {
        if !seen.insert(list) {
            continue;
        }
        let Some(members) = cx.link.sequences.get(&list) else {
            continue;
        };
        need(list.0);
        for member in members {
            let Some(ent) = member.filter(|ent| cx.module_at(ent.m).is_some()) else {
                // The unknown is the list's product's: its set may miss the item.
                let named = format!("{}.{}", cx.text(list.0), cx.text(list.1));
                let module = Some(cx.text(list.0).to_string());
                out.incomplete.push(("unread-items", at, module, named));
                continue;
            };
            need(ent.m);
            if cx.link.sequences.contains_key(&(ent.m, ent.s)) {
                stack.push((ent.m, ent.s));
            }
        }
    }
}

/// A registered list the selection leaves out (a core module's) may hold lists it keeps: a core
/// module that re-lists a product's list (`ALL = [*P_WORKFLOWS]`) carries the registration
/// through to that list. Each list it holds, at any depth, is judged as the edge's destination.
struct Through<'n, 'a> {
    cx: Cx<'a>,
    rest: &'n Staged,
    edge_truth: Truth,
    id: u32,
    src: Endpoint<'a>,
    at: u32,
    found: &'n mut FxHashMap<String, (u32, Option<String>)>,
    out: &'n mut Required,
}

impl Through<'_, '_> {
    fn lists(&mut self, list: (Sid, Sid)) {
        let cx = self.cx;
        let mut stack = vec![list];
        let mut seen = FxHashSet::default();
        while let Some(list) = stack.pop() {
            if !seen.insert(list) {
                continue;
            }
            let Some(members) = cx.link.sequences.get(&list) else {
                continue;
            };
            // An item of the list the link cannot read may be a product's list: no group owns the
            // list, so the unknown is everyone's (a problem of the answer).
            if members.contains(&None) {
                let module = cx.text(list.0).to_string();
                let named = format!("{module}.{}", cx.text(list.1));
                self.out
                    .incomplete
                    .push(("unread-core-items", self.at, Some(module), named));
            }
            for member in members.iter().flatten() {
                let inner = (member.m, member.s);
                if !cx.link.sequences.contains_key(&inner) || cx.module_at(member.m).is_none() {
                    continue;
                }
                let env = Env::new(cx, Some(self.id), self.src, cx.entity_endpoint(*member));
                match all_of([self.edge_truth, self.rest.truth(&env)]) {
                    Truth::True => sequence_files(cx, inner, self.at, self.found, self.out),
                    Truth::Unknown => {
                        let row = unplaced(cx, self.at, &self.src, self.id);
                        self.out.incomplete.push(row);
                    }
                    Truth::False => stack.push(inner),
                }
            }
        }
    }
}

/// A selection that reads `via.*`, judged at each module between the edge's surface and its
/// origin: true when it holds at one, false when the name passes none.
fn via_truth<'a>(
    cx: Cx<'a>,
    rest: &Staged,
    id: u32,
    src: Endpoint<'a>,
    dst: Endpoint<'a>,
    hops: &[Endpoint<'a>],
) -> Truth {
    let mut out = Truth::False;
    for hop in hops {
        let mut env = Env::new(cx, Some(id), src, dst);
        env.via = Some(*hop);
        match rest.truth(&env) {
            Truth::True => return Truth::True,
            Truth::Unknown => out = Truth::Unknown,
            Truth::False => {}
        }
    }
    out
}

/// Where a module a location cover keeps stands, and that place's dotted name: the outermost
/// package or directory without `__init__.py` at or above the module that the selection keeps, as
/// the directory with a trailing slash; else the module's own file. `kept` holds the selection's
/// truth per graph module; `directories` remembers it per directory without `__init__.py`.
fn location_of(
    cx: Cx,
    selection: &[Node],
    kept: &[Truth],
    directories: &mut FxHashMap<Sid, Truth>,
    module: Sid,
    place: u32,
) -> (String, String) {
    let name = cx.text(module);
    let heads = name.match_indices('.').map(|(dot, _)| &name[..dot]);
    for head in heads.chain([name]) {
        if let Some(directory) = kept_directory(cx, selection, kept, directories, head) {
            return (directory, head.to_string());
        }
    }
    let path = cx.rules.module_path(cx.link, place).to_string();
    (path, name.to_string())
}

/// The directory of package or namespace `name`, with a trailing slash, when the selection keeps
/// it: a package by its `__init__.py`, a directory without one by its own labels.
fn kept_directory(
    cx: Cx,
    selection: &[Node],
    kept: &[Truth],
    directories: &mut FxHashMap<Sid, Truth>,
    name: &str,
) -> Option<String> {
    let sid = cx.link.core.it.get(name)?;
    if let Some(&at) = cx.rules.module_at.get(&sid) {
        if kept[at as usize] != Truth::True {
            return None;
        }
        let path = cx.rules.module_path(cx.link, at);
        return path
            .strip_suffix("/__init__.py")
            .map(|directory| format!("{directory}/"));
    }
    let namespace = cx.rules.labels.namespaces.get(name)?;
    let truth = *directories.entry(sid).or_insert_with(|| {
        let env = EntityEnv {
            cx,
            module: Some(sid),
            at: None,
            symbol: None,
            subject: None,
        };
        class_truth(selection, &env, cx.rules.touch)
    });
    (truth == Truth::True).then(|| format!("{}/", namespace.directory))
}

/// A cover cannot place edge `id`: an "unplaced" row.
fn unplaced(
    cx: Cx,
    at: u32,
    src: &Endpoint,
    id: u32,
) -> (&'static str, u32, Option<String>, String) {
    let module = src.module.map(|module| cx.text(module).to_string());
    let surface = cx.text(cx.link.edges.surface_of(id)).to_string();
    ("unplaced", at, module, surface)
}

/// The edges an edge cover reads, in order: kind by kind, or every edge.
pub fn cover_edges(link: &mut Link, cover: &Cover) -> Vec<u32> {
    let Cover::Edges { kinds, .. } = cover else {
        return Vec::new();
    };
    match kinds {
        None => (0..link.edges.len() as u32).collect(),
        Some(kinds) => {
            let mut out = Vec::new();
            for kind in super::known_kinds(link, kinds).unwrap_or_default() {
                out.extend(link.ids_of_kind(kind));
            }
            out
        }
    }
}
