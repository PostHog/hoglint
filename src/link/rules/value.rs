//! Three-valued truth and selector values (hoglint/selectors.py): the comparisons a selector
//! makes, and how a value renders as an identity column.

use crate::link::scope::CONTEXT_NAMES;
use std::borrow::Cow;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Truth {
    False,
    True,
    Unknown,
}

impl Truth {
    pub fn of(value: bool) -> Truth {
        if value { Truth::True } else { Truth::False }
    }

    pub fn negate(self) -> Truth {
        match self {
            Truth::False => Truth::True,
            Truth::True => Truth::False,
            Truth::Unknown => Truth::Unknown,
        }
    }
}

/// Kleene `and`: false wins, then unknown.
pub fn all_of(parts: impl IntoIterator<Item = Truth>) -> Truth {
    let mut out = Truth::True;
    for part in parts {
        match part {
            Truth::False => return Truth::False,
            Truth::Unknown => out = Truth::Unknown,
            Truth::True => {}
        }
    }
    out
}

/// Kleene `or`: true wins, then unknown.
pub fn any_of(parts: impl IntoIterator<Item = Truth>) -> Truth {
    let mut out = Truth::False;
    for part in parts {
        match part {
            Truth::True => return Truth::True,
            Truth::Unknown => out = Truth::Unknown,
            Truth::False => {}
        }
    }
    out
}

/// A zone and its ancestors, nearest first.
#[derive(Debug)]
pub struct Zone {
    pub name: String,
    pub chain: Vec<String>,
}

