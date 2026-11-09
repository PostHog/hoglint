//! One string table for a link. Ids are dense u32s; id 0 is the empty string. The text of
//! every string sits in one buffer, so a string costs its bytes and two ids, not an allocation.

use rustc_hash::{FxHashMap, FxHasher};
use std::hash::{Hash, Hasher};
use std::sync::Arc;

pub type Sid = u32;
pub const EMPTY: Sid = 0;
/// The end of a chain of strings with one hash.
const NO_SID: Sid = Sid::MAX;

pub struct Interner {
    /// Every string's text, back to back, in id order.
    text: String,
    /// Where each string ends in `text`; it starts where the string before it ends.
    ends: Vec<u32>,
    /// The newest string with each text hash; `older` chains the others with the same hash.
    newest: FxHashMap<u64, Sid>,
    older: Vec<Sid>,
    joins: FxHashMap<(Sid, Sid), Sid>,
    segs: FxHashMap<Sid, Arc<[Sid]>>,
}

impl Default for Interner {
    fn default() -> Self {
        Self::new()
    }
}

fn hash_of(text: &str) -> u64 {
    let mut hasher = FxHasher::default();
    text.hash(&mut hasher);
    hasher.finish()
}

impl Interner {
    pub fn new() -> Self {
        let mut interner = Interner {
            text: String::new(),
            ends: Vec::new(),
            newest: FxHashMap::default(),
            older: Vec::new(),
            joins: FxHashMap::default(),
            segs: FxHashMap::default(),
        };
        interner.intern("");
        interner
    }

    /// The id of `text` among the strings with hash `hash`.
    fn find(&self, hash: u64, text: &str) -> Option<Sid> {
        let mut at = self.newest.get(&hash).copied().unwrap_or(NO_SID);
        while at != NO_SID {
            if self.s(at) == text {
                return Some(at);
            }
            at = self.older[at as usize];
        }
        None
    }

    pub fn intern(&mut self, text: &str) -> Sid {
        let hash = hash_of(text);
        if let Some(id) = self.find(hash, text) {
            return id;
        }
        let id = self.ends.len() as Sid;
        self.text.push_str(text);
        self.ends.push(self.text.len() as u32);
        self.older
            .push(self.newest.insert(hash, id).unwrap_or(NO_SID));
        id
    }

    pub fn get(&self, text: &str) -> Option<Sid> {
        self.find(hash_of(text), text)
    }

    pub fn s(&self, id: Sid) -> &str {
        let at = id as usize;
        let start = if at == 0 {
            0
        } else {
            self.ends[at - 1] as usize
        };
        &self.text[start..self.ends[at] as usize]
    }

    /// Estimated heap bytes: the text, the ids, the hash chains and the join and split caches.
    pub fn heap(&self) -> usize {
        let table =
            |entries: usize, entry: usize| (entries * 8 / 7).next_power_of_two() * (entry + 1);
        let mut out = self.text.capacity() + 4 * (self.ends.capacity() + self.older.capacity());
        out += table(self.newest.capacity(), 12);
        out += table(self.joins.capacity(), 12);
        out += table(self.segs.capacity(), 20);
        out + self
            .segs
            .values()
            .map(|parts| 16 + 4 * parts.len())
            .sum::<usize>()
    }

    pub fn len(&self) -> usize {
        self.ends.len()
    }

    pub fn is_empty(&self) -> bool {
        self.ends.len() <= 1
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
