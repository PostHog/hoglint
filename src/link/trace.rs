//! What each analyzed file's edges read from other files (stage S5): its trace, at the grain of
//! (module, name), and the interface of every repo file, a digest per name of what other files
//! can read of it. A later check compares the interfaces of the files a commit changed and
//! recomputes only the files whose trace names a part that moved.
//!
//! Recording is off unless `LinkConfig::trace` asks for it. While it is on, every read of a
//! module table, the module index or another file's def goes into the current owner's trace.
//! A cached answer keeps the reads that produced it (`Memo`), and a later hit adds them again,
//! so a file's trace never depends on which file asked first.

use super::Link;
use super::facts::{Assign, Def, FileFacts, Import, V};
use super::intern::{EMPTY, Interner, Sid};
use super::produce::NO_FILE;
use super::resolve::{Core, Ent, unpacked_names};
use super::surface::sorted_names;
use rayon::prelude::*;
use rustc_hash::FxHashMap;
use serde_json::json;
use std::fs::File;
use std::hash::Hasher;
use std::io::{BufWriter, Write};
use std::path::Path;

/// What a read asked. The names are the dump's.
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
#[repr(u8)]
pub enum Kind {
    /// A module table binds the name.
    Found,
    /// A module table does not bind the name.
    Missing,
    /// A module's star import sources.
    Stars,
    /// A module's lazy (PEP 562) map.
    Lazy,
    /// Every name a module binds, and its `__all__` (a star import handed out).
    Names,
    /// Whether a module exists, is a package, and which file it is.
    Module,
    /// Whether a top-level name is repo code.
    Head,
    /// A class's def record, read for its bases.
    Class,
    /// A def's record, read for the signature or fields of a handed-out callable.
    Def,
    /// An alias's assignment, read in another module.
    Alias,
    /// Which of several module-level imports of a name comes before a line.
    Lines,
    /// A target or a `via` step of an edge sited in the file.
    Target,
    /// The module an edge spells, and the name after it.
    Spelled,
    /// The labels a rule row reads of an endpoint's module.
    Label,
}

impl Kind {
    /// Every kind, in the order of its bit.
    pub const ALL: [Kind; 14] = [
        Kind::Found,
        Kind::Missing,
        Kind::Stars,
        Kind::Lazy,
        Kind::Names,
        Kind::Module,
        Kind::Head,
        Kind::Class,
        Kind::Def,
        Kind::Alias,
        Kind::Lines,
        Kind::Target,
        Kind::Spelled,
        Kind::Label,
    ];

    pub fn name(self) -> &'static str {
        match self {
            Kind::Found => "found",
            Kind::Missing => "missing",
            Kind::Stars => "stars",
            Kind::Lazy => "lazy",
            Kind::Names => "names",
            Kind::Module => "module",
            Kind::Head => "head",
            Kind::Class => "class",
            Kind::Def => "def",
            Kind::Alias => "alias",
            Kind::Lines => "lines",
            Kind::Target => "target",
            Kind::Spelled => "spelled",
            Kind::Label => "label",
        }
    }
}

// The dump names kind k at position k, so `ALL` must follow the enum's own order.
const _: () = {
    let mut at = 0;
    while at < Kind::ALL.len() {
        assert!(Kind::ALL[at] as usize == at);
        at += 1;
    }
};

/// A cached answer whose reads `Recorder` keeps.
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub enum Memo {
    Table(Sid),
    Name(Sid, Sid),
    From(Sid, Sid),
    Bases(Ent),
    Mro(Ent),
    Ancestors(Ent),
    /// A module-level lookup of a file's scope: (file, name).
    Lookup(u32, Sid),
    /// An alias expansion: (alias module, alias name, owner, scope, line).
    Expansion(Sid, Sid, u32, i32, u32),
    /// Whether a class has a dataclass base: (owner, def index).
    DataclassBase(u32, u32),
}

