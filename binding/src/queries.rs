//! Edge ids by kind, by resolved origin, by source module and by spelling: the queries packs
//! select their candidates with.

use crate::link::Link;
use crate::{HitTuple, Pair, ids_bytes, ids_from};
use hoglint_facts::link::facts::{FileFacts, V};
use hoglint_facts::link::intern::{EMPTY, Sid};
use hoglint_facts::link::resolve::{Binding, Ent};
use pyo3::exceptions::{PyKeyError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyList, PyString};
use rustc_hash::{FxHashMap, FxHashSet};
use serde_json::value::RawValue;

/// Where a call kind's rows hold the callee (`CALLEE_FIELD` in hoglint/native/queries.py).
fn callee_position(kind: &str) -> PyResult<usize> {
    match kind {
        "calls" => Ok(5),
        "callees" => Ok(2),
        other => Err(PyValueError::new_err(format!("{other} is not a call kind"))),
    }
}

#[pymethods]
impl Link {
    /// Edge ids of one kind, core and pack, in graph order, as little-endian u32 bytes.
    fn ids_of_kind<'py>(&mut self, py: Python<'py>, kind: &str) -> Bound<'py, PyBytes> {
        let Some(kind) = self.inner.kind_sid(kind) else {
            return ids_bytes(py, &[]);
        };
        let link = &mut self.inner;
        ids_bytes(py, link.index.of_kind(&link.edges, kind))
    }

    /// Edges of `kind` whose resolved origin is one of `targets` ((module, symbol) pairs).
    fn ids_to<'py>(
        &mut self,
        py: Python<'py>,
        kind: &str,
        targets: Vec<Pair>,
    ) -> Bound<'py, PyBytes> {
        let Some(kind) = self.inner.kind_sid(kind) else {
            return ids_bytes(py, &[]);
        };
        let targets: Vec<Ent> = targets.iter().map(|(m, s)| self.ent(m, s)).collect();
        let link = &mut self.inner;
        ids_bytes(py, &link.index.to_targets(&link.edges, kind, &targets))
    }

    /// Edges of `kind` from one source module.
    fn ids_from<'py>(&mut self, py: Python<'py>, kind: &str, module: &str) -> Bound<'py, PyBytes> {
        let Some(kind) = self.inner.kind_sid(kind) else {
            return ids_bytes(py, &[]);
        };
        let module = self.sid(module);
        ids_bytes(py, &self.inner.ids_from(kind, module))
    }

    /// Source modules of `kind`, in the order their first edge appears.
    fn source_modules(&mut self, kind: &str) -> Vec<String> {
        let Some(kind) = self.inner.kind_sid(kind) else {
            return Vec::new();
        };
        let modules = self.inner.source_modules(kind);
        let it = &self.inner.core.it;
        modules.iter().map(|&m| it.s(m).to_string()).collect()
    }

    /// Edges of `kind` whose surface's last dotted part is one of `names`, resolved or not.
    fn ids_spelled<'py>(
        &mut self,
        py: Python<'py>,
        kind: &str,
        names: Vec<String>,
    ) -> Bound<'py, PyBytes> {
        let Some(kind) = self.inner.kind_sid(kind) else {
            return ids_bytes(py, &[]);
        };
        let names: Vec<Sid> = names.iter().map(|name| self.sid(name)).collect();
        let link = &mut self.inner;
        ids_bytes(
            py,
            &link
                .index
                .spelled(&mut link.core.it, &link.edges, kind, &names),
        )
    }
}

/// Every string a value tree holds: chains, string texts, attribute names, callees.
fn value_strings(value: &V, out: &mut Vec<Sid>) {
    match value {
        V::Null | V::Other => {}
        V::Chain(s) | V::Opaque(s) => out.push(*s),
        V::Str(s, parsed) => {
            out.push(*s);
            if let Some(parsed) = parsed {
                value_strings(parsed, out);
            }
        }
        V::Call(inner, _) | V::Star(inner) => value_strings(inner, out),
        V::Attr(inner, name) => {
            value_strings(inner, out);
            out.push(*name);
        }
        V::Sub(a, b) | V::Bin(_, a, b) => {
            value_strings(a, out);
            value_strings(b, out);
        }
        V::Tuple(items) | V::List(items) | V::Set(items) => {
            items.iter().for_each(|item| value_strings(item, out))
        }
        V::Dict(pairs) => {
            for (key, value) in pairs {
                if let Some(key) = key {
                    value_strings(key, out);
                }
                value_strings(value, out);
            }
        }
    }
}

