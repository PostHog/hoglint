from __future__ import annotations

from pathlib import Path

import pytest

from core.support import func_def
from django_facts import FactRepo
from hoglint.core.config import CoreConfig
from hoglint.core.reader import python_pack_io
from hoglint.model import Edge, Graph
from hoglint.presets_py.posthog.facade import FacadeFacts
from hoglint.presets_py.posthog.pack import POSTHOG_PACK
from hoglint.rules.engine import Engine
from rules.support import config_from

FACADE = {
    "products/shop/backend/facade/api.py": {
        "defs": [func_def("close_order", 1, params=[["order_id", "a"]]), func_def("count_orders", 3, ret="int")],
    }
}


def test_a_null_return_is_no_untyped_slot_but_an_untyped_parameter_is() -> None:
    build = FactRepo(FACADE).build(CoreConfig(first_party=("products",)))
    edges = FacadeFacts(python_pack_io(build, Graph())[0]).produce()

    slots = sorted(
        (e.site.scope, e.attr("slot"), e.surface, e.attr("annotation")) for e in edges if e.kind == "exposes"
    )

    assert slots == [
        ("close_order", "param:order_id", "", "missing"),
        ("count_orders", "return", "builtins.int", ""),
    ]


RULES = """
schema = 1
extends = ["hoglint:django"]

[rules.bases]
primitive = "edge"
kinds = ["inherit"]
select = "edge.resolution == 'unresolved'"
ledger = false

[rules.slots]
primitive = "edge"
kinds = ["exposes"]
select = "edge.resolution == 'unresolved'"
ledger = false
"""


def test_a_facade_failure_leaves_rules_on_core_copied_kinds_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(self: FacadeFacts) -> list[Edge]:
        raise RuntimeError("facade facts broke")

    monkeypatch.setattr(FacadeFacts, "produce", fail)
    graph = Graph()
    POSTHOG_PACK.run(*python_pack_io(FactRepo(FACADE).build(CoreConfig(first_party=("products",))), graph))

    result = Engine(config_from(tmp_path, RULES), graph, tmp_path).evaluate()

    assert {rule: bool(run.incomplete) for rule, run in result.runs.items()} == {"bases": False, "slots": True}
