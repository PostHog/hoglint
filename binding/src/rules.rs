//! Candidate selection and grouping for rule evaluation.

use crate::link::Link;
use crate::{ids_bytes, ids_from};
use hoglint_facts::link::Link as CoreLink;
use hoglint_facts::link::groups::{End, Role, Stable};
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

/// An end by name: `src`, or `dst-<role>`.
fn end_of(end: &str) -> PyResult<End> {
    match end {
        "src" => Ok(End::Src),
        _ => {
            let role = end
                .strip_prefix("dst-")
                .ok_or_else(|| PyValueError::new_err(format!("no end {end}")))?;
            Ok(End::Dst(role_of(role)?))
        }
    }
}

/// A stable-key lookup as Python reads it: (key of each id, keys known before the call,
/// representative ids of the keys it added, distinct keys of the ids).
type StableBytes<'py> = (
    Bound<'py, PyBytes>,
    u32,
    Bound<'py, PyBytes>,
    Bound<'py, PyBytes>,
);

fn stable_bytes<'py>(py: Python<'py>, stable: &Stable) -> StableBytes<'py> {
    (
        ids_bytes(py, &stable.key_of),
        stable.known,
        ids_bytes(py, &stable.added),
        ids_bytes(py, &stable.distinct),
    )
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
        let codes: Vec<Sid> = kinds
            .iter()
            .filter_map(|kind| self.inner.kind_sid(kind))
            .collect();
        if codes.is_empty() && !kinds.is_empty() {
            return ids_bytes(py, &[]);
        }
        ids_bytes(py, &self.inner.candidates(&codes, include, exclude))
    }

    /// `ids` split into (those a group may speak for, the irregular ones): an edge with several
    /// targets or with attrs that override its endpoints' labels is judged on its own.
    fn split_irregular<'py>(
        &self,
        py: Python<'py>,
        ids: &[u8],
    ) -> PyResult<(Bound<'py, PyBytes>, Bound<'py, PyBytes>)> {
        let (regular, irregular) = self.inner.split_irregular(&ids_from(ids)?);
        Ok((ids_bytes(py, &regular), ids_bytes(py, &irregular)))
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
            if id as usize >= link.edges.len() {
                return Err(PyKeyError::new_err(id));
            }
            let path = paths
                .entry(link.file_key(id))
                .or_insert_with(|| PyString::new(py, link.path_of_edge(id)))
                .clone();
            let (module, scope) = link.site_of(id);
            out.push((texts.of(module), texts.of(scope), path));
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
        let edges = &link.edges;
        for id in ids_from(ids)? {
            if id as usize >= edges.len() {
                return Err(PyKeyError::new_err(id));
            }
            let (module, symbol) = match edges.target_of(id) {
                Some(t) => (Some(texts.of(t.m)), texts.of(t.s)),
                None => (None, texts.of(EMPTY)),
            };
            out.push((
                edges.res_of(id).name(),
                texts.of(edges.surface_of(id)),
                module,
                symbol,
            ));
        }
        Ok(out)
    }

    /// The stable basis of each id at `end` ("src" or "dst-<role>"): what its module-level
    /// label values depend on. A basis keeps its id for the life of the link.
    fn end_bases<'py>(
        &mut self,
        py: Python<'py>,
        ids: &[u8],
        end: &str,
    ) -> PyResult<StableBytes<'py>> {
        let ids = ids_from(ids)?;
        let end = end_of(end)?;
        Ok(stable_bytes(py, &self.inner.end_bases(&ids, end)))
    }

    /// The stable entity key of each id at `end`, given the basis of each (`end_bases`). With
    /// `mask`, one byte per basis, a basis whose byte is zero keeps no entity. Also returns the
    /// basis of each key the call added.
    #[allow(clippy::type_complexity)]
    fn end_entities<'py>(
        &mut self,
        py: Python<'py>,
        ids: &[u8],
        end: &str,
        basis_of: &[u8],
        mask: Option<&[u8]>,
    ) -> PyResult<(StableBytes<'py>, Bound<'py, PyBytes>)> {
        let ids = ids_from(ids)?;
        let end = end_of(end)?;
        let basis_of = ids_from(basis_of)?;
        let stable = self
            .inner
            .end_entities(&ids, end, &basis_of, mask)
            .ok_or_else(|| PyValueError::new_err("a basis per id, and a mask byte per basis"))?;
        let added: Vec<u32> = (stable.known..stable.known + stable.added.len() as u32).collect();
        let bases = self
            .inner
            .entity_bases(end, &added)
            .ok_or_else(|| PyValueError::new_err("an entity key the table does not hold"))?;
        Ok((stable_bytes(py, &stable), ids_bytes(py, &bases)))
    }

    /// Positions into `ids` in the order a rule judges its edges: kind by kind as `kinds` lists
    /// them, then source module by source module in the order each module's first edge of that
    /// kind appears, then graph order.
    fn rule_order<'py>(
        &mut self,
        py: Python<'py>,
        ids: &[u8],
        kinds: Vec<String>,
    ) -> PyResult<Bound<'py, PyBytes>> {
        let ids = ids_from(ids)?;
        let codes: Vec<Sid> = kinds
            .iter()
            .filter_map(|kind| self.inner.kind_sid(kind))
            .collect();
        let order = self
            .inner
            .rule_order(&ids, &codes)
            .ok_or_else(|| PyValueError::new_err("an edge whose kind is not among the kinds"))?;
        Ok(ids_bytes(py, &order))
    }

    /// The label class of each key in `keys` over code columns, one u32 code per key in each:
    /// (class of each key in `size` slots, u32::MAX for a key not in `keys`; each class's
    /// codes, column by column).
    #[staticmethod]
    fn key_classes<'py>(
        py: Python<'py>,
        keys: &[u8],
        size: usize,
        columns: Vec<Bound<'py, PyBytes>>,
    ) -> PyResult<(Bound<'py, PyBytes>, Bound<'py, PyBytes>)> {
        let keys = ids_from(keys)?;
        let columns: Vec<Vec<u32>> = columns
            .iter()
            .map(|column| ids_from(column.as_bytes()))
            .collect::<PyResult<_>>()?;
        let (class_of, rows) = CoreLink::key_classes(&keys, size, &columns)
            .ok_or_else(|| PyValueError::new_err("a key past a column's end or `size`"))?;
        Ok((ids_bytes(py, &class_of), ids_bytes(py, &rows)))
    }

    /// The keys in `keys` whose code in `codes` is u32::MAX: the values not read yet.
    #[staticmethod]
    fn unread<'py>(py: Python<'py>, keys: &[u8], codes: &[u8]) -> PyResult<Bound<'py, PyBytes>> {
        let out = CoreLink::unread(&ids_from(keys)?, &ids_from(codes)?)
            .ok_or_else(|| PyValueError::new_err("a key past the codes' end"))?;
        Ok(ids_bytes(py, &out))
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
        let Some(kind) = self.inner.kind_sid(kind) else {
            return ids_bytes(py, &[]);
        };
        let module = self.sid(module);
        let ids = self.inner.ids_from(kind, module);
        let edges = &self.inner.edges;
        let seen: Vec<u32> = ids
            .into_iter()
            .filter(|&id| {
                let ctx = edges.seen_ctx(id);
                edges.res_of(id) != Res::NotApplicable
                    && (include == 0 || ctx & include != 0)
                    && ctx & exclude == 0
            })
            .collect();
        ids_bytes(py, &seen)
    }
}
