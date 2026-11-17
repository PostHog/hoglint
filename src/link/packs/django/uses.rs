//! `django.uses@1`: the Django shape of every use of a model, judged in the link with no Python
//! object per use.
//!
//! Core makes one `reference` edge per use site. This producer takes those whose one target is
//! a model, gives each its Django shape, and follows the locals a use flows into (`qs =
//! M.objects.filter(); qs.update()`), which only the file's `derived` rows show. A file whose
//! uses the shape code cannot read fails whole: it adds no refinement, and the caller words its
//! failure (hoglint/packs/django/native_uses.py).

use super::shapes::{Shape, Unreadable, UsePosition, Vocabulary};
use crate::link::Link;
use crate::link::intern::{EMPTY, Sid};
use crate::link::packs::Refinements;
use crate::link::resolve::Ent;
use rustc_hash::{FxHashMap, FxHashSet};
use serde::de::IgnoredAny;

const CORE_REFERENCE_PRODUCER: &str = "core-reference@1";
const FLOW_SHAPE: &str = "flow_shape";
const INSTANCE_WRITE: &str = "instance_write";

/// One `derived` row: (scope, header, line, column, flags, root, chain, up, origin).
type Derived = (
    i64,
    IgnoredAny,
    i64,
    i64,
    IgnoredAny,
    String,
    String,
    String,
    i64,
);

/// What the producer made: its refinements in the order they apply, and the files that failed.
#[derive(Default)]
pub struct Uses {
    pub refinements: Refinements,
    pub failed: Vec<u32>,
}

/// A use position, the local it is assigned to, and its shape.
struct Shaped {
    position: UsePosition,
    local: Option<String>,
    shape: Shape,
}

/// A later use of a local: its method names, and the local it is assigned to in turn.
struct Later {
    methods: Vec<String>,
    local: Option<String>,
}

/// Each (chain, up) pair of a use parses and classifies once, and each pair of a later use
/// parses once. A pair that cannot be read stays unreadable.
#[derive(Default)]
struct Memo {
    shaped_at: FxHashMap<(Sid, Sid), Result<usize, Unreadable>>,
    shaped: Vec<Shaped>,
    texts: FxHashMap<Box<str>, u32>,
    later_at: FxHashMap<(u32, u32), usize>,
    later: Vec<Later>,
}

impl Memo {
    fn shaped(
        &mut self,
        vocabulary: &Vocabulary,
        chain: &str,
        up: &str,
        key: (Sid, Sid),
    ) -> Result<usize, Unreadable> {
        if let Some(&found) = self.shaped_at.get(&key) {
            return found;
        }
        let position = UsePosition::of(chain, up);
        let found = vocabulary.classify(&position).map(|shape| {
            let local = vocabulary.local_target(&position);
            self.shaped.push(Shaped {
                position,
                local,
                shape,
            });
            self.shaped.len() - 1
        });
        self.shaped_at.insert(key, found);
        found
    }

    fn text(&mut self, text: &str) -> u32 {
        if let Some(&id) = self.texts.get(text) {
            return id;
        }
        let id = self.texts.len() as u32;
        self.texts.insert(text.into(), id);
        id
    }

    fn later(&mut self, vocabulary: &Vocabulary, chain: &str, up: &str) -> usize {
        let key = (self.text(chain), self.text(up));
        if let Some(&at) = self.later_at.get(&key) {
            return at;
        }
        let position = UsePosition::of(chain, up);
        let methods = position
            .methods()
            .0
            .into_iter()
            .map(str::to_string)
            .collect();
        self.later.push(Later {
            methods,
            local: vocabulary.local_target(&position),
        });
        self.later_at.insert(key, self.later.len() - 1);
        self.later.len() - 1
    }
}

/// One assignment of a local: (line, column, the index of the use it holds).
type Assigned = (i64, i64, usize);

/// Which use a local holds at a point in its scope: a reassignment starts a new source. Each
/// local keeps its assignments sorted as (line, column, use index), so a use finds its source
/// with one predecessor search.
#[derive(Default)]
struct LocalSources {
    assigned: FxHashMap<Sid, FxHashMap<String, Vec<Assigned>>>,
}

