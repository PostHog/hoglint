//! One file's bytes to its L1 record (reference `FileRecord`).

use crate::config::{ExtractConfig, Kind};
use crate::defs::{DefNode, DefTable, is_stub_body, parameter_kinds, stored_names};
use crate::derive::Derivation;
use crate::facts::flags::CLASS;
use crate::facts::{
    Assign, Count, Def, DefKind, Facts, FileRecord, Param, Ref, Status, TextKey, TypeParam,
};
use crate::loops::Sequences;
use crate::self_attrs::self_attrs;
use crate::source::{LineIndex, parse_py314, source_text};
use crate::value::{Encoder, Keyword, MAX_HEADER_NODES, Value, keyword_name};
use crate::walker::{Counts, TypedDictClass, Walk, Walker};
use ruff_python_ast::{self as ast, Expr, Parameters};
use ruff_python_parser::Mode;
use std::collections::HashSet;

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

/// The use reading's inventory of one file's bytes (`Walker::inventory`), for the test against
/// CPython's symbol table; None when the file does not parse.
pub fn inventory(raw: &[u8]) -> Option<Vec<(String, String)>> {
    let text = source_text(raw).ok()?;
    let parsed = parse_py314(&text, Mode::Module);
    if parsed.has_syntax_errors() {
        return None;
    }
    let module = parsed.syntax().as_module()?;
    let config = ExtractConfig::new(Vec::new());
    let lines = LineIndex::new(&text);
    let (defs, bound) = DefTable::build(&module.body, &text, &lines, &config);
    let stored = stored_names(&module.body);
    let sequences = Sequences::of(&[], &defs);
    let walker = Walker::new(&text, &lines, &defs, &bound, &stored, &sequences, &config);
    Some(walker.inventory(&module.body))
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
        let kinds = self.config.kinds;
        let (defs, bound) = DefTable::build(suite, self.src, &self.lines, self.config);
        // Derived names read stores; escapes and computed rows tell a builtin from a name the file
        // binds itself.
        let stored = if kinds.any(&[Kind::Derived, Kind::Escapes, Kind::Computed]) {
            stored_names(suite)
        } else {
            HashSet::new()
        };
        let sequences = Sequences::of(if kinds.has(Kind::Calls) { suite } else { &[] }, &defs);
        let walker = Walker::new(
            self.src,
            &self.lines,
            &defs,
            &bound,
            &stored,
            &sequences,
            self.config,
        );
        let mut walk = walker.run(suite);
        let derived = if kinds.has(Kind::Derived) {
            let candidates = std::mem::take(&mut walk.candidates);
            Derivation::new(&defs).resolve(&walk.refs, candidates)
        } else {
            Vec::new()
        };
        let mut def_rows: Vec<Def> = if kinds.has(Kind::Defs) {
            (0..defs.entries.len())
                .map(|idx| self.def_record(&defs, &walk.def_flags, idx))
                .collect()
        } else {
            Vec::new()
        };
        for declared in std::mem::take(&mut walk.typed_dicts) {
            Self::declare_typed_dict(declared, &mut def_rows, &mut walk.assigns);
        }
        let mut facts = Self::facts_of(walk, def_rows, derived);
        facts.keep(kinds);
        facts.sort();
        facts
    }

    /// A functional `TypedDict` as the class form records it: a class def after the file's own
    /// defs, so their scope ids stay, and one annotated field per key in its body.
    fn declare_typed_dict(
        declared: TypedDictClass,
        defs: &mut Vec<Def>,
        assigns: &mut Vec<Assign>,
    ) {
        let scope = defs.len() as i64;
        for (name, ann, l, c) in declared.fields {
            assigns.push(Assign {
                ann: Some(ann),
                c,
                f: declared.flags | CLASS,
                l,
                op: ":".to_string(),
                s: scope,
                t: vec![Value::Chain(name)],
                v: Value::Null,
            });
        }
        defs.push(Def {
            attrs: Vec::new(),
            bases: vec![declared.base],
            c: declared.c,
            dec: Vec::new(),
            e: declared.e,
            f: declared.flags,
            k: DefKind::Class,
            kw: declared.kw,
            l: declared.l,
            n: declared.name,
            params: Vec::new(),
            ret: Value::Null,
            s: declared.scope,
            stub: false,
            tp: Vec::new(),
        });
    }

    fn facts_of(walk: Walk, defs: Vec<Def>, derived: Vec<Ref>) -> Facts {
        Facts {
            assigns: walk.assigns,
            branches: walk.branches,
            callees: counts(walk.callees),
            calls: walk.calls,
            computed: walk.computed,
            defs,
            derived,
            imports: walk.imports,
            listed: listed_rows(walk.loads, walk.listed),
            names: counts(walk.names),
            refs: walk.refs,
            strs: walk.strs,
            tags: walk.tags,
            uses: counts(walk.uses),
            watched: walk.watched,
            escapes: walk.escapes,
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

    /// The PEP 695 type parameters, each with its bound or its tuple of constraints.
    fn type_params(&self, enc: &mut Encoder, params: Option<&ast::TypeParams>) -> Vec<TypeParam> {
        let Some(params) = params else {
            return Vec::new();
        };
        params
            .type_params
            .iter()
            .map(|param| {
                let (name, bound) = match param {
                    ast::TypeParam::TypeVar(t) => (&t.name, t.bound.as_deref()),
                    ast::TypeParam::ParamSpec(t) => (&t.name, None),
                    ast::TypeParam::TypeVarTuple(t) => (&t.name, None),
                };
                TypeParam {
                    name: name.to_string(),
                    bound: self.header_value(enc, bound, true),
                }
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
            attrs: Vec::new(),
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
            tp: Vec::new(),
        };
        def.tp = self.type_params(&mut enc, entry.node.type_params());
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
                if defs.is_class(entry.parent) {
                    def.attrs = self_attrs(f, self.src, &self.lines);
                }
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

/// The `listed` rows: the names whose every load sits in a literal.
fn listed_rows(loads: Counts<TextKey>, listed: Counts<TextKey>) -> Vec<Count<TextKey>> {
    counts(listed)
        .into_iter()
        .filter(|row| {
            loads
                .get(&row.key)
                .is_some_and(|(all, _, _)| *all == row.count)
        })
        .collect()
}

fn counts<K>(table: Counts<K>) -> Vec<Count<K>> {
    table
        .into_iter()
        .map(|(key, (count, l, c))| Count { key, count, l, c })
        .collect()
}
