//! Building a link, the snapshot's records, and edges as tuples.

use crate::blobs::{Blob, BlobRequest, configs, load};
use crate::{HitTuple, Pair, ids_bytes, ids_from};
use hoglint_facts::link::facts::{FactSet, decode_lines};
use hoglint_facts::link::index::kind_code;
use hoglint_facts::link::intern::{EMPTY, Interner, Sid};
use hoglint_facts::link::produce::KIND_NAMES;
use hoglint_facts::link::resolve::{Binding, Ent, HitId};
use hoglint_facts::link::scope::Lookup;
use hoglint_facts::link::{ClassFact, Link as CoreLink, LinkConfig};
use pyo3::exceptions::{PyKeyError, PyValueError};
use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyList, PyString, PyTuple};
use rustc_hash::FxHashMap;
use std::ffi::{c_int, c_void};
use std::sync::Arc;

/// The JSON text of every record, as one read-only buffer: `memoryview(link.text())` reads it with
/// no copy. `Link.kind_ranges` says where each record's fact lists sit in it.
#[pyclass(module = "hoglint_native", frozen)]
pub(crate) struct FactText {
    facts: Arc<FactSet>,
}

#[pymethods]
impl FactText {
    unsafe fn __getbuffer__(
        slf: Bound<'_, Self>,
        view: *mut ffi::Py_buffer,
        flags: c_int,
    ) -> PyResult<()> {
        let text = slf.get().facts.text().map_err(PyValueError::new_err)?;
        let buf = text.as_ptr() as *mut c_void;
        let filled = unsafe {
            ffi::PyBuffer_FillInfo(
                view,
                slf.as_ptr(),
                buf,
                text.len() as ffi::Py_ssize_t,
                1,
                flags,
            )
        };
        if filled == -1 {
            return Err(PyErr::fetch(slf.py()));
        }
        Ok(())
    }

    unsafe fn __releasebuffer__(&self, _view: *mut ffi::Py_buffer) {}
}

/// A linked snapshot, (cache hits, files extracted), each record's fact cache key, and the
/// working tree files that changed during the run.
type Loaded = (CoreLink, (usize, usize), Vec<Option<String>>, Vec<String>);

/// One linked snapshot: facts, module index, resolver state and the core edges.
#[pyclass(module = "hoglint_native", unsendable)]
pub(crate) struct Link {
    pub(crate) inner: CoreLink,
    pub(crate) file_of: FxHashMap<String, u32>,
    /// The fact cache key of each record (`BlobStats::keys`); None for JSONL input.
    pub(crate) keys: Vec<Option<String>>,
    /// Working tree files that changed while the run read them (`BlobStats::drifted`).
    pub(crate) drifted: Vec<String>,
}

impl Link {
    pub(crate) fn raw_value(&self, file: usize, key: &str) -> PyResult<Option<Vec<u8>>> {
        let facts = &self.inner.core.facts;
        if key == "path" {
            return Ok(serde_json::to_vec(&*facts.files[file].path).ok());
        }
        let raw = facts.raw(file, key).map_err(PyValueError::new_err)?;
        Ok(raw.map(<[u8]>::to_vec))
    }

    pub(crate) fn pair(&self, e: Ent) -> Pair {
        let it = &self.inner.core.it;
        (it.s(e.m).to_string(), it.s(e.s).to_string())
    }

    pub(crate) fn hit(&self, hit: Option<HitId>) -> Option<HitTuple> {
        let hit = hit?;
        let core = &self.inner.core;
        let chain = core.chain(hit).iter().map(|&e| self.pair(e)).collect();
        Some((core.kind(hit).name(), chain))
    }

    pub(crate) fn lookup(&self, found: Lookup) -> (String, Option<HitTuple>, bool) {
        (
            self.inner.core.it.s(found.surface).to_string(),
            self.hit(found.hit),
            found.local,
        )
    }

    pub(crate) fn ent(&mut self, module: &str, symbol: &str) -> Ent {
        let it = &mut self.inner.core.it;
        Ent {
            m: it.intern(module),
            s: it.intern(symbol),
        }
    }

    pub(crate) fn sid(&mut self, text: &str) -> Sid {
        self.inner.core.it.intern(text)
    }