/// Pushes the strings a record's rows of `kind` hold: interned ids into `sids`, and the fields
/// the link keeps as plain text into `texts`. False for a kind the link does not decode.
fn kind_strings<'f>(
    facts: &'f FileFacts,
    kind: &str,
    sids: &mut Vec<Sid>,
    texts: &mut Vec<&'f str>,
) -> bool {
    match kind {
        "imports" => {
            for row in &facts.imports {
                sids.extend(row.target.iter().chain(&row.name).chain(&row.asname));
            }
        }
        "defs" => {
            for def in &facts.defs {
                sids.push(def.n);
                def.bases
                    .iter()
                    .chain(&def.dec)
                    .for_each(|v| value_strings(v, sids));
                for param in &def.params {
                    sids.push(param.name);
                    if let Some(annotation) = &param.annotation {
                        value_strings(annotation, sids);
                    }
                }
                if let Some(ret) = &def.ret {
                    value_strings(ret, sids);
                }
            }
        }
        "refs" => {
            for row in &facts.refs {
                sids.push(row.name);
                texts.push(&row.chain);
                texts.push(&row.up);
            }
        }
        "uses" => facts
            .uses
            .iter()
            .for_each(|row| sids.extend([row.root, row.attrs])),
        "callees" => sids.extend(facts.callees.iter().map(|row| row.text)),
        "names" => sids.extend(facts.names.iter().map(|row| row.text)),
        "tags" => sids.extend(facts.tags.iter().map(|row| row.name)),
        "calls" => texts.extend(facts.calls.iter().map(|row| &*row.callee)),
        _ => return false,
    }
    true
}

impl Link {
    /// The analyzed files' ids, in fact order.
    fn analyzed_files(&self) -> Vec<usize> {
        (0..self.inner.core.facts.files.len())
            .filter(|file| self.inner.scope_of_file.contains_key(&(*file as u32)))
            .collect()
    }

    /// The JSON text of the rows at `positions` of a record's `kind` list.
    fn json_rows<'py>(
        &self,
        py: Python<'py>,
        file: usize,
        kind: &str,
        positions: &[usize],
    ) -> PyResult<Vec<Bound<'py, PyBytes>>> {
        let fail = |e: serde_json::Error| PyValueError::new_err(format!("{kind} rows: {e}"));
        let Some(text) = self.raw_value(file, kind)? else {
            return Ok(Vec::new());
        };
        let rows: Vec<&RawValue> = serde_json::from_slice(&text).map_err(fail)?;
        positions
            .iter()
            .map(|&at| {
                let row = rows
                    .get(at)
                    .ok_or_else(|| PyValueError::new_err(format!("{kind} has no row {at}")))?;
                Ok(PyBytes::new(py, row.get().as_bytes()))
            })
            .collect()
    }
}

