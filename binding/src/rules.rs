//! Candidate selection for rule evaluation, for the proofs and tools that list a rule's edges.

use crate::ids_bytes;
use crate::link::Link;
use hoglint_facts::link::intern::Sid;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

#[pymethods]
impl Link {
    /// Edges a rule sees: kind in `kinds` (every kind when empty), resolution not
    /// not-applicable, context bits meeting `include` (any) and avoiding `exclude`.
    fn candidates<'py>(
        &mut self,
        py: Python<'py>,
        kinds: Vec<String>,
        include: u8,
        exclude: u8,
    ) -> Bound<'py, PyBytes> {
        let codes: Vec<Sid> = kinds
            .iter()
            .filter_map(|kind| self.inner.kind_sid(kind))
            .collect();
        if codes.is_empty() && !kinds.is_empty() {
            return ids_bytes(py, &[]);
        }
        ids_bytes(py, &self.inner.candidates(&codes, include, exclude))
    }
}
