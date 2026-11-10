//! Rule evaluation (`hoglint_facts::link::rules`): one `Rules` per run, made from a plan.

use crate::link::Link;
use crate::{ids_bytes, ids_from};
use hoglint_facts::link::rules::conflicts::Conflict;
use hoglint_facts::link::rules::export::ExportRow;
use hoglint_facts::link::rules::labels::Cell;
use hoglint_facts::link::rules::reach::Found;
use hoglint_facts::link::rules::{NO_PLACE, Rules as CoreRules, place_of};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use rustc_hash::FxHashMap;

/// The rows of a judged rule as columns, so Python makes no object per row: (edge ids,
/// destination indexes, judgment names, each row's judgment by place, distinct identities, each
/// row's identity by place (`Judgment::has_identity`), distinct modules, each row's source module by place, each row's
/// destination module by place). Per-row columns are little-endian u32s; a row with no identity
/// or no destination module has `NO_PLACE` there.
type JudgedColumns<'py> = (
    Bound<'py, PyBytes>,
    Bound<'py, PyBytes>,
    Vec<&'static str>,
    Bound<'py, PyBytes>,
    Vec<Vec<String>>,
    Bound<'py, PyBytes>,
    Vec<String>,
    Bound<'py, PyBytes>,
    Bound<'py, PyBytes>,
);