/// A range of packed reads in `Recorder::arena`.
type ReadSet = (u32, u32);

/// One read: (kind, module, name).
pub type Read = (Kind, Sid, Sid);

/// Bits of a packed read: the kind above two string ids.
const ID_BITS: u32 = 29;
const ID_MASK: u64 = (1 << ID_BITS) - 1;

/// A read as one number, so a trace sorts and dedups without a hash table. A string id past
/// `ID_BITS` cannot be packed; the link holds far fewer strings.
fn pack(kind: Kind, module: Sid, name: Sid) -> u64 {
    debug_assert!(u64::from(module) <= ID_MASK && u64::from(name) <= ID_MASK);
    (kind as u64) << (2 * ID_BITS) | u64::from(module) << ID_BITS | u64::from(name)
}

fn unpack(read: u64) -> (u8, Sid, Sid) {
    (
        (read >> (2 * ID_BITS)) as u8,
        ((read >> ID_BITS) & ID_MASK) as Sid,
        (read & ID_MASK) as Sid,
    )
}

/// Sorts and dedups `items[start..]` in place and drops the repeats.
fn sort_tail(items: &mut Vec<u64>, start: usize) {
    items[start..].sort_unstable();
    let mut kept = start;
    for at in start..items.len() {
        if at == start || items[at] != items[kept - 1] {
            items[kept] = items[at];
            kept += 1;
        }
    }
    items.truncate(kept);
}

/// The reads of the file being produced, and of every cached answer.
#[derive(Default)]
pub struct Recorder {
    pub on: bool,
    /// Reads since the current owner started, cached answers' reads included.
    buf: Vec<u64>,
    arena: Vec<u64>,
    memos: FxHashMap<Memo, ReadSet>,
    owner: Option<usize>,
    /// The packed reads of each owner (a scope index), sorted and without repeats.
    traces: Vec<Vec<u64>>,
}

impl Recorder {
    pub fn read(&mut self, kind: Kind, module: Sid, name: Sid) {
        if self.on {
            self.buf.push(pack(kind, module, name));
        }
    }

    /// Where the reads of an answer about to be computed start.
    pub fn mark(&self) -> usize {
        self.buf.len()
    }

    /// Keeps the reads since `start` as the reads of `memo`. They stay in the current owner's
    /// reads too, sorted in place.
    pub fn remember(&mut self, memo: Memo, start: usize) {
        if !self.on {
            return;
        }
        sort_tail(&mut self.buf, start);
        let at = self.arena.len() as u32;
        self.arena.extend_from_slice(&self.buf[start..]);
        self.memos
            .insert(memo, (at, (self.buf.len() - start) as u32));
    }

    /// Adds the reads of a cached answer to the current owner's.
    pub fn replay(&mut self, memo: Memo) {
        if !self.on {
            return;
        }
        if let Some(&(at, len)) = self.memos.get(&memo) {
            self.buf
                .extend_from_slice(&self.arena[at as usize..(at + len) as usize]);
        }
    }

    pub fn enter(&mut self, owner: usize, owners: usize) {
        if !self.on {
            return;
        }
        if self.traces.len() < owners {
            self.traces.resize_with(owners, Vec::new);
        }
        self.buf.clear();
        self.owner = Some(owner);
    }

    pub fn leave(&mut self) {
        let Some(owner) = self.owner.take() else {
            return;
        };
        let trace = &mut self.traces[owner];
        trace.append(&mut self.buf);
        sort_tail(trace, 0);
    }

    /// Ends recording: what packs resolve later belongs to no file's edges.
    pub fn finish(&mut self) {
        self.leave();
        self.on = false;
        self.buf = Vec::new();
        self.memos = FxHashMap::default();
        self.arena = Vec::new();
    }

    /// The reads of one owner (a scope index), unpacked.
    pub fn trace(&self, owner: usize) -> impl Iterator<Item = (u8, Sid, Sid)> + '_ {
        self.traces
            .get(owner)
            .map_or(&[][..], Vec::as_slice)
            .iter()
            .map(|&read| unpack(read))
    }
}

