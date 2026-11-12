//! ORM and DRF meaning of a neutral use position: hoglint/packs/django/shapes.py, rule for
//! rule. Precedence follows the Python code: `lock` and `write` beat a scalar terminal, so
//! `select_for_update().count()` is `lock`.
//!
//! Text is split and matched as Python does it: `str.split()` whitespace, `re`'s `\w`, and
//! `str.isidentifier`, so an unusual character gives the same answer in both engines.

use crate::link::rules::env::last_part;
use regex::Regex;
use std::sync::LazyLock;

const ESCAPE_WRAPPERS: &[&str] = &["await", "ifexp:body", "ifexp:else", "star"];

/// The Django names the shape code reads: the tables of hoglint/packs/django/shapes.py, which
/// Python hands over with each run of the producer, so the two engines read one list.
pub struct Vocabulary {
    pub managers: Vec<String>,
    pub scalar_terminals: Vec<String>,
    pub single_terminals: Vec<String>,
    pub meta_managers: Vec<String>,
    pub meta_class_attrs: Vec<String>,
    pub write_methods: Vec<String>,
    pub subquery_calls: Vec<String>,
    /// Methods after which a chain yields rows of values, never an instance.
    pub value_methods: Vec<String>,
    /// The keywords a signal call takes its sending model by.
    pub sender_keywords: Vec<String>,
    /// Prefixes of an escape step that ends an annotation, and of one that assigns a local.
    pub annotation_terminals: Vec<String>,
    pub local_assign_steps: Vec<String>,
    /// The steps of a union of classes, the steps a type alias may pass on the way up (a subscript,
    /// a tuple), and the prefixes of a step that assigns it to a name.
    pub union_steps: Vec<String>,
    pub alias_pass_steps: Vec<String>,
    pub alias_assign_steps: Vec<String>,
}

/// Whether `names` holds `name`.
fn holds(names: &[String], name: &str) -> bool {
    names.iter().any(|known| known == name)
}

/// Whether `text` starts with one of `prefixes`.
fn starts_with_any(text: &str, prefixes: &[String]) -> bool {
    prefixes
        .iter()
        .any(|prefix| text.starts_with(prefix.as_str()))
}

/// `CHAIN_TOKEN` of hoglint/core/producers.py. Python's `\w` is a letter, a number or `_`.
static CHAIN_TOKEN: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\.[\p{L}\p{N}_]+|\(\)|\[\]").expect("a valid pattern"));
/// `str.isidentifier`.
static IDENTIFIER: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\A[_\p{XID_Start}]\p{XID_Continue}*\z").expect("a valid pattern")
});

/// The AST class of the parent the first escape step names, for `other(<class>)`.
fn step_parent_of(step: &str) -> Option<&'static str> {
    Some(match step {
        "expr" => "Expr",
        "assign" | "assign-target" => "Assign",
        "annassign" | "annotation" | "annassign-target" => "AnnAssign",
        "augassign" | "augassign-target" => "AugAssign",
        "return" => "Return",
        "for-iter" | "for-target" => "For",
        "while-test" => "While",
        "if-test" => "If",
        "with" | "with-target" => "withitem",
        "raise" | "raise-from" => "Raise",
        "assert" | "assert-msg" => "Assert",
        "del" => "Delete",
        "match" => "Match",
        "case-guard" => "match_case",
        "except" => "ExceptHandler",
        "kw" | "classkw" => "keyword",
        "star" => "Starred",
        "await" => "Await",
        "ifexp" => "IfExp",
        "bin" => "BinOp",
        "unary" => "UnaryOp",
        "bool" => "BoolOp",
        "cmp" => "Compare",
        "comp:key" | "comp:val" => "DictComp",
        "comp:iter" | "comp:target" | "comp:if" => "comprehension",
        "lambda" => "Lambda",
        "lambda:default" | "param-default" => "arguments",
        "walrus" | "walrus-target" => "NamedExpr",
        "fstr" => "FormattedValue",
        "tstr" => "Interpolation",
        "yield" => "Yield",
        "yieldfrom" => "YieldFrom",
        "slice-part" => "Slice",
        "param-ann" => "arg",
        "typealias" | "typealias-target" => "TypeAlias",
        "typeparam" => "TypeVar",
        _ => return None,
    })
}

fn step_parent(step: &str) -> &str {
    if let Some(rest) = step.strip_prefix('?') {
        return rest.split('.').next().unwrap_or("");
    }
    if let Some(parent) = step_parent_of(step) {
        return parent;
    }
    if step.starts_with("comp:") {
        return if step == "comp:elt" {
            "ListComp"
        } else {
            "comprehension"
        };
    }
    let head = step.split(':').next().unwrap_or("");
    step_parent_of(head).unwrap_or(head)
}