/// A cycle row: (edge id, destination index, judgment name, the pair's groups or None).
type PairTuple = (u32, u32, &'static str, Option<(String, String)>);

/// A label set two ways: (what, entity, field). What: "module" (a module's field), "zones" (leaf
/// zones that claim a module's path), "kind" (a name two kinds hold), "symbol" (a symbol's field),
/// "provided" (a module's provider labels). The field is empty where it names none.
type ConflictTuple = (&'static str, String, String);

/// A reach finding: (what, start module, path of (edge id, destination index), verdict).
/// What: "start" (path empty), "unresolved", "unknown" or "denied". The verdict of a denied
/// target is a truth's name, "true" or "unknown"; None for anything else.
type FoundTuple = (&'static str, String, Vec<(u32, u32)>, Option<&'static str>);

/// A module field's column: its distinct values as (type, items), and each module's place.
type ValueColumn<'py> = (Vec<(&'static str, Vec<String>)>, Bound<'py, PyBytes>);

/// An export rule's report, as data the rule's Python class words. A finding: ("finding",
/// symbol, module, label, trail, witness edge, ledgerable, unknown, identity, subject). A symbol
/// it cannot tell about: ("unknown", symbol, module, why code, trail, None, false, false, [],
/// detail). An import that did not resolve: ("unresolved", surface, module, "", [], None,
/// false, false, [], "").
type ExportTuple = (
    &'static str,
    String,
    String,
    String,
    Vec<String>,
    Option<u32>,
    bool,
    bool,
    Vec<String>,
    String,
);

/// The run's labels and compiled rules over one link.
#[pyclass(module = "hoglint_native", unsendable)]
pub(crate) struct Rules {
    link: Py<Link>,
    inner: CoreRules,
}

impl Rules {
    fn conflict_tuple(
        &self,
        link: &hoglint_facts::link::Link,
        conflict: Conflict,
    ) -> ConflictTuple {
        let module = |at: u32| self.inner.module_name(link, at).to_string();
        let field = |at: usize| self.inner.labels.fields[at].name.clone();
        match conflict {
            Conflict::Module(at, name) => ("module", module(at), field(name)),
            Conflict::Zones(at) => ("zones", module(at), String::new()),
            Conflict::Kind(symbol) => ("kind", symbol, String::new()),
            Conflict::Symbol(symbol, name) => ("symbol", symbol, field(name)),
            Conflict::Provided(at) => ("provided", module(at), String::new()),
        }
    }
}

#[pymethods]
impl Rules {
    /// The module fields a Python provider sets that a plan's compiled rules, audiences, shapes,
    /// domains and levels read (D54): Python sends their columns in the plan it runs.
    #[staticmethod]
    fn reads(py: Python<'_>, link: Py<Link>, plan: &str) -> PyResult<Vec<String>> {
        let bound = link.bind(py).borrow();
        CoreRules::reads(&bound.inner, plan).map_err(PyValueError::new_err)
    }

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
    /// the Python engine judges them, as columns (`JudgedColumns`). A judgment travels by its
    /// name (`rules.base.Judgment`).
    fn judge<'py>(&self, py: Python<'py>, rule: usize) -> PyResult<JudgedColumns<'py>> {
        let mut bound = self.link.bind(py).borrow_mut();
        let rows = self
            .inner
            .judge(&mut bound.inner, rule)
            .map_err(PyValueError::new_err)?;
        let it = &bound.inner.core.it;
        let count = rows.len();
        let (mut edges, mut targets) = (Vec::with_capacity(count), Vec::with_capacity(count));
        let (mut judgment_at, mut identity_at) =
            (Vec::with_capacity(count), Vec::with_capacity(count));
        let (mut src_at, mut dst_at) = (Vec::with_capacity(count), Vec::with_capacity(count));
        let mut judgments = Vec::new();
        let mut judgment_places = FxHashMap::default();
        let mut identities = Vec::new();
        let mut identity_places = FxHashMap::default();
        let mut modules = Vec::new();
        let mut module_places = FxHashMap::default();
        for row in rows {
            edges.push(row.edge);
            targets.push(row.target);
            judgment_at.push(place_of(
                &mut judgment_places,
                &mut judgments,
                row.judgment.name(),
            ));
            identity_at.push(if row.judgment.has_identity() {
                place_of(&mut identity_places, &mut identities, row.identity)
            } else {
                NO_PLACE
            });
            src_at.push(place_of(&mut module_places, &mut modules, row.src_module));
            dst_at.push(match row.dst_module {
                Some(module) => place_of(&mut module_places, &mut modules, module),
                None => NO_PLACE,
            });
        }
        Ok((
            ids_bytes(py, &edges),
            ids_bytes(py, &targets),
            judgments,
            ids_bytes(py, &judgment_at),
            identities,
            ids_bytes(py, &identity_at),
            modules
                .into_iter()
                .map(|module| it.s(module).to_string())
                .collect(),
            ids_bytes(py, &src_at),
            ids_bytes(py, &dst_at),
        ))
    }

    /// What cycle rule `rule` makes of each destination it cannot leave alone, in judging order:
    /// (edge id, destination index, judgment name, (source group, destination group) of a pair or
    /// None). Judgments: unresolved, unknown selection, a violation for a pair.
    fn pairs(&self, py: Python<'_>, rule: usize) -> PyResult<Vec<PairTuple>> {
        let mut bound = self.link.bind(py).borrow_mut();
        let rows = self
            .inner
            .pairs(&mut bound.inner, rule)
            .map_err(PyValueError::new_err)?;
        Ok(rows
            .into_iter()
            .map(|row| (row.edge, row.target, row.judgment.name(), row.pair))
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
        Ok(found
            .into_iter()
            .map(|found| match found {
                Found::UnknownStart { module } => {
                    ("start", it.s(module).to_string(), Vec::new(), None)
                }
                Found::Unresolved { source, step } => {
                    ("unresolved", it.s(source).to_string(), vec![step], None)
                }
                Found::Unknown { source, step } => {
                    ("unknown", it.s(source).to_string(), vec![step], None)
                }
                Found::Denied {
                    source,
                    path,
                    verdict,
                } => (
                    "denied",
                    it.s(source).to_string(),
                    path,
                    Some(verdict.name()),
                ),
            })
            .collect())
    }

    /// What export rule `rule` reports, in the order the Python primitive reports it.
    fn export(&self, py: Python<'_>, rule: usize) -> PyResult<Vec<ExportTuple>> {
        let mut bound = self.link.bind(py).borrow_mut();
        let rows = self
            .inner
            .export(&mut bound.inner, rule)
            .map_err(PyValueError::new_err)?;
        Ok(rows
            .into_iter()
            .map(|row| match row {
                ExportRow::Finding {
                    symbol,
                    module,
                    label,
                    trail,
                    edge,
                    ledgerable,
                    unknown,
                    identity,
                    subject,
                } => (
                    "finding", symbol, module, label, trail, edge, ledgerable, unknown, identity,
                    subject,
                ),
                ExportRow::Unknown {
                    symbol,
                    module,
                    trail,
                    why,
                    detail,
                } => (
                    "unknown",
                    symbol,
                    module,
                    why.to_string(),
                    trail,
                    None,
                    false,
                    false,
                    Vec::new(),
                    detail,
                ),
                ExportRow::Unresolved { surface, module } => (
                    "unresolved",
                    surface,
                    module,
                    String::new(),
                    Vec::new(),
                    None,
                    false,
                    false,
                    Vec::new(),
                    String::new(),
                ),
            })
            .collect())
    }

    /// The paths coverage rule `rule` needs covered: (path, the place of the cover that names it
    /// first, module), sorted by path; and in order what it cannot tell, (what, cover, source
    /// module, surface), what "not-run" (the cover's rule did not run) or "unplaced" (an edge it
    /// cannot place). `witnesses` holds, per cover, the witness edge ids of the findings of the
    /// rule it reads (little-endian u32s), or None when that rule did not run; an edge cover's
    /// entry is ignored.
    #[allow(clippy::type_complexity)]
    fn coverage(
        &self,
        py: Python<'_>,
        rule: usize,
        witnesses: Vec<Option<Vec<u8>>>,
    ) -> PyResult<(
        Vec<(String, u32, Option<String>)>,
        Vec<(&'static str, u32, Option<String>, String)>,
    )> {
        let inputs = witnesses
            .iter()
            .map(|found| found.as_deref().map(ids_from).transpose())
            .collect::<PyResult<Vec<_>>>()?;
        let mut bound = self.link.bind(py).borrow_mut();
        let found = self
            .inner
            .coverage(&mut bound.inner, rule, inputs)
            .map_err(PyValueError::new_err)?;
        Ok((found.paths, found.incomplete))
    }

    /// What a layout rule or an entity inventory reports on its entities, in order, as data the
    /// rule's Python class words: (what, subject, module, count, identity), what "finding",
    /// "unknown-selection" or "unknown-value".
    #[allow(clippy::type_complexity)]
    fn entities(
        &self,
        py: Python<'_>,
        rule: usize,
    ) -> PyResult<Vec<(&'static str, String, Option<String>, u32, Vec<String>)>> {
        let bound = self.link.bind(py).borrow();
        self.inner
            .entity_rows(&bound.inner, rule)
            .map_err(PyValueError::new_err)
    }

    /// The groups level `level` (its place in the plan) judges: its group field on every graph
    /// module where it is known and its `select` is true, sorted.
    fn level_groups(&self, py: Python<'_>, level: usize) -> PyResult<Vec<String>> {
        let bound = self.link.bind(py).borrow();
        self.inner
            .level_groups(&bound.inner, level)
            .map_err(PyValueError::new_err)
    }

    /// Every name a kind holds, sorted.
    fn kind_members(&self, name: &str) -> PyResult<Vec<String>> {
        self.inner.kind_members(name).map_err(PyValueError::new_err)
    }

    /// The roots a layout rule's `within` names among the graph's module paths, sorted.
    fn layout_roots(&self, py: Python<'_>, within: &str) -> PyResult<Vec<String>> {
        let bound = self.link.bind(py).borrow();
        self.inner
            .layout_roots(&bound.inner, within)
            .map_err(PyValueError::new_err)
    }

    /// The graph's (module, path) whose path a registry's `module` glob matches, by module.
    fn registry_modules(&self, py: Python<'_>, pattern: &str) -> PyResult<Vec<(String, String)>> {
        let bound = self.link.bind(py).borrow();
        self.inner
            .registry_modules(&bound.inner, pattern)
            .map_err(PyValueError::new_err)
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

    /// Every label `Labeler.find_conflicts` finds set two ways on this graph, for the Python
    /// labeler to word.
    fn conflicts(&self, py: Python<'_>) -> Vec<ConflictTuple> {
        let bound = self.link.bind(py).borrow();
        let found = self.inner.conflicts(&bound.inner);
        found
            .into_iter()
            .map(|conflict| self.conflict_tuple(&bound.inner, conflict))
            .collect()
    }

    /// The labels set two ways that the rules met while they ran: what the Python engine meets
    /// lazily, where a rule reads them.
    fn met(&self, py: Python<'_>) -> Vec<ConflictTuple> {
        let bound = self.link.bind(py).borrow();
        self.inner
            .met()
            .into_iter()
            .map(|conflict| self.conflict_tuple(&bound.inner, conflict))
            .collect()
    }

    /// A module field's value on every graph module (`Labeler.field_value`): its distinct
    /// values as (type, items), and each module's place among them as little-endian u32s,
    /// 0xFFFFFFFF where Python must read the value itself (it meets a provider conflict).
    /// Types: unknown, none, text, bool (items "True" or "False"), set, zone (the zone's name).
    fn values<'py>(&self, py: Python<'py>, field: &str) -> PyResult<ValueColumn<'py>> {
        let (distinct, codes) = self
            .inner
            .value_column(field)
            .map_err(PyValueError::new_err)?;
        let zones = &self.inner.labels.zones.infos;
        let typed = distinct
            .into_iter()
            .map(|cell| match cell {
                Cell::Unknown => ("unknown", Vec::new()),
                Cell::None => ("none", Vec::new()),
                Cell::Text(text) => ("text", vec![text.to_string()]),
                Cell::Bool(flag) => ("bool", vec![if *flag { "True" } else { "False" }.into()]),
                Cell::Set(items) => ("set", items.iter().map(|item| item.to_string()).collect()),
                Cell::Zone(code) => ("zone", vec![zones[*code as usize].name.clone()]),
            })
            .collect();
        Ok((typed, ids_bytes(py, &codes)))
    }

    /// Each graph module's registry, by its place in the plan's registries, as little-endian
    /// u32s; 0xFFFFFFFF where no registry holds the module.
    fn registries<'py>(&self, py: Python<'py>) -> Bound<'py, PyBytes> {
        ids_bytes(py, &self.inner.registry_column())
    }
}
