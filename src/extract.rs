//! One file's bytes to its L1 record (reference `FileRecord`).

use crate::config::ExtractConfig;
use crate::defs::{DefNode, DefTable, is_stub_body, parameter_kinds, stored_names};
use crate::derive::Derivation;
use crate::facts::{Count, Def, DefKind, Facts, FileRecord, Param, SiteFlags, Status};
use crate::source::{LineIndex, parse_py314, source_text};
use crate::value::{Encoder, Keyword, MAX_HEADER_NODES, Value, keyword_name};
use crate::walker::{Counts, Walk, Walker};
use ruff_python_ast::{Expr, Parameters};
use ruff_python_parser::Mode;

/// Builds the record for one file's bytes and their `sha256_hex`. Never fails: a file CPython
/// rejects gets a status.
pub fn extract(raw: &[u8], sha256: String, config: &ExtractConfig) -> FileRecord {
    let bytes = raw.len();
    let failed = |status: Status| FileRecord {
        sha256: sha256.clone(),
        bytes,
        status,
        facts: Facts::default(),
    };
    let text = match source_text(raw) {
        Ok(text) => text,
        Err(status) => return failed(status),
    };
    let parsed = parse_py314(&text, Mode::Module);
    if parsed.has_syntax_errors() {
        return failed(Status::SyntaxError);
    }
    let Some(module) = parsed.syntax().as_module() else {
        return failed(Status::SyntaxError);
    };
    let facts = FileFacts::new(&text, config).collect(&module.body);
    FileRecord {
        sha256,
        bytes,
        status: Status::Ok,
        facts,
    }
}

struct FileFacts<'s, 'c> {
    src: &'s str,
    lines: LineIndex,
    config: &'c ExtractConfig,
}

impl<'s, 'c> FileFacts<'s, 'c> {
    fn new(src: &'s str, config: &'c ExtractConfig) -> Self {
        FileFacts {
            src,
            lines: LineIndex::new(src),
            config,
        }
    }

    fn collect(&self, suite: &'s [ruff_python_ast::Stmt]) -> Facts {
        let (defs, bound) = DefTable::build(suite, self.src, &self.lines, self.config);
        let stored = stored_names(suite);
        let walker = Walker::new(self.src, &self.lines, &defs, &bound, &stored, self.config);
        let mut walk = walker.run(suite);
        let candidates = std::mem::take(&mut walk.candidates);
        for derived in Derivation::new(&defs).resolve(&walk.refs, candidates) {
            walk.flag_site(derived.row.site.l, derived.row.site.c, derived.flags);
            walk.refs.push(derived.row);
        }
        let def_rows = (0..defs.entries.len())
            .map(|idx| self.def_record(&defs, &walk.def_flags, idx))
            .collect();
        let mut facts = Self::facts_of(walk, def_rows);
        facts.sort();
        facts
    }

    fn facts_of(walk: Walk, defs: Vec<Def>) -> Facts {
        let site_flags = walk
            .site_flags
            .into_iter()
            .map(|((l, c), flags)| SiteFlags { l, c, flags })
            .collect();
        Facts {
            assigns: walk.assigns,
            branches: walk.branches,
            callees: counts(walk.callees),
            calls: walk.calls,
            defs,
            imports: walk.imports,
            names: counts(walk.names),
            refs: walk.refs,
            site_flags,
            strs: walk.strs,
            tags: walk.tags,
            uses: counts(walk.uses),
            watched: walk.watched,
        }
    }

    fn header_value(&self, enc: &mut Encoder, expr: Option<&Expr>, typed: bool) -> Value {
        enc.value(expr, typed, MAX_HEADER_NODES, self.src)
    }

    fn params(&self, enc: &mut Encoder, p: &Parameters) -> Vec<Param> {
        parameter_kinds(p)
            .map(|(param, kind)| Param {
                name: param.name().to_string(),
                kind,
                annotation: self.header_value(enc, param.annotation(), true),
                default: self.default_value(enc, param.default()),
            })
            .collect()
    }

    fn default_value(&self, enc: &Encoder, default: Option<&Expr>) -> Value {
        default
            .map(|d| enc.shallow(d, self.src))
            .unwrap_or(Value::Null)
    }

    fn def_record(&self, defs: &DefTable, flags: &[u32], idx: usize) -> Def {
        let entry = &defs.entries[idx];
        let mut enc = Encoder::default();
        let mut def = Def {
            bases: Vec::new(),
            c: entry.c,
            dec: entry
                .node
                .decorators()
                .iter()
                .map(|d| enc.shallow_call(&d.expression, self.src))
                .collect(),
            e: entry.e,
            f: flags[idx],
            k: DefKind::Class,
            kw: Vec::new(),
            l: entry.l,
            n: String::new(),
            params: Vec::new(),
            ret: Value::Null,
            s: entry.parent,
            stub: is_stub_body(entry.node.body()),
        };
        match entry.node {
            DefNode::Func(f) => {
                def.n = f.name.to_string();
                def.k = if f.is_async {
                    DefKind::Async
                } else {
                    DefKind::Def
                };
                def.params = self.params(&mut enc, &f.parameters);
                def.ret = self.header_value(&mut enc, f.returns.as_deref(), true);
            }
            DefNode::Class(c) => {
                def.n = c.name.to_string();
                if let Some(arguments) = &c.arguments {
                    for base in arguments.args.iter() {
                        def.bases
                            .push(self.header_value(&mut enc, Some(base), false));
                    }
                    for k in arguments.keywords.iter() {
                        let value = self.header_value(&mut enc, Some(&k.value), false);
                        def.kw.push(Keyword(keyword_name(k), value));
                    }
                }
            }
        }
        def
    }
}

fn counts<K>(table: Counts<K>) -> Vec<Count<K>> {
    table
        .into_iter()
        .map(|(key, (count, l, c))| Count { key, count, l, c })
        .collect()
}