/// Python's `str.split()` whitespace: Unicode white space and the four separators U+001C to U+001F.
fn python_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// A use position the shape code cannot read: Python raises there, and the use's file fails.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Unreadable;

/// `step.split(":", 2)[2]`, which raises in Python when the step has fewer than two colons.
fn third_part(step: &str) -> Result<&str, Unreadable> {
    step.splitn(3, ':').nth(2).ok_or(Unreadable)
}

/// The last dotted part of a callee text; None for a bare expression (`?`).
fn last_name(callee: &str) -> Option<&str> {
    if callee == "?" {
        return None;
    }
    Some(last_part(callee))
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Shape {
    pub kind: &'static str,
    pub detail: String,
}

impl Shape {
    fn of(kind: &'static str) -> Shape {
        Shape {
            kind,
            detail: String::new(),
        }
    }

    fn with(kind: &'static str, detail: impl Into<String>) -> Shape {
        Shape {
            kind,
            detail: detail.into(),
        }
    }

    pub fn text(&self) -> String {
        if self.detail.is_empty() {
            self.kind.to_string()
        } else {
            format!("{}({})", self.kind, self.detail)
        }
    }
}

/// A neutral use position: the trailers directly above the used node, then the escape steps.
pub struct UsePosition {
    trailers: Vec<String>,
    up: Vec<String>,
}

impl UsePosition {
    pub fn of(chain: &str, up: &str) -> UsePosition {
        UsePosition {
            trailers: CHAIN_TOKEN
                .find_iter(chain)
                .map(|m| m.as_str().to_string())
                .collect(),
            up: up
                .split(python_space)
                .filter(|step| !step.is_empty())
                .map(str::to_string)
                .collect(),
        }
    }

    /// Attribute names before the first subscript, and the trailers from that subscript on.
    pub fn methods(&self) -> (Vec<&str>, &[String]) {
        let mut methods = Vec::new();
        for (at, trailer) in self.trailers.iter().enumerate() {
            if trailer == "[]" {
                return (methods, &self.trailers[at..]);
            }
            if trailer != "()" {
                methods.push(&trailer[1..]);
            }
        }
        (methods, &[])
    }

    /// The methods called after the first subscript: `values` in `qs[:5].values()`.
    fn called_after_subscript(&self) -> Vec<&str> {
        let (_, rest) = self.methods();
        rest.windows(2)
            .filter(|pair| pair[1] == "()")
            .filter_map(|pair| pair[0].strip_prefix('.'))
            .collect()
    }
}

/// The first call above the top of the attribute and call chain whose callee is a name or an
/// attribute.
fn enclosing_call<'p>(
    position: &'p UsePosition,
    rest: &'p [String],
) -> Result<Option<&'p str>, Unreadable> {
    let mut previous: Option<&str> = None;
    for trailer in rest {
        if trailer == "()"
            && let Some(name) = previous.and_then(|p| p.strip_prefix('.'))
        {
            return Ok(Some(name));
        }
        previous = Some(trailer);
    }
    previous = rest.last().map(String::as_str);
    for step in &position.up {
        if step.starts_with("arg:") || step.starts_with("kw:") {
            if let Some(name) = last_name(third_part(step)?).filter(|name| !name.is_empty()) {
                return Ok(Some(name));
            }
        } else if step == "()" {
            if let Some(name) = previous.and_then(|p| p.strip_prefix('.')) {
                return Ok(Some(name));
            }
        } else if step.starts_with("classkw:") {
            return Ok(None);
        }
        previous = Some(step);
    }
    Ok(None)
}

impl Vocabulary {
    /// The plain local name a use is assigned to (`qs = M.objects.filter()`), else None.
    pub fn local_target(&self, position: &UsePosition) -> Option<String> {
        let first = position
            .up
            .iter()
            .find(|step| !ESCAPE_WRAPPERS.contains(&step.as_str()))?;
        if !starts_with_any(first, &self.local_assign_steps) {
            return None;
        }
        let target = first.split_once(':')?.1;
        IDENTIFIER.is_match(target).then(|| target.to_string())
    }

    fn in_annotation(&self, position: &UsePosition) -> bool {
        if position.trailers.iter().any(|trailer| trailer == "()") {
            return false;
        }
        for step in &position.up {
            if starts_with_any(step, &self.annotation_terminals) {
                return true;
            }
            let passes = matches!(step.as_str(), "slice" | "elt:tuple" | "[]")
                || step.starts_with("bin:")
                || step.starts_with('.');
            if !passes {
                return false;
            }
        }
        false
    }

