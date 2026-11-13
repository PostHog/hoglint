//! The binary facts of one cached record: what the link reads, so a warm run never parses JSON
//! in Rust. The record's JSON text, which packs read, is stored beside it, not in it.
//!
//! Layout: MAGIC, the string table (u32 count, then u32 length and bytes per string), then the
//! body, whose strings are indexes into that table.
//! The [facts] id that names the fact database covers this file, `facts.rs` and `intern.rs`, so
//! a change to the layout never meets facts written by older code.

use super::facts::{
    Argument, Assign, CallRow, CountRow, Def, DefKind, FileFacts, Import, Op, Param, RefRow,
    StrRow, TagRow, UseRow, V,
};
use super::intern::{Interner, Sid};
use rustc_hash::FxHashMap;

/// Marks binary facts of this layout.
pub const MAGIC: &[u8; 8] = b"HLFACTS3";

struct Enc<'i> {
    it: &'i Interner,
    local: FxHashMap<Sid, u32>,
    strings: Vec<Sid>,
    body: Vec<u8>,
}

impl Enc<'_> {
    fn u8(&mut self, v: u8) {
        self.body.push(v);
    }
    fn u32(&mut self, v: u32) {
        self.body.extend_from_slice(&v.to_le_bytes());
    }
    fn i32(&mut self, v: i32) {
        self.u32(v as u32);
    }
    fn sid(&mut self, s: Sid) {
        let next = self.local.len() as u32;
        let id = *self.local.entry(s).or_insert(next);
        if id == next {
            self.strings.push(s);
        }
        self.u32(id);
    }
    fn osid(&mut self, s: Option<Sid>) {
        match s {
            Some(s) => self.sid(s),
            None => self.u32(u32::MAX),
        }
    }
    fn text(&mut self, s: &str) {
        self.u32(s.len() as u32);
        self.body.extend_from_slice(s.as_bytes());
    }
    fn otext(&mut self, s: Option<&str>) {
        match s {
            Some(s) => {
                self.u8(1);
                self.text(s);
            }
            None => self.u8(0),
        }
    }
    fn values(&mut self, values: &[V]) {
        self.u32(values.len() as u32);
        for v in values {
            self.v(v);
        }
    }
    fn ov(&mut self, v: Option<&V>) {
        match v {
            Some(v) => {
                self.u8(1);
                self.v(v);
            }
            None => self.u8(0),
        }
    }
    fn v(&mut self, v: &V) {
        match v {
            V::Null => self.u8(0),
            V::Chain(s) => {
                self.u8(1);
                self.sid(*s);
            }
            V::Str(s, parsed) => {
                self.u8(2);
                self.sid(*s);
                self.ov(parsed.as_deref());
            }
            V::Call(inner, frozen, sole) => {
                self.u8(3);
                self.v(inner);
                self.u8(u8::from(*frozen));
                self.ov(sole.as_deref());
            }
            V::Attr(inner, a) => {
                self.u8(4);
                self.v(inner);
                self.sid(*a);
            }
            V::Sub(inner, index) => {
                self.u8(5);
                self.v(inner);
                self.v(index);
            }
            V::Tuple(items) => {
                self.u8(6);
                self.values(items);
            }
            V::List(items) => {
                self.u8(7);
                self.values(items);
            }
            V::Set(items) => {
                self.u8(8);
                self.values(items);
            }
            V::Dict(pairs) => {
                self.u8(9);
                self.u32(pairs.len() as u32);
                for (key, value) in pairs {
                    self.ov(key.as_ref());
                    self.v(value);
                }
            }
            V::Star(inner) => {
                self.u8(10);
                self.v(inner);
            }
            V::Bin(pipe, l, r) => {
                self.u8(11);
                self.u8(*pipe as u8);
                self.v(l);
                self.v(r);
            }
            V::Opaque(s) => {
                self.u8(12);
                self.sid(*s);
            }
            V::Other => self.u8(13),
            V::Scalar => self.u8(14),
            V::Unary(inner) => {
                self.u8(15);
                self.v(inner);
            }
            V::BoolOp(items) => {
                self.u8(16);
                self.values(items);
            }
            V::Compare(first, rest) => {
                self.u8(17);
                self.v(first);
                self.values(rest);
            }
        }
    }

    fn file(&mut self, f: &FileFacts, line_start: usize) {
        self.u32(f.keys.len() as u32);
        for (key, range) in &f.keys {
            self.text(key);
            self.u32((range.start - line_start) as u32);
            self.u32((range.end - line_start) as u32);
        }
        self.text(&f.status);
        self.u32(f.imports.len() as u32);
        for r in &f.imports {
            self.i32(r.scope);
            self.u32(r.line);
            self.u32(r.col);
            self.u32(r.flags);
            self.osid(r.target);
            self.u32(r.level);
            self.osid(r.name);
            self.osid(r.asname);
        }
        self.u32(f.defs.len() as u32);
        for d in &f.defs {
            self.i32(d.s);
            self.u32(d.l);
            self.u32(d.c);
            self.u32(d.f);
            self.u8(d.kind as u8);
            self.sid(d.n);
            self.values(&d.bases);
            self.values(&d.dec);
            self.u32(d.params.len() as u32);
            for p in &d.params {
                self.sid(p.name);
                self.ov(p.annotation.as_ref());
                self.u8(p.positional as u8);
            }
            self.ov(d.ret.as_ref());
            self.u8(d.stub as u8);
        }
        self.u32(f.assigns.len() as u32);
        for a in &f.assigns {
            self.i32(a.s);
            self.u32(a.l);
            self.u32(a.c);
            self.u32(a.f);
            self.u8(a.op as u8);
            self.values(&a.targets);
            self.ov(a.value.as_ref());
            self.ov(a.ann.as_ref());
        }
        self.u32(f.refs.len() as u32);
        for r in &f.refs {
            self.i32(r.scope);
            self.i32(r.header);
            self.u32(r.line);
            self.u32(r.col);
            self.u32(r.flags);
            self.sid(r.name);
            self.text(&r.chain);
            self.text(&r.up);
        }
        self.u32(f.strs.len() as u32);
        for r in &f.strs {
            self.i32(r.scope);
            self.u32(r.line);
            self.u32(r.col);
            self.u32(r.flags);
            self.otext(r.value.as_deref());
            self.text(&r.up);
            match &r.parts {
                Some(parts) => {
                    self.u8(1);
                    self.u32(parts.len() as u32);
                    for part in parts {
                        self.otext(part.as_deref());
                    }
                }
                None => self.u8(0),
            }
        }
        self.u32(f.calls.len() as u32);
        for r in &f.calls {
            self.i32(r.scope);
            self.u32(r.line);
            self.u32(r.col);
            self.u32(r.flags);
            self.text(&r.callee);
            match &r.argument {
                Argument::Missing => self.u8(0),
                Argument::Literal(s) => {
                    self.u8(1);
                    self.text(s);
                }
                Argument::Name(s) => {
                    self.u8(2);
                    self.text(s);
                }
                Argument::Opaque(s) => {
                    self.u8(3);
                    self.text(s);
                }
                Argument::Expr => self.u8(4),
            }
        }
        self.u32(f.uses.len() as u32);
        for r in &f.uses {
            self.i32(r.scope);
            self.i32(r.header);
            self.sid(r.root);
            self.sid(r.attrs);
            self.u32(r.count);
            self.u32(r.line);
            self.u32(r.col);
        }
        self.counts(&f.callees);
        self.counts(&f.names);
        self.u32(f.tags.len() as u32);
        for r in &f.tags {
            self.i32(r.scope);
            self.i32(r.header);
            self.u32(r.line);
            self.u32(r.col);
            self.u32(r.flags);
            self.sid(r.name);
        }
    }

    fn counts(&mut self, rows: &[CountRow]) {
        self.u32(rows.len() as u32);
        for r in rows {
            self.i32(r.scope);
            self.i32(r.header);
            self.sid(r.text);
            self.u32(r.count);
            self.u32(r.line);
            self.u32(r.col);
        }
    }
}