    pub(crate) fn scope_index(&self, scope: u32) -> PyResult<usize> {
        let index = scope as usize;
        if index >= self.inner.scopes.len() {
            return Err(PyValueError::new_err(format!("no scope {scope}")));
        }
        Ok(index)
    }
}

#[pymethods]
impl Link {
    /// Seconds per stage of the last build: [(name, seconds)].
    fn timings(&self) -> Vec<(&'static str, f64)> {
        self.inner.timings.stages.clone()
    }

    /// Estimated heap bytes per part of the link: [(part, bytes)].
    fn heap(&self) -> Vec<(&'static str, usize)> {
        self.inner.heap()
    }

    fn edge_count(&self) -> usize {
        self.inner.edges.rows.len()
    }

    /// Paths of every record, in fact order; a path's index is its file id.
    fn paths(&self) -> Vec<String> {
        self.inner
            .core
            .facts
            .files
            .iter()
            .map(|f| f.path.to_string())
            .collect()
    }

    /// The file id of each analyzed file's scope, in scope order.
    fn scope_files(&self) -> Vec<u32> {
        self.inner.scopes.iter().map(|s| s.file).collect()
    }

    /// Analyzed files in scope order: (file id, module, is_package, path context bits).
    fn scopes(&self) -> Vec<(u32, String, bool, u8)> {
        let it = &self.inner.core.it;
        self.inner
            .scopes
            .iter()
            .map(|s| (s.file, it.s(s.module).to_string(), s.is_package, s.base_ctx))
            .collect()
    }

    fn file_id(&self, path: &str) -> Option<u32> {
        self.file_of.get(path).copied()
    }