// ── interfaces ──

/// A keyed SipHash-2-4: stable across runs and Rust versions, unlike `DefaultHasher`.
#[allow(deprecated)]
type Sip = std::hash::SipHasher;

const KEY: (u64, u64) = (0x686f_676c_696e_7435, 0x7335_6e61_6d65_0001);

/// A 64-bit digest of strings, numbers and value trees.
struct Digest<'a> {
    sip: Sip,
    it: &'a Interner,
}

impl<'a> Digest<'a> {
    #[allow(deprecated)]
    fn new(it: &'a Interner) -> Digest<'a> {
        Digest {
            sip: Sip::new_with_keys(KEY.0, KEY.1),
            it,
        }
    }

    fn text(&mut self, text: &str) {
        self.sip.write_u64(text.len() as u64);
        self.sip.write(text.as_bytes());
    }

    fn sid(&mut self, sid: Sid) {
        self.text(self.it.s(sid));
    }

    fn number(&mut self, value: u64) {
        self.sip.write_u64(value);
    }

    fn option(&mut self, value: Option<&V>) {
        match value {
            None => self.number(0),
            Some(value) => {
                self.number(1);
                self.value(value);
            }
        }
    }

    fn values(&mut self, items: &[V]) {
        self.number(items.len() as u64);
        for item in items {
            self.value(item);
        }
    }

    fn value(&mut self, value: &V) {
        match value {
            V::Null => self.number(1),
            V::Chain(name) => {
                self.number(2);
                self.sid(*name);
            }
            V::Str(text, parsed) => {
                self.number(3);
                self.sid(*text);
                self.option(parsed.as_deref());
            }
            V::Call(callee, frozen, sole) => {
                self.number(4);
                self.value(callee);
                self.number(u64::from(*frozen));
                self.option(sole.as_deref());
            }
            V::Attr(inner, attr) => {
                self.number(5);
                self.value(inner);
                self.sid(*attr);
            }
            V::Sub(inner, index) => {
                self.number(6);
                self.value(inner);
                self.value(index);
            }
            V::Tuple(items) => {
                self.number(7);
                self.values(items);
            }
            V::List(items) => {
                self.number(8);
                self.values(items);
            }
            V::Set(items) => {
                self.number(9);
                self.values(items);
            }
            V::Dict(pairs) => {
                self.number(10);
                self.number(pairs.len() as u64);
                for (key, item) in pairs {
                    self.option(key.as_ref());
                    self.value(item);
                }
            }
            V::Star(inner) => {
                self.number(11);
                self.value(inner);
            }
            V::Bin(pipe, left, right) => {
                self.number(12);
                self.number(u64::from(*pipe));
                self.value(left);
                self.value(right);
            }
            V::Opaque(what) => {
                self.number(13);
                self.sid(*what);
            }
            V::Other => self.number(14),
            V::Scalar => self.number(15),
        }
    }

    /// A def row without its position or its place in the record.
    fn def(&mut self, node: &Def) {
        self.text(node.kind.name());
        self.sid(node.n);
        self.number(u64::from(node.f));
        self.number(u64::from(node.stub));
        self.values(&node.bases);
        self.values(&node.dec);
        self.number(node.params.len() as u64);
        for param in &node.params {
            self.sid(param.name);
            self.number(u64::from(param.positional));
            self.option(param.annotation.as_ref());
        }
        self.option(node.ret.as_ref());
    }

    fn assign(&mut self, row: &Assign) {
        self.number(row.op as u64);
        self.number(u64::from(row.f));
        self.values(&row.targets);
        self.option(row.value.as_ref());
        self.option(row.ann.as_ref());
    }

    fn import(&mut self, row: &Import) {
        self.number(u64::from(row.flags));
        self.sid(row.target.unwrap_or(EMPTY));
        self.number(u64::from(row.level));
        self.number(u64::from(row.name.is_some()));
        self.sid(row.name.unwrap_or(EMPTY));
        self.sid(row.asname.unwrap_or(EMPTY));
    }

    fn finish(&self) -> u64 {
        self.sip.finish()
    }
}

/// The names of the interface entries that are no Python name.
pub const STARS: &str = "<stars>";
pub const LAZY: &str = "<lazy>";
pub const NAMES: &str = "<names>";
pub const LINES: &str = "<lines>";

/// The top-level def each def sits in, by def index.
fn top_defs(defs: &[Def]) -> Vec<usize> {
    let mut top = Vec::with_capacity(defs.len());
    for (at, node) in defs.iter().enumerate() {
        let root = if node.s == -1 {
            at
        } else {
            top[node.s as usize]
        };
        top.push(root);
    }
    top
}

/// The top-level def a scope sits in; None for the module scope.
fn top_of(top: &[usize], scope: i32) -> Option<usize> {
    (scope != -1).then(|| top[scope as usize])
}

/// The digest of one interface entry, made on first use.
fn entry<'p, 'a>(
    parts: &'p mut FxHashMap<&'a str, Digest<'a>>,
    it: &'a Interner,
    name: &'a str,
) -> &'p mut Digest<'a> {
    parts.entry(name).or_insert_with(|| Digest::new(it))
}

/// What other files can read of one record, by name: a digest per module-level name of every
/// row that binds it (a def with everything nested in it, an import, an assignment), and the
/// entries that are no name: the star sources, the lazy map's inputs, the set of names with
/// `__all__`, and the positions of names imported more than once. Positions are left out
/// elsewhere, so an edit that only moves code changes nothing here. The digest depends on the
/// record alone, so it never changes for a blob.
pub fn interface(it: &Interner, record: &FileFacts) -> Vec<(String, u64)> {
    let mut parts: FxHashMap<&str, Digest> = FxHashMap::default();
    entry(&mut parts, it, NAMES).number(u64::from(record.ok()));
    if record.ok() {
        bindings(it, record, &mut parts);
    }
    let mut out: Vec<(String, u64)> = parts
        .into_iter()
        .map(|(name, digest)| (name.to_string(), digest.finish()))
        .collect();
    out.sort_unstable();
    out
}

/// The rows of a parsed record, each under the names it binds.
fn bindings<'a>(
    it: &'a Interner,
    record: &'a FileFacts,
    parts: &mut FxHashMap<&'a str, Digest<'a>>,
) {
    let top = top_defs(&record.defs);
    for (at, node) in record.defs.iter().enumerate() {
        let digest = entry(parts, it, it.s(record.defs[top[at]].n));
        digest.number((at - top[at]) as u64);
        digest.def(node);
        if node.s != -1 {
            digest.number(node.s as u64 - top[at] as u64);
        }
    }
    for row in &record.imports {
        if let Some(root) = top_of(&top, row.scope) {
            let digest = entry(parts, it, it.s(record.defs[root].n));
            digest.number(row.scope as u64 - root as u64);
            digest.import(row);
            continue;
        }
        entry(parts, it, import_name(it, row)).import(row);
    }
    let mut module_level: Vec<&Assign> = Vec::new();
    for row in &record.assigns {
        if let Some(root) = top_of(&top, row.s) {
            let digest = entry(parts, it, it.s(record.defs[root].n));
            digest.number(row.s as u64 - root as u64);
            digest.assign(row);
            continue;
        }
        module_level.push(row);
        let mut names = Vec::new();
        for target in &row.targets {
            unpacked_names(it, target, &mut names);
        }
        for name in names {
            entry(parts, it, it.s(name)).assign(row);
        }
        if matches!(row.value, Some(V::Str(..)) | Some(V::Dict(..))) {
            entry(parts, it, LAZY).assign(row);
        }
    }
    let mut names: Vec<&str> = parts
        .keys()
        .copied()
        .filter(|n| !n.starts_with('<'))
        .collect();
    names.sort_unstable();
    let all = entry(parts, it, NAMES);
    for row in &module_level {
        all.assign(row);
    }
    for row in &record.callees {
        if row.scope == -1 && row.header == -1 && it.s(row.text).starts_with("__all__.") {
            all.sid(row.text);
        }
    }
    for name in names {
        all.text(name);
    }
    if let Some(lines) = lines_digest(it, record) {
        entry(parts, it, LINES).number(lines);
    }
}

