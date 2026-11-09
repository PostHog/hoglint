//! Text rules for string literals and chains (schema.md section 5, `strs` and `uses`).

/// Longest literal, in code points, that still counts as symbolic for `strs` and `names`.
const MAX_SYMBOLIC: usize = 1_000;

fn is_word_char(ch: char) -> bool {
    !ch.is_ascii() || ch.is_ascii_alphanumeric() || ch == '_'
}

/// At least two dot-separated parts, each non-empty, of word chars or `*`.
pub fn is_dotted_path(value: &str) -> bool {
    let mut parts = 0;
    for part in value.split('.') {
        if part.is_empty() || !part.chars().all(|c| is_word_char(c) || c == '*') {
            return false;
        }
        parts += 1;
    }
    parts >= 2
}

fn is_camel_name(value: &str) -> bool {
    value.starts_with(|c: char| c.is_ascii_uppercase()) && value.chars().all(is_word_char)
}

/// A dotted path or a CamelCase name, at most `MAX_SYMBOLIC` code points, without surrogates.
pub fn is_symbolic(value: &str, surrogate: bool) -> bool {
    if value.is_empty() || surrogate || value.chars().count() > MAX_SYMBOLIC {
        return false;
    }
    is_dotted_path(value) || is_camel_name(value)
}

/// Matches of `<([A-Z]\w*)\b` (Python `re`, Unicode `\w`).
pub fn tag_names(value: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut rest = value;
    while let Some(at) = rest.find('<') {
        let after = &rest[at + 1..];
        if !after.starts_with(|c: char| c.is_ascii_uppercase()) {
            rest = after;
            continue;
        }
        let end = after
            .find(|c: char| !(c.is_alphanumeric() || c == '_'))
            .unwrap_or(after.len());
        out.push(&after[..end]);
        rest = &after[end..];
    }
    out
}

/// `[A-Za-z_][A-Za-z0-9_]*`.
pub fn is_identifier(value: &str) -> bool {
    let mut chars = value.chars();
    matches!(chars.next(), Some(c) if c.is_ascii_alphabetic() || c == '_')
        && chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
}

/// `(\.\w+)*` at the start of a chain.
pub fn leading_attrs(chain: &str) -> &str {
    let bytes = chain.as_bytes();
    let mut end = 0;
    let mut i = 0;
    while i < bytes.len() && bytes[i] == b'.' {
        let rest = &chain[i + 1..];
        let len = rest
            .find(|c: char| !(c.is_alphanumeric() || c == '_'))
            .unwrap_or(rest.len());
        if len == 0 {
            break;
        }
        i += 1 + len;
        end = i;
    }
    &chain[..end]
}
