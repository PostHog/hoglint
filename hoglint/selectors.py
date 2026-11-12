"""The three-valued selector language.

A selector is a small boolean expression over labels: `==`, `!=`, `in`, `not in`, `startswith`,
`subset_of`, `and`, `or`, `not`, and comparisons between two endpoints such as
`src.package != dst.package`. There are no joins, no recursion and no aggregation: anything that
needs one is a derived-edge producer.

Every value has a state. A known value compares normally. A known `none` means the field does
not apply to the entity (outside the field's domain). An unknown value means hoglint cannot
tell (inside the domain but unset, or an unresolved endpoint). Any comparison that reads an
unknown value is unknown, and Kleene logic carries it through `and`, `or` and `not`.
"""

from __future__ import annotations

import re
import difflib
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from functools import cache


class SelectorError(ValueError):
    """A selector that does not parse or names something the schema does not know."""


class Truth(StrEnum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"

    def negate(self) -> Truth:
        if self is Truth.UNKNOWN:
            return Truth.UNKNOWN
        return Truth.FALSE if self is Truth.TRUE else Truth.TRUE

    @staticmethod
    def of(value: bool) -> Truth:
        return Truth.TRUE if value else Truth.FALSE


class State(StrEnum):
    KNOWN = "known"
    NONE = "none"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Value:
    """One resolved operand. `chain` holds a hierarchical value and its ancestors (zones)."""

    state: State
    data: str | bool | int | frozenset[str] | tuple[str, ...] | None = None
    chain: tuple[str, ...] = ()

    @staticmethod
    def known(data: str | bool | int | frozenset[str] | tuple[str, ...], chain: tuple[str, ...] = ()) -> Value:
        return Value(State.KNOWN, data, chain)

    @property
    def is_unknown(self) -> bool:
        return self.state is State.UNKNOWN


NONE = Value(State.NONE)
UNKNOWN = Value(State.UNKNOWN)


# ── Syntax tree ───────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Ref:
    path: tuple[str, ...]

    def __str__(self) -> str:
        return ".".join(self.path)


@dataclass(frozen=True, slots=True)
class Literal:
    value: str | bool | int | None


@dataclass(frozen=True, slots=True)
class ListLiteral:
    items: tuple[str | int | bool, ...]


Operand = Ref | Literal | ListLiteral


@dataclass(frozen=True, slots=True)
class Compare:
    op: str
    left: Operand
    right: Operand


@dataclass(frozen=True, slots=True)
class Bare:
    """A lone operand used as a condition: `dst.exported`, `true`."""

    operand: Operand


@dataclass(frozen=True, slots=True)
class Not:
    inner: Node


@dataclass(frozen=True, slots=True)
class And:
    parts: tuple[Node, ...]


@dataclass(frozen=True, slots=True)
class Or:
    parts: tuple[Node, ...]


Node = Compare | Bare | Not | And | Or


@dataclass(frozen=True, slots=True)
class Condition:
    """A named selector from `[conditions]`: a rule names it where an expression may stand, and the
    name reads as its text in parentheses."""

    name: str
    source: str
    node: Node


KEYWORDS = frozenset({"and", "or", "not", "in", "startswith", "subset_of", "true", "false", "none"})
# The deepest a selector nests, in its text (parentheses, `not`) and in its tree: a limit of the
# config, so both engines judge every selector that loads.
MAX_DEPTH = 32

# One dotted part of a name: a condition's whole name, or a segment of `src.zone`.
NAME_PART = r"[A-Za-z_][A-Za-z0-9_\-]*"
TOKEN_RE = re.compile(
    rf"""
    \s*(?:
      (?P<string>'[^']*'|"[^"]*")
    | (?P<number>-?\d+)
    | (?P<op>==|!=)
    | (?P<punct>[()\[\],])
    | (?P<name>{NAME_PART}(?:\.{NAME_PART})*)
    )
    """,
    re.VERBOSE,
)


@dataclass(frozen=True, slots=True)
class Token:
    kind: str
    text: str


def tokenize(source: str) -> list[Token]:
    tokens: list[Token] = []
    position = 0
    stripped = source.rstrip()
    while position < len(stripped):
        match = TOKEN_RE.match(stripped, position)
        if match is None or match.end() == position:
            raise SelectorError(f"cannot read {stripped[position:]!r} in selector {source.strip()!r}")
        position = match.end()
        kind = match.lastgroup or ""
        tokens.append(Token(kind, match.group(kind)))
    return tokens


class Parser:
    """Recursive descent: or > and > not > comparison. A bare name in `conditions` becomes that
    condition's tree."""

    def __init__(self, source: str, conditions: Mapping[str, Condition] | None = None) -> None:
        self.source = source
        self.tokens = tokenize(source)
        self.index = 0
        self.depth = 0
        self.conditions: Mapping[str, Condition] = conditions or {}
        self.used: list[str] = []

    def fail(self, message: str) -> SelectorError:
        return SelectorError(f"{message} in selector {self.source.strip()!r}")

    def peek(self) -> Token | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def peek_word(self) -> str:
        token = self.peek()
        if token is None or token.kind != "name":
            return ""
        return token.text

    def take(self) -> Token:
        token = self.peek()
        if token is None:
            raise self.fail("unexpected end")
        self.index += 1
        return token

    def expect(self, text: str) -> None:
        token = self.take()
        if token.text != text:
            raise self.fail(f"expected {text!r}, found {token.text!r}")

    def parse(self) -> Node:
        if not self.tokens:
            raise self.fail("empty expression")
        node = self.parse_or()
        if self.peek() is not None:
            raise self.fail(f"unexpected {self.take().text!r}")
        if depth(node) > MAX_DEPTH:
            raise self.fail(f"nested more than {MAX_DEPTH} deep")
        return node

    def nest(self) -> None:
        """One level deeper in the text, failing before the recursion grows past the limit."""
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise self.fail(f"nested more than {MAX_DEPTH} deep")

    # An `and` inside an `and` (from parentheses or a named condition) joins it, and so does an
    # `or` inside an `or`: rules stage the top-level parts of a selection one by one.
    def parse_joined(self, word: str, joiner: type[And] | type[Or], parse_part: Callable[[], Node]) -> Node:
        parts: list[Node] = []
        while True:
            part = parse_part()
            parts.extend(part.parts if isinstance(part, joiner) else (part,))
            if self.peek_word() != word:
                break
            self.take()
        return parts[0] if len(parts) == 1 else joiner(tuple(parts))

    def parse_or(self) -> Node:
        return self.parse_joined("or", Or, self.parse_and)

    def parse_and(self) -> Node:
        return self.parse_joined("and", And, self.parse_not)

    def parse_not(self) -> Node:
        if self.peek_word() == "not":
            self.take()
            self.nest()
            inner = self.parse_not()
            self.depth -= 1
            return Not(inner)
        return self.parse_comparison()

    def parse_comparison(self) -> Node:
        token = self.peek()
        if token is not None and token.text == "(":
            self.take()
            self.nest()
            node = self.parse_or()
            self.expect(")")
            self.depth -= 1
            return node
        left = self.parse_operand()
        op = self.parse_operator()
        if op is None:
            condition = self.condition_of(left)
            return condition.node if condition is not None else Bare(left)
        right = self.parse_operand()
        for operand in (left, right):
            condition = self.condition_of(operand)
            if condition is not None:
                raise self.fail(f"condition {condition.name!r} is an expression and cannot be compared")
        return Compare(op, left, right)

    def condition_of(self, operand: Operand) -> Condition | None:
        """The condition a lone name stands for; a field always has a root or a dot in edge
        selectors, and a condition's name may not be a field's."""
        if not isinstance(operand, Ref) or len(operand.path) != 1:
            return None
        condition = self.conditions.get(operand.path[0])
        if condition is not None and condition.name not in self.used:
            self.used.append(condition.name)
        return condition

    def parse_operator(self) -> str | None:
        token = self.peek()
        if token is None:
            return None
        if token.kind == "op":
            self.take()
            return token.text
        if token.text in ("in", "startswith", "subset_of"):
            self.take()
            return token.text
        if token.text == "not" and self.index + 1 < len(self.tokens) and self.tokens[self.index + 1].text == "in":
            self.index += 2
            return "not in"
        return None

    def parse_operand(self) -> Operand:
        token = self.take()
        if token.kind == "string":
            return Literal(token.text[1:-1])
        if token.kind == "number":
            return Literal(int(token.text))
        if token.text == "[":
            return self.parse_list()
        if token.kind != "name":
            raise self.fail(f"unexpected {token.text!r}")
        if token.text == "true":
            return Literal(True)
        if token.text == "false":
            return Literal(False)
        if token.text == "none":
            return Literal(None)
        if token.text in KEYWORDS:
            raise self.fail(f"unexpected keyword {token.text!r}")
        return Ref(tuple(token.text.split(".")))

    def parse_list(self) -> ListLiteral:
        items: list[str | int | bool] = []
        while True:
            token = self.take()
            if token.text == "]":
                return ListLiteral(tuple(items))
            if token.kind == "string":
                items.append(token.text[1:-1])
            elif token.kind == "number":
                items.append(int(token.text))
            else:
                raise self.fail(f"list items must be literals, found {token.text!r}")
            separator = self.take()
            if separator.text == "]":
                return ListLiteral(tuple(items))
            if separator.text != ",":
                raise self.fail(f"expected ',' or ']', found {separator.text!r}")


# ── Schema for validation ─────────────────────────────────────────


class FieldType(StrEnum):
    STRING = "string"
    ID = "id"
    ENUM = "enum"
    SET = "set"
    BOOL = "bool"
    ZONE = "zone"
    ORDERED = "ordered"
    INT = "int"


@dataclass(frozen=True, slots=True)
class FieldInfo:
    type: FieldType
    values: tuple[str, ...] = ()


@dataclass(frozen=False, slots=True)
class Catalog:
    """What a selector may name at one position.

    `roots` maps a root (`src`, `dst`, `edge`, or "" for bare entity fields) to its fields.
    `open_roots` accept any attribute name (producer-defined edge attributes).
    """

    roots: dict[str, dict[str, FieldInfo]] = field(default_factory=dict)
    open_roots: frozenset[str] = frozenset()

    def lookup(self, ref: Ref) -> FieldInfo | None:
        if len(ref.path) == 1 and "" in self.roots:
            return self.roots[""].get(ref.path[0])
        if len(ref.path) != 2:
            return None
        root, name = ref.path
        if root in self.open_roots:
            fields = self.roots.get(root, {})
            return fields.get(name, FieldInfo(FieldType.STRING))
        fields = self.roots.get(root)
        if fields is None:
            return None
        return fields.get(name)

    def names(self) -> list[str]:
        out: list[str] = []
        for root, fields in self.roots.items():
            out.extend(f"{root}.{name}" if root else name for name in fields)
        return sorted(out)


def literal_values(operand: Operand) -> list[str | int | bool | None]:
    if isinstance(operand, Literal):
        return [operand.value]
    if isinstance(operand, ListLiteral):
        return list(operand.items)
    return []


class Validator:
    """Checks names and literal values against a catalog. Typos are config errors."""

    def __init__(self, catalog: Catalog, source: str) -> None:
        self.catalog = catalog
        self.source = source

    def fail(self, message: str) -> SelectorError:
        return SelectorError(f"{message} in selector {self.source.strip()!r}")

    def check(self, node: Node) -> None:
        if isinstance(node, (And, Or)):
            for part in node.parts:
                self.check(part)
            return
        if isinstance(node, Not):
            self.check(node.inner)
            return
        if isinstance(node, Bare):
            self.check_operand(node.operand)
            return
        self.check_compare(node)

    def check_operand(self, operand: Operand) -> FieldInfo | None:
        if not isinstance(operand, Ref):
            return None
        info = self.catalog.lookup(operand)
        if info is None:
            hint = suggest(str(operand), self.catalog.names())
            raise self.fail(f"unknown field {str(operand)!r}{hint}")
        return info

    def check_compare(self, node: Compare) -> None:
        left = self.check_operand(node.left)
        right = self.check_operand(node.right)
        if node.op in ("in", "not in", "subset_of") and not isinstance(node.right, (ListLiteral, Ref)):
            raise self.fail(f"'{node.op}' needs a list or a field on the right")
        for info, other in ((left, node.right), (right, node.left)):
            if info is None or info.type not in (FieldType.ENUM, FieldType.ZONE, FieldType.ORDERED):
                continue
            for value in literal_values(other):
                if value is None or value in info.values:
                    continue
                hint = suggest(str(value), list(info.values))
                raise self.fail(f"{value!r} is not a value of this field{hint}")


def suggest(word: str, choices: Iterable[str]) -> str:
    close = difflib.get_close_matches(word, list(choices), n=1)
    return f" (did you mean {close[0]!r}?)" if close else ""


# ── Evaluation ────────────────────────────────────────────────────

Resolver = Callable[[Ref], Value]


@cache
def literal_value(operand: Literal | ListLiteral) -> Value:
    if isinstance(operand, ListLiteral):
        return Value.known(tuple(str(item) for item in operand.items))
    if operand.value is None:
        return NONE
    if isinstance(operand.value, bool):
        return Value.known(operand.value)
    return Value.known(str(operand.value))


def to_value(operand: Operand, resolve: Resolver) -> Value:
    if isinstance(operand, Ref):
        return resolve(operand)
    return literal_value(operand)


def members(value: Value) -> frozenset[str]:
    """The comparable members of a known value: a set's items, a zone and its ancestors."""
    if value.chain:
        return frozenset(value.chain)
    if isinstance(value.data, frozenset):
        return value.data
    if isinstance(value.data, tuple):
        return frozenset(value.data)
    if isinstance(value.data, bool):
        return frozenset({"true" if value.data else "false"})
    return frozenset({str(value.data)})


def split_list(raw: str) -> list[str]:
    """A comma-separated list, as producer attrs and symbol facts carry sets."""
    return [item.strip() for item in raw.split(",") if item.strip()]


def render_data(data: object) -> str:
    """The text of known data as identity columns and exception matches write it: a bool as
    true/false, a set or list as its sorted items joined by commas, `-` when it is empty."""
    if isinstance(data, bool):
        return "true" if data else "false"
    if isinstance(data, (frozenset, set, tuple, list)):
        return ",".join(sorted(str(item) for item in data)) or "-"
    return str(data)


def render_value(value: Value) -> str:
    """A value as one identity column: `?` unknown, `-` none."""
    if value.state is State.UNKNOWN:
        return "?"
    if value.state is State.NONE:
        return "-"
    return render_data(value.data)


def scalar(value: Value) -> str:
    """A known value read as text, in its one text form: a set or a list as its identity column,
    never `str()` of a frozenset, whose item order follows the hash seed."""
    return render_data(value.data)


def equals(left: Value, right: Value) -> Truth:
    if left.is_unknown or right.is_unknown:
        return Truth.UNKNOWN
    if left.state is State.NONE or right.state is State.NONE:
        return Truth.of(left.state is right.state)
    if isinstance(left.data, (frozenset, tuple)) and isinstance(right.data, (frozenset, tuple)):
        return Truth.of(frozenset(left.data) == frozenset(right.data))
    if left.chain and not right.chain:
        return Truth.of(scalar(right) in left.chain)
    if right.chain and not left.chain:
        return Truth.of(scalar(left) in right.chain)
    if isinstance(left.data, frozenset):
        return Truth.of(scalar(right) in left.data)
    if isinstance(right.data, frozenset):
        return Truth.of(scalar(left) in right.data)
    return Truth.of(scalar(left) == scalar(right))


def contained(left: Value, right: Value) -> Truth:
    if left.is_unknown or right.is_unknown:
        return Truth.UNKNOWN
    if left.state is State.NONE or right.state is State.NONE:
        return Truth.FALSE
    return Truth.of(bool(members(left) & members(right)))


def set_members(value: Value) -> frozenset[str]:
    """`subset_of` reads a plain string as a comma-separated set (producer attrs carry sets so)."""
    if value.state is State.NONE:
        return frozenset()
    if isinstance(value.data, str) and not value.chain:
        return frozenset(split_list(value.data))
    return members(value)


def subset(left: Value, right: Value) -> Truth:
    if left.is_unknown or right.is_unknown:
        return Truth.UNKNOWN
    return Truth.of(set_members(left) <= set_members(right))


def starts(left: Value, right: Value) -> Truth:
    if left.is_unknown or right.is_unknown:
        return Truth.UNKNOWN
    if left.state is State.NONE or right.state is State.NONE:
        return Truth.FALSE
    return Truth.of(scalar(left).startswith(scalar(right)))


def truthy(value: Value) -> Truth:
    if value.is_unknown:
        return Truth.UNKNOWN
    if value.state is State.NONE:
        return Truth.FALSE
    return Truth.of(bool(value.data))


def all_of(parts: Iterable[Truth]) -> Truth:
    result = Truth.TRUE
    for part in parts:
        if part is Truth.FALSE:
            return Truth.FALSE
        if part is Truth.UNKNOWN:
            result = Truth.UNKNOWN
    return result


def any_of(parts: Iterable[Truth]) -> Truth:
    result = Truth.FALSE
    for part in parts:
        if part is Truth.TRUE:
            return Truth.TRUE
        if part is Truth.UNKNOWN:
            result = Truth.UNKNOWN
    return result


def evaluate(node: Node, resolve: Resolver) -> Truth:
    if isinstance(node, And):
        return all_of(evaluate(part, resolve) for part in node.parts)
    if isinstance(node, Or):
        return any_of(evaluate(part, resolve) for part in node.parts)
    if isinstance(node, Not):
        return evaluate(node.inner, resolve).negate()
    if isinstance(node, Bare):
        return truthy(to_value(node.operand, resolve))
    left = to_value(node.left, resolve)
    right = to_value(node.right, resolve)
    if node.op == "==":
        return equals(left, right)
    if node.op == "!=":
        return equals(left, right).negate()
    if node.op == "in":
        return contained(left, right)
    if node.op == "not in":
        return contained(left, right).negate()
    if node.op == "startswith":
        return starts(left, right)
    return subset(left, right)


def depth(node: Node) -> int:
    """The nodes on the longest path from the root to a leaf."""
    if isinstance(node, (And, Or)):
        return 1 + max(depth(part) for part in node.parts)
    if isinstance(node, Not):
        return 1 + depth(node.inner)
    return 1


def leaves(node: Node) -> Iterator[Compare | Bare]:
    """The comparisons and bare operands of a selector, in order."""
    if isinstance(node, (And, Or)):
        for part in node.parts:
            yield from leaves(part)
        return
    if isinstance(node, Not):
        yield from leaves(node.inner)
        return
    yield node


def refs(node: Node) -> list[Ref]:
    """Every field a selector reads, in order."""
    out: list[Ref] = []
    for leaf in leaves(node):
        operands = [leaf.operand] if isinstance(leaf, Bare) else [leaf.left, leaf.right]
        out.extend(operand for operand in operands if isinstance(operand, Ref))
    return out


def compared_values(node: Node, name: str) -> set[str]:
    """Literals compared against a `<root>.<name>` or bare `<name>` field (never `edge.*`)."""
    out: set[str] = set()
    for leaf in leaves(node):
        if isinstance(leaf, Bare):
            continue
        for side, other in ((leaf.left, leaf.right), (leaf.right, leaf.left)):
            if isinstance(side, Ref) and side.path[-1] == name and side.path[0] != "edge":
                out.update(str(value) for value in literal_values(other) if value is not None)
    return out


class ClassSelection:
    """A selector evaluated once per label class: the values of the fields it reads. The parts of
    an `and` are classed apart and judged in order, so a false part stops the rest as `and`
    does, and the rest's fields are never read."""

    def __init__(self, node: Node) -> None:
        parts = node.parts if isinstance(node, And) else (node,)
        self.parts = [(part, tuple(dict.fromkeys(refs(part)))) for part in parts]
        self.truths: list[dict[tuple[Value, ...], Truth]] = [{} for _ in parts]

    def truth(self, resolve: Resolver) -> Truth:
        result = Truth.TRUE
        for (part, part_refs), truths in zip(self.parts, self.truths):
            values = tuple(resolve(ref) for ref in part_refs)
            found = truths.get(values)
            if found is None:
                found = truths[values] = evaluate(part, dict(zip(part_refs, values)).__getitem__)
            if found is Truth.FALSE:
                return Truth.FALSE
            if found is Truth.UNKNOWN:
                result = Truth.UNKNOWN
        return result


@dataclass(frozen=True, slots=True)
class Selector:
    """A parsed and validated selector, kept with its source text for explain output. `node` holds
    the conditions it names expanded; `conditions` names them."""

    source: str
    node: Node
    conditions: tuple[str, ...] = ()

    @staticmethod
    def parse(
        source: str, catalog: Catalog | None = None, conditions: Mapping[str, Condition] | None = None
    ) -> Selector:
        parser = Parser(source, conditions)
        node = parser.parse()
        if catalog is not None:
            for name in parser.used:
                condition = parser.conditions[name]
                try:
                    Validator(catalog, condition.source).check(condition.node)
                except SelectorError as error:
                    raise SelectorError(f"in condition {name!r}: {error}") from None
            Validator(catalog, source).check(node)
        return Selector(source.strip(), node, tuple(parser.used))

    def evaluate(self, resolve: Resolver) -> Truth:
        return evaluate(self.node, resolve)

    def refs(self) -> list[Ref]:
        return refs(self.node)

    def compared_values(self, name: str) -> set[str]:
        return compared_values(self.node, name)

    def restricts(self, path: tuple[str, ...]) -> frozenset[str] | None:
        """Literal values the selector requires at `path` (from `==` or `in` at the top level of
        an `and`), or None when it does not pin the field. Lets callers pre-filter edges. Only
        text literals pin: `== none` holds for a field with no value."""
        parts = self.node.parts if isinstance(self.node, And) else (self.node,)
        for part in parts:
            if not isinstance(part, Compare) or not isinstance(part.left, Ref) or part.left.path != path:
                continue
            values = literal_values(part.right)
            if part.op in ("==", "in") and all(isinstance(value, str) for value in values):
                return frozenset(str(value) for value in values)
        return None
