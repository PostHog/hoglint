//! The paths a coverage rule needs covered: another rule's column read off its findings'
//! witnesses, the endpoints of an edge selection (or the modules on a name's path to its origin),
//! and the modules or the locations an entity selection keeps.
//! Whether an outside input covers each path stays with the coverage adapter, a Python plugin.

use super::env::{Cx, Endpoint, EntityEnv, Env, Role, Symbol};
use super::labels::Field;
use super::plan::CoverPlan;
use super::selector::{Compiler, Node, Ref, Resolve, evaluate};
use super::staged::{Staged, class_truth};
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
        /// The modules that define the items of the sequence the origin names (`: items`).
        items: bool,
    },
    /// The files of the modules an entity selection keeps, in its `and` parts. With `location`,
    /// the place each such module stands in instead (`location_of`).
    Modules {
        selection: Vec<Node>,
        location: bool,
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
        if plan.endpoint == "module" || plan.endpoint == "location" {
            return Ok(Cover::Modules {
                selection: entity.nodes(&plan.rest)?,
                location: plan.endpoint == "location",
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
                        let held = if *via {
                            via_truth(cx, rest, id, src, *dst, &hops)
                        } else {
                            rest.truth(&Env::new(cx, Some(id), src, *dst))
                        };
                        let truth = all_of([edge_truth, held]);
                        if truth == Truth::False {
                            continue;
                        }
                        if truth == Truth::Unknown {
                            out.incomplete.push(unplaced(cx, at, &src, id));
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
                        if *items {
                            need.items(dst);
                            continue;
                        }
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

impl Need<'_, '_> {
    /// The files of the modules that define the items of the sequence `origin` names, through
    /// each item that is itself such a sequence. An origin that names no sequence needs nothing.
    fn items(&mut self, origin: &Endpoint) {
        let cx = self.cx;
        let Symbol::Inside(module, name) = origin.symbol else {
            return;
        };
        let mut stack = vec![(module, name)];
        let mut seen = FxHashSet::default();
        while let Some(list) = stack.pop() {
            if !seen.insert(list) {
                continue;
            }
            let Some(members) = cx.link.sequences.get(&list) else {
                continue;
            };
            for member in members {
                let place = member.and_then(|ent| cx.module_at(ent.m).map(|at| (ent, at)));
                let Some((ent, at)) = place else {
                    // The unknown is the list's product's: its set may miss the item.
                    let named = format!("{}.{}", cx.text(list.0), cx.text(list.1));
                    let module = Some(cx.text(list.0).to_string());
                    self.out
                        .incomplete
                        .push(("unread-items", self.at, module, named));
                    continue;
                };
                let path = cx.rules.module_path(cx.link, at).to_string();
                let owner = Some(cx.text(ent.m).to_string());
                self.found.entry(path).or_insert((self.at, owner));
                if cx.link.sequences.contains_key(&(ent.m, ent.s)) {
                    stack.push((ent.m, ent.s));
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