    /// The JSON text of one top-level key of a record, as the extractor wrote it. `path` is the
    /// snapshot's path of the file: a cached record holds an empty one.
    fn raw<'py>(
        &self,
        py: Python<'py>,
        file: usize,
        key: &str,
    ) -> PyResult<Option<Bound<'py, PyBytes>>> {
        if file >= self.inner.core.facts.files.len() {
            return Err(PyKeyError::new_err(file));
        }
        Ok(self.raw_value(file, key)?.map(|raw| PyBytes::new(py, &raw)))
    }

    /// The JSON text of every record, for zero-copy reads. The first call reads it from the
    /// fact database.
    fn text(&self, py: Python<'_>) -> PyResult<FactText> {
        let facts = self.inner.core.facts.clone();
        py.detach(|| facts.text().map(|_| ()))
            .map_err(PyValueError::new_err)?;
        Ok(FactText { facts })
    }

    /// Where one top-level key of every record sits in `text()`: (start, end) u32 pairs in fact
    /// order, u32::MAX for a record without the key.
    fn kind_ranges<'py>(&self, py: Python<'py>, key: &str) -> Bound<'py, PyBytes> {
        let facts = &self.inner.core.facts;
        let mut out = Vec::with_capacity(facts.files.len() * 2);
        for file in &facts.files {
            match file.keys.iter().find(|(name, _)| &**name == key) {
                Some((_, range)) => out.extend_from_slice(&[range.start as u32, range.end as u32]),
                None => out.extend_from_slice(&[u32::MAX, u32::MAX]),
            }
        }
        ids_bytes(py, &out)
    }

    /// One top-level key of every record, in fact order: its JSON text, or None where the
    /// record has no such key. Packs read a fact kind for a file set in one call.
    fn raw_kind<'py>(
        &self,
        py: Python<'py>,
        key: &str,
    ) -> PyResult<Vec<Option<Bound<'py, PyBytes>>>> {
        let count = self.inner.core.facts.files.len();
        (0..count)
            .map(|file| Ok(self.raw_value(file, key)?.map(|raw| PyBytes::new(py, &raw))))
            .collect()
    }

    /// A module's table, or None when the module does not exist: (is_package, names in
    /// binding order as (name, binding), star import sources, lazy map in order). A binding is
    /// the Python resolver's tuple: ("class"|"def", def index, ""|"stub"), ("variable",),
    /// ("module", dotted), ("from", source, name) or ("alias", chain).
    #[allow(clippy::type_complexity)]
    fn table<'py>(
        &mut self,
        py: Python<'py>,
        module: &str,
    ) -> PyResult<
        Option<(
            bool,
            Vec<(String, Bound<'py, PyTuple>)>,
            Vec<String>,
            Vec<Pair>,
        )>,
    > {
        let module = self.sid(module);
        let Some(id) = self.inner.core.table(module) else {
            return Ok(None);
        };
        let core = &self.inner.core;
        let it = &core.it;
        let table = core.table_at(id);
        let mut names = Vec::new();
        for (name, binding) in table.names.iter() {
            let marker = |stub: bool| if stub { "stub" } else { "" };
            let tuple = match binding {
                Binding::Class(idx, stub) => ("class", idx, marker(stub)).into_pyobject(py)?,
                Binding::Def(idx, stub) => ("def", idx, marker(stub)).into_pyobject(py)?,
                Binding::Variable => PyTuple::new(py, ["variable"])?,
                Binding::Module(dotted) => ("module", it.s(dotted)).into_pyobject(py)?,
                Binding::From(source, attr) => {
                    ("from", it.s(source), it.s(attr)).into_pyobject(py)?
                }
                Binding::Alias(chain) => ("alias", it.s(chain)).into_pyobject(py)?,
            };
            names.push((it.s(name).to_string(), tuple));
        }
        let stars = table
            .star_imports
            .iter()
            .map(|&s| it.s(s).to_string())
            .collect();
        let lazy = table
            .lazy
            .iter()
            .map(|(k, v)| (it.s(k).to_string(), it.s(v).to_string()))
            .collect();
        Ok(Some((table.is_package, names, stars, lazy)))
    }

    /// Top-level keys of a record.
    fn keys(&self, file: usize) -> Vec<String> {
        self.inner.core.facts.files[file]
            .keys
            .iter()
            .map(|(k, _)| k.to_string())
            .collect()
    }

    /// `graph.symbols` of the core: [(symbol, kind, path)] in insertion order.
    fn symbols(&self) -> Vec<(String, &'static str, String)> {
        let kinds = ["def", "async", "class"];
        let core = &self.inner.core;
        self.inner
            .symbols
            .order
            .iter()
            .map(|&(name, kind, file)| {
                (
                    core.it.s(name).to_string(),
                    kinds[kind as usize],
                    core.facts.files[file as usize].path.to_string(),
                )
            })
            .collect()
    }

    /// `graph.symbols` of the core as columns, in insertion order: (names, kind code of each
    /// (0 def, 1 async, 2 class) as bytes, file id of each as u32 bytes). No dict per symbol
    /// crosses.
    fn symbol_columns<'py>(
        &self,
        py: Python<'py>,
    ) -> (
        Vec<Bound<'py, PyString>>,
        Bound<'py, PyBytes>,
        Bound<'py, PyBytes>,
    ) {
        let order = &self.inner.symbols.order;
        let it = &self.inner.core.it;
        let names = order
            .iter()
            .map(|&(name, _, _)| PyString::new(py, it.s(name)))
            .collect();
        let kinds: Vec<u8> = order.iter().map(|&(_, kind, _)| kind).collect();
        let files: Vec<u32> = order.iter().map(|&(_, _, file)| file).collect();
        (names, PyBytes::new(py, &kinds), ids_bytes(py, &files))
    }

    /// Applies refinements to core edges (D22): for each edge id, the shape and shape detail to
    /// set (None keeps the edge's) and attrs to add after the edge's own. Python checks first that
    /// no two producers set one field apart.
    #[allow(clippy::type_complexity)]
    fn refine(
        &mut self,
        ids: &[u8],
        shapes: Vec<Option<String>>,
        details: Vec<Option<String>>,
        attrs: Vec<Vec<(String, String)>>,
    ) -> PyResult<()> {
        let ids = ids_from(ids)?;
        if shapes.len() != ids.len() || details.len() != ids.len() || attrs.len() != ids.len() {
            return Err(PyValueError::new_err(
                "one shape, detail and attr list per edge",
            ));
        }
        for (((id, shape), detail), pairs) in ids.into_iter().zip(shapes).zip(details).zip(attrs) {
            if id as usize >= self.inner.edges.rows.len() {
                return Err(PyKeyError::new_err(id));
            }
            let shape = shape.map(|s| self.sid(&s));
            let detail = detail.map(|s| self.sid(&s));
            let pairs: Vec<(Sid, Sid)> = pairs
                .iter()
                .map(|(k, v)| (self.sid(k), self.sid(v)))
                .collect();
            self.inner.edges.refine(id, shape, detail, &pairs);
        }
        Ok(())
    }

    /// Core edge kinds in the order their first edge appears.
    fn kind_order(&self) -> Vec<&'static str> {
        self.inner
            .kind_order()
            .into_iter()
            .map(|kind| KIND_NAMES[kind as usize])
            .collect()
    }

    /// The core's inherit (`owner_attr` "class") or decorate ("decorated") facts for SymbolFacts:
    /// [(owner symbol, surface, names)] in graph order.
    fn class_facts(&self, kind: &str, owner_attr: &str) -> Vec<ClassFact> {
        match kind_code(kind) {
            Some(code) => self.inner.class_facts(code, owner_attr),
            None => Vec::new(),
        }
    }

    /// Edges as tuples: (kind, path, module, scope, line, column, surface, target module or
    /// None, target symbol, resolution, context bits, via ((module, symbol), ...), use,
    /// attrs ((key, value), ...), shape, shape detail), with what refinements set.
    fn edges<'py>(&self, py: Python<'py>, ids: &[u8]) -> PyResult<Bound<'py, PyList>> {
        let ids = ids_from(ids)?;
        let link = &self.inner;
        let core = &link.core;
        let it = &core.it;
        let mut strings: FxHashMap<Sid, Bound<'py, PyString>> = FxHashMap::default();
        let mut text = |sid: Sid| -> Bound<'py, PyString> {
            strings
                .entry(sid)
                .or_insert_with(|| PyString::new(py, it.s(sid)))
                .clone()
        };
        let mut paths: FxHashMap<u32, Bound<'py, PyString>> = FxHashMap::default();
        let mut out = Vec::with_capacity(ids.len());
        for id in ids {
            let Some(row) = link.edges.rows.get(id as usize) else {
                return Err(PyKeyError::new_err(id));
            };
            let scope = &link.scopes[row.owner as usize];
            let scope_name = scope.scope_name(row.scope, link.settings.module_scope);
            let (target_module, target_symbol) = match row.target {
                Some(t) => (Some(text(t.m)), text(t.s)),
                None => (None, text(EMPTY)),
            };
            let via: Vec<Bound<'py, PyTuple>> = link
                .edges
                .via_of(row)
                .iter()
                .map(|e| PyTuple::new(py, [text(e.m), text(e.s)]))
                .collect::<PyResult<_>>()?;
            let attrs: Vec<Bound<'py, PyTuple>> = link
                .edges
                .attrs_with(id)
                .map(|&(k, v)| PyTuple::new(py, [text(k), text(v)]))
                .collect::<PyResult<_>>()?;
            let (shape, shape_detail) = link.edges.shape_of(id);
            let path = paths
                .entry(row.file)
                .or_insert_with(|| PyString::new(py, &core.facts.files[row.file as usize].path))
                .clone();
            let target_module = match target_module {
                Some(module) => module.into_any(),
                None => py.None().into_bound(py),
            };
            let fields: [Bound<'py, PyAny>; 16] = [
                PyString::new(py, KIND_NAMES[row.kind as usize]).into_any(),
                path.into_any(),
                text(scope.module).into_any(),
                text(scope_name).into_any(),
                row.line.into_pyobject(py)?.into_any(),
                row.col.into_pyobject(py)?.into_any(),
                text(row.surface).into_any(),
                target_module,
                target_symbol.into_any(),
                PyString::new(py, row.res.name()).into_any(),
                row.ctx.into_pyobject(py)?.into_any(),
                PyTuple::new(py, via)?.into_any(),
                text(row.use_).into_any(),
                PyTuple::new(py, attrs)?.into_any(),
                text(shape).into_any(),
                text(shape_detail).into_any(),
            ];
            out.push(PyTuple::new(py, fields)?);
        }
        PyList::new(py, out)
    }
}

