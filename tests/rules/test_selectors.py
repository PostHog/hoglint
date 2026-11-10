from __future__ import annotations

import itertools

import pytest

from hoglint.selectors import (
    NONE,
    UNKNOWN,
    Catalog,
    ClassSelection,
    FieldInfo,
    FieldType,
    Ref,
    Selector,
    SelectorError,
    Truth,
    Value,
)

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


def test_a_selector_judged_per_label_class_answers_like_evaluating_each_entity() -> None:
    selector = Selector.parse(
        "src.package != dst.package and (dst.package == 'core' or dst.tier == 'product') "
        "and dst.zone in ['facade', 'logic'] and not dst.role == 'feature'"
    )
    classes = ClassSelection(selector.node)
    choices = {
        "src.package": [Value.known("billing"), Value.known("core")],
        "dst.package": [Value.known("billing"), Value.known("core"), UNKNOWN],
        "dst.tier": [Value.known("product"), Value.known("core"), Value.known("billing"), NONE, UNKNOWN],
        "dst.zone": [Value.known("contracts", chain=("contracts", "facade")), Value.known("logic"), NONE],
        "dst.role": [Value.known("feature"), NONE, UNKNOWN],
    }
    for picked in itertools.product(*choices.values()):
        values = dict(zip(choices, picked))

        def resolve(ref: Ref, values: dict[str, Value] = values) -> Value:
            return values[str(ref)]

        assert classes.truth(resolve) is selector.evaluate(resolve), values


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
