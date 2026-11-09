//! Edge ids by kind, by resolved origin, by source module and by spelling: the queries packs
//! select their candidates with.

use crate::link::Link;
use crate::{Pair, ids_bytes, ids_from};
use hoglint_facts::link::intern::{EMPTY, Sid};
use hoglint_facts::link::resolve::{Binding, Ent};
use pyo3::exceptions::{PyKeyError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyList, PyString};
use regex::bytes::Regex;
use rustc_hash::{FxHashMap, FxHashSet};
use serde_json::value::RawValue;
use std::borrow::Cow;

/// Where an `imports` row holds the imported name: [scope, line, column, flags, module, level,
/// name, asname].
const IMPORTED_NAME: usize = 6;

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

impl Link {
    /// The rows of `kind` in the analyzed files whose field at `position` is a string that
    /// `wanted` accepts: (file id, the row's JSON text) in fact order. A file whose rows of the
    /// kind hold none of `names` as text is never parsed.
    fn matching_rows<'py>(
        &self,
        py: Python<'py>,
        kind: &str,
        position: usize,
        names: &[String],
        wanted: impl Fn(&str) -> bool,
    ) -> PyResult<Vec<(u32, Bound<'py, PyBytes>)>> {
        let mut out = Vec::new();
        if names.is_empty() {
            return Ok(out);
        }
        let pattern = names
            .iter()
            .map(|name| regex::escape(name))
            .collect::<Vec<_>>()
            .join("|");
        let mention = Regex::new(&pattern).map_err(|e| PyValueError::new_err(e.to_string()))?;
        let facts = &self.inner.core.facts;
        let text = facts.text().map_err(PyValueError::new_err)?;
        let fail = |e: serde_json::Error| PyValueError::new_err(format!("{kind} rows: {e}"));
        for (file, record) in facts.files.iter().enumerate() {
            if !self.inner.scope_of_file.contains_key(&(file as u32)) {
                continue;
            }
            let Some((_, range)) = record.keys.iter().find(|(key, _)| &**key == kind) else {
                continue;
            };
            let slice = &text[range.clone()];
            if !mention.is_match(slice) {
                continue;
            }
            let rows: Vec<&RawValue> = serde_json::from_slice(slice).map_err(fail)?;
            for row in rows {
                let fields: Vec<&RawValue> = serde_json::from_str(row.get()).map_err(fail)?;
                let Some(field) = fields.get(position) else {
                    continue;
                };
                let Ok(field) = serde_json::from_str::<Cow<str>>(field.get()) else {
                    continue;
                };
                if wanted(&field) {
                    out.push((file as u32, PyBytes::new(py, row.get().as_bytes())));
                }
            }
        }
        Ok(out)
    }
}

#[pymethods]
impl Link {
    /// Rows of a call kind (`calls`, `callees`) in the analyzed files whose callee's last dotted
    /// name is one of `names`, resolved or not: (file id, the row's JSON text) in fact order.
    fn rows_named<'py>(
        &self,
        py: Python<'py>,
        kind: &str,
        names: Vec<String>,
    ) -> PyResult<Vec<(u32, Bound<'py, PyBytes>)>> {
        let position = callee_position(kind)?;
        let wanted: FxHashSet<&str> = names.iter().map(String::as_str).collect();
        self.matching_rows(py, kind, position, &names, |callee| {
            wanted.contains(callee.rsplit('.').next().unwrap_or(""))
        })
    }

    /// `imports` rows in the analyzed files that import one of `names` by name (`from x import
    /// name`): (file id, the row's JSON text) in fact order.
    fn import_rows_named<'py>(
        &self,
        py: Python<'py>,
        names: Vec<String>,
    ) -> PyResult<Vec<(u32, Bound<'py, PyBytes>)>> {
        let wanted: FxHashSet<&str> = names.iter().map(String::as_str).collect();
        self.matching_rows(py, "imports", IMPORTED_NAME, &names, |name| {
            wanted.contains(name)
        })
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
    fn subclasses(&mut self, qualified: &str) -> Vec<Pair> {
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
        out
    }
}