/// The binary facts of a record whose JSON text starts at `line_start` in the fact set.
pub fn encode_facts(it: &Interner, facts: &FileFacts, line_start: usize) -> Vec<u8> {
    let mut enc = Enc {
        it,
        local: FxHashMap::default(),
        strings: Vec::new(),
        body: Vec::new(),
    };
    enc.file(facts, line_start);
    let mut out = Vec::with_capacity(MAGIC.len() + 4 + enc.body.len() + enc.strings.len() * 16);
    out.extend_from_slice(MAGIC);
    out.extend_from_slice(&(enc.strings.len() as u32).to_le_bytes());
    for &s in &enc.strings {
        let text = enc.it.s(s);
        out.extend_from_slice(&(text.len() as u32).to_le_bytes());
        out.extend_from_slice(text.as_bytes());
    }
    out.extend_from_slice(&enc.body);
    out
}

struct Dec<'b, 'm> {
    b: &'b [u8],
    p: usize,
    map: &'m [Sid],
}

impl<'b> Dec<'b, '_> {
    fn bytes(&mut self, n: usize) -> Result<&'b [u8], String> {
        let out = self
            .b
            .get(self.p..self.p + n)
            .ok_or("a cache entry ends early")?;
        self.p += n;
        Ok(out)
    }
    fn u8(&mut self) -> Result<u8, String> {
        Ok(self.bytes(1)?[0])
    }
    fn u32(&mut self) -> Result<u32, String> {
        let raw = self.bytes(4)?;
        Ok(u32::from_le_bytes([raw[0], raw[1], raw[2], raw[3]]))
    }
    fn i32(&mut self) -> Result<i32, String> {
        Ok(self.u32()? as i32)
    }
    fn sid(&mut self) -> Result<Sid, String> {
        let i = self.u32()? as usize;
        self.map
            .get(i)
            .copied()
            .ok_or_else(|| "a cache entry names a string it lacks".to_string())
    }
    fn osid(&mut self) -> Result<Option<Sid>, String> {
        let i = self.u32()?;
        if i == u32::MAX {
            Ok(None)
        } else {
            self.map
                .get(i as usize)
                .copied()
                .map(Some)
                .ok_or("bad string".into())
        }
    }
    fn str(&mut self) -> Result<&'b str, String> {
        let n = self.u32()? as usize;
        std::str::from_utf8(self.bytes(n)?).map_err(|e| e.to_string())
    }
    fn text(&mut self) -> Result<Box<str>, String> {
        Ok(self.str()?.into())
    }
    fn otext(&mut self) -> Result<Option<Box<str>>, String> {
        Ok(if self.u8()? == 1 {
            Some(self.text()?)
        } else {
            None
        })
    }
    fn values(&mut self) -> Result<Vec<V>, String> {
        let n = self.u32()? as usize;
        // Sized up front: collecting results grows the vector by doubling.
        let mut out = Vec::with_capacity(n);
        for _ in 0..n {
            out.push(self.v()?);
        }
        Ok(out)
    }
    fn counts(&mut self) -> Result<Vec<CountRow>, String> {
        let n = self.u32()? as usize;
        let mut out = Vec::with_capacity(n);
        for _ in 0..n {
            out.push(CountRow {
                scope: self.i32()?,
                header: self.i32()?,
                text: self.sid()?,
                count: self.u32()?,
                line: self.u32()?,
                col: self.u32()?,
            });
        }
        Ok(out)
    }
    fn ov(&mut self) -> Result<Option<V>, String> {
        Ok(if self.u8()? == 1 {
            Some(self.v()?)
        } else {
            None
        })
    }
    fn v(&mut self) -> Result<V, String> {
        Ok(match self.u8()? {
            0 => V::Null,
            1 => V::Chain(self.sid()?),
            2 => {
                let s = self.sid()?;
                V::Str(s, self.ov()?.map(Box::new))
            }
            3 => {
                let inner = self.v()?;
                let frozen = self.u8()? == 1;
                V::Call(Box::new(inner), frozen, self.ov()?.map(Box::new))
            }
            4 => {
                let inner = self.v()?;
                V::Attr(Box::new(inner), self.sid()?)
            }
            5 => {
                let inner = self.v()?;
                V::Sub(Box::new(inner), Box::new(self.v()?))
            }
            6 => V::Tuple(self.values()?),
            7 => V::List(self.values()?),
            8 => V::Set(self.values()?),
            9 => {
                let n = self.u32()? as usize;
                let mut pairs = Vec::with_capacity(n);
                for _ in 0..n {
                    let key = self.ov()?;
                    pairs.push((key, self.v()?));
                }
                V::Dict(pairs)
            }
            10 => V::Star(Box::new(self.v()?)),
            11 => {
                let pipe = self.u8()? == 1;
                let l = self.v()?;
                V::Bin(pipe, Box::new(l), Box::new(self.v()?))
            }
            12 => V::Opaque(self.sid()?),
            14 => V::Scalar,
            15 => V::Unary(Box::new(self.v()?)),
            16 => V::BoolOp(self.values()?),
            17 => {
                let first = self.v()?;
                V::Compare(Box::new(first), self.values()?)
            }
            _ => V::Other,
        })
    }
}

