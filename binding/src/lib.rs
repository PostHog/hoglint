//! hoglint_native: the Python binding of the native link. A thin shell: it converts arguments,
//! releases the GIL for the heavy calls and hands out compact results. All semantics live in
//! `hoglint_facts::link`.
//!
//! A `Link` is an opaque handle on one linked snapshot. Bulk results cross as `bytes` of
//! little-endian u32 edge ids; Python builds Edge objects only for the ids it asks for.

mod blobs;

use blobs::{Blob, BlobRequest, configs, load};
use hoglint_facts::link::facts::{FactSet, V, decode_lines};
use hoglint_facts::link::groups::Role;
use hoglint_facts::link::index::kind_code;
use hoglint_facts::link::intern::{EMPTY, Interner, Sid};
use hoglint_facts::link::produce::KIND_NAMES;
use hoglint_facts::link::resolve::{Binding, Ent, HitId};
use hoglint_facts::link::scope::Lookup;
use hoglint_facts::link::{Link as CoreLink, LinkConfig};
use pyo3::exceptions::{PyKeyError, PyValueError};
use pyo3::ffi;
use pyo3::prelude::*;
use std::ffi::{c_int, c_void};
use std::sync::Arc;
use pyo3::types::{PyBytes, PyDict, PyList, PyString, PyTuple};
use rustc_hash::FxHashMap;

/// The binding's call protocol. Bump it when a call's shape or meaning changes.
const PROTOCOL: u32 = 1;

type Pair = (String, String);
type HitTuple = (&'static str, Vec<Pair>);

fn ids_bytes<'py>(py: Python<'py>, ids: &[u32]) -> Bound<'py, PyBytes> {
    let mut raw = Vec::with_capacity(ids.len() * 4);
    for id in ids {
        raw.extend_from_slice(&id.to_le_bytes());
    }
    PyBytes::new(py, &raw)
}

fn ids_from(raw: &[u8]) -> PyResult<Vec<u32>> {
    let (chunks, rest) = raw.as_chunks::<4>();
    if !rest.is_empty() {
        return Err(PyValueError::new_err("edge ids must be little-endian u32s"));
    }
    Ok(chunks.iter().map(|c| u32::from_le_bytes(*c)).collect())
}

/// A JSON-like Python value tree (what `orjson` makes of a fact) as the link's V.
fn py_value(it: &mut Interner, obj: &Bound<'_, PyAny>) -> PyResult<V> {
    if obj.is_none() {
        return Ok(V::Null);
    }
    if let Ok(text) = obj.cast::<PyString>() {
        return Ok(V::Chain(it.intern(&text.to_cow()?)));
    }
    let Ok(map) = obj.cast::<PyDict>() else {
        return Ok(V::Other);
    };
    let get = |key: &str| map.get_item(key);
    let items = |it: &mut Interner, value: Bound<'_, PyAny>| -> PyResult<Vec<V>> {
        let mut out = Vec::new();
        for item in value.try_iter()? {
            out.push(py_value(it, &item?)?);
        }
        Ok(out)
    };
    if let Some(inner) = get("sub")? {
        let index = match get("i")? {
            Some(index) => py_value(it, &index)?,
            None => V::Null,
        };
        return Ok(V::Sub(Box::new(py_value(it, &inner)?), Box::new(index)));
    }
    if let Some(s) = get("s")? {
        let parsed = match get("sa")? {
            Some(sa) => Some(Box::new(py_value(it, &sa)?)),
            None => None,
        };
        let text: String = s.extract().unwrap_or_default();
        return Ok(V::Str(it.intern(&text), parsed));
    }
    if let Some(callee) = get("call")? {
        return Ok(V::Call(Box::new(py_value(it, &callee)?)));
    }
    if let (Some(a), Some(v)) = (get("a")?, get("v")?) {
        let attr: String = a.extract().unwrap_or_default();
        let attr = it.intern(&attr);
        return Ok(V::Attr(Box::new(py_value(it, &v)?), attr));
    }
    if let Some(value) = get("tuple")? {
        return Ok(V::Tuple(items(it, value)?));
    }
    if let Some(value) = get("list")? {
        return Ok(V::List(items(it, value)?));
    }
    if let Some(value) = get("set")? {
        return Ok(V::Set(items(it, value)?));
    }
    if let Some(value) = get("dict")? {
        let mut pairs = Vec::new();
        for pair in value.try_iter()? {
            let pair = pair?;
            let key = pair.get_item(0)?;
            let key = if key.is_none() {
                None
            } else {
                Some(py_value(it, &key)?)
            };
            pairs.push((key, py_value(it, &pair.get_item(1)?)?));
        }
        return Ok(V::Dict(pairs));
    }
    if let Some(inner) = get("star")? {
        return Ok(V::Star(Box::new(py_value(it, &inner)?)));
    }
    if let Some(op) = get("bin")? {
        let pipe = op.extract::<String>().is_ok_and(|op| op == "|");
        let l = match get("l")? {
            Some(l) => py_value(it, &l)?,
            None => V::Null,
        };
        let r = match get("r")? {
            Some(r) => py_value(it, &r)?,
            None => V::Null,
        };
        return Ok(V::Bin(pipe, Box::new(l), Box::new(r)));
    }
    if let Some(what) = get("o")? {
        let what: String = what.extract().unwrap_or_default();
        return Ok(V::Opaque(it.intern(&what)));
    }
    Ok(V::Other)
}

