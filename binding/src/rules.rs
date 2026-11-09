//! Candidate selection and grouping for rule evaluation.

use crate::link::Link;
use crate::{ids_bytes, ids_from};
use hoglint_facts::link::Link as CoreLink;
use hoglint_facts::link::groups::Role;
use hoglint_facts::link::index::kind_code;
use pyo3::exceptions::PyValueError;
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
        let codes: Vec<u8> = kinds.iter().filter_map(|kind| kind_code(kind)).collect();
        if codes.is_empty() && !kinds.is_empty() {
            return ids_bytes(py, &[]);
        }
        ids_bytes(py, &self.inner.candidates(&codes, include, exclude))
    }

    /// Src and dst keys of `ids`: (src key of each id, src representative ids, dst key of each
    /// id, dst representative ids). `role` is "origin" or "surface".
    #[allow(clippy::type_complexity)]
    fn endpoint_keys<'py>(
        &mut self,
        py: Python<'py>,
        ids: &[u8],
        role: &str,
        src_scope: bool,
        dst_entity: bool,
    ) -> PyResult<(
        Bound<'py, PyBytes>,
        Bound<'py, PyBytes>,
        Bound<'py, PyBytes>,
        Bound<'py, PyBytes>,
    )> {
        let ids = ids_from(ids)?;
        let role = match role {
            "origin" => Role::Origin,
            "surface" => Role::Surface,
            other => return Err(PyValueError::new_err(format!("no keys for role {other}"))),
        };
        let (src, dst) = self.inner.endpoint_keys(&ids, role, src_scope, dst_entity);
        Ok((
            ids_bytes(py, &src.key_of),
            ids_bytes(py, &src.reps),
            ids_bytes(py, &dst.key_of),
            ids_bytes(py, &dst.reps),
        ))
    }

    /// Edge keys of `ids` over the named edge fields: (key of each id, representative ids).
    fn edge_keys<'py>(
        &mut self,
        py: Python<'py>,
        ids: &[u8],
        fields: Vec<String>,
    ) -> PyResult<(Bound<'py, PyBytes>, Bound<'py, PyBytes>)> {
        let ids = ids_from(ids)?;
        let keyed = self.inner.edge_keys(&ids, &fields);
        Ok((ids_bytes(py, &keyed.key_of), ids_bytes(py, &keyed.reps)))
    }

    /// Groups `ids` by layers of (key of each id, class of each key): (group of each id,
    /// representative ids).
    #[staticmethod]
    fn group<'py>(
        py: Python<'py>,
        ids: &[u8],
        layers: Vec<(Bound<'py, PyBytes>, Bound<'py, PyBytes>)>,
    ) -> PyResult<(Bound<'py, PyBytes>, Bound<'py, PyBytes>)> {
        let ids = ids_from(ids)?;
        let layers: Vec<(Vec<u32>, Vec<u32>)> = layers
            .iter()
            .map(|(k, c)| Ok((ids_from(k.as_bytes())?, ids_from(c.as_bytes())?)))
            .collect::<PyResult<_>>()?;
        if let Some((key_of, _)) = layers.iter().find(|(key_of, _)| key_of.len() != ids.len()) {
            return Err(PyValueError::new_err(format!(
                "a layer has {} keys for {} ids",
                key_of.len(),
                ids.len()
            )));
        }
        let keyed = CoreLink::group(&ids, &layers);
        Ok((ids_bytes(py, &keyed.key_of), ids_bytes(py, &keyed.reps)))
    }
}