/// The strings binary facts name, interned in their order, and where their body starts.
/// Interning is the only step that needs the shared interner, so a loader runs it in fact order
/// and decodes the bodies in parallel.
pub fn facts_strings(it: &mut Interner, blob: &[u8]) -> Result<(Vec<Sid>, usize), String> {
    if !blob.starts_with(MAGIC) {
        return Err("not binary facts of this layout".to_string());
    }
    let mut d = Dec {
        b: blob,
        p: MAGIC.len(),
        map: &[],
    };
    let n = d.u32()? as usize;
    let mut map = Vec::with_capacity(n);
    for _ in 0..n {
        map.push(it.intern(d.str()?));
    }
    Ok((map, d.p))
}

/// The facts of a body. `map` holds the interned strings of its table; `line_start` is where
/// its JSON text (`json_len` bytes) starts in the fact set; `path` is the snapshot's path for
/// the blob.
pub fn decode_body(
    map: &[Sid],
    body: &[u8],
    line_start: usize,
    json_len: usize,
    path: &str,
) -> Result<FileFacts, String> {
    let mut d = Dec { b: body, p: 0, map };
    let nkeys = d.u32()? as usize;
    let mut keys = Vec::with_capacity(nkeys);
    for _ in 0..nkeys {
        let key = d.text()?;
        let start = d.u32()? as usize + line_start;
        let end = d.u32()? as usize + line_start;
        keys.push((key, start..end));
    }
    let status = d.text()?;
    let n = d.u32()? as usize;
    let mut imports = Vec::with_capacity(n);
    for _ in 0..n {
        imports.push(Import {
            scope: d.i32()?,
            line: d.u32()?,
            col: d.u32()?,
            flags: d.u32()?,
            target: d.osid()?,
            level: d.u32()?,
            name: d.osid()?,
            asname: d.osid()?,
        });
    }
    let n = d.u32()? as usize;
    let mut defs = Vec::with_capacity(n);
    for _ in 0..n {
        let s = d.i32()?;
        let l = d.u32()?;
        let c = d.u32()?;
        let f = d.u32()?;
        let kind = match d.u8()? {
            0 => DefKind::Def,
            1 => DefKind::Async,
            _ => DefKind::Class,
        };
        let name = d.sid()?;
        let bases = d.values()?;
        let dec = d.values()?;
        let np = d.u32()? as usize;
        let mut params = Vec::with_capacity(np);
        for _ in 0..np {
            let pname = d.sid()?;
            let annotation = d.ov()?;
            params.push(Param {
                name: pname,
                annotation,
                positional: d.u8()? != 0,
            });
        }
        let ret = d.ov()?;
        let stub = d.u8()? == 1;
        defs.push(Def {
            s,
            l,
            c,
            f,
            kind,
            n: name,
            bases,
            dec,
            params,
            ret,
            stub,
        });
    }
    let n = d.u32()? as usize;
    let mut assigns = Vec::with_capacity(n);
    for _ in 0..n {
        let s = d.i32()?;
        let l = d.u32()?;
        let c = d.u32()?;
        let f = d.u32()?;
        let op = match d.u8()? {
            0 => Op::Assign,
            1 => Op::Annotated,
            2 => Op::TypeAlias,
            4 => Op::AddAssign,
            _ => Op::Other,
        };
        let targets = d.values()?;
        let value = d.ov()?;
        let ann = d.ov()?;
        assigns.push(Assign {
            s,
            l,
            c,
            f,
            op,
            targets,
            value,
            ann,
        });
    }
    let n = d.u32()? as usize;
    let mut refs = Vec::with_capacity(n);
    for _ in 0..n {
        refs.push(RefRow {
            scope: d.i32()?,
            header: d.i32()?,
            line: d.u32()?,
            col: d.u32()?,
            flags: d.u32()?,
            name: d.sid()?,
            chain: d.text()?,
            up: d.text()?,
        });
    }
    let n = d.u32()? as usize;
    let mut strs = Vec::with_capacity(n);
    for _ in 0..n {
        let scope = d.i32()?;
        let line = d.u32()?;
        let col = d.u32()?;
        let flags = d.u32()?;
        let value = d.otext()?;
        let up = d.text()?;
        let parts = if d.u8()? == 1 {
            let k = d.u32()? as usize;
            Some((0..k).map(|_| d.otext()).collect::<Result<Vec<_>, _>>()?)
        } else {
            None
        };
        strs.push(StrRow {
            scope,
            line,
            col,
            flags,
            value,
            up,
            parts,
        });
    }
    let n = d.u32()? as usize;
    let mut calls = Vec::with_capacity(n);
    for _ in 0..n {
        let scope = d.i32()?;
        let line = d.u32()?;
        let col = d.u32()?;
        let flags = d.u32()?;
        let callee = d.text()?;
        let argument = match d.u8()? {
            0 => Argument::Missing,
            1 => Argument::Literal(d.text()?),
            2 => Argument::Name(d.text()?),
            3 => Argument::Opaque(d.text()?),
            _ => Argument::Expr,
        };
        calls.push(CallRow {
            scope,
            line,
            col,
            flags,
            callee,
            argument,
        });
    }
    let n = d.u32()? as usize;
    let mut uses = Vec::with_capacity(n);
    for _ in 0..n {
        uses.push(UseRow {
            scope: d.i32()?,
            header: d.i32()?,
            root: d.sid()?,
            attrs: d.sid()?,
            count: d.u32()?,
            line: d.u32()?,
            col: d.u32()?,
        });
    }
    let callees = d.counts()?;
    let names = d.counts()?;
    let n = d.u32()? as usize;
    let mut tags = Vec::with_capacity(n);
    for _ in 0..n {
        tags.push(TagRow {
            scope: d.i32()?,
            header: d.i32()?,
            line: d.u32()?,
            col: d.u32()?,
            flags: d.u32()?,
            name: d.sid()?,
        });
    }
    Ok(FileFacts {
        path: path.into(),
        status,
        imports,
        defs,
        assigns,
        refs,
        strs,
        calls,
        uses,
        callees,
        names,
        tags,
        line: line_start..line_start + json_len,
        keys,
    })
}
