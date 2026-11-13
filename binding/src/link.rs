//! Building a link, the snapshot's records, and edges as tuples.

use crate::blobs::{Blob, BlobRequest, configs, load};
use crate::{HitTuple, Pair, ids_bytes, ids_from};
use hoglint_facts::link::facts::decode_lines;
use hoglint_facts::link::intern::{Interner, Sid};
use hoglint_facts::link::packs::Refinements;
use hoglint_facts::link::packs::django::shapes::Vocabulary;
use hoglint_facts::link::packs::refined::{Field, RefinedFields};
use hoglint_facts::link::produce::NO_FILE;
use hoglint_facts::link::resolve::{Binding, Ent, HitId};
use hoglint_facts::link::scope::{Lookup, Res};
use hoglint_facts::link::surface::sorted_names;
use hoglint_facts::link::{ClassFact, Link as CoreLink, LinkConfig, NewPackRow};
use pyo3::exceptions::{PyKeyError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyList, PyString, PyTuple};
use rustc_hash::FxHashMap;
use std::collections::HashMap;

/// A linked snapshot, (cache hits, files extracted), each record's fact cache key, and the
/// working tree files that changed during the run.
type Loaded = (CoreLink, (usize, usize), Vec<Option<String>>, Vec<String>);

/// The first of the fields a batch of refinements would set apart, for Python to word:
/// (how many fields conflict, the field, the edge id, the producer that set it or None for an
/// attr the edge has, the value there).
type ConflictRow = (usize, String, u32, Option<String>, String);

/// Refinements a link producer judged and has not applied, and the files it could not read.
/// Its rows stay in Rust: Python holds one object per batch.
#[pyclass(module = "hoglint_native", unsendable)]
pub(crate) struct Staged {
    pub(crate) refinements: Refinements,
    pub(crate) failed: Vec<u32>,
}

#[pymethods]
impl Staged {
    fn __len__(&self) -> usize {
        self.refinements.len()
    }

    /// The file ids whose uses the producer could not read, in the producer's order.
    fn failed(&self) -> Vec<u32> {
        self.failed.clone()
    }
}

/// One linked snapshot: facts, module index, resolver state and the core edges.
#[pyclass(module = "hoglint_native", unsendable)]
pub(crate) struct Link {
    pub(crate) inner: CoreLink,
    pub(crate) file_of: FxHashMap<String, u32>,
    /// The fact cache key of each record (`BlobStats::keys`); None for JSONL input.
    pub(crate) keys: Vec<Option<String>>,
    /// Working tree files that changed while the run read them (`BlobStats::drifted`).
    pub(crate) drifted: Vec<String>,
    /// `from` import rows by imported name, built on the first `import_rows_named`.
    pub(crate) import_names: Option<FxHashMap<Sid, Vec<(u32, u32)>>>,
    /// The patterns of the registered mention words (`words`), by id; None for no words.
    pub(crate) patterns: Vec<Option<regex::Regex>>,
    /// Per pattern, whether it matched each interned string it met (`mentions`).
    pub(crate) matched: Vec<FxHashMap<Sid, bool>>,
    /// The worker threads the link was built with (0: every core); rule evaluation takes them.
    pub(crate) threads: usize,
    /// What each pack producer set on each edge it refined, for the sink's conflict check.
    pub(crate) refined: RefinedFields,
}

