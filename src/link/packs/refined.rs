//! The native sink's conflict bookkeeping, in the link.
//! It records what each producer set on each edge it refined, whether the producer runs in Python
//! or in the link, so a later producer that sets one field of one edge apart meets the conflict.
//!
//! A field's owner is the first producer that set it, and a producer's first value of a field is
//! the one that counts. A refinement may not change an attr its edge has. A batch with a conflict
//! is not applied, and the caller fails its producer with the first conflict.

use super::Refinements;
use crate::link::intern::Sid;
use crate::link::produce::EdgeStore;
use rustc_hash::FxHashMap;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Field {
    Shape,
    ShapeDetail,
    Attr(Sid),
}

/// A field a refinement would set apart: `owner` set it to `value`, or, with no owner, the edge
/// has `value` there.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Conflict {
    pub field: Field,
    pub id: u32,
    pub owner: Option<Sid>,
    pub value: Sid,
}

/// (field, value) of what row `at` of `made` sets, in the order `fields_of` lists them.
fn fields(made: &Refinements, at: usize) -> impl Iterator<Item = (Field, Sid)> + '_ {
    let shape = made.shapes[at].map(|value| (Field::Shape, value));
    let detail = made.details[at].map(|value| (Field::ShapeDetail, value));
    let attrs = made.attrs[at]
        .iter()
        .map(|&(key, value)| (Field::Attr(key), value));
    shape.into_iter().chain(detail).chain(attrs)
}

/// What each producer refined, in the order producers first recorded a batch.
#[derive(Default)]
pub struct RefinedFields {
    made: Vec<(Sid, Refinements)>,
    /// Per producer, edge id -> its rows; built when a later producer asks, dropped on a record.
    rows: Vec<Option<FxHashMap<u32, Vec<u32>>>>,
}

impl RefinedFields {
    fn index(&mut self, at: usize) {
        if self.rows[at].is_some() {
            return;
        }
        let mut found: FxHashMap<u32, Vec<u32>> = FxHashMap::default();
        for (row, &id) in self.made[at].1.ids.iter().enumerate() {
            found.entry(id).or_default().push(row as u32);
        }
        self.rows[at] = Some(found);
    }

    /// (producer, value) of the first producer that set `field` of edge `id`.
    fn owner(&mut self, id: u32, field: Field) -> Option<(Sid, Sid)> {
        for at in 0..self.made.len() {
            self.index(at);
            let (producer, made) = &self.made[at];
            let rows = self.rows[at].as_ref().and_then(|rows| rows.get(&id));
            for &row in rows.into_iter().flatten() {
                if let Some((_, value)) =
                    fields(made, row as usize).find(|(name, _)| *name == field)
                {
                    return Some((*producer, value));
                }
            }
        }
        None
    }

    /// The fields of `batch` another producer set to another value, or an attr of an edge the
    /// batch would change, in row and field order.
    pub fn conflicts(
        &mut self,
        edges: &EdgeStore,
        producer: Sid,
        batch: &Refinements,
    ) -> Vec<Conflict> {
        let mut out = Vec::new();
        for at in 0..batch.len() {
            if self.made.is_empty() && batch.attrs[at].is_empty() {
                continue;
            }
            let id = batch.ids[at];
            for (field, value) in fields(batch, at) {
                if !self.made.is_empty()
                    && let Some((owner, set)) = self.owner(id, field)
                    && owner != producer
                    && set != value
                {
                    out.push(Conflict {
                        field,
                        id,
                        owner: Some(owner),
                        value: set,
                    });
                }
                let Field::Attr(key) = field else {
                    continue;
                };
                // The edge's attrs as a dict reads them: refinements included, the last one wins.
                let has = edges.attrs_with(id).filter(|(name, _)| *name == key).last();
                if let Some(&(_, has)) = has
                    && has != value
                {
                    out.push(Conflict {
                        field,
                        id,
                        owner: None,
                        value: has,
                    });
                }
            }
        }
        out
    }

    pub fn record(&mut self, producer: Sid, batch: Refinements) {
        let Some(at) = self.made.iter().position(|(name, _)| *name == producer) else {
            self.made.push((producer, batch));
            self.rows.push(None);
            return;
        };
        let made = &mut self.made[at].1;
        made.ids.extend(batch.ids);
        made.shapes.extend(batch.shapes);
        made.details.extend(batch.details);
        made.attrs.extend(batch.attrs);
        self.rows[at] = None;
    }

    /// Applies `producer`'s batch to `edges` and records it, or, when a field conflicts, applies
    /// none of it and returns the conflicts.
    pub fn apply(
        &mut self,
        edges: &mut EdgeStore,
        producer: Sid,
        batch: Refinements,
    ) -> Vec<Conflict> {
        let conflicts = self.conflicts(edges, producer, &batch);
        if !conflicts.is_empty() {
            return conflicts;
        }
        for at in 0..batch.len() {
            edges.refine(
                batch.ids[at],
                batch.shapes[at],
                batch.details[at],
                &batch.attrs[at],
            );
        }
        self.record(producer, batch);
        conflicts
    }
}