/// One resolved operand (`selectors.Value`).
#[derive(Clone, Debug)]
pub enum Value<'a> {
    Unknown,
    None,
    Text(Cow<'a, str>),
    Bool(bool),
    /// A frozenset of strings.
    Set(Vec<&'a str>),
    /// An edge's contexts: a frozenset of context names, as bits.
    Contexts(u8),
    /// A zone: its name, compared by ancestry against a plain value.
    Zone(&'a Zone),
    /// A list literal: a tuple of strings.
    List(&'a [String]),
}

/// What `str()` of a frozenset gives: no label value ever spells it.
const SET_TEXT: &str = "\u{0}frozenset";

fn context_names(bits: u8) -> Vec<&'static str> {
    CONTEXT_NAMES
        .iter()
        .enumerate()
        .filter(|(bit, _)| bits & (1 << bit) != 0)
        .map(|(_, name)| *name)
        .collect()
}

/// Python's `repr` of a string.
pub fn py_repr(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::new();
    out.push(quote);
    for c in text.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || c as u32 == 0x7f => {
                out.push_str(&format!("\\x{:02x}", c as u32))
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

/// Python's `str()` of a tuple of strings.
fn tuple_text(items: &[String]) -> String {
    let parts: Vec<String> = items.iter().map(|item| py_repr(item)).collect();
    match parts.len() {
        1 => format!("({},)", parts[0]),
        _ => format!("({})", parts.join(", ")),
    }
}

impl<'a> Value<'a> {
    pub fn text(text: &'a str) -> Value<'a> {
        Value::Text(Cow::Borrowed(text))
    }

    /// A known string, or `none` when it is empty: how edge fields and endpoint names read.
    pub fn text_or_none(text: &'a str) -> Value<'a> {
        if text.is_empty() {
            Value::None
        } else {
            Value::text(text)
        }
    }

    pub fn is_unknown(&self) -> bool {
        matches!(self, Value::Unknown)
    }

    fn is_none(&self) -> bool {
        matches!(self, Value::None)
    }

    /// `isinstance(data, frozenset)`.
    fn is_frozenset(&self) -> bool {
        matches!(self, Value::Set(_) | Value::Contexts(_))
    }

    fn chain(&self) -> Option<&[String]> {
        match self {
            Value::Zone(zone) => Some(&zone.chain),
            _ => None,
        }
    }

    /// `selectors.scalar`: a bool as true/false, anything else as `str()`.
    fn scalar(&self) -> Cow<'_, str> {
        match self {
            Value::Text(text) => Cow::Borrowed(text),
            Value::Bool(true) => Cow::Borrowed("true"),
            Value::Bool(false) => Cow::Borrowed("false"),
            Value::Zone(zone) => Cow::Borrowed(&zone.name),
            Value::List(items) => Cow::Owned(tuple_text(items)),
            Value::Set(_) | Value::Contexts(_) => Cow::Borrowed(SET_TEXT),
            Value::Unknown | Value::None => Cow::Borrowed("None"),
        }
    }

    /// `selectors.members`: a set's items, a zone and its ancestors, else the value itself.
    fn members(&self) -> Vec<Cow<'_, str>> {
        match self {
            Value::Zone(zone) => zone
                .chain
                .iter()
                .map(|item| Cow::Borrowed(item.as_str()))
                .collect(),
            Value::Set(items) => items.iter().map(|item| Cow::Borrowed(*item)).collect(),
            Value::List(items) => items
                .iter()
                .map(|item| Cow::Borrowed(item.as_str()))
                .collect(),
            Value::Contexts(bits) => context_names(*bits)
                .into_iter()
                .map(Cow::Borrowed)
                .collect(),
            other => vec![other.scalar()],
        }
    }

    /// `selectors.set_members`: `subset_of` reads a plain string as a comma-separated set.
    fn set_members(&self) -> Vec<Cow<'_, str>> {
        match self {
            Value::None => Vec::new(),
            Value::Text(text) => split_list(text).into_iter().map(Cow::Borrowed).collect(),
            other => other.members(),
        }
    }
}

/// A comma-separated list, as producer attrs and symbol facts carry sets.
pub fn split_list(raw: &str) -> Vec<&str> {
    raw.split(',')
        .map(str::trim)
        .filter(|item| !item.is_empty())
        .collect()
}

fn same_items(left: &[Cow<'_, str>], right: &[Cow<'_, str>]) -> bool {
    left.iter().all(|item| right.contains(item)) && right.iter().all(|item| left.contains(item))
}

pub fn equals(left: &Value, right: &Value) -> Truth {
    if left.is_unknown() || right.is_unknown() {
        return Truth::Unknown;
    }
    if left.is_none() || right.is_none() {
        return Truth::of(left.is_none() && right.is_none());
    }
    let collection = |value: &Value| value.is_frozenset() || matches!(value, Value::List(_));
    if collection(left) && collection(right) {
        return Truth::of(same_items(&left.members(), &right.members()));
    }
    match (left.chain(), right.chain()) {
        (Some(chain), None) => return Truth::of(chain.iter().any(|item| *item == right.scalar())),
        (None, Some(chain)) => return Truth::of(chain.iter().any(|item| *item == left.scalar())),
        _ => {}
    }
    if left.is_frozenset() {
        return Truth::of(left.members().contains(&right.scalar()));
    }
    if right.is_frozenset() {
        return Truth::of(right.members().contains(&left.scalar()));
    }
    Truth::of(left.scalar() == right.scalar())
}

pub fn contained(left: &Value, right: &Value) -> Truth {
    if left.is_unknown() || right.is_unknown() {
        return Truth::Unknown;
    }
    if left.is_none() || right.is_none() {
        return Truth::False;
    }
    let right = right.members();
    Truth::of(left.members().iter().any(|item| right.contains(item)))
}

pub fn subset(left: &Value, right: &Value) -> Truth {
    if left.is_unknown() || right.is_unknown() {
        return Truth::Unknown;
    }
    let right = right.set_members();
    Truth::of(left.set_members().iter().all(|item| right.contains(item)))
}

pub fn starts(left: &Value, right: &Value) -> Truth {
    if left.is_unknown() || right.is_unknown() {
        return Truth::Unknown;
    }
    if left.is_none() || right.is_none() {
        return Truth::False;
    }
    Truth::of(left.scalar().starts_with(right.scalar().as_ref()))
}

pub fn truthy(value: &Value) -> Truth {
    match value {
        Value::Unknown => Truth::Unknown,
        Value::None => Truth::False,
        Value::Text(text) => Truth::of(!text.is_empty()),
        Value::Bool(flag) => Truth::of(*flag),
        Value::Set(items) => Truth::of(!items.is_empty()),
        Value::List(items) => Truth::of(!items.is_empty()),
        Value::Contexts(_) => Truth::True,
        Value::Zone(zone) => Truth::of(!zone.name.is_empty()),
    }
}

/// `selectors.render_value`: one identity column, `?` unknown and `-` none.
pub fn render(value: &Value) -> String {
    let joined = |mut items: Vec<Cow<'_, str>>| {
        items.sort();
        items.dedup();
        if items.is_empty() {
            "-".to_string()
        } else {
            items.join(",")
        }
    };
    match value {
        Value::Unknown => "?".to_string(),
        Value::None => "-".to_string(),
        Value::Bool(flag) => if *flag { "true" } else { "false" }.to_string(),
        Value::Set(_) | Value::Contexts(_) => joined(value.members()),
        Value::List(items) => {
            let mut items: Vec<Cow<'_, str>> = items
                .iter()
                .map(|item| Cow::Borrowed(item.as_str()))
                .collect();
            items.sort();
            if items.is_empty() {
                "-".to_string()
            } else {
                items.join(",")
            }
        }
        Value::Text(text) => text.to_string(),
        Value::Zone(zone) => zone.name.clone(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn text(value: &str) -> Value<'_> {
        Value::text(value)
    }

    #[test]
    fn a_zone_matches_a_plain_value_by_ancestry_and_another_zone_by_name() {
        let facade = Zone {
            name: "facade".into(),
            chain: vec!["facade".into(), "backend".into()],
        };
        let backend = Zone {
            name: "backend".into(),
            chain: vec!["backend".into()],
        };
        assert_eq!(equals(&Value::Zone(&facade), &text("backend")), Truth::True);
        assert_eq!(
            equals(&Value::Zone(&facade), &Value::Zone(&backend)),
            Truth::False
        );
        let list = ["backend".to_string()];
        assert_eq!(
            contained(&Value::Zone(&facade), &Value::List(&list)),
            Truth::True
        );
    }

    #[test]
    fn a_set_matches_a_member_and_a_list_literal_only_its_repr() {
        assert_eq!(equals(&Value::Set(vec!["a", "b"]), &text("a")), Truth::True);
        let list = ["a".to_string()];
        assert_eq!(equals(&Value::List(&list), &text("a")), Truth::False);
        assert_eq!(equals(&Value::List(&list), &text("('a',)")), Truth::True);
        assert_eq!(
            equals(&Value::List(&list), &Value::Set(vec!["a"])),
            Truth::True
        );
    }

    #[test]
    fn bools_compare_as_true_and_false() {
        assert_eq!(equals(&Value::Bool(true), &text("true")), Truth::True);
        assert_eq!(
            subset(&text("a, b"), &Value::Set(vec!["a", "b", "c"])),
            Truth::True
        );
        assert_eq!(render(&Value::Bool(false)), "false");
        assert_eq!(render(&Value::Set(vec![])), "-");
    }
}
