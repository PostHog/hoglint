//! Edge ids by kind, by resolved origin, by source module and by spelling: the queries packs
//! select their candidates with.

use crate::link::Link;
use crate::{Pair, ids_bytes};
use hoglint_facts::link::index::kind_code;
use hoglint_facts::link::intern::Sid;
use hoglint_facts::link::resolve::Ent;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

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
}
