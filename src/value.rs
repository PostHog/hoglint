//! Value trees (V) and shallow values (SV), schema.md section 4.

use crate::json::J;
use ruff_python_ast::{self as ast, BoolOp, CmpOp, Expr, Number, Operator, UnaryOp};
use ruff_text_size::Ranged;

pub const MAX_STR: usize = 100;
const MAX_NODES: usize = 400;
const MAX_SYMBOLIC: usize = 100;

/// Caps that schema v1.1 raises (`--max-nodes`, `--max-symbolic`); set once at startup.
pub struct Limits {
    pub max_nodes: usize,
    pub max_symbolic: usize,
}

static LIMITS: std::sync::OnceLock<Limits> = std::sync::OnceLock::new();

pub fn set_limits(limits: Limits) {
    LIMITS.set(limits).ok().expect("limits set twice");
}

fn limits() -> &'static Limits {
    LIMITS.get_or_init(|| Limits { max_nodes: MAX_NODES, max_symbolic: MAX_SYMBOLIC })
}

/// Element budget for assignment values and branch tests (header slots keep MAX_HEADER_NODES).
pub fn max_nodes() -> usize {
    limits().max_nodes
}

/// Longest literal that still counts as symbolic for `strs` and `names`.
pub fn max_symbolic() -> usize {
    limits().max_symbolic
}
pub const MAX_HEADER_NODES: usize = 64;
const MAX_DEPTH: usize = 40;

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
        Expr::Attribute(a) => chain_text(&a.value).map(|base| format!("{base}.{}", a.attr.as_str())),
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
        let hex = bytes.get(i + 2..i + 2 + width).and_then(|h| std::str::from_utf8(h).ok());
        if let Some(code) = hex.and_then(|h| u32::from_str_radix(h, 16).ok()) {
            if (0xD800..=0xDFFF).contains(&code) {
                return true;
            }
        }
        i += 2;
    }
    false
}

/// A str constant as CPython sees it: the merged value and whether it holds a lone surrogate.
pub struct StrValue {
    pub text: String,
    pub surrogate: bool,
    pub parts: Vec<(String, bool)>,
}

impl StrValue {
    pub fn of(lit: &ast::ExprStringLiteral, src: &str) -> StrValue {
        let parts: Vec<(String, bool)> =
            lit.value.iter().map(|p| (p.value.to_string(), part_has_surrogate(p, src))).collect();
        StrValue { text: lit.value.to_str().to_string(), surrogate: parts.iter().any(|p| p.1), parts }
    }
}

pub fn str_constant(text: &str, surrogate: bool) -> J {
    if surrogate {
        return J::o("badstr");
    }
    if text.chars().count() <= MAX_STR {
        J::obj(vec![("s", J::s(text))])
    } else {
        J::o("longstr")
    }
}

/// Constants other than str; None when the expression is not a constant.
fn other_constant(expr: &Expr) -> Option<J> {
    Some(match expr {
        Expr::NoneLiteral(_) => J::Null,
        Expr::BooleanLiteral(b) => J::Bool(b.value),
        Expr::NumberLiteral(n) => match &n.value {
            Number::Int(i) => match i.as_i64() {
                Some(v) => J::Int(v),
                None => J::o("int"),
            },
            Number::Float(_) => J::o("float"),
            Number::Complex { .. } => J::o("complex"),
        },
        Expr::BytesLiteral(_) => J::o("bytes"),
        Expr::EllipsisLiteral(_) => J::o("..."),
        _ => return None,
    })
}

pub struct Encoder {
    count: usize,
}

impl Encoder {
    pub fn new() -> Encoder {
        Encoder { count: 0 }
    }

    pub fn shallow(&self, expr: &Expr, src: &str) -> J {
        if let Expr::StringLiteral(s) = expr {
            let v = StrValue::of(s, src);
            return str_constant(&v.text, v.surrogate);
        }
        if let Some(c) = other_constant(expr) {
            return c;
        }
        if let Some(text) = chain_text(expr) {
            return J::Str(text);
        }
        J::o(class_name(expr))
    }

    /// Decorator form: a chain, or a call with shallow arguments.
    pub fn shallow_call(&self, expr: &Expr, src: &str) -> J {
        let Expr::Call(call) = expr else {
            return self.shallow(expr, src);
        };
        let mut pairs = vec![("call", self.shallow(&call.func, src))];
        if !call.arguments.args.is_empty() {
            pairs.push(("args", J::Arr(call.arguments.args.iter().map(|a| self.shallow(a, src)).collect())));
        }
        if !call.arguments.keywords.is_empty() {
            let kws = call
                .arguments
                .keywords
                .iter()
                .map(|k| J::Arr(vec![J::opt_s(k.arg.as_ref().map(|a| a.as_str())), self.shallow(&k.value, src)]))
                .collect();
            pairs.push(("kw", J::Arr(kws)));
        }
        J::obj(pairs)
    }