/// The name a module-level import binds; `STARS` for a star import.
fn import_name<'a>(it: &'a Interner, row: &Import) -> &'a str {
    if let Some(asname) = row.asname.filter(|&a| a != EMPTY) {
        return it.s(asname);
    }
    match row.name {
        Some(name) if it.s(name) == "*" => STARS,
        Some(name) => it.s(name),
        None => {
            let target = it.s(row.target.unwrap_or(EMPTY));
            target.split('.').next().unwrap_or(target)
        }
    }
}

/// The lines of every module-level import and def, when a name is imported more than once at
/// module level: which import a lookup at a line takes then depends on where the rows sit.
fn lines_digest(it: &Interner, record: &FileFacts) -> Option<u64> {
    let mut seen: FxHashMap<Sid, u32> = FxHashMap::default();
    let mut repeated = false;
    for row in record.imports.iter().filter(|row| row.scope == -1) {
        let bound = row
            .asname
            .filter(|&a| a != EMPTY)
            .or(row.name)
            .or(row.target);
        let count = seen.entry(bound.unwrap_or(EMPTY)).or_insert(0);
        *count += 1;
        repeated |= *count > 1;
    }
    if !repeated {
        return None;
    }
    let mut digest = Digest::new(it);
    for row in record.imports.iter().filter(|row| row.scope == -1) {
        digest.number(u64::from(row.line));
        digest.import(row);
    }
    for node in record.defs.iter().filter(|node| node.s == -1) {
        digest.number(u64::from(node.l));
        digest.sid(node.n);
    }
    Some(digest.finish())
}