/// Links facts from a JSONL file (one record per line) under a link config (JSON text).
/// `threads` caps the worker threads; 0 uses every core.
#[pyfunction]
#[pyo3(signature = (path, config, threads=0))]
pub(crate) fn link_jsonl(
    py: Python<'_>,
    path: &str,
    config: &str,
    threads: usize,
) -> PyResult<Link> {
    let config: LinkConfig = LinkConfig::from_json(config)
        .map_err(|e| PyValueError::new_err(format!("link config: {e}")))?;
    let path = path.to_string();
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(threads)
        .build()
        .map_err(|e| PyValueError::new_err(format!("threads: {e}")))?;
    let inner = py
        .detach(move || {
            pool.install(|| -> Result<CoreLink, String> {
                let text = std::fs::read(&path).map_err(|e| format!("read {path}: {e}"))?;
                let mut it = Interner::new();
                let facts = decode_lines(&mut it, text).map_err(|e| e.to_string())?;
                CoreLink::build(&config, it, facts)
            })
        })
        .map_err(PyValueError::new_err)?;
    let file_of: FxHashMap<String, u32> = inner
        .core
        .facts
        .files
        .iter()
        .enumerate()
        .map(|(i, f)| (f.path.to_string(), i as u32))
        .collect();
    let keys = vec![None; file_of.len()];
    Ok(Link {
        inner,
        file_of,
        keys,
        drifted: Vec::new(),
    })
}

