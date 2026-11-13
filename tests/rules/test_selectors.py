from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from hoglint.selectors import NONE, UNKNOWN, Catalog, FieldInfo, FieldType, Ref, Selector, SelectorError, Truth, Value

VALUES: dict[str, Value] = {
    "src.package": Value.known("billing"),
    "dst.package": Value.known("core"),
    "dst.tier": Value.known("product"),
    "dst.zone": Value.known("contracts", chain=("contracts", "facade", "backend")),
    "dst.role": NONE,
    "dst.layer": UNKNOWN,
    "src.aliases": Value.known(frozenset({"default"})),
    "dst.aliases": Value.known(frozenset({"default", "replica"})),
    "src.context": Value.known(frozenset({"test", "function_local"})),
    "dst.exported": Value.known(True),
    "dst.module": Value.known("saleor.core.development.utils"),
}


def resolve(ref: Ref) -> Value:
    return VALUES[str(ref)]


@pytest.mark.parametrize(
    "source, expected",
    [
        ("src.package != dst.package", Truth.TRUE),
        ("dst.tier in ['core', 'product']", Truth.TRUE),
        ("dst.tier not in ['core', 'product']", Truth.FALSE),
        # a zone includes its ancestors: selecting facade includes contracts
        ("dst.zone == 'facade'", Truth.TRUE),
        ("dst.zone in ['backend'] and not dst.zone in ['facade']", Truth.FALSE),
        # outside its domain a field is a known none, so comparisons are definite
        ("dst.role == 'feature'", Truth.FALSE),
        ("dst.role == none", Truth.TRUE),
        ("not dst.role == 'feature'", Truth.TRUE),
        # inside its domain a missing value is unknown, and unknown never escapes
        ("dst.layer == 'graphql'", Truth.UNKNOWN),
        ("not dst.layer == 'graphql'", Truth.UNKNOWN),
        ("dst.layer == 'graphql' or dst.tier == 'product'", Truth.TRUE),
        ("dst.layer == 'graphql' and dst.tier == 'core'", Truth.FALSE),
        ("src.aliases subset_of dst.aliases", Truth.TRUE),
        ("dst.aliases subset_of src.aliases", Truth.FALSE),
        ("src.context == 'test'", Truth.TRUE),
        ("dst.exported", Truth.TRUE),
        ("dst.module startswith 'saleor.core.development'", Truth.TRUE),
        # a set or a list read as text is its identity column, whatever the hash seed
        ("dst.aliases startswith 'default,replica'", Truth.TRUE),
        ("src.context startswith 'function_local,test'", Truth.TRUE),
        ("dst.package == ['core']", Truth.TRUE),
        ("true", Truth.TRUE),
    ],
)
def test_three_valued_evaluation(source: str, expected: Truth) -> None:
    assert Selector.parse(source).evaluate(resolve) is expected


MISSING_SIDE_TABLE = Path(__file__).with_name("missing_side.txt")


@dataclass(frozen=True, slots=True)
class MissingSideCase:
    """One row of missing_side.txt: a comparison with a side that may have no value."""

    op: str
    left: str
    right: str
    truth: Truth
    negated: Truth

    @staticmethod
    def read_all() -> list[MissingSideCase]:
        cases = []
        for line in MISSING_SIDE_TABLE.read_text().splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            op, left, right, truth, negated = line.split()
            cases.append(MissingSideCase(op, left, right, Truth(truth), Truth(negated)))
        return cases

    @staticmethod
    def operand_text(spec: str, ref: str) -> str:
        kind, _, rest = spec.partition(":")
        if kind in ("field", "set"):
            return ref
        if kind == "list":
            return "[" + ", ".join(f"'{item}'" for item in rest.split(",")) + "]"
        return "none" if rest == "none" else f"'{rest}'"

    @staticmethod
    def operand_value(spec: str) -> Value:
        kind, _, rest = spec.partition(":")
        if kind == "set":
            return Value.known(frozenset(rest.split(",")))
        if rest == "none":
            return NONE
        if rest == "unknown":
            return UNKNOWN
        return Value.known(rest)

    def text(self) -> str:
        left = self.operand_text(self.left, "src.left")
        if self.op == "bare":
            return left
        return f"{left} {self.op.replace('-', ' ')} {self.operand_text(self.right, 'dst.right')}"

    def resolve(self, ref: Ref) -> Value:
        return self.operand_value(self.left if str(ref) == "src.left" else self.right)


@pytest.mark.parametrize("case", MissingSideCase.read_all(), ids=lambda case: case.text())
def test_a_missing_side_differs_from_every_value_and_equals_another_missing_side(case: MissingSideCase) -> None:
    assert Selector.parse(case.text()).evaluate(case.resolve) is case.truth
    assert Selector.parse(f"not ({case.text()})").evaluate(case.resolve) is case.negated


CATALOG = Catalog(
    {
        "src": {"package": FieldInfo(FieldType.ID), "tier": FieldInfo(FieldType.ENUM, ("core", "product", "leaf"))},
        "dst": {
            "tier": FieldInfo(FieldType.ENUM, ("core", "product", "leaf")),
            "zone": FieldInfo(FieldType.ZONE, ("facade", "logic")),
        },
    }
)


@pytest.mark.parametrize(
    "source, message",
    [
        ("dst.teir == 'core'", "did you mean 'dst.tier'"),
        ("dst.tier == 'prodcut'", "did you mean 'product'"),
        ("dst.zone in ['facde']", "did you mean 'facade'"),
        ("src.package ==", "unexpected end"),
        ("src.package == 'a' and", "unexpected end"),
        ("src.package in 'a'", "needs a list"),
    ],
)
def test_validation_rejects_typos(source: str, message: str) -> None:
    with pytest.raises(SelectorError, match=message):
        Selector.parse(source, CATALOG)
