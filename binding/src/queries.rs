//! Edge ids by kind, by resolved origin, by source module and by spelling: the queries packs
//! select their candidates with.

use crate::link::Link;
use crate::{Pair, ids_bytes};
use hoglint_facts::link::index::kind_code;
use hoglint_facts::link::intern::Sid;
use hoglint_facts::link::resolve::{Binding, Ent};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use regex::bytes::Regex;
use rustc_hash::FxHashSet;
use serde_json::value::RawValue;
use std::borrow::Cow;

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
    /// Edge ids of one kind, in graph order, as little-endian u32 bytes.
    fn ids_of_kind<'py>(&mut self, py: Python<'py>, kind: &str) -> Bound<'py, PyBytes> {
        let Some(code) = kind_code(kind) else {
            return ids_bytes(py, &[]);
        };
        let link = &mut self.inner;
        ids_bytes(py, link.index.of_kind(&link.edges.rows, code))
    }

    /// Edges of `kind` whose resolved origin is one of `targets` ((module, symbol) pairs).
    fn ids_to<'py>(
        &mut self,
        py: Python<'py>,
        kind: &str,
        targets: Vec<Pair>,
    ) -> Bound<'py, PyBytes> {
        let Some(code) = kind_code(kind) else {
            return ids_bytes(py, &[]);
        };
        let targets: Vec<Ent> = targets.iter().map(|(m, s)| self.ent(m, s)).collect();
        let link = &mut self.inner;
        ids_bytes(py, &link.index.to_targets(&link.edges.rows, code, &targets))
    }

    /// Edges of `kind` from one source module.
    fn ids_from<'py>(&mut self, py: Python<'py>, kind: &str, module: &str) -> Bound<'py, PyBytes> {
        let Some(code) = kind_code(kind) else {
            return ids_bytes(py, &[]);
        };
        let module = self.sid(module);
        let link = &mut self.inner;
        ids_bytes(
            py,
            link.index
                .from_module(&link.edges.rows, &link.scopes, code, module),
        )
    }

    /// Source modules of `kind`, in the order their first edge appears.
    fn source_modules(&mut self, kind: &str) -> Vec<String> {
        let Some(code) = kind_code(kind) else {
            return Vec::new();
        };
        let link = &mut self.inner;
        let modules = link
            .index
            .source_modules(&link.edges.rows, &link.scopes, code)
            .to_vec();
        modules
            .iter()
            .map(|&m| link.core.it.s(m).to_string())
            .collect()
    }

    /// Edges of `kind` whose surface's last dotted part is one of `names`, resolved or not.
    fn ids_spelled<'py>(
        &mut self,
        py: Python<'py>,
        kind: &str,
        names: Vec<String>,
    ) -> Bound<'py, PyBytes> {
        let Some(code) = kind_code(kind) else {
            return ids_bytes(py, &[]);
        };
        let names: Vec<Sid> = names.iter().map(|name| self.sid(name)).collect();
        let link = &mut self.inner;
        ids_bytes(
            py,
            &link
                .index
                .spelled(&mut link.core.it, &link.edges.rows, code, &names),
        )
    }

    /// Rows of a call kind (`calls`, `callees`) in the analyzed files whose callee's last dotted
    /// name is one of `names`, resolved or not: (file id, the row's JSON text) in fact order.
    /// A file whose rows cannot hold one of the names is never parsed.
    fn rows_named<'py>(
        &self,
        py: Python<'py>,
        kind: &str,
        names: Vec<String>,
    ) -> PyResult<Vec<(u32, Bound<'py, PyBytes>)>> {
        let position = callee_position(kind)?;
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
        let wanted: FxHashSet<&str> = names.iter().map(String::as_str).collect();
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
                let Some(callee) = fields.get(position) else {
                    continue;
                };
                let Ok(callee) = serde_json::from_str::<Cow<str>>(callee.get()) else {
                    continue;
                };
                let last = callee.rsplit('.').next().unwrap_or("");
                if wanted.contains(last) {
                    out.push((file as u32, PyBytes::new(py, row.get().as_bytes())));
                }
            }
        }
        Ok(out)
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