    /// An operand of a union of classes that is assigned to a name, also inside a subscript or a
    /// tuple: `Target = A | B`, `Cache = dict[str, A | None]`.
    fn in_type_alias(&self, position: &UsePosition) -> bool {
        let mut unions = 0;
        let mut at = 0;
        while let Some(step) = position.up.get(at) {
            if holds(&self.union_steps, step) {
                unions += 1;
            } else if !holds(&self.alias_pass_steps, step) {
                break;
            }
            at += 1;
        }
        unions > 0
            && position
                .up
                .get(at)
                .is_some_and(|step| starts_with_any(step, &self.alias_assign_steps))
    }

    fn manager_chain(&self, methods: &[&str], enclosing: Option<&str>) -> Shape {
        let terminal = methods.last().copied().unwrap_or("manager");
        if methods.contains(&"select_for_update") {
            return Shape::of("lock");
        }
        let writes: Vec<&str> = methods
            .iter()
            .copied()
            .filter(|method| holds(&self.write_methods, method))
            .collect();
        if !writes.is_empty() {
            return Shape::with("write", writes.join(","));
        }
        if enclosing == Some("Prefetch") {
            return Shape::of("prefetch");
        }
        if let Some(call) = enclosing.filter(|call| holds(&self.subquery_calls, call)) {
            return Shape::with("subquery", call);
        }
        if holds(&self.scalar_terminals, terminal) {
            return Shape::with("scalar-chain", terminal);
        }
        if let Some(values) = methods
            .iter()
            .find(|method| holds(&self.value_methods, method))
        {
            return Shape::with("scalar-chain", *values);
        }
        if enclosing.is_some_and(|call| call.ends_with("Field")) {
            return Shape::of("drf-field-queryset");
        }
        if holds(&self.single_terminals, terminal) {
            return Shape::of("instance-single");
        }
        Shape::with("instance-many", terminal)
    }

    fn attribute_chain(&self, methods: &[&str], enclosing: Option<&str>) -> Shape {
        let head = methods[0];
        if head == "DoesNotExist" {
            return Shape::of("exception");
        }
        if head == "_meta" {
            if methods.len() == 1 {
                return Shape::of("_meta");
            }
            if holds(&self.meta_managers, methods[1]) {
                return self.manager_chain(&methods[2..], enclosing);
            }
            if holds(&self.meta_class_attrs, methods[1]) {
                if methods.len() > 2 {
                    return self.attribute_chain(&methods[2..], enclosing);
                }
                return Shape::of("_meta");
            }
            if methods[1] == "managers" {
                return Shape::with("other", "Attribute:_meta.managers");
            }
            return Shape::of("_meta");
        }
        if holds(&self.managers, head) {
            return self.manager_chain(&methods[1..], enclosing);
        }
        if head.chars().next().is_some_and(char::is_uppercase) {
            return Shape::with("nested-class-attr", head);
        }
        Shape::with("other", format!("Attribute:{head}"))
    }

    /// The shape of one use position: `OrmShapes.classify`.
    pub fn classify(&self, position: &UsePosition) -> Result<Shape, Unreadable> {
        if self.in_annotation(position) {
            return Ok(Shape::of("annotation"));
        }
        if position.trailers.is_empty() && self.in_type_alias(position) {
            return Ok(Shape::of("annotation"));
        }
        let (methods, rest) = position.methods();
        if !methods.is_empty() {
            let shape = self.attribute_chain(&methods, enclosing_call(position, rest)?);
            let values = position
                .called_after_subscript()
                .into_iter()
                .find(|method| holds(&self.value_methods, method));
            if let Some(values) = values
                && matches!(shape.kind, "instance-single" | "instance-many")
            {
                return Ok(Shape::with("scalar-chain", values));
            }
            return Ok(shape);
        }
        if let Some(first) = position.trailers.first() {
            // The parent is the call that calls the node, or the subscript that indexes it.
            return Ok(Shape::of(if first == "()" {
                "construct"
            } else {
                "in-collection"
            }));
        }
        let step = position.up.first().map_or("?", String::as_str);
        if step.starts_with("arg:") {
            let callee = third_part(step)?;
            let name = if callee.starts_with('?') {
                "?"
            } else {
                last_part(callee)
            };
            if name == "isinstance" {
                return Ok(Shape::of("isinstance"));
            }
            return Ok(Shape::with("passed-as-class", name));
        }
        if let Some(rest) = step.strip_prefix("kw:")
            && holds(&self.sender_keywords, rest.split(':').next().unwrap_or(""))
        {
            return Ok(Shape::of("sender"));
        }
        if let Some(targets) = step.strip_prefix("assign:")
            && targets.split(',').any(|target| target == "model")
        {
            return Ok(Shape::of("drf-model-serializer"));
        }
        let model_keyword = step.starts_with("kw:model:") || step.starts_with("classkw:model");
        if model_keyword && step.split(':').nth(1) == Some("model") {
            return Ok(Shape::of("drf-model-serializer"));
        }
        if step.starts_with("elt:") || step.starts_with("dict:") || step == "slice" {
            return Ok(Shape::of("in-collection"));
        }
        Ok(Shape::with("other", step_parent(step)))
    }