/// Every repo file's interface, by file id. Third-party files never change within a run's
/// environment, so they have none.
pub fn interfaces(core: &Core) -> Vec<(u32, Vec<(String, u64)>)> {
    let it = &core.it;
    let site = &core.index.site;
    core.facts
        .files
        .par_iter()
        .enumerate()
        .filter(|(file, _)| !site[*file])
        .map(|(file, record)| (file as u32, interface(it, record)))
        .collect()
}

/// The name of a read as the dump writes it: its first dotted part, because an interface holds
/// a nested def under its top-level name.
fn name_text(it: &Interner, name: Sid) -> &str {
    let name = it.s(name);
    name.split('.').next().unwrap_or(name)
}

impl Link {
    /// The reads of the finished edges sited in each analyzed file, core and pack edges alike:
    /// every target and `via` step, the module the surface spells with the name after it, and
    /// the labels a rule row reads of each endpoint's module.
    fn edge_reads(&mut self) -> Vec<Vec<Read>> {
        let mut out: Vec<Vec<Read>> = vec![Vec::new(); self.scopes.len()];
        for id in 0..self.edges.len() as u32 {
            let owner = match self.edges.pack_row(id) {
                None => self.edges.rows[id as usize].owner as usize,
                Some(pack) if pack.file == NO_FILE => continue,
                Some(pack) => match self.scope_of_file.get(&pack.file) {
                    Some(&owner) => owner as usize,
                    None => continue,
                },
            };
            let reads = &mut out[owner];
            for &ent in self
                .edges
                .targets_of(id)
                .iter()
                .chain(self.edges.via_of_id(id))
            {
                reads.push((Kind::Target, ent.m, ent.s));
                reads.push((Kind::Label, ent.m, EMPTY));
            }
            let surface = self.edges.surface_of(id);
            if surface == EMPTY {
                continue;
            }
            let core = &mut self.core;
            let found = core
                .index
                .longest_module(&mut core.it, surface, &mut |_, _| {});
            let Some((module, end)) = found else {
                continue;
            };
            let after = core.it.segs(surface).get(end).copied().unwrap_or(EMPTY);
            reads.push((Kind::Spelled, module, after));
            reads.push((Kind::Label, module, EMPTY));
        }
        out
    }