impl LocalSources {
    fn assign(&mut self, scope: Sid, local: &str, line: i64, column: i64, index: usize) {
        let rows = self
            .assigned
            .entry(scope)
            .or_default()
            .entry(local.to_string())
            .or_default();
        let entry = (line, column, index);
        let at = rows.partition_point(|row| *row <= entry);
        rows.insert(at, entry);
    }

    /// The last assignment before the use. A use above every assignment (a loop) takes the
    /// first one.
    fn held(&self, scope: Sid, local: &str, line: i64, column: i64) -> Option<usize> {
        let rows = self.assigned.get(&scope)?.get(local)?;
        let first = rows.first()?;
        let before = rows.partition_point(|row| (row.0, row.1) < (line, column));
        Some(if before > 0 {
            rows[before - 1].2
        } else {
            first.2
        })
    }
}

/// One use of a file once judged: its edge, its shape in the memo, its flow shape, and the
/// instance writes called on its value.
type Judged = (u32, usize, Option<String>, Option<String>);

struct Judge<'l> {
    link: &'l Link,
    vocabulary: &'l Vocabulary,
    chain_key: Option<Sid>,
    up_key: Option<Sid>,
    memo: Memo,
}

impl<'l> Judge<'l> {
    fn new(link: &'l Link, vocabulary: &'l Vocabulary) -> Judge<'l> {
        let it = &link.core.it;
        Judge {
            link,
            vocabulary,
            chain_key: it.get("chain"),
            up_key: it.get("up"),
            memo: Memo::default(),
        }
    }

    /// The first value of an edge's attr, or "" without one, as `Edge.attr` reads it.
    fn attr(&self, id: u32, key: Option<Sid>) -> Sid {
        key.and_then(|key| {
            self.link
                .edges
                .attrs_with(id)
                .find(|(name, _)| *name == key)
                .map(|&(_, value)| value)
        })
        .unwrap_or(EMPTY)
    }

    /// The analyzed file's scope name of a def index, -1 being the module: `scope_name`, which
    /// indexes a Python list, so a negative index counts from the end.
    fn scope_name(&self, file: u32, at: i64) -> Result<Sid, Unreadable> {
        if at == -1 {
            return Ok(self.link.settings.module_scope);
        }
        let scope = self.link.scope_of_file.get(&file).ok_or(Unreadable)?;
        let names = &self.link.scopes[*scope as usize].qualnames;
        let index = if at < 0 { at + names.len() as i64 } else { at };
        usize::try_from(index)
            .ok()
            .and_then(|index| names.get(index))
            .copied()
            .ok_or(Unreadable)
    }

    /// One file's uses (`rows`, edge ids in batch order) once every shape of the file is
    /// known.
    fn file(&mut self, file: u32, rows: &[u32]) -> Result<Vec<Judged>, Unreadable> {
        let link = self.link;
        let it = &link.core.it;
        let mut positions = Vec::with_capacity(rows.len());
        for &id in rows {
            let key = (self.attr(id, self.chain_key), self.attr(id, self.up_key));
            let found = self
                .memo
                .shaped(self.vocabulary, it.s(key.0), it.s(key.1), key)?;
            positions.push(found);
        }
        let mut sources = LocalSources::default();
        let mut locals: FxHashSet<String> = FxHashSet::default();
        for (index, (&id, &at)) in rows.iter().zip(&positions).enumerate() {
            if let Some(local) = &self.memo.shaped[at].local {
                let (line, column) = link.line_col(id);
                let scope = link.site_of(id).1;
                sources.assign(scope, local, line.into(), column.into(), index);
                locals.insert(local.clone());
            }
        }
        let mut later: FxHashMap<usize, Vec<usize>> = FxHashMap::default();
        if !locals.is_empty() {
            let raw = link
                .core
                .facts
                .raw(file as usize, "derived")
                .map_err(|_| Unreadable)?;
            let mut derived: Vec<Derived> = match raw {
                Some(raw) => serde_json::from_slice(&raw).map_err(|_| Unreadable)?,
                None => Vec::new(),
            };
            derived.sort_by_key(|row| (row.2, row.3));
            for (at_scope, _, line, column, _, root, chain, up, origin) in &derived {
                if !locals.contains(root) {
                    continue;
                }
                let origin = self.scope_name(file, *origin)?;
                let Some(index) = sources.held(origin, root, *line, *column) else {
                    continue;
                };
                let found = self.memo.later(self.vocabulary, chain, up);
                later.entry(index).or_default().push(found);
                if let Some(local) = self.memo.later[found].local.clone() {
                    sources.assign(
                        self.scope_name(file, *at_scope)?,
                        &local,
                        *line,
                        *column,
                        index,
                    );
                    locals.insert(local);
                }
            }
        }
        let mut out = Vec::with_capacity(rows.len());
        for (index, (&id, &at)) in rows.iter().zip(&positions).enumerate() {
            let shaped = &self.memo.shaped[at];
            let methods: Vec<&[String]> = later.get(&index).map_or_else(Vec::new, |found| {
                found
                    .iter()
                    .map(|&at| self.memo.later[at].methods.as_slice())
                    .collect()
            });
            let flow = if methods.is_empty() {
                None
            } else {
                self.vocabulary
                    .refine(&shaped.position, &methods)
                    .filter(|refined| *refined != shaped.shape)
                    .map(|refined| refined.text())
            };
            let written = self.vocabulary.instance_write(&shaped.position, &methods);
            out.push((id, at, flow, written));
        }
        Ok(out)
    }
}