/// The JSON text of every record, as one read-only buffer: `memoryview(link.text())` reads it with
/// no copy. `Link.kind_ranges` says where each record's fact lists sit in it.
#[pyclass(module = "hoglint_native", frozen)]
struct FactText {
    facts: Arc<FactSet>,
}

#[pymethods]
impl FactText {
    unsafe fn __getbuffer__(slf: Bound<'_, Self>, view: *mut ffi::Py_buffer, flags: c_int) -> PyResult<()> {
        let text = &slf.get().facts.text;
        let buf = text.as_ptr() as *mut c_void;
        let filled = unsafe { ffi::PyBuffer_FillInfo(view, slf.as_ptr(), buf, text.len() as ffi::Py_ssize_t, 1, flags) };
        if filled == -1 {
            return Err(PyErr::fetch(slf.py()));
        }
        Ok(())
    }

    unsafe fn __releasebuffer__(&self, _view: *mut ffi::Py_buffer) {}
}

/// One linked snapshot: facts, module index, resolver state and the core edges.
#[pyclass(module = "hoglint_native", unsendable)]
struct Link {
    inner: CoreLink,
    file_of: FxHashMap<String, u32>,
}

impl Link {
    fn raw_value(&self, file: usize, key: &str) -> Option<Vec<u8>> {
        let facts = &self.inner.core.facts;
        if key == "path" {
            return serde_json::to_vec(&*facts.files[file].path).ok();
        }
        facts.raw(file, key).map(<[u8]>::to_vec)
    }

    fn pair(&self, e: Ent) -> Pair {
        let it = &self.inner.core.it;
        (it.s(e.m).to_string(), it.s(e.s).to_string())
    }

    fn hit(&self, hit: Option<HitId>) -> Option<HitTuple> {
        let hit = hit?;
        let core = &self.inner.core;
        let chain = core.chain(hit).iter().map(|&e| self.pair(e)).collect();
        Some((core.kind(hit).name(), chain))
    }

    fn lookup(&self, found: Lookup) -> (String, Option<HitTuple>, bool) {
        (
            self.inner.core.it.s(found.surface).to_string(),
            self.hit(found.hit),
            found.local,
        )
    }

    fn ent(&mut self, module: &str, symbol: &str) -> Ent {
        let it = &mut self.inner.core.it;
        Ent {
            m: it.intern(module),
            s: it.intern(symbol),
        }
    }

    fn sid(&mut self, text: &str) -> Sid {
        self.inner.core.it.intern(text)
    }

    fn scope_index(&self, scope: u32) -> PyResult<usize> {
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
        Ok(self.raw_value(file, key).map(|raw| PyBytes::new(py, &raw)))
    }

