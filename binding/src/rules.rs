//! Candidate selection and grouping for rule evaluation.

use crate::link::Link;
use crate::{ids_bytes, ids_from};
use hoglint_facts::link::Link as CoreLink;
use hoglint_facts::link::groups::{Endpoint, Role};
use hoglint_facts::link::index::kind_code;
use hoglint_facts::link::intern::{EMPTY, Sid};
use hoglint_facts::link::scope::Res;
use pyo3::exceptions::{PyKeyError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyString};
use rustc_hash::FxHashMap;

fn role_of(role: &str) -> PyResult<Role> {
    match role {
        "origin" => Ok(Role::Origin),
        "surface" => Ok(Role::Surface),
        other => Err(PyValueError::new_err(format!("no keys for role {other}"))),
    }
}

/// An endpoint table by name: `src`, `src-scope`, or `dst-<role>` with `-entity` for entity
/// granularity.
fn endpoint_table(which: &str) -> PyResult<Endpoint> {
    match which {
        "src" => Ok(Endpoint::SrcModule),
        "src-scope" => Ok(Endpoint::SrcScope),
        _ => {
            let rest = which
                .strip_prefix("dst-")
                .ok_or_else(|| PyValueError::new_err(format!("no endpoint table {which}")))?;
            let (role, entity) = match rest.strip_suffix("-entity") {
                Some(role) => (role, true),
                None => (rest, false),
            };
            Ok(Endpoint::Dst(role_of(role)?, entity))
        }
    }
}

/// Python strings of interned ids, made once per call.
struct Texts<'py, 'l> {
    py: Python<'py>,
    link: &'l CoreLink,
    made: FxHashMap<Sid, Bound<'py, PyString>>,
}