    /// One top-level value. `typed` marks a type-expression slot.
    pub fn value(&mut self, expr: Option<&Expr>, typed: bool, budget: usize, src: &str) -> J {
        let Some(expr) = expr else {
            return J::Null;
        };
        self.count = 0;
        let result = self.tree(expr, typed, 0, src);
        if self.count <= budget {
            return result;
        }
        if let Expr::Call(call) = expr {
            self.count = 0;
            return J::obj(vec![("call", self.tree(&call.func, false, 1, src)), ("o", J::s("big"))]);
        }
        J::o("big")
    }

    fn type_string(&mut self, value: &StrValue) -> J {
        let raw = str_constant(&value.text, value.surrogate);
        if !matches!(&raw, J::Obj(p) if p[0].0 == "s") {
            return raw;
        }
        let text = value.text.as_str();
        // CPython rejects leading whitespace (unexpected indent) and null bytes.
        let starts_blank = text.starts_with([' ', '\t', '\x0c']);
        let parsed = if starts_blank || text.contains('\0') {
            None
        } else {
            Some(crate::record::parse_py314(text, ruff_python_parser::Mode::Expression))
        };
        let sa = match &parsed {
            Some(p) if !p.has_syntax_errors() => match p.syntax() {
                ast::Mod::Expression(e) => self.tree(&e.body, true, 1, text),
                ast::Mod::Module(_) => J::Null,
            },
            _ => J::Null,
        };
        J::obj(vec![("s", J::s(text)), ("sa", sa)])
    }

    fn list(&mut self, items: &[Expr], typed: bool, depth: usize, src: &str) -> J {
        J::Arr(items.iter().map(|e| self.tree(e, typed, depth, src)).collect())
    }

    pub fn tree(&mut self, expr: &Expr, typed: bool, depth: usize, src: &str) -> J {
        self.count += 1;
        if depth > MAX_DEPTH {
            return J::o("deep");
        }
        let d = depth + 1;
        if let Expr::StringLiteral(s) = expr {
            let v = StrValue::of(s, src);
            if typed {
                return self.type_string(&v);
            }
            return str_constant(&v.text, v.surrogate);
        }
        if let Some(c) = other_constant(expr) {
            return c;
        }
        if let Some(text) = chain_text(expr) {
            return J::Str(text);
        }
        match expr {
            Expr::Attribute(a) => {
                J::obj(vec![("a", J::s(a.attr.as_str())), ("v", self.tree(&a.value, typed, d, src))])
            }
            Expr::Call(call) => {
                let mut pairs = vec![("call", self.tree(&call.func, false, d, src))];
                if !call.arguments.args.is_empty() {
                    pairs.push(("args", self.list(&call.arguments.args, false, d, src)));
                }
                if !call.arguments.keywords.is_empty() {
                    let mut kws = Vec::new();
                    for k in call.arguments.keywords.iter() {
                        let name = J::opt_s(k.arg.as_ref().map(|a| a.as_str()));
                        kws.push(J::Arr(vec![name, self.tree(&k.value, false, d, src)]));
                    }
                    pairs.push(("kw", J::Arr(kws)));
                }
                J::obj(pairs)
            }
            Expr::Subscript(s) => J::obj(vec![
                ("sub", self.tree(&s.value, typed, d, src)),
                ("i", self.tree(&s.slice, true, d, src)),
            ]),
            Expr::List(l) => J::obj(vec![("list", self.list(&l.elts, typed, d, src))]),
            Expr::Tuple(t) => J::obj(vec![("tuple", self.list(&t.elts, typed, d, src))]),
            Expr::Set(s) => J::obj(vec![("set", self.list(&s.elts, typed, d, src))]),
            Expr::Dict(dict) => {
                let mut items = Vec::new();
                for item in dict.items.iter() {
                    let key = match &item.key {
                        Some(k) => self.tree(k, false, d, src),
                        None => J::Null,
                    };
                    items.push(J::Arr(vec![key, self.tree(&item.value, false, d, src)]));
                }
                J::obj(vec![("dict", J::Arr(items))])
            }
            Expr::Starred(s) => J::obj(vec![("star", self.tree(&s.value, typed, d, src))]),
            Expr::BinOp(b) => {
                let op = binop(b.op);
                let inner = typed || op == "|";
                J::obj(vec![
                    ("bin", J::s(op)),
                    ("l", self.tree(&b.left, inner, d, src)),
                    ("r", self.tree(&b.right, inner, d, src)),
                ])
            }
            Expr::BoolOp(b) => {
                J::obj(vec![("bool", J::s(boolop(b.op))), ("vs", self.list(&b.values, false, d, src))])
            }
            Expr::UnaryOp(u) => {
                J::obj(vec![("un", J::s(unaryop(u.op))), ("v", self.tree(&u.operand, false, d, src))])
            }
            Expr::Compare(c) => {
                let left = self.tree(&c.operands[0], false, d, src);
                let ops = J::Arr(c.ops.iter().map(|o| J::s(cmpop(*o))).collect());
                let cs = self.list(&c.operands[1..], false, d, src);
                J::obj(vec![("cmp", left), ("ops", ops), ("cs", cs)])
            }
            other => J::o(class_name(other)),
        }
    }
}