impl Link {
    /// The `django.uses@1` refinements of the analyzed files `files` (file ids, in the
    /// producer's order) onto `models`, and the files that failed, with the shape code's names
    /// from `vocabulary`. Nothing is applied.
    pub fn django_uses(&mut self, files: &[u32], models: &[Ent], vocabulary: &Vocabulary) -> Uses {
        let Some(kind) = self.kind_sid("reference") else {
            return Uses::default();
        };
        let ids = self.index.to_targets(&self.edges, kind, models);
        let models: FxHashSet<Ent> = models.iter().copied().collect();
        let core_reference = self.core.it.get(CORE_REFERENCE_PRODUCER);
        let mut by_file: FxHashMap<u32, Vec<u32>> = FxHashMap::default();
        for id in ids {
            if Some(self.edges.producer_of(id)) != core_reference {
                continue;
            }
            if !self
                .edges
                .target_of(id)
                .is_some_and(|t| models.contains(&t))
            {
                continue;
            }
            by_file.entry(self.file_key(id)).or_default().push(id);
        }
        let mut judge = Judge::new(self, vocabulary);
        let mut judged: Vec<Judged> = Vec::new();
        let mut failed = Vec::new();
        for &file in files {
            let Some(rows) = by_file.get(&file) else {
                continue;
            };
            match judge.file(file, rows) {
                Ok(found) => judged.extend(found),
                Err(Unreadable) => failed.push(file),
            }
        }
        let memo = judge.memo;
        let it = &mut self.core.it;
        let flow_key = it.intern(FLOW_SHAPE);
        let written_key = it.intern(INSTANCE_WRITE);
        let mut texts: Vec<Option<(Sid, Sid)>> = vec![None; memo.shaped.len()];
        let mut refinements = Refinements::default();
        for (id, at, flow, written) in judged {
            let (shape, detail) = *texts[at].get_or_insert_with(|| {
                let shape = &memo.shaped[at].shape;
                (it.intern(shape.kind), it.intern(&shape.detail))
            });
            let mut attrs = Vec::new();
            if let Some(flow) = flow {
                attrs.push((flow_key, it.intern(&flow)));
            }
            if let Some(written) = written {
                attrs.push((written_key, it.intern(&written)));
            }
            refinements.add(id, Some(shape), Some(detail), attrs);
        }
        Uses {
            refinements,
            failed,
        }
    }
}
