//! Pack producers the link runs itself (D49): a producer whose judgment is per row moves here
//! when a profile shows it pays, and writes its refinements with no Python object per row.
//!
//! They read linked edges and the records through the link, never the fact cache's own format,
//! so they stay out of the `[facts]` id (engine-sources.txt). `refined` keeps the native sink's
//! conflict bookkeeping, for the producers in Python and in the link alike.

pub mod django;
pub mod refined;

use super::intern::Sid;

/// Refinements as columns, one row per refined edge, in the order they apply: `Refinements` in
/// hoglint/plugins.py. A field left None is not set; `attrs` are added after the edge's own.
#[derive(Default)]
pub struct Refinements {
    pub ids: Vec<u32>,
    pub shapes: Vec<Option<Sid>>,
    pub details: Vec<Option<Sid>>,
    pub attrs: Vec<Vec<(Sid, Sid)>>,
}

impl Refinements {
    pub fn len(&self) -> usize {
        self.ids.len()
    }

    pub fn is_empty(&self) -> bool {
        self.ids.is_empty()
    }

    pub fn add(
        &mut self,
        id: u32,
        shape: Option<Sid>,
        detail: Option<Sid>,
        attrs: Vec<(Sid, Sid)>,
    ) {
        self.ids.push(id);
        self.shapes.push(shape);
        self.details.push(detail);
        self.attrs.push(attrs);
    }
}
