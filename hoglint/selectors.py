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
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum


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

OPERATORS = ("==", "!=", "in", "not in", "startswith", "subset_of")
KEYWORDS = frozenset({"and", "or", "not", "in", "startswith", "subset_of", "true", "false", "none"})

TOKEN_RE = re.compile(
    r"""
    \s*(?:
      (?P<string>'[^']*'|"[^"]*")
    | (?P<number>-?\d+)
    | (?P<op>==|!=)
    | (?P<punct>[()\[\],])
    | (?P<name>[A-Za-z_][A-Za-z0-9_\-]*(?:\.[A-Za-z_][A-Za-z0-9_\-]*)*)
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
    """Recursive descent: or > and > not > comparison."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.tokens = tokenize(source)
        self.index = 0

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
        return node

    def parse_or(self) -> Node:
        parts = [self.parse_and()]
        while self.peek_word() == "or":
            self.take()
            parts.append(self.parse_and())
        return parts[0] if len(parts) == 1 else Or(tuple(parts))

    def parse_and(self) -> Node:
        parts = [self.parse_not()]
        while self.peek_word() == "and":
            self.take()
            parts.append(self.parse_not())
        return parts[0] if len(parts) == 1 else And(tuple(parts))

    def parse_not(self) -> Node:
        if self.peek_word() == "not":
            self.take()
            return Not(self.parse_not())
        return self.parse_comparison()

    def parse_comparison(self) -> Node:
        token = self.peek()
        if token is not None and token.text == "(":
            self.take()
            node = self.parse_or()
            self.expect(")")
            return node
        left = self.parse_operand()
        op = self.parse_operator()
        if op is None:
            return Bare(left)
        return Compare(op, left, self.parse_operand())

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
    import difflib  # noqa: PLC0415 — only error paths need it

    close = difflib.get_close_matches(word, list(choices), n=1)
    return f" (did you mean {close[0]!r}?)" if close else ""


# ── Evaluation ────────────────────────────────────────────────────

Resolver = Callable[[Ref], Value]


def to_value(operand: Operand, resolve: Resolver) -> Value:
    if isinstance(operand, Ref):
        return resolve(operand)
    if isinstance(operand, ListLiteral):
        return Value.known(tuple(str(item) for item in operand.items))
    if operand.value is None:
        return NONE
    if isinstance(operand.value, bool):
        return Value.known(operand.value)
    return Value.known(str(operand.value))


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


def scalar(value: Value) -> str:
    if isinstance(value.data, bool):
        return "true" if value.data else "false"
    return str(value.data)


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
        return frozenset(item.strip() for item in value.data.split(",") if item.strip())
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
    if isinstance(value.data, bool):
        return Truth.of(value.data)
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


def refs(node: Node) -> list[Ref]:
    """Every field a selector reads, in order."""
    if isinstance(node, (And, Or)):
        return [ref for part in node.parts for ref in refs(part)]
    if isinstance(node, Not):
        return refs(node.inner)
    operands = [node.operand] if isinstance(node, Bare) else [node.left, node.right]
    return [operand for operand in operands if isinstance(operand, Ref)]


@dataclass(frozen=True, slots=True)
class Selector:
    """A parsed and validated selector, kept with its source text for explain output."""

    source: str
    node: Node

    @staticmethod
    def parse(source: str, catalog: Catalog | None = None) -> Selector:
        node = Parser(source).parse()
        if catalog is not None:
            Validator(catalog, source).check(node)
        return Selector(source.strip(), node)

    def evaluate(self, resolve: Resolver) -> Truth:
        return evaluate(self.node, resolve)

    def refs(self) -> list[Ref]:
        return refs(self.node)

    def restricts(self, path: tuple[str, ...]) -> frozenset[str] | None:
        """Literal values the selector requires at `path` (from `==` or `in` at the top level of
        an `and`), or None when it does not pin the field. Lets callers pre-filter edges."""
        parts = self.node.parts if isinstance(self.node, And) else (self.node,)
        for part in parts:
            if not isinstance(part, Compare) or not isinstance(part.left, Ref) or part.left.path != path:
                continue
            if part.op in ("==", "in"):
                return frozenset(str(value) for value in literal_values(part.right))
        return None

    def roots(self) -> set[str]:
        return {ref.path[0] for ref in self.refs() if len(ref.path) > 1}


ALWAYS = Selector("true", Bare(Literal(True)))