fn strings(spec: &serde_json::Value, key: &str) -> Vec<String> {
    spec[key]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|v| v.as_str().map(str::to_string))
        .collect()
}

/// Links a snapshot given as blobs: (path, blob oid, read from the working tree) in discovery
/// order, then third-party files as (record path, file). Facts come from the fact database `db`
/// (a SQLite file, one per facts id) or are extracted in process. `extract` is the extractor's options as JSON: first_party,
/// kinds, watch_callees, watch_prefixes, watch_keys, lite_kinds. Returns the link and (cache
/// hits, files extracted).
#[pyfunction]
#[pyo3(signature = (repo, blobs, external, extract, config, db=None, threads=0))]
#[allow(clippy::too_many_arguments)]
pub(crate) fn link_blobs(
    py: Python<'_>,
    repo: &str,
    blobs: Vec<(String, String, bool)>,
    external: Vec<(String, String)>,
    extract: &str,
    config: &str,
    db: Option<String>,
    threads: usize,
) -> PyResult<(Link, (usize, usize))> {
    let config: LinkConfig = LinkConfig::from_json(config)
        .map_err(|e| PyValueError::new_err(format!("link config: {e}")))?;
    let spec: serde_json::Value = serde_json::from_str(extract)
        .map_err(|e| PyValueError::new_err(format!("extract options: {e}")))?;
    let watch = (
        strings(&spec, "watch_callees"),
        strings(&spec, "watch_prefixes"),
        strings(&spec, "watch_keys"),
    );
    let (full, lite) = configs(
        strings(&spec, "first_party"),
        &strings(&spec, "kinds"),
        watch,
        &strings(&spec, "lite_kinds"),
    )
    .map_err(PyValueError::new_err)?;
    let request = BlobRequest {
        repo: repo.into(),
        blobs: blobs
            .into_iter()
            .map(|(path, oid, from_disk)| Blob {
                path,
                oid,
                from_disk,
            })
            .collect(),
        external: external
            .into_iter()
            .map(|(path, file)| (path, file.into()))
            .collect(),
        full,
        lite,
        db: db.map(Into::into),
    };
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(threads)
        .build()
        .map_err(|e| PyValueError::new_err(format!("threads: {e}")))?;
    let (inner, stats, keys, drifted) = py
        .detach(move || {
            pool.install(|| -> Result<Loaded, String> {
                let started = std::time::Instant::now();
                let mut it = Interner::new();
                let (facts, stats) = load(&request, &mut it)?;
                let read = started.elapsed().as_secs_f64();
                let mut link = CoreLink::build(&config, it, facts)?;
                link.timings.stages.insert(0, ("facts", read));
                for (at, stage) in stats.stages.iter().enumerate() {
                    link.timings.stages.insert(at + 1, *stage);
                }
                Ok((
                    link,
                    (stats.hits, stats.extracted),
                    stats.keys,
                    stats.drifted,
                ))
            })
        })
        .map_err(PyValueError::new_err)?;
    let file_of = inner
        .core
        .facts
        .files
        .iter()
        .enumerate()
        .map(|(i, f)| (f.path.to_string(), i as u32))
        .collect();
    Ok((
        Link {
            inner,
            file_of,
            keys,
            drifted,
        },
        stats,
    ))
}