    /// Writes every analyzed file's trace and every repo file's interface to `path`, one JSON
    /// array per line: `["kinds", [names]]` first, then `["trace", path, module, [[module,
    /// name, kinds], ...]]` per analyzed file, where `kinds` has bit k set for each kind k
    /// that read (module, name), and `["interface", path, module, [[name, digest], ...]]` per
    /// repo file, then `["handout", module, [handed], [kept], unread]` per export module: what its
    /// rule rows' `dst.exported` reads. A read of the file's own module is left out: a change to the module's files
    /// makes the file recompute anyway.
    pub fn write_traces(&mut self, path: &Path) -> Result<(), String> {
        if self.core.it.len() as u64 > ID_MASK {
            return Err("too many strings to pack the reads of a trace".to_string());
        }
        let edge_reads = self.edge_reads();
        let file = File::create(path).map_err(|e| format!("{}: {e}", path.display()))?;
        let mut out = BufWriter::new(file);
        let fail = |e: std::io::Error| format!("{}: {e}", path.display());
        let kinds = Kind::ALL.map(Kind::name);
        writeln!(out, "{}", json!(["kinds", kinds])).map_err(fail)?;
        let it = &self.core.it;
        let rec = &self.core.rec;
        for (owner, scope) in self.scopes.iter().enumerate() {
            let module = scope.module;
            let mut kinds: FxHashMap<(&str, &str), u32> = FxHashMap::default();
            let posthoc = edge_reads[owner]
                .iter()
                .map(|&(kind, read_module, name)| (kind as u8, read_module, name));
            for (kind, read_module, name) in rec.trace(owner).chain(posthoc) {
                if read_module == module {
                    continue;
                }
                let key = (it.s(read_module), name_text(it, name));
                *kinds.entry(key).or_insert(0) |= 1 << kind;
            }
            let mut reads: Vec<(&str, &str, u32)> = kinds
                .into_iter()
                .map(|((m, n), mask)| (m, n, mask))
                .collect();
            reads.sort_unstable();
            let path = &*self.core.facts.files[scope.file as usize].path;
            let module = it.s(module);
            writeln!(out, "{}", json!(["trace", path, module, reads])).map_err(fail)?;
        }
        for (file, digests) in &self.interfaces {
            let module = self.core.index.module_of_file[*file as usize]
                .map_or("", |(module, _)| it.s(module));
            let path = &*self.core.facts.files[*file as usize].path;
            let rows: Vec<(&str, String)> = digests
                .iter()
                .map(|(name, digest)| (name.as_str(), format!("{digest:016x}")))
                .collect();
            writeln!(out, "{}", json!(["interface", path, module, rows])).map_err(fail)?;
        }
        let mut modules: Vec<Sid> = self.hand_outs.keys().copied().collect();
        modules.sort_by(|a, b| it.s(*a).cmp(it.s(*b)));
        for module in modules {
            let found = &self.hand_outs[&module];
            let row = json!([
                "handout",
                it.s(module),
                sorted_names(it, &found.handed),
                sorted_names(it, &found.kept),
                found.unread,
                sorted_names(it, &found.unknown),
                sorted_names(it, &found.listed_private),
                sorted_names(it, &found.unknown_values)
            ]);
            writeln!(out, "{row}").map_err(fail)?;
        }
        out.flush().map_err(fail)
    }
}
