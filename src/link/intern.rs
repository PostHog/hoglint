//! One string table for a link. Ids are dense u32s; id 0 is the empty string.

use rustc_hash::FxHashMap;
use std::sync::Arc;

pub type Sid = u32;
pub const EMPTY: Sid = 0;

pub struct Interner {
    map: FxHashMap<Arc<str>, Sid>,
    strs: Vec<Arc<str>>,
    joins: FxHashMap<(Sid, Sid), Sid>,
    segs: FxHashMap<Sid, Arc<[Sid]>>,
}

impl Default for Interner {
    fn default() -> Self {
        Self::new()
    }
}

impl Interner {
    pub fn new() -> Self {
        let mut interner = Interner {
            map: FxHashMap::default(),
            strs: Vec::new(),
            joins: FxHashMap::default(),
            segs: FxHashMap::default(),
        };
        interner.intern("");
        interner
    }

    pub fn intern(&mut self, text: &str) -> Sid {
        if let Some(&id) = self.map.get(text) {
            return id;
        }
        let id = self.strs.len() as Sid;
        let shared: Arc<str> = Arc::from(text);
        self.strs.push(shared.clone());
        self.map.insert(shared, id);
        id
    }

    pub fn get(&self, text: &str) -> Option<Sid> {
        self.map.get(text).copied()
    }

    pub fn s(&self, id: Sid) -> &str {
        &self.strs[id as usize]
    }

    pub fn len(&self) -> usize {
        self.strs.len()
    }

    pub fn is_empty(&self) -> bool {
        self.strs.len() <= 1
    }

    /// `a + "." + b`, as Python's `".".join([a, b])`: an empty `a` still gets its dot.
    pub fn join(&mut self, a: Sid, b: Sid) -> Sid {
        if let Some(&joined) = self.joins.get(&(a, b)) {
            return joined;
        }
        let text = format!("{}.{}", self.s(a), self.s(b));
        let id = self.intern(&text);
        self.joins.insert((a, b), id);
        id
    }

    pub fn join_all(&mut self, base: Sid, rest: &[Sid]) -> Sid {
        let mut current = base;
        for &part in rest {
            current = self.join(current, part);
        }
        current
    }

    /// `".".join(parts)`; the empty string for no parts.
    pub fn from_segs(&mut self, parts: &[Sid]) -> Sid {
        match parts.split_first() {
            None => EMPTY,
            Some((&first, rest)) => self.join_all(first, rest),
        }
    }

    /// `text.split(".")`, interned. The empty string splits into one empty part, as in Python.
    pub fn segs(&mut self, id: Sid) -> Arc<[Sid]> {
        if let Some(found) = self.segs.get(&id) {
            return found.clone();
        }
        let text = self.s(id).to_string();
        let parts: Vec<Sid> = text.split('.').map(|part| self.intern(part)).collect();
        let shared: Arc<[Sid]> = parts.into();
        self.segs.insert(id, shared.clone());
        shared
    }

    /// The first dotted part.
    pub fn head(&mut self, id: Sid) -> Sid {
        self.segs(id)[0]
    }

    /// `text.rpartition(".")[0]`: everything before the last dot, or "" without a dot.
    pub fn parent(&mut self, id: Sid) -> Sid {
        let parts = self.segs(id);
        if parts.len() <= 1 {
            return EMPTY;
        }
        self.from_segs(&parts[..parts.len() - 1])
    }

    /// `text.rpartition(".")[2]`: the last dotted part.
    pub fn last(&mut self, id: Sid) -> Sid {
        let parts = self.segs(id);
        parts[parts.len() - 1]
    }
}
