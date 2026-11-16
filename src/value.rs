//! Value trees (V) and shallow values (SV).
//!
//! Each struct serializes to one JSON object form. Fields are declared in key order, because
//! records sort object keys by code point.

use crate::source::parse_py314;
use ruff_python_ast::{self as ast, BoolOp, CmpOp, Expr, Number, Operator, UnaryOp};
use ruff_python_parser::Mode;
use ruff_text_size::Ranged;
use serde::Serialize;

pub const MAX_STR: usize = 100;
/// Element budget for assignment values and branch tests.
pub const MAX_NODES: usize = 1_000_000;
/// The same budget for header slots: decorators, bases, class keywords, annotations, defaults.
pub const MAX_HEADER_NODES: usize = 64;
const MAX_DEPTH: usize = 40;

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(untagged)]
pub enum Value {
    Null,
    Bool(bool),
    Int(i64),
    /// A Name, or an attribute chain rooted at a Name: `a.b.c`.
    Chain(String),
    Str(StrConst),
    Opaque(Opaque),
    BigCall(BigCall),
    Attr(Attr),
    Call(Call),
    Sub(Sub),
    List(List),
    Tuple(Tuple),
    Set(Set),
    Dict(Dict),
    Star(Star),
    Bin(Bin),
    BoolOp(BoolOps),
    Unary(Unary),
    Compare(Compare),
}

/// `{"s": text}`; in a type-expression slot also `"sa"`: the parsed text, or null.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct StrConst {
    pub s: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub sa: Option<Box<Value>>,
}

/// `{"o": what}`: a constant or node the tree does not spell out (`float`, `longstr`, `Lambda`).
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Opaque {
    pub o: &'static str,
}

/// A call over the element budget keeps its callee: `{"call": V, "o": "big"}`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct BigCall {
    pub call: Box<Value>,
    pub o: &'static str,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Attr {
    pub a: String,
    pub v: Box<Value>,
}