impl<'py> Texts<'py, '_> {
    fn of(&mut self, sid: Sid) -> Bound<'py, PyString> {
        let (py, it) = (self.py, &self.link.core.it);
        self.made
            .entry(sid)
            .or_insert_with(|| PyString::new(py, it.s(sid)))
            .clone()
    }
}

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
        let role = role_of(role)?;
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

    /// The ids whose key keeps, and their keys: `keep` holds one byte per key, non-zero to keep.
    #[staticmethod]
    fn keep_keys<'py>(
        py: Python<'py>,
        ids: &[u8],
        key_of: &[u8],
        keep: &[u8],
    ) -> PyResult<(Bound<'py, PyBytes>, Bound<'py, PyBytes>)> {
        let ids = ids_from(ids)?;
        let key_of = ids_from(key_of)?;
        if key_of.len() != ids.len() {
            return Err(PyValueError::new_err(format!(
                "{} keys for {} ids",
                key_of.len(),
                ids.len()
            )));
        }
        let mut kept = Vec::with_capacity(ids.len());
        let mut keys = Vec::with_capacity(ids.len());
        for (id, key) in ids.into_iter().zip(key_of) {
            match keep.get(key as usize) {
                Some(&flag) if flag != 0 => {
                    kept.push(id);
                    keys.push(key);
                }
                Some(_) => {}
                None => return Err(PyValueError::new_err(format!("no keep flag for key {key}"))),
            }
        }
        Ok((ids_bytes(py, &kept), ids_bytes(py, &keys)))
    }

    /// The site of each id as (module, scope, path): what a rule's `src` endpoint reads, with
    /// no Edge made.
    #[allow(clippy::type_complexity)]
    fn src_sites<'py>(
        &self,
        py: Python<'py>,
        ids: &[u8],
    ) -> PyResult<
        Vec<(
            Bound<'py, PyString>,
            Bound<'py, PyString>,
            Bound<'py, PyString>,
        )>,
    > {
        let link = &self.inner;
        let mut texts = Texts {
            py,
            link,
            made: FxHashMap::default(),
        };
        let mut paths: FxHashMap<u32, Bound<'py, PyString>> = FxHashMap::default();
        let mut out = Vec::new();
        for id in ids_from(ids)? {
            let row = link
                .edges
                .rows
                .get(id as usize)
                .ok_or_else(|| PyKeyError::new_err(id))?;
            let scope = &link.scopes[row.owner as usize];
            let path = paths
                .entry(row.file)
                .or_insert_with(|| {
                    PyString::new(py, &link.core.facts.files[row.file as usize].path)
                })
                .clone();
            let scope_name = scope.scope_name(row.scope, link.settings.module_scope);
            out.push((texts.of(scope.module), texts.of(scope_name), path));
        }
        Ok(out)
    }

    /// What a rule's `dst` endpoint reads of each id: (resolution, surface, target module or
    /// None, target symbol).
    #[allow(clippy::type_complexity)]
    fn dst_parts<'py>(
        &self,
        py: Python<'py>,
        ids: &[u8],
    ) -> PyResult<
        Vec<(
            &'static str,
            Bound<'py, PyString>,
            Option<Bound<'py, PyString>>,
            Bound<'py, PyString>,
        )>,
    > {
        let link = &self.inner;
        let mut texts = Texts {
            py,
            link,
            made: FxHashMap::default(),
        };
        let mut out = Vec::new();
        for id in ids_from(ids)? {
            let row = link
                .edges
                .rows
                .get(id as usize)
                .ok_or_else(|| PyKeyError::new_err(id))?;
            let (module, symbol) = match row.target {
                Some(t) => (Some(texts.of(t.m)), texts.of(t.s)),
                None => (None, texts.of(EMPTY)),
            };
            out.push((row.res.name(), texts.of(row.surface), module, symbol));
        }
        Ok(out)
    }

    /// Stable endpoint keys of `ids` in table `which` (see `endpoint_table`): (key of each id,
    /// keys known before this call, representative ids of the keys it added, distinct keys of
    /// `ids`). A key keeps its id for the life of the link.
    #[allow(clippy::type_complexity)]
    fn stable_keys<'py>(
        &mut self,
        py: Python<'py>,
        ids: &[u8],
        which: &str,
    ) -> PyResult<(
        Bound<'py, PyBytes>,
        u32,
        Bound<'py, PyBytes>,
        Bound<'py, PyBytes>,
    )> {
        let ids = ids_from(ids)?;
        let which = endpoint_table(which)?;
        let (key_of, known, added, distinct) = self.inner.stable_keys(&ids, which);
        Ok((
            ids_bytes(py, &key_of),
            known,
            ids_bytes(py, &added),
            ids_bytes(py, &distinct),
        ))
    }

    /// The ids whose key's class keeps: `class_of` holds a class per key, `keep` a byte per
    /// class.
    #[staticmethod]
    fn keep_classes<'py>(
        py: Python<'py>,
        ids: &[u8],
        key_of: &[u8],
        class_of: &[u8],
        keep: &[u8],
    ) -> PyResult<Bound<'py, PyBytes>> {
        let ids = ids_from(ids)?;
        let key_of = ids_from(key_of)?;
        let class_of = ids_from(class_of)?;
        if key_of.len() != ids.len() {
            return Err(PyValueError::new_err("one key per id"));
        }
        let kept = CoreLink::keep_classes(&ids, &key_of, &class_of, keep).ok_or_else(|| {
            PyValueError::new_err("a key without a class, or a class without a flag")
        })?;
        Ok(ids_bytes(py, &kept))
    }

    /// Edges of `kind` from one source module that a rule sees: resolution not not-applicable,
    /// context bits meeting `include` (any) and avoiding `exclude`, in graph order.
    fn ids_seen_from<'py>(
        &mut self,
        py: Python<'py>,
        kind: &str,
        module: &str,
        include: u8,
        exclude: u8,
    ) -> Bound<'py, PyBytes> {
        let Some(code) = kind_code(kind) else {
            return ids_bytes(py, &[]);
        };
        let module = self.sid(module);
        let link = &mut self.inner;
        let ids = link
            .index
            .from_module(&link.edges.rows, &link.scopes, code, module)
            .to_vec();
        let rows = &link.edges.rows;
        let seen: Vec<u32> = ids
            .into_iter()
            .filter(|&id| {
                let row = &rows[id as usize];
                row.res != Res::NotApplicable
                    && (include == 0 || row.ctx & include != 0)
                    && row.ctx & exclude == 0
            })
            .collect();
        ids_bytes(py, &seen)
    }
}