#[pymethods]
impl Link {
    /// Rows of a call kind (`calls`, `callees`) in the analyzed files whose callee's last dotted
    /// name is one of `names`, resolved or not: (file id, the row's JSON text) in fact order. The
    /// callees are matched on the link's own rows; only the files with a match read JSON text.
    fn rows_named<'py>(
        &self,
        py: Python<'py>,
        kind: &str,
        names: Vec<String>,
    ) -> PyResult<Vec<(u32, Bound<'py, PyBytes>)>> {
        self.rows()?;
        callee_position(kind)?;
        let wanted: FxHashSet<&str> = names.iter().map(String::as_str).collect();
        let last = |callee: &str| wanted.contains(callee.rsplit('.').next().unwrap_or(""));
        let it = &self.inner.core.it;
        let mut out = Vec::new();
        for file in self.analyzed_files() {
            let facts = &self.inner.core.facts.files[file];
            let positions: Vec<usize> = if kind == "calls" {
                facts
                    .calls
                    .iter()
                    .enumerate()
                    .filter(|(_, r)| last(&r.callee))
                    .map(|(at, _)| at)
                    .collect()
            } else {
                facts
                    .callees
                    .iter()
                    .enumerate()
                    .filter(|(_, r)| last(it.s(r.text)))
                    .map(|(at, _)| at)
                    .collect()
            };
            if positions.is_empty() {
                continue;
            }
            for row in self.json_rows(py, file, kind, &positions)? {
                out.push((file as u32, row));
            }
        }
        Ok(out)
    }

    /// `imports` rows in the analyzed files that import one of `names` by name (`from x import
    /// name`), in fact order: (file id, (scope, line, column, flags, module or None, level,
    /// name, asname or None)), from the link's own rows. The first call indexes every such row
    /// by name, so later calls cost only what they find.
    #[allow(clippy::type_complexity)]
    fn import_rows_named(
        &mut self,
        names: Vec<String>,
    ) -> PyResult<
        Vec<(
            u32,
            (
                i32,
                u32,
                u32,
                u32,
                Option<String>,
                u32,
                String,
                Option<String>,
            ),
        )>,
    > {
        self.rows()?;
        if self.import_names.is_none() {
            self.import_names = Some(self.inner.imports_by_name());
        }
        let index = self.import_names.as_ref().expect("built");
        let it = &self.inner.core.it;
        let mut found: Vec<(u32, u32)> = names
            .iter()
            .filter_map(|name| it.get(name))
            .filter_map(|name| index.get(&name))
            .flatten()
            .copied()
            .collect();
        found.sort_unstable();
        found.dedup();
        let text = |sid: Option<Sid>| sid.map(|s| it.s(s).to_string());
        let files = &self.inner.core.facts.files;
        Ok(found
            .into_iter()
            .map(|(file, at)| {
                let row = &files[file as usize].imports[at as usize];
                let name = row
                    .name
                    .map_or(String::new(), |name| it.s(name).to_string());
                let fields = (
                    row.scope,
                    row.line,
                    row.col,
                    row.flags,
                    text(row.target),
                    row.level,
                    name,
                    text(row.asname),
                );
                (file, fields)
            })
            .collect())
    }

    /// `uses` rows with attributes whose root a first-party import binds and whose chain
    /// resolves to a class among `targets` (`Link::uses_to`): (scope, at scope, line, column,
    /// count, (surface, hit, local)) in scope order.
    #[allow(clippy::type_complexity)]
    fn uses_to(
        &mut self,
        targets: Vec<Pair>,
    ) -> PyResult<Vec<(u32, i32, u32, u32, u32, (String, Option<HitTuple>, bool))>> {
        self.rows()?;
        let wanted = targets.iter().map(|(m, s)| self.ent(m, s)).collect();
        let found = self.inner.uses_to(&wanted);
        Ok(found
            .into_iter()
            .map(|hit| {
                let lookup = self.lookup(hit.found);
                (hit.scope, hit.at, hit.line, hit.col, hit.count, lookup)
            })
            .collect())
    }

    /// Whether a record's rows of any of `kinds` hold a string that ends in one of `ending` or
    /// contains one of `inside`, read off the link's own rows: what a pack's text prefilter asks.
    /// Kinds the link does not decode raise.
    fn mentions(
        &self,
        file: usize,
        kinds: Vec<String>,
        ending: Vec<String>,
        inside: Vec<String>,
    ) -> PyResult<bool> {
        self.rows()?;
        let facts = self
            .inner
            .core
            .facts
            .files
            .get(file)
            .ok_or_else(|| PyKeyError::new_err(file))?;
        let it = &self.inner.core.it;
        let matches = |text: &str| {
            ending.iter().any(|word| text.ends_with(word.as_str()))
                || inside.iter().any(|word| text.contains(word.as_str()))
        };
        for kind in &kinds {
            let (mut sids, mut texts) = (Vec::new(), Vec::new());
            if !kind_strings(facts, kind, &mut sids, &mut texts) {
                return Err(PyValueError::new_err(format!(
                    "the link does not read {kind} rows"
                )));
            }
            let mut seen: FxHashSet<Sid> = FxHashSet::default();
            if sids
                .into_iter()
                .any(|sid| seen.insert(sid) && matches(it.s(sid)))
            {
                return Ok(true);
            }
            if texts.into_iter().any(matches) {
                return Ok(true);
            }
        }
        Ok(false)
    }

    /// The fact cache key of each record in fact order: the blob oid of a repo file, the content
    /// hash of a third-party file, None for a file that could not be read or a JSONL input. What
    /// a pack keys a per-file cache on.
    fn blob_keys(&self) -> Vec<Option<String>> {
        self.keys.clone()
    }

    /// Columns of edges `ids`, one list per name in `names` with a value per edge: `path`,
    /// `module`, `scope`, `line`, `column`, `surface`, `target_module` (None without a target),
    /// `target_symbol`, `producer`, or `attr:<name>` ("" without it). Only these cross, with
    /// no Edge made: a pack's projected candidate batch.
    fn columns<'py>(
        &self,
        py: Python<'py>,
        ids: &[u8],
        names: Vec<String>,
    ) -> PyResult<Vec<Bound<'py, PyList>>> {
        let ids = ids_from(ids)?;
        let link = &self.inner;
        let it = &link.core.it;
        if let Some(&bad) = ids.iter().find(|&&id| id as usize >= link.edges.len()) {
            return Err(PyKeyError::new_err(bad));
        }
        let mut strings: FxHashMap<Sid, Bound<'py, PyString>> = FxHashMap::default();
        let mut text = |sid: Sid| -> Bound<'py, PyAny> {
            strings
                .entry(sid)
                .or_insert_with(|| PyString::new(py, it.s(sid)))
                .clone()
                .into_any()
        };
        let mut out = Vec::with_capacity(names.len());
        for name in &names {
            let attr = name.strip_prefix("attr:").map(|key| it.get(key));
            let mut values: Vec<Bound<'py, PyAny>> = Vec::with_capacity(ids.len());
            for &id in &ids {
                let edges = &link.edges;
                let value = match (name.as_str(), attr) {
                    (_, Some(key)) => {
                        let found =
                            key.and_then(|key| edges.attrs_with(id).find(|(k, _)| *k == key));
                        text(found.map_or(EMPTY, |&(_, v)| v))
                    }
                    ("path", _) => PyString::new(py, link.path_of_edge(id)).into_any(),
                    ("module", _) => text(link.site_of(id).0),
                    ("scope", _) => text(link.site_of(id).1),
                    ("line", _) => link.line_col(id).0.into_pyobject(py)?.into_any(),
                    ("column", _) => link.line_col(id).1.into_pyobject(py)?.into_any(),
                    ("surface", _) => text(edges.surface_of(id)),
                    ("target_module", _) => match edges.target_of(id) {
                        Some(target) => text(target.m),
                        None => py.None().into_bound(py),
                    },
                    ("target_symbol", _) => {
                        text(edges.target_of(id).map_or(EMPTY, |target| target.s))
                    }
                    ("producer", _) => text(edges.producer_of(id)),
                    (other, _) => {
                        return Err(PyValueError::new_err(format!("no edge column {other}")));
                    }
                };
                values.push(value);
            }
            out.push(PyList::new(py, values)?);
        }
        Ok(out)
    }

    /// Working tree files whose bytes did not hash to the snapshot's oid when the run read them:
    /// they changed during the run, so no fact entry was stored for them, and no run summary of
    /// this run may be stored either.
    fn drifted(&self) -> Vec<String> {
        self.drifted.clone()
    }

    /// Classes the analyzed modules' own source defines at module level whose MRO holds
    /// `qualified` (the class itself included), as (module, symbol) in scope order and table
    /// order: `is_subclass` asked of every class at once.
    fn subclasses(&mut self, qualified: &str) -> PyResult<Vec<Pair>> {
        self.rows()?;
        let qualified = self.sid(qualified);
        let mut seen: FxHashSet<Sid> = FxHashSet::default();
        let modules: Vec<Sid> = self
            .inner
            .scopes
            .iter()
            .map(|scope| scope.module)
            .filter(|&module| seen.insert(module))
            .collect();
        let mut out = Vec::new();
        for module in modules {
            let core = &mut self.inner.core;
            let Some(id) = core.table(module) else {
                continue;
            };
            let classes: Vec<Sid> = core
                .table_at(id)
                .names
                .iter()
                .filter(|(_, binding)| matches!(binding, Binding::Class(_, false)))
                .map(|(name, _)| name)
                .collect();
            for name in classes {
                let class = Ent { m: module, s: name };
                if self.inner.core.is_subclass(class, qualified) {
                    out.push(self.pair(class));
                }
            }
        }
        Ok(out)
    }
}