/// A keyword argument: `[name | null, value]`. A null name is `**x`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Keyword(pub Option<String>, pub Value);

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Call {
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub args: Vec<Value>,
    pub call: Box<Value>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub kw: Vec<Keyword>,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Sub {
    pub i: Box<Value>,
    pub sub: Box<Value>,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct List {
    pub list: Vec<Value>,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Tuple {
    pub tuple: Vec<Value>,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Set {
    pub set: Vec<Value>,
}

/// Dict items as `[key, value]`. A null key is `**x`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Dict {
    pub dict: Vec<(Option<Value>, Value)>,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Star {
    pub star: Box<Value>,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Bin {
    pub bin: &'static str,
    pub l: Box<Value>,
    pub r: Box<Value>,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct BoolOps {
    #[serde(rename = "bool")]
    pub op: &'static str,
    pub vs: Vec<Value>,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Unary {
    pub un: &'static str,
    pub v: Box<Value>,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Compare {
    pub cmp: Box<Value>,
    pub cs: Vec<Value>,
    pub ops: Vec<&'static str>,
}

impl Value {
    pub fn opaque(what: &'static str) -> Value {
        Value::Opaque(Opaque { o: what })
    }

    pub fn is_null(&self) -> bool {
        matches!(self, Value::Null)
    }

    /// Optional def keys are left out when null, false or 0.
    pub fn is_blank(&self) -> bool {
        matches!(self, Value::Null | Value::Bool(false) | Value::Int(0))
    }
}

pub fn binop(op: Operator) -> &'static str {
    match op {
        Operator::Add => "+",
        Operator::Sub => "-",
        Operator::Mult => "*",
        Operator::MatMult => "@",
        Operator::Div => "/",
        Operator::FloorDiv => "//",
        Operator::Mod => "%",
        Operator::Pow => "**",
        Operator::LShift => "<<",
        Operator::RShift => ">>",
        Operator::BitOr => "|",
        Operator::BitXor => "^",
        Operator::BitAnd => "&",
    }
}

pub fn unaryop(op: UnaryOp) -> &'static str {
    match op {
        UnaryOp::Not => "not",
        UnaryOp::USub => "-",
        UnaryOp::UAdd => "+",
        UnaryOp::Invert => "~",
    }
}

pub fn cmpop(op: CmpOp) -> &'static str {
    match op {
        CmpOp::Eq => "==",
        CmpOp::NotEq => "!=",
        CmpOp::Lt => "<",
        CmpOp::LtE => "<=",
        CmpOp::Gt => ">",
        CmpOp::GtE => ">=",
        CmpOp::Is => "is",
        CmpOp::IsNot => "isnot",
        CmpOp::In => "in",
        CmpOp::NotIn => "notin",
    }
}

pub fn boolop(op: BoolOp) -> &'static str {
    match op {
        BoolOp::And => "and",
        BoolOp::Or => "or",
    }
}

/// CPython `ast` class name of an expression.
pub fn class_name(expr: &Expr) -> &'static str {
    match expr {
        Expr::BoolOp(_) => "BoolOp",
        Expr::Named(_) => "NamedExpr",
        Expr::BinOp(_) => "BinOp",
        Expr::UnaryOp(_) => "UnaryOp",
        Expr::Lambda(_) => "Lambda",
        Expr::If(_) => "IfExp",
        Expr::Dict(_) => "Dict",
        Expr::Set(_) => "Set",
        Expr::ListComp(_) => "ListComp",
        Expr::SetComp(_) => "SetComp",
        Expr::DictComp(_) => "DictComp",
        Expr::Generator(_) => "GeneratorExp",
        Expr::Await(_) => "Await",
        Expr::Yield(_) => "Yield",
        Expr::YieldFrom(_) => "YieldFrom",
        Expr::Compare(_) => "Compare",
        Expr::Call(_) => "Call",
        Expr::FString(_) => "JoinedStr",
        Expr::TString(_) => "TemplateStr",
        Expr::StringLiteral(_)
        | Expr::BytesLiteral(_)
        | Expr::NumberLiteral(_)
        | Expr::BooleanLiteral(_)
        | Expr::NoneLiteral(_)
        | Expr::EllipsisLiteral(_) => "Constant",
        Expr::Attribute(_) => "Attribute",
        Expr::Subscript(_) => "Subscript",
        Expr::Starred(_) => "Starred",
        Expr::Name(_) => "Name",
        Expr::List(_) => "List",
        Expr::Tuple(_) => "Tuple",
        Expr::Slice(_) => "Slice",
        Expr::IpyEscapeCommand(_) => "IpyEscapeCommand",
    }
}

/// `a.b.c` for a Name or an Attribute chain rooted at a Name.
pub fn chain_text(expr: &Expr) -> Option<String> {
    match expr {
        Expr::Name(n) => Some(n.id.to_string()),
        Expr::Attribute(a) => {
            chain_text(&a.value).map(|base| format!("{base}.{}", a.attr.as_str()))
        }
        _ => None,
    }
}

pub fn callee_text(call: &ast::ExprCall) -> String {
    if let Some(text) = chain_text(&call.func) {
        return text;
    }
    if let Expr::Attribute(a) = call.func.as_ref() {
        return format!("?.{}", a.attr.as_str());
    }
    "?".to_string()
}

pub fn keyword_name(keyword: &ast::Keyword) -> Option<String> {
    keyword.arg.as_ref().map(|a| a.to_string())
}

/// True when a `\u` / `\U` escape in a non-raw str part names a surrogate. CPython keeps the
/// lone surrogate in the value; ruff replaces it with U+FFFD.
fn part_has_surrogate(part: &ast::StringLiteral, src: &str) -> bool {
    if !part.value.contains('\u{FFFD}') || part.flags.prefix().is_raw() {
        return false;
    }
    let bytes = src[part.range()].as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] != b'\\' {
            i += 1;
            continue;
        }
        let width = match bytes.get(i + 1) {
            Some(b'u') => 4,
            Some(b'U') => 8,
            _ => {
                i += 2;
                continue;
            }
        };
        let hex = bytes
            .get(i + 2..i + 2 + width)
            .and_then(|h| std::str::from_utf8(h).ok());
        if let Some(code) = hex.and_then(|h| u32::from_str_radix(h, 16).ok())
            && (0xD800..=0xDFFF).contains(&code)
        {
            return true;
        }
        i += 2;
    }
    false
}

/// A str constant as CPython sees it: the merged value and whether it holds a lone surrogate.
pub struct StrValue {
    pub text: String,
    pub surrogate: bool,
    /// Each implicitly concatenated part, with its own surrogate flag.
    pub parts: Vec<(String, bool)>,
}

impl StrValue {
    pub fn of(lit: &ast::ExprStringLiteral, src: &str) -> StrValue {
        let parts: Vec<(String, bool)> = lit
            .value
            .iter()
            .map(|p| (p.value.to_string(), part_has_surrogate(p, src)))
            .collect();
        StrValue {
            text: lit.value.to_str().to_string(),
            surrogate: parts.iter().any(|p| p.1),
            parts,
        }
    }

    pub fn constant(&self) -> Value {
        if self.surrogate {
            return Value::opaque("badstr");
        }
        if self.text.chars().count() > MAX_STR {
            return Value::opaque("longstr");
        }
        Value::Str(StrConst {
            s: self.text.clone(),
            sa: None,
        })
    }
}

/// Constants other than str; None when the expression is not one.
fn other_constant(expr: &Expr) -> Option<Value> {
    Some(match expr {
        Expr::NoneLiteral(_) => Value::Null,
        Expr::BooleanLiteral(b) => Value::Bool(b.value),
        Expr::NumberLiteral(n) => match &n.value {
            Number::Int(i) => match i.as_i64() {
                Some(v) => Value::Int(v),
                None => Value::opaque("int"),
            },
            Number::Float(_) => Value::opaque("float"),
            Number::Complex { .. } => Value::opaque("complex"),
        },
        Expr::BytesLiteral(_) => Value::opaque("bytes"),
        Expr::EllipsisLiteral(_) => Value::opaque("..."),
        _ => return None,
    })
}

/// The chains of a non-empty list or tuple whose elements are all names.
fn name_elements(elts: &[Expr]) -> Option<Vec<Value>> {
    if elts.is_empty() {
        return None;
    }
    elts.iter()
        .map(|e| chain_text(e).map(Value::Chain))
        .collect()
}

/// Builds value trees. The element count is per top-level value, so one encoder serves a file.
#[derive(Default)]
pub struct Encoder {
    count: usize,
}

impl Encoder {
    /// SV: a constant, a chain, a list or tuple of names (`[post_save, post_delete]`), else the
    /// node's class.
    pub fn shallow(&self, expr: &Expr, src: &str) -> Value {
        match expr {
            Expr::StringLiteral(s) => return StrValue::of(s, src).constant(),
            Expr::List(l) => {
                if let Some(list) = name_elements(&l.elts) {
                    return Value::List(List { list });
                }
            }
            Expr::Tuple(t) => {
                if let Some(tuple) = name_elements(&t.elts) {
                    return Value::Tuple(Tuple { tuple });
                }
            }
            _ => {}
        }
        if let Some(c) = other_constant(expr) {
            return c;
        }
        if let Some(text) = chain_text(expr) {
            return Value::Chain(text);
        }
        Value::opaque(class_name(expr))
    }

    pub fn shallow_args(&self, call: &ast::ExprCall, src: &str) -> Vec<Value> {
        call.arguments
            .args
            .iter()
            .map(|a| self.shallow(a, src))
            .collect()
    }

    pub fn shallow_keywords(&self, call: &ast::ExprCall, src: &str) -> Vec<Keyword> {
        call.arguments
            .keywords
            .iter()
            .map(|k| Keyword(keyword_name(k), self.shallow(&k.value, src)))
            .collect()
    }

    /// Decorator form: a chain, or a call with shallow arguments.
    pub fn shallow_call(&self, expr: &Expr, src: &str) -> Value {
        let Expr::Call(call) = expr else {
            return self.shallow(expr, src);
        };
        Value::Call(Call {
            args: self.shallow_args(call, src),
            call: Box::new(self.shallow(&call.func, src)),
            kw: self.shallow_keywords(call, src),
        })
    }

    /// One top-level value. `typed` marks a type-expression slot.
    pub fn value(&mut self, expr: Option<&Expr>, typed: bool, budget: usize, src: &str) -> Value {
        let Some(expr) = expr else {
            return Value::Null;
        };
        self.count = 0;
        let result = self.tree(expr, typed, 0, src);
        if self.count <= budget {
            return result;
        }
        if let Expr::Call(call) = expr {
            self.count = 0;
            return Value::BigCall(BigCall {
                call: Box::new(self.tree(&call.func, false, 1, src)),
                o: "big",
            });
        }
        Value::opaque("big")
    }

    fn type_string(&mut self, value: &StrValue) -> Value {
        let raw = value.constant();
        let Value::Str(StrConst { s, .. }) = raw else {
            return raw;
        };
        // CPython rejects leading whitespace (unexpected indent) and null bytes.
        let starts_blank = s.starts_with([' ', '\t', '\x0c']);
        let parsed = if starts_blank || s.contains('\0') {
            None
        } else {
            Some(parse_py314(&s, Mode::Expression))
        };
        let sa = match &parsed {
            Some(p) if !p.has_syntax_errors() => match p.syntax() {
                ast::Mod::Expression(e) => self.tree(&e.body, true, 1, &s),
                ast::Mod::Module(_) => Value::Null,
            },
            _ => Value::Null,
        };
        Value::Str(StrConst {
            s: s.clone(),
            sa: Some(Box::new(sa)),
        })
    }

    fn list(&mut self, items: &[Expr], typed: bool, depth: usize, src: &str) -> Vec<Value> {
        items
            .iter()
            .map(|e| self.tree(e, typed, depth, src))
            .collect()
    }

    fn boxed(&mut self, expr: &Expr, typed: bool, depth: usize, src: &str) -> Box<Value> {
        Box::new(self.tree(expr, typed, depth, src))
    }

    fn tree(&mut self, expr: &Expr, typed: bool, depth: usize, src: &str) -> Value {
        self.count += 1;
        if depth > MAX_DEPTH {
            return Value::opaque("deep");
        }
        let d = depth + 1;
        if let Expr::StringLiteral(s) = expr {
            let v = StrValue::of(s, src);
            if typed {
                return self.type_string(&v);
            }
            return v.constant();
        }
        // In a type expression `None` is the type None, so `-> None` differs from no annotation.
        if typed && expr.is_none_literal_expr() {
            return Value::Chain("None".to_string());
        }
        if let Some(c) = other_constant(expr) {
            return c;
        }
        if let Some(text) = chain_text(expr) {
            return Value::Chain(text);
        }
        match expr {
            Expr::Attribute(a) => Value::Attr(Attr {
                a: a.attr.to_string(),
                v: self.boxed(&a.value, typed, d, src),
            }),
            Expr::Call(call) => {
                let callee = self.boxed(&call.func, false, d, src);
                // A `TypeVar`'s constraints and bound are type expressions, a string one too.
                let last = match &*callee {
                    Value::Chain(name) => name.rsplit('.').next(),
                    _ => None,
                };
                let declares = last == Some("TypeVar");
                let mut args = Vec::new();
                for (at, arg) in call.arguments.args.iter().enumerate() {
                    args.push(self.tree(arg, declares && at > 0, d, src));
                }
                let mut kw = Vec::new();
                for k in call.arguments.keywords.iter() {
                    let name = keyword_name(k);
                    let typed = declares && name.as_deref() == Some("bound");
                    kw.push(Keyword(name, self.tree(&k.value, typed, d, src)));
                }
                Value::Call(Call {
                    args,
                    call: callee,
                    kw,
                })
            }
            Expr::Subscript(s) => {
                let sub = self.boxed(&s.value, typed, d, src);
                let i = self.boxed(&s.slice, true, d, src);
                Value::Sub(Sub { i, sub })
            }
            Expr::List(l) => Value::List(List {
                list: self.list(&l.elts, typed, d, src),
            }),
            Expr::Tuple(t) => Value::Tuple(Tuple {
                tuple: self.list(&t.elts, typed, d, src),
            }),
            Expr::Set(s) => Value::Set(Set {
                set: self.list(&s.elts, typed, d, src),
            }),
            Expr::Dict(dict) => {
                let mut items = Vec::new();
                for item in dict.items.iter() {
                    let key = item.key.as_ref().map(|k| self.tree(k, false, d, src));
                    items.push((key, self.tree(&item.value, false, d, src)));
                }
                Value::Dict(Dict { dict: items })
            }
            Expr::Starred(s) => Value::Star(Star {
                star: self.boxed(&s.value, typed, d, src),
            }),
            Expr::BinOp(b) => {
                let op = binop(b.op);
                let inner = typed || op == "|";
                let l = self.boxed(&b.left, inner, d, src);
                let r = self.boxed(&b.right, inner, d, src);
                Value::Bin(Bin { bin: op, l, r })
            }
            Expr::BoolOp(b) => Value::BoolOp(BoolOps {
                op: boolop(b.op),
                vs: self.list(&b.values, false, d, src),
            }),
            Expr::UnaryOp(u) => Value::Unary(Unary {
                un: unaryop(u.op),
                v: self.boxed(&u.operand, false, d, src),
            }),
            Expr::Compare(c) => {
                let cmp = self.boxed(&c.operands[0], false, d, src);
                Value::Compare(Compare {
                    cmp,
                    cs: self.list(&c.operands[1..], false, d, src),
                    ops: c.ops.iter().map(|o| cmpop(*o)).collect(),
                })
            }
            other => Value::opaque(class_name(other)),
        }
    }
}
