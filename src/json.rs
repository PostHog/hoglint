//! Minimal JSON value with the reference encoding: compact separators, no ASCII escaping,
//! object keys sorted by code point (`json.dumps(..., ensure_ascii=False, sort_keys=True)`).

pub enum J {
    Null,
    Bool(bool),
    Int(i64),
    Str(String),
    Arr(Vec<J>),
    Obj(Vec<(&'static str, J)>),
}

impl J {
    pub fn s(value: &str) -> J {
        J::Str(value.to_string())
    }

    pub fn opt_s(value: Option<&str>) -> J {
        match value {
            Some(v) => J::s(v),
            None => J::Null,
        }
    }

    /// Builds an object; keys are sorted here so callers can list them in any order.
    pub fn obj(mut pairs: Vec<(&'static str, J)>) -> J {
        pairs.sort_by(|a, b| a.0.cmp(b.0));
        J::Obj(pairs)
    }

    pub fn o(class: &str) -> J {
        J::Obj(vec![("o", J::s(class))])
    }

    pub fn is_null(&self) -> bool {
        matches!(self, J::Null)
    }

    pub fn to_json(&self) -> String {
        let mut out = String::new();
        self.write(&mut out);
        out
    }

    pub fn write(&self, out: &mut String) {
        match self {
            J::Null => out.push_str("null"),
            J::Bool(true) => out.push_str("true"),
            J::Bool(false) => out.push_str("false"),
            J::Int(i) => out.push_str(&i.to_string()),
            J::Str(s) => write_str(out, s),
            J::Arr(items) => {
                out.push('[');
                for (i, item) in items.iter().enumerate() {
                    if i > 0 {
                        out.push(',');
                    }
                    item.write(out);
                }
                out.push(']');
            }
            J::Obj(pairs) => {
                out.push('{');
                for (i, (key, value)) in pairs.iter().enumerate() {
                    if i > 0 {
                        out.push(',');
                    }
                    write_str(out, key);
                    out.push(':');
                    value.write(out);
                }
                out.push('}');
            }
        }
    }
}

pub fn write_str(out: &mut String, s: &str) {
    out.push('"');
    for ch in s.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
}
