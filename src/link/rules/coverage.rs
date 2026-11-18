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
/// "unread-items" for a sequence with an item it cannot read (the surface is the sequence).
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
                            if *items
                                && let Symbol::Inside(module, name) = dst.symbol
                                && parts_truth(rest.src(), &env) == Truth::True
                            {
                                let list = read_list(cx, id, (module, name));
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
                            continue;
                        }
                        if truth == Truth::Unknown {
                            out.incomplete.push(unplaced(cx, at, &src, id));
                            continue;
                        }
                        if *items {
                            match dst.symbol {
                                Symbol::Inside(module, name) => {
                                    let list = read_list(cx, id, (module, name));
                                    sequence_files(cx, list, at, &mut found, &mut out);
                                }
                                Symbol::Name(surface) if dst.module.is_some() => {
                                    let module =
                                        dst.module.map(|module| cx.text(module).to_string());
                                    let named = cx.text(surface).to_string();
                                    out.incomplete.push(("unread-items", at, module, named));
                                }
                                _ => {}
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

/// The attribute the registry of the module at `place` reads, when the module has a registry and
/// the snapshot has the attribute's name.
fn registered_attribute(cx: Cx, place: u32) -> Option<Sid> {
    let registry = cx.rules.labels.registry_of[place as usize]?;
    let attribute = &cx.rules.labels.registries[registry as usize].attribute;
    cx.link.core.it.get(attribute)
}

/// The list edge `id` reads at its origin `name`: the class attribute its chain reads next
/// (`Class.ATTR`) when the link holds one, else the name.
fn read_list(cx: Cx, id: u32, name: (Sid, Sid)) -> (Sid, Sid) {
    let chain = cx.attr(id, cx.link.core.it.get("chain"));
    let Some(rest) = chain.strip_prefix('.') else {
        return name;
    };
    let attribute = rest.split(['.', '(', '[']).next().unwrap_or_default();
    let key = format!("{}.{attribute}", cx.text(name.1));
    match cx.link.core.it.get(&key) {
        Some(sid) if cx.link.sequences.contains_key(&(name.0, sid)) => (name.0, sid),
        _ => name,
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