    /// The JSON text of every record, for zero-copy reads.
    fn text(&self) -> FactText {
        FactText { facts: self.inner.core.facts.clone() }
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
    fn raw_kind<'py>(&self, py: Python<'py>, key: &str) -> Vec<Option<Bound<'py, PyBytes>>> {
        let count = self.inner.core.facts.files.len();
        (0..count)
            .map(|file| self.raw_value(file, key).map(|raw| PyBytes::new(py, &raw)))
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

    /// Core edge kinds in the order their first edge appears.
    fn kind_order(&self) -> Vec<&'static str> {
        self.inner
            .kind_order()
            .into_iter()
            .map(|kind| KIND_NAMES[kind as usize])
            .collect()
    }

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

    /// The core's inherit (`owner_attr` "class") or decorate ("decorated") facts for SymbolFacts:
    /// [(owner symbol, surface, names)] in graph order.
    fn class_facts(&self, kind: &str, owner_attr: &str) -> Vec<(String, String, Vec<String>)> {
        match kind_code(kind) {
            Some(code) => self.inner.class_facts(code, owner_attr),
            None => Vec::new(),
        }
    }

    /// Edges as tuples: (kind, path, module, scope, line, column, surface, target module or
    /// None, target symbol, resolution, context bits, via ((module, symbol), ...), use,
    /// attrs ((key, value), ...), producer).
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
                .attrs_of(row)
                .iter()
                .map(|&(k, v)| PyTuple::new(py, [text(k), text(v)]))
                .collect::<PyResult<_>>()?;
            let path = paths
                .entry(row.file)
                .or_insert_with(|| PyString::new(py, &core.facts.files[row.file as usize].path))
                .clone();
            let target_module = match target_module {
                Some(module) => module.into_any(),
                None => py.None().into_bound(py),
            };
            let fields: [Bound<'py, PyAny>; 14] = [
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
            ];
            out.push(PyTuple::new(py, fields)?);
        }
        PyList::new(py, out)
    }

    // ── resolution, for packs ──

    fn resolve_name(&mut self, module: &str, name: &str) -> Option<HitTuple> {
        let (module, name) = (self.sid(module), self.sid(name));
        let hit = self.inner.core.resolve_name(module, name);
        self.hit(hit)
    }

    /// (hit or None, attribute names left over).
    fn resolve_dotted(&mut self, dotted: &str) -> (Option<HitTuple>, Vec<String>) {
        let dotted = self.sid(dotted);
        let (hit, rest) = self.inner.core.resolve_dotted(dotted);
        let rest = rest
            .iter()
            .map(|&s| self.inner.core.it.s(s).to_string())
            .collect();
        (self.hit(hit), rest)
    }

    fn resolve_value(
        &mut self,
        module: &str,
        value: &Bound<'_, PyAny>,
    ) -> PyResult<Option<HitTuple>> {
        let module = self.sid(module);
        let value = py_value(&mut self.inner.core.it, value)?;
        let hit = self.inner.core.resolve_value(module, &value);
        Ok(self.hit(hit))
    }

    fn resolve_class(&mut self, module: &str, value: &Bound<'_, PyAny>) -> PyResult<Option<Pair>> {
        let module = self.sid(module);
        let value = py_value(&mut self.inner.core.it, value)?;
        Ok(self
            .inner
            .core
            .resolve_class(module, &value)
            .map(|e| self.pair(e)))
    }

    fn mro(&mut self, module: &str, symbol: &str) -> Vec<Pair> {
        let class = self.ent(module, symbol);
        let mro = self.inner.core.mro(class);
        mro.into_iter().map(|e| self.pair(e)).collect()
    }

    fn bases(&mut self, module: &str, symbol: &str) -> Vec<Pair> {
        let class = self.ent(module, symbol);
        let bases = self.inner.core.bases(class);
        bases.into_iter().map(|e| self.pair(e)).collect()
    }

    fn is_subclass(&mut self, module: &str, symbol: &str, qualified: &str) -> bool {
        let class = self.ent(module, symbol);
        let qualified = self.sid(qualified);
        self.inner.core.is_subclass(class, qualified)
    }

    /// (file id, def index) of the record that defines a class, or None.
    fn located(&mut self, module: &str, symbol: &str) -> Option<(u32, u32)> {
        let class = self.ent(module, symbol);
        self.inner.core.located(class)
    }

    /// `BuildContext.value_lookup` at a site of the analyzed file with this scope index.
    fn value_lookup(
        &mut self,
        scope: u32,
        value: &Bound<'_, PyAny>,
        at: i32,
        line: u32,
    ) -> PyResult<Option<(String, Option<HitTuple>, bool)>> {
        let index = self.scope_index(scope)?;
        let value = py_value(&mut self.inner.core.it, value)?;
        let link = &mut self.inner;
        let found = link.core.value_lookup(
            &link.settings.names,
            &mut link.scopes[index],
            &value,
            at,
            line,
        );
        Ok(found.map(|found| self.lookup(found)))
    }

    /// `BuildContext.chain_lookup`: the lookup and the attribute names left over.
    #[allow(clippy::type_complexity)]
    fn chain_lookup(
        &mut self,
        scope: u32,
        chain: &str,
        at: i32,
        line: u32,
    ) -> PyResult<((String, Option<HitTuple>, bool), Vec<String>)> {
        let index = self.scope_index(scope)?;
        let chain = self.sid(chain);
        let link = &mut self.inner;
        let (found, rest) = link.core.chain_lookup(
            &link.settings.names,
            &mut link.scopes[index],
            chain,
            at,
            line,
        );
        let rest = rest
            .iter()
            .map(|&s| self.inner.core.it.s(s).to_string())
            .collect();
        Ok((self.lookup(found), rest))
    }
}