impl Link {
    /// One key of a record as JSON text. The path and the status come from the link's own
    /// record, so the many reads of a file's status read no record text.
    pub(crate) fn raw_value(&self, file: usize, key: &str) -> PyResult<Option<Vec<u8>>> {
        let facts = &self.inner.core.facts;
        let record = &facts.files[file];
        if key == "path" {
            return Ok(serde_json::to_vec(&*record.path).ok());
        }
        if key == "status" && record.keys.iter().any(|(name, _)| &**name == "status") {
            return Ok(serde_json::to_vec(&*record.status).ok());
        }
        facts.raw(file, key).map_err(PyValueError::new_err)
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

    /// Raises once the link released its fact rows (`release_facts`): a resolution or a row
    /// query then would answer from nothing.
    pub(crate) fn rows(&self) -> PyResult<()> {
        if self.inner.core.facts.released {
            return Err(PyValueError::new_err(
                "the link released its fact rows when the pack phase ended",
            ));
        }
        Ok(())
    }

    /// Raises when the rows of `kind` were dropped: no pack declared it (`release_kinds`).
    pub(crate) fn rows_of(&self, kind: &str) -> PyResult<()> {
        self.rows()?;
        if self.inner.core.facts.dropped.contains(&kind) {
            return Err(PyValueError::new_err(format!(
                "the link dropped its {kind} rows: no pack declares the kind"
            )));
        }
        Ok(())
    }

    /// Applies one producer's batch through the sink's bookkeeping (`RefinedFields`): all of
    /// it, or none and the first conflict.
    fn apply(&mut self, producer: &str, batch: Refinements) -> Option<ConflictRow> {
        let producer = self.sid(producer);
        let conflicts = self.refined.apply(&mut self.inner.edges, producer, batch);
        let first = conflicts.first()?;
        let it = &self.inner.core.it;
        let field = match first.field {
            Field::Shape => "shape".to_string(),
            Field::ShapeDetail => "shape_detail".to_string(),
            Field::Attr(key) => format!("attr:{}", it.s(key)),
        };
        let owner = first.owner.map(|owner| it.s(owner).to_string());
        Some((
            conflicts.len(),
            field,
            first.id,
            owner,
            it.s(first.value).to_string(),
        ))
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

    /// Drops the rows of the query kinds not in `kept`, the kinds the packs declare: right after
    /// the link, only packs read them. False when the rows could not be dropped.
    fn release_kinds(&mut self, kept: Vec<String>) -> bool {
        self.inner.release_kinds(&kept)
    }

    /// Drops the records' fact rows once the pack phase ends (D23): rules read edges, symbols
    /// and scopes, never rows. Resolutions and row queries raise after it. False when the rows
    /// could not be dropped because another holder shares them.
    fn release_facts(&mut self) -> bool {
        self.import_names = None;
        self.inner.release_facts()
    }

    /// Writes what each analyzed file's edges read from other files and every repo file's
    /// interface to `path` (`Link::write_traces`), when the link was built with `trace`.
    fn write_traces(&mut self, path: &str) -> PyResult<()> {
        self.inner
            .write_traces(std::path::Path::new(path))
            .map_err(PyValueError::new_err)
    }

    /// Every edge: core edges, then pack edges.
    fn edge_count(&self) -> usize {
        self.inner.edges.len()
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

    /// The qualified name and the flags of every def of the analyzed file with this scope
    /// index, by def index: what `FileScope.qualnames`, `in_function` and `is_function_scope`
    /// read from the defs. Flags: bit 0, the def sits inside a function body; bit 1, it is a
    /// class.
    fn def_scopes<'py>(
        &self,
        py: Python<'py>,
        scope: u32,
    ) -> PyResult<(Vec<String>, Bound<'py, PyBytes>)> {
        self.rows()?;
        let found = &self.inner.scopes[self.scope_index(scope)?];
        let defs = &self.inner.core.facts.files[found.file as usize].defs;
        let it = &self.inner.core.it;
        let names = found
            .qualnames
            .iter()
            .map(|&s| it.s(s).to_string())
            .collect();
        let flags: Vec<u8> = found
            .in_function
            .iter()
            .zip(defs)
            .map(|(&inside, def)| u8::from(inside) | (u8::from(def.is_class()) << 1))
            .collect();
        Ok((names, PyBytes::new(py, &flags)))
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

    /// Each record's status in fact order, None for a record with no status: what `raw`
    /// gives for "status", for every record in one call.
    fn statuses(&self) -> Vec<Option<String>> {
        self.inner
            .core
            .facts
            .files
            .iter()
            .map(|record| {
                record
                    .keys
                    .iter()
                    .any(|(name, _)| &**name == "status")
                    .then(|| record.status.to_string())
            })
            .collect()
    }

    /// Whether a record has rows under `key`, read off its key ranges with no JSON text.
    fn has_rows(&self, file: usize, key: &str) -> PyResult<bool> {
        if file >= self.inner.core.facts.files.len() {
            return Err(PyKeyError::new_err(file));
        }
        Ok(self.inner.core.facts.has_rows(file, key))
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
        self.rows()?;
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

    /// What each export module hands out by import (`Graph.hand_outs`): (module, handed names,
    /// kept names, whether its `__all__` cannot be read), in module name order.
    #[allow(clippy::type_complexity)]
    fn hand_outs(&self) -> Vec<(&str, Vec<&str>, Vec<&str>, bool)> {
        let it = &self.inner.core.it;
        let mut out: Vec<(&str, Vec<&str>, Vec<&str>, bool)> = self
            .inner
            .hand_outs
            .iter()
            .map(|(&module, found)| {
                (
                    it.s(module),
                    sorted_names(it, &found.handed),
                    sorted_names(it, &found.kept),
                    found.unread,
                )
            })
            .collect();
        out.sort();
        out
    }

    /// Applies one producer's refinements to edges (D22): for each edge id, the shape and shape
    /// detail to set (None keeps the edge's) and attrs to add after the edge's own. Applies none of
    /// them when a field conflicts (`RefinedFields`), and returns the first conflict.
    #[allow(clippy::type_complexity)]
    fn refine(
        &mut self,
        producer: &str,
        ids: &[u8],
        shapes: Vec<Option<String>>,
        details: Vec<Option<String>>,
        attrs: Vec<Vec<(String, String)>>,
    ) -> PyResult<Option<ConflictRow>> {
        let ids = ids_from(ids)?;
        if shapes.len() != ids.len() || details.len() != ids.len() || attrs.len() != ids.len() {
            return Err(PyValueError::new_err(
                "one shape, detail and attr list per edge",
            ));
        }
        let mut batch = Refinements::default();
        for (((id, shape), detail), pairs) in ids.into_iter().zip(shapes).zip(details).zip(attrs) {
            if id as usize >= self.inner.edges.len() {
                return Err(PyKeyError::new_err(id));
            }
            let shape = shape.map(|s| self.sid(&s));
            let detail = detail.map(|s| self.sid(&s));
            let pairs: Vec<(Sid, Sid)> = pairs
                .iter()
                .map(|(k, v)| (self.sid(k), self.sid(v)))
                .collect();
            batch.add(id, shape, detail, pairs);
        }
        Ok(self.apply(producer, batch))
    }

    /// Judges the `django.uses@1` refinements of the analyzed files `files` (file ids, in the
    /// producer's order) onto `models` in the link (hoglint/packs/django/uses.py), with no
    /// Python object per use, reading the shape code's names from `vocabulary` (the tables of
    /// hoglint/packs/django/shapes.py by name). Nothing is applied: `refine_staged` applies them.
    fn django_uses(
        &mut self,
        files: Vec<u32>,
        models: Vec<Pair>,
        mut vocabulary: HashMap<String, Vec<String>>,
    ) -> PyResult<Staged> {
        self.rows()?;
        let mut names = |name: &str| {
            vocabulary
                .remove(name)
                .ok_or_else(|| PyValueError::new_err(format!("the vocabulary has no {name}")))
        };
        let vocabulary = Vocabulary {
            managers: names("managers")?,
            scalar_terminals: names("scalar_terminals")?,
            single_terminals: names("single_terminals")?,
            meta_managers: names("meta_managers")?,
            meta_class_attrs: names("meta_class_attrs")?,
            write_methods: names("write_methods")?,
            subquery_calls: names("subquery_calls")?,
            value_methods: names("value_methods")?,
            sender_keywords: names("sender_keywords")?,
            annotation_terminals: names("annotation_terminals")?,
            local_assign_steps: names("local_assign_steps")?,
            union_steps: names("union_steps")?,
            alias_pass_steps: names("alias_pass_steps")?,
            alias_assign_steps: names("alias_assign_steps")?,
        };
        let models: Vec<Ent> = models.iter().map(|(m, s)| self.ent(m, s)).collect();
        let uses = self.inner.django_uses(&files, &models, &vocabulary);
        Ok(Staged {
            refinements: uses.refinements,
            failed: uses.failed,
        })
    }

    /// Refinements a link producer judged, as (edge ids, shapes, details, attrs): for the
    /// proofs that hold them equal to the Python producer's.
    #[allow(clippy::type_complexity)]
    fn staged_columns<'py>(
        &self,
        py: Python<'py>,
        staged: &Staged,
    ) -> (
        Bound<'py, PyBytes>,
        Vec<Option<&str>>,
        Vec<Option<&str>>,
        Vec<Vec<(&str, &str)>>,
    ) {
        let it = &self.inner.core.it;
        let made = &staged.refinements;
        let text = |sid: &Option<Sid>| sid.map(|sid| it.s(sid));
        (
            ids_bytes(py, &made.ids),
            made.shapes.iter().map(text).collect(),
            made.details.iter().map(text).collect(),
            made.attrs
                .iter()
                .map(|pairs| pairs.iter().map(|&(k, v)| (it.s(k), it.s(v))).collect())
                .collect(),
        )
    }

    /// Applies the refinements a link producer judged, as `refine` does. `staged` is empty
    /// afterwards.
    fn refine_staged(&mut self, producer: &str, staged: &mut Staged) -> Option<ConflictRow> {
        let batch = std::mem::take(&mut staged.refinements);
        self.apply(producer, batch)
    }

    /// Records the facts packs wrote for some symbols: each symbol's (key, value) pairs replace
    /// whatever was written for it before, as `NativeSymbols.update` does.
    fn add_symbol_facts(&mut self, facts: Vec<(String, Vec<(String, String)>)>) {
        for (name, pairs) in facts {
            let name = self.sid(&name);
            let pairs = pairs
                .iter()
                .map(|(key, value)| (self.sid(key), self.sid(value)))
                .collect();
            self.inner.symbols.added.insert(name, pairs);
        }
    }

    /// Edge kinds in the order their first edge appears: core kinds, then pack kinds.
    fn kind_order(&self) -> Vec<String> {
        let it = &self.inner.core.it;
        self.inner
            .kind_order()
            .into_iter()
            .map(|kind| it.s(kind).to_string())
            .collect()
    }

    /// The inherit (`owner_attr` "class") or decorate ("decorated") facts of every edge, core
    /// and pack, for SymbolFacts: [(owner symbol, surface, names, frozen, called, resolved)] in
    /// graph order.
    fn class_facts(&mut self, kind: &str, owner_attr: &str) -> Vec<ClassFact> {
        match self.inner.kind_sid(kind) {
            Some(kind) => self.inner.class_facts(kind, owner_attr),
            None => Vec::new(),
        }
    }

    /// The number of core edges: pack edges come after them.
    fn core_count(&self) -> usize {
        self.inner.edges.rows.len()
    }

    /// Adds pack edges after every edge the link holds (D22), one list per field with a value
    /// per edge: kinds, producers, paths, modules, scopes, lines, columns, surfaces, targets
    /// ((module, symbol) pairs), resolutions, context bits, via pairs, uses, shapes, shape
    /// details, attrs. Returns the first new edge id.
    #[allow(clippy::too_many_arguments)]
    fn add_edges(
        &mut self,
        kinds: Vec<String>,
        producers: Vec<String>,
        sites: Vec<(String, String, String, u32, u32)>,
        surfaces: Vec<String>,
        targets: Vec<Vec<Pair>>,
        resolutions: Vec<String>,
        contexts: Vec<u8>,
        via: Vec<Vec<Pair>>,
        uses: Vec<String>,
        shapes: Vec<(String, String)>,
        attrs: Vec<Vec<(String, String)>>,
    ) -> PyResult<usize> {
        let count = kinds.len();
        let lengths = [
            producers.len(),
            sites.len(),
            surfaces.len(),
            targets.len(),
            resolutions.len(),
            contexts.len(),
            via.len(),
            uses.len(),
            shapes.len(),
            attrs.len(),
        ];
        if lengths.iter().any(|&length| length != count) {
            return Err(PyValueError::new_err("one value per edge in every field"));
        }
        let first = self.inner.edges.len();
        let mut rows = Vec::with_capacity(count);
        for at in 0..count {
            let (path, module, scope, line, col) = &sites[at];
            let res = Res::of_name(&resolutions[at]).ok_or_else(|| {
                PyValueError::new_err(format!("no resolution {}", resolutions[at]))
            })?;
            let ents = |link: &mut Link, pairs: &[Pair]| -> Vec<Ent> {
                pairs.iter().map(|(m, s)| link.ent(m, s)).collect()
            };
            let row = NewPackRow {
                kind: self.sid(&kinds[at]),
                producer: self.sid(&producers[at]),
                path: self.sid(path),
                file: self.file_of.get(path.as_str()).copied().unwrap_or(NO_FILE),
                module: self.sid(module),
                scope: self.sid(scope),
                line: *line,
                col: *col,
                surface: self.sid(&surfaces[at]),
                targets: ents(self, &targets[at]),
                res,
                ctx: contexts[at],
                via: ents(self, &via[at]),
                use_: self.sid(&uses[at]),
                shape: self.sid(&shapes[at].0),
                shape_detail: self.sid(&shapes[at].1),
                attrs: attrs[at]
                    .iter()
                    .map(|(k, v)| (self.sid(k), self.sid(v)))
                    .collect(),
            };
            rows.push(row);
        }
        self.inner.add_pack(rows);
        Ok(first)
    }

    /// Edges as tuples: (kind, path, module, scope, line, column, surface, targets ((module,
    /// symbol), ...), resolution, context bits, via ((module, symbol), ...), use, attrs ((key,
    /// value), ...), shape, shape detail, producer), with what refinements set.
    fn edges<'py>(&self, py: Python<'py>, ids: &[u8]) -> PyResult<Bound<'py, PyList>> {
        let ids = ids_from(ids)?;
        let link = &self.inner;
        let edges = &link.edges;
        let it = &link.core.it;
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
            if id as usize >= edges.len() {
                return Err(PyKeyError::new_err(id));
            }
            let pairs = |ents: &[Ent], text: &mut dyn FnMut(Sid) -> Bound<'py, PyString>| {
                let items: Vec<Bound<'py, PyTuple>> = ents
                    .iter()
                    .map(|e| PyTuple::new(py, [text(e.m), text(e.s)]))
                    .collect::<PyResult<_>>()?;
                PyTuple::new(py, items)
            };
            let targets = pairs(edges.targets_of(id), &mut text)?;
            let via = pairs(edges.via_of_id(id), &mut text)?;
            let attrs: Vec<Bound<'py, PyTuple>> = edges
                .attrs_with(id)
                .map(|&(k, v)| PyTuple::new(py, [text(k), text(v)]))
                .collect::<PyResult<_>>()?;
            let (shape, shape_detail) = edges.shape_of(id);
            let path = paths
                .entry(link.file_key(id))
                .or_insert_with(|| PyString::new(py, link.path_of_edge(id)))
                .clone();
            let (module, scope) = link.site_of(id);
            let (line, col) = link.line_col(id);
            let fields: [Bound<'py, PyAny>; 16] = [
                text(edges.kind_of(id)).into_any(),
                path.into_any(),
                text(module).into_any(),
                text(scope).into_any(),
                line.into_pyobject(py)?.into_any(),
                col.into_pyobject(py)?.into_any(),
                text(edges.surface_of(id)).into_any(),
                targets.into_any(),
                PyString::new(py, edges.res_of(id).name()).into_any(),
                edges.ctx_of(id).into_pyobject(py)?.into_any(),
                via.into_any(),
                text(edges.use_of(id)).into_any(),
                PyTuple::new(py, attrs)?.into_any(),
                text(shape).into_any(),
                text(shape_detail).into_any(),
                text(edges.producer_of(id)).into_any(),
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
        import_names: None,
        patterns: Vec::new(),
        matched: Vec::new(),
        threads,
        refined: RefinedFields::default(),
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
            import_names: None,
            patterns: Vec::new(),
            matched: Vec::new(),
            threads,
            refined: RefinedFields::default(),
        },
        stats,
    ))
}
