from __future__ import annotations

from django_facts import FactRepo, func_def, import_from
from hoglint.model import Resolution
from hoglint.spike.core import FUNC

RECEIVERS = {
    "shop/receivers.py": {
        "imports": [
            import_from("django.dispatch.dispatcher", "receiver"),
            import_from("django.db.models.signals", "post_save", line=2),
            import_from("shop.models", "Order", line=3),
        ],
        "defs": [
            func_def("on_order", 5, dec=[{"call": "receiver", "args": ["post_save"], "kw": [["sender", "Order"]]}]),
            func_def(
                "on_label",
                7,
                dec=[{"call": "receiver", "args": ["post_save"], "kw": [["sender", {"s": "shop.Order"}]]}],
            ),
            func_def("on_any", 9, dec=[{"call": "receiver", "args": ["post_save"]}]),
            func_def(
                "on_variable", 11, dec=[{"call": "receiver", "args": ["post_save"], "kw": [["sender", {"o": "Call"}]]}]
            ),
        ],
    }
}

HOOKS = {
    "billing/hooks.py": {"defs": [func_def("register_refund_hook", 1)]},
    "shop/wiring.py": {
        "imports": [import_from("billing.hooks", "register_refund_hook", scope=0, flags=FUNC, line=2)],
        "defs": [
            func_def("wire", 1),
            func_def("handler", 7, dec=[{"call": "register_refund_hook", "args": []}]),
        ],
        "callees": [
            [0, -1, "register_refund_hook", 1, 3, 4],
            [0, -1, "self.registry.register_thing", 1, 4, 4],
            [-1, 1, "register_refund_hook", 1, 6, 1],
        ],
    },
}


def test_reverse_accessor_owner_is_the_defining_module_and_sealed_relations_add_none() -> None:
    graph = FactRepo().graph()

    accessors = graph.by_kind("reverse-accessor")
    relations = {e.attr("field"): e for e in graph.by_kind("relation")}

    assert [(e.site.module, e.surface, str(e.target), e.attr("model_label")) for e in accessors] == [
        ("billing.models", "invoice_set", "shop.models.Order", "legacy_billing.Invoice")
    ]
    assert relations["hidden_order"].attr("sealed") == "true"
    assert graph.failed_producers == {}


def test_installed_app_without_facts_is_a_gap_and_the_rest_still_loads() -> None:
    graph = FactRepo().graph()

    ghost = graph.symbols["ghost"]
    assert (ghost["kind"], ghost["label"], ghost["known"]) == ("django-app", "ghost", "false")
    assert graph.symbols["shop.models.Order"]["label"] == "shop.Order"
    assert [e.resolution for e in graph.by_kind("loads") if e.attr("app") == "ghost"] == [Resolution.UNRESOLVED]


def test_signal_edges_resolve_class_and_label_senders_and_keep_the_rest_unresolved() -> None:
    graph = FactRepo(RECEIVERS).graph()

    edges = {e.site.scope: e for e in graph.by_kind("signal")}

    assert {scope: (str(e.target), e.resolution, e.attr("reason")) for scope, e in edges.items()} == {
        "on_order": ("shop.models.Order", Resolution.RESOLVED, ""),
        "on_label": ("shop.models.Order", Resolution.RESOLVED, ""),
        "on_any": ("None", Resolution.UNRESOLVED, "no-sender"),
        "on_variable": ("None", Resolution.UNRESOLVED, "variable-sender"),
    }
    assert {e.attr("signal") for e in edges.values()} == {"django.db.models.signals.post_save"}


def test_installs_edges_from_calls_and_decorators_through_function_local_imports() -> None:
    graph = FactRepo(HOOKS).graph(hooks=("**.register_*",))

    edges = sorted(
        (e.site.scope, e.attr("form"), str(e.target), e.resolution, e.attr("callback"))
        for e in graph.by_kind("installs")
    )

    assert edges == [
        ("handler", "decorator", "billing.hooks.register_refund_hook", Resolution.RESOLVED, "shop.wiring.handler"),
        ("wire", "call", "None", Resolution.UNRESOLVED, ""),
        ("wire", "call", "billing.hooks.register_refund_hook", Resolution.RESOLVED, ""),
    ]