/// Links facts from a JSONL file (one record per line) under a link config (JSON text).
/// `threads` caps the worker threads; 0 uses every core.
#[pyfunction]
#[pyo3(signature = (path, config, threads=0))]
fn link_jsonl(py: Python<'_>, path: &str, config: &str, threads: usize) -> PyResult<Link> {
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
    let file_of = inner
        .core
        .facts
        .files
        .iter()
        .enumerate()
        .map(|(i, f)| (f.path.to_string(), i as u32))
        .collect();
    Ok(Link { inner, file_of })
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
/// order, then third-party files as (record path, file). Facts come from the cache under (facts
/// id, oid) or are extracted in process. `extract` is the extractor's options as JSON: first_party,
/// kinds, watch_callees, watch_prefixes, watch_keys, lite_kinds. Returns the link and (cache
/// hits, files extracted).
#[pyfunction]
#[pyo3(signature = (repo, blobs, external, extract, config, cache=None, threads=0))]
#[allow(clippy::too_many_arguments)]
fn link_blobs(
    py: Python<'_>,
    repo: &str,
    blobs: Vec<(String, String, bool)>,
    external: Vec<(String, String)>,
    extract: &str,
    config: &str,
    cache: Option<String>,
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
        cache: cache.map(Into::into),
    };
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(threads)
        .build()
        .map_err(|e| PyValueError::new_err(format!("threads: {e}")))?;
    let (inner, stats) = py
        .detach(move || {
            pool.install(|| -> Result<(CoreLink, (usize, usize)), String> {
                let started = std::time::Instant::now();
                let mut it = Interner::new();
                let (facts, stats) = load(&request, &mut it)?;
                let read = started.elapsed().as_secs_f64();
                let mut link = CoreLink::build(&config, it, facts)?;
                link.timings.stages.insert(0, ("facts", read));
                Ok((link, (stats.hits, stats.extracted)))
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
    Ok((Link { inner, file_of }, stats))
}

#[pymodule]
fn hoglint_native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<Link>()?;
    module.add_class::<FactText>()?;
    module.add_function(wrap_pyfunction!(link_jsonl, module)?)?;
    module.add_function(wrap_pyfunction!(link_blobs, module)?)?;
    module.add("FACTS_ID", hoglint_facts::FACTS_ID)?;
    module.add("NATIVE_ID", hoglint_facts::NATIVE_ID)?;
    module.add("PROTOCOL", PROTOCOL)?;
    Ok(())
}
