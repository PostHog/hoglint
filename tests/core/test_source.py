from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from hoglint.facts import FACT_KINDS, FactNeeds
from hoglint.model import Graph
from hoglint.source import GraphSource

DECLARED = FactNeeds(kinds=frozenset({"imports"}), watch_callees=frozenset({"connect"}))


class UndeclaredPack:
    name = "undeclared"
    allowed_keys: frozenset[str] = frozenset()
    edge_kinds: frozenset[str] = frozenset()

    def produce(self, section: Mapping[str, Any], core: Any, graph: Graph, root: Path) -> None:
        return None


class DeclaredPack(UndeclaredPack):
    name = "declared"

    def fact_needs(self, section: Mapping[str, Any]) -> FactNeeds:
        return DECLARED


@pytest.mark.parametrize(
    "packs, all_kinds",
    [
        ([], False),
        ([DeclaredPack()], False),
        ([DeclaredPack(), UndeclaredPack()], True),
    ],
)
def test_fact_needs_count_a_pack_without_a_declaration_as_needing_every_kind(packs: list, all_kinds: bool) -> None:
    needs = GraphSource.fact_needs([(pack, {}) for pack in packs])

    if not packs:
        assert needs == FactNeeds()
    assert (needs.kinds >= FACT_KINDS) is all_kinds
    assert (DECLARED.watch_callees <= needs.watch_callees) is bool(packs)