    /// The shape of a manager chain held in a local and used again (`qs = ...; qs.update()`):
    /// `OrmShapes.refine`. Some only when a later use makes it stronger (lock or write); `later`
    /// holds the method names of each later use.
    pub fn refine(&self, source: &UsePosition, later: &[&[String]]) -> Option<Shape> {
        let (methods, _) = source.methods();
        if methods.is_empty() || !holds(&self.managers, methods[0]) {
            return None;
        }
        let mut combined: Vec<&str> = methods[1..].to_vec();
        for names in later {
            combined.extend(names.iter().map(String::as_str));
        }
        let refined = self.manager_chain(&combined, None);
        matches!(refined.kind, "lock" | "write").then_some(refined)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn names(items: &[&str]) -> Vec<String> {
        items.iter().map(|item| item.to_string()).collect()
    }

    /// A vocabulary with the names these cases read: the product's comes from Python.
    fn vocabulary() -> Vocabulary {
        Vocabulary {
            managers: names(&["objects"]),
            scalar_terminals: names(&["count"]),
            single_terminals: names(&["get"]),
            meta_managers: names(&["default_manager"]),
            meta_class_attrs: names(&["model"]),
            write_methods: names(&["update"]),
            subquery_calls: names(&["Exists"]),
            value_methods: names(&["values", "values_list"]),
            sender_keywords: names(&["sender"]),
            annotation_terminals: names(&["param-ann:", "returns", "annotation", "typealias:"]),
            local_assign_steps: names(&["assign:", "annassign:", "walrus:"]),
            union_steps: names(&["bin:|"]),
            alias_pass_steps: names(&["slice", "elt:tuple"]),
            alias_assign_steps: names(&["assign:", "annassign:"]),
        }
    }

    fn shape(chain: &str, up: &str) -> Result<String, Unreadable> {
        vocabulary()
            .classify(&UsePosition::of(chain, up))
            .map(|shape| shape.text())
    }

    #[test]
    fn classifies_as_the_python_shape_code_does() {
        let cases = [
            (".objects.select_for_update().count()", "expr", "lock"),
            (".objects.filter().count()", "return", "scalar-chain(count)"),
            (".objects.filter().update()", "expr", "write(update)"),
            (".objects.filter()", "arg:1:Prefetch", "prefetch"),
            (
                ".objects.all()",
                "kw:queryset:serializers.PrimaryKeyRelatedField",
                "drf-field-queryset",
            ),
            (".DoesNotExist", "except", "exception"),
            ("", "param-ann:team", "annotation"),
            ("", "assign:model", "drf-model-serializer"),
            ("", "arg:0:cast", "passed-as-class(cast)"),
            ("", "comp:elt", "other(ListComp)"),
            ("", "", "other"),
            (".objects", "?Call.func", "instance-many(manager)"),
            (".Meta", "expr", "nested-class-attr(Meta)"),
            (".pk", "expr", "other(Attribute:pk)"),
            (
                ".objects.filter().values_list().first()",
                "return",
                "scalar-chain(values_list)",
            ),
            (
                ".objects.values().filter().update()",
                "expr",
                "write(update)",
            ),
            ("", "kw:sender:receiver", "sender"),
            ("", "bin:| bin:| assign:Target", "annotation"),
            ("", "bin:| typealias:Target", "annotation"),
            ("", "bin:| arg:1:isinstance", "other(BinOp)"),
            ("", "bin:| elt:tuple slice assign:Cache", "annotation"),
            (
                ".objects.filter().order_by()[].values()",
                "assign:rows",
                "scalar-chain(values)",
            ),
            (
                ".objects.filter()[].first()",
                "assign:row",
                "instance-many(filter)",
            ),
        ];
        for (chain, up, expected) in cases {
            assert_eq!(
                shape(chain, up),
                Ok(expected.to_string()),
                "{chain:?} {up:?}"
            );
        }
        assert_eq!(shape("", "arg:0"), Err(Unreadable));
    }

    #[test]
    fn a_local_target_is_a_python_identifier() {
        let local = |up: &str| vocabulary().local_target(&UsePosition::of(".objects.all()", up));
        assert_eq!(local("await assign:qs"), Some("qs".to_string()));
        assert_eq!(local("assign:a,b"), None);
        assert_eq!(local("expr"), None);
    }
}
