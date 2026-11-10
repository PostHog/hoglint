//! Rule evaluation (`hoglint_facts::link::rules`): one `Rules` per run, made from a plan.

use crate::ids_bytes;
use crate::link::Link;
use hoglint_facts::link::rules::Rules as CoreRules;
use hoglint_facts::link::rules::reach::Found;
use hoglint_facts::link::rules::value::Truth;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

/// A finding row: (edge id, destination index, judgment code, identity, source module,
/// destination module or None).
type RowTuple = (u32, u32, u8, Vec<String>, String, Option<String>);

/// A cycle row: (edge id, destination index, judgment code, the pair's groups or None).
type PairTuple = (u32, u32, u8, Option<(String, String)>);

/// A reach finding: (what, start module, path of (edge id, destination index), verdict code).
/// What: "start" (path empty), "unresolved", "unknown" or "denied". Verdict codes: 1 true,
/// 2 unknown; 0 for anything but "denied".
type FoundTuple = (&'static str, String, Vec<(u32, u32)>, u8);

/// The run's labels and compiled rules over one link.
#[pyclass(module = "hoglint_native", unsendable)]
pub(crate) struct Rules {
    link: Py<Link>,
    inner: CoreRules,
}

#[pymethods]
impl Rules {
    /// Reads the plan (JSON), labels every graph module and compiles every rule. Evaluation runs
    /// on the threads the link was built with.
    #[new]
    fn new(py: Python<'_>, link: Py<Link>, plan: &str) -> PyResult<Rules> {
        let inner = {
            let mut bound = link.bind(py).borrow_mut();
            let threads = bound.threads;
            CoreRules::new(&mut bound.inner, plan, threads).map_err(PyValueError::new_err)?
        };
        Ok(Rules { link, inner })
    }

    /// Every destination of every candidate edge rule `rule` does not judge clean, in the order
    /// the Python engine judges them. Judgment codes: 1 unresolved, 2 unknown selection, 3 unknown
    /// violation, 4 violation.
    fn judge(&self, py: Python<'_>, rule: usize) -> PyResult<Vec<RowTuple>> {
        let mut bound = self.link.bind(py).borrow_mut();
        let rows = self
            .inner
            .judge(&mut bound.inner, rule)
            .map_err(PyValueError::new_err)?;
        let it = &bound.inner.core.it;
        Ok(rows
            .into_iter()
            .map(|row| {
                (
                    row.edge,
                    row.target,
                    row.judgment as u8,
                    row.identity,
                    it.s(row.src_module).to_string(),
                    row.dst_module.map(|module| it.s(module).to_string()),
                )
            })
            .collect())
    }

    /// What cycle rule `rule` makes of each destination it cannot leave alone, in judging order:
    /// (edge id, destination index, judgment code, (source group, destination group) of a pair or
    /// None). Codes: 1 unresolved, 2 unknown selection, 4 a pair.
    fn pairs(&self, py: Python<'_>, rule: usize) -> PyResult<Vec<PairTuple>> {
        let mut bound = self.link.bind(py).borrow_mut();
        let rows = self
            .inner
            .pairs(&mut bound.inner, rule)
            .map_err(PyValueError::new_err)?;
        Ok(rows
            .into_iter()
            .map(|row| (row.edge, row.target, row.judgment as u8, row.pair))
            .collect())
    }

    /// The search of reach rule `rule`, from every start module its `from` selects, in the order
    /// the Python search finds things.
    fn reach(&self, py: Python<'_>, rule: usize) -> PyResult<Vec<FoundTuple>> {
        let mut bound = self.link.bind(py).borrow_mut();
        let found = self
            .inner
            .reach(&mut bound.inner, rule)
            .map_err(PyValueError::new_err)?;
        let it = &bound.inner.core.it;
        let verdict = |truth: Truth| match truth {
            Truth::True => 1,
            Truth::Unknown => 2,
            Truth::False => 0,
        };
        Ok(found
            .into_iter()
            .map(|found| match found {
                Found::UnknownStart { module } => {
                    ("start", it.s(module).to_string(), Vec::new(), 0)
                }
                Found::Unresolved { source, step } => {
                    ("unresolved", it.s(source).to_string(), vec![step], 0)
                }
                Found::Unknown { source, step } => {
                    ("unknown", it.s(source).to_string(), vec![step], 0)
                }
                Found::Denied {
                    source,
                    path,
                    verdict: truth,
                } => ("denied", it.s(source).to_string(), path, verdict(truth)),
            })
            .collect())
    }

    /// The candidate edges of rule `rule` its selection does not rule out, in judging order.
    fn select<'py>(&self, py: Python<'py>, rule: usize) -> PyResult<Bound<'py, PyBytes>> {
        let mut bound = self.link.bind(py).borrow_mut();
        let ids = self
            .inner
            .select(&mut bound.inner, rule)
            .map_err(PyValueError::new_err)?;
        Ok(ids_bytes(py, &ids))
    }

    /// The edges of `kinds`, kind by kind in graph order, that a selector (its plan tree as JSON)
    /// does not make false, each read with its source as both ends.
    fn passing<'py>(
        &self,
        py: Python<'py>,
        kinds: Vec<String>,
        selector: &str,
    ) -> PyResult<Bound<'py, PyBytes>> {
        let mut bound = self.link.bind(py).borrow_mut();
        let ids = self
            .inner
            .passing(&mut bound.inner, &kinds, selector)
            .map_err(PyValueError::new_err)?;
        Ok(ids_bytes(py, &ids))
    }

    /// One field's value (or `registry`, `lifecycle`) on every graph module, rendered as an
    /// identity column, a zone as `name|chain`.
    fn column(&self, py: Python<'_>, field: &str) -> PyResult<Vec<String>> {
        let bound = self.link.bind(py).borrow();
        self.inner
            .module_column(&bound.inner, field)
            .map_err(PyValueError::new_err)
    }
}
