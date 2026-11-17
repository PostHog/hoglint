from __future__ import annotations

from core.support import func_def
from django_facts import FactRepo
from hoglint.core.config import CoreConfig
from hoglint.packs.posthog.facade import FacadeFacts

FACADE = {
    "products/shop/backend/facade/api.py": {
        "defs": [func_def("close_order", 1, params=[["order_id", "a"]]), func_def("count_orders", 3, ret="int")],
    }
}


def test_a_null_return_is_no_untyped_slot_but_an_untyped_parameter_is() -> None:
    edges = FacadeFacts(FactRepo(FACADE).link(CoreConfig(first_party=("products",))).reader()).produce()

    slots = sorted(
        (e.site.scope, e.attr("slot"), e.surface, e.attr("annotation")) for e in edges if e.kind == "exposes"
    )

    assert slots == [
        ("close_order", "param:order_id", "", "missing"),
        ("count_orders", "return", "builtins.int", ""),
    ]
