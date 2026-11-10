//! hoglint_native: the Python binding of the native link. A thin shell: it converts arguments,
//! releases the GIL for the heavy calls and hands out compact results. All semantics live in
//! `hoglint_facts::link`.
//!
//! A `Link` is an opaque handle on one linked snapshot. Bulk results cross as `bytes` of
//! little-endian u32 edge ids; Python builds Edge objects only for the ids it asks for.
//!
//! Each concern adds its own `#[pymethods] impl Link` block in its own module:
//! - `link`: building a link, the snapshot's records and the edges as tuples;
//! - `queries`: edge ids by kind, origin, source module and spelling, for packs;
//! - `evaluator`: the `Rules` of one run: labels, and edge rules judged per edge;
//! - `resolver`: name resolution and class ancestry, for packs.

mod blobs;
mod evaluator;
mod factdb;
mod link;
mod queries;
mod resolver;

use link::{Link, link_blobs, link_jsonl};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

/// The binding's call protocol. Bump it when a call's shape or meaning changes.
const PROTOCOL: u32 = 1;

pub(crate) type Pair = (String, String);
pub(crate) type HitTuple = (&'static str, Vec<Pair>);

pub(crate) fn ids_bytes<'py>(py: Python<'py>, ids: &[u32]) -> Bound<'py, PyBytes> {
    let mut raw = Vec::with_capacity(ids.len() * 4);
    for id in ids {
        raw.extend_from_slice(&id.to_le_bytes());
    }
    PyBytes::new(py, &raw)
}

pub(crate) fn ids_from(raw: &[u8]) -> PyResult<Vec<u32>> {
    let (chunks, rest) = raw.as_chunks::<4>();
    if !rest.is_empty() {
        return Err(PyValueError::new_err("edge ids must be little-endian u32s"));
    }
    Ok(chunks.iter().map(|c| u32::from_le_bytes(*c)).collect())
}

#[pymodule]
fn hoglint_native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<Link>()?;
    module.add_class::<link::Staged>()?;
    module.add_class::<evaluator::Rules>()?;
    module.add_function(wrap_pyfunction!(link_jsonl, module)?)?;
    module.add_function(wrap_pyfunction!(link_blobs, module)?)?;
    module.add("FACTS_ID", hoglint_facts::FACTS_ID)?;
    module.add("NATIVE_ID", hoglint_facts::NATIVE_ID)?;
    module.add("PROTOCOL", PROTOCOL)?;
    module.add("FACT_LAYOUT", factdb::LAYOUT)?;
    module.add("NO_PLACE", hoglint_facts::link::rules::NO_PLACE)?;
    Ok(())
}
