from __future__ import annotations

from pathlib import Path

from core.support import (
    assign_row as assign,
    class_def,
    func_def,
    import_row as import_from,
)
from django_facts import FactRepo
from hoglint.core.symbols import FUNC
from hoglint.model import Resolution
from hoglint.packs.django.config import DatabaseRouting
from hoglint.packs.django.pack import MODEL_KINDS

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
    "billing/hooks.py": {"defs": [func_def("register_refund_hook", 1), class_def("register_payload", [], 2)]},
    "shop/wiring.py": {
        "imports": [
            import_from("billing.hooks", "register_refund_hook", scope=0, flags=FUNC, line=2),
            import_from("billing.hooks", "register_payload", scope=0, flags=FUNC, line=2),
        ],
        "defs": [
            func_def("wire", 1),
            func_def("handler", 7, dec=[{"call": "register_refund_hook", "args": []}]),
        ],
        "callees": [
            [0, -1, "register_refund_hook", 1, 3, 4],
            [0, -1, "self.registry.register_refund_hook", 1, 4, 4],
            [0, -1, "register_payload", 1, 5, 4],
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


def test_installs_edges_see_imports_only_where_they_are_bound_and_skip_class_hooks() -> None:
    graph = FactRepo(HOOKS).graph(hooks=("billing.hooks.register_refund_hook", "billing.hooks.register_payload"))

    edges = sorted(
        (e.site.scope, e.attr("form"), str(e.target), e.resolution, e.attr("callback"))
        for e in graph.by_kind("installs")
    )

    assert edges == [
        ("handler", "decorator", "None", Resolution.UNRESOLVED, "shop.wiring.handler"),
        ("wire", "call", "None", Resolution.UNRESOLVED, ""),
        ("wire", "call", "billing.hooks.register_refund_hook", Resolution.RESOLVED, ""),
    ]


RECEIVER_LISTS = {
    "shop/receivers.py": {
        "imports": [
            import_from("django.dispatch.dispatcher", "receiver"),
            import_from("django.db.models.signals", "post_save", line=2),
            import_from("django.db.models.signals", "post_delete", line=2),
            import_from("shop.models", "Order", line=3),
        ],
        "defs": [
            func_def("on_opaque", 6, dec=[{"call": "receiver", "args": [{"o": "List"}], "kw": [["sender", "Order"]]}]),
            func_def(
                "on_literal",
                9,
                dec=[
                    {"call": "receiver", "args": [{"tuple": ["post_save", "post_delete"]}], "kw": [["sender", "Order"]]}
                ],
            ),
        ],
        "uses": [[-1, 0, "post_save", "", 1, 5, 11], [-1, 0, "post_delete", "", 1, 5, 22]],
    }
}


def test_receiver_on_a_list_of_signals_gives_one_edge_per_signal() -> None:
    graph = FactRepo(RECEIVER_LISTS).graph()

    edges = sorted((e.site.scope, e.attr("signal"), str(e.target)) for e in graph.by_kind("signal"))

    assert edges == [
        ("on_literal", "django.db.models.signals.post_delete", "shop.models.Order"),
        ("on_literal", "django.db.models.signals.post_save", "shop.models.Order"),
        ("on_opaque", "django.db.models.signals.post_delete", "shop.models.Order"),
        ("on_opaque", "django.db.models.signals.post_save", "shop.models.Order"),
    ]


TEAMS = {
    "shop/teams.py": {
        "imports": [
            import_from("django.db.models.base", "Model"),
            import_from("django.db.models.fields.related", "ManyToManyField"),
            import_from("django.contrib.auth.models", "Group"),
        ],
        "defs": [
            class_def("Team", ["Model"], 2),
            class_def("Staff", ["Group"], 4),
            class_def("Ledger", ["Model"], 6),
            class_def("Meta", [], 7, scope=2, flags=8),
            class_def("Entry", ["Ledger"], 9),
        ],
        "assigns": [
            assign("friends", {"call": "ManyToManyField", "args": [{"s": "Team"}]}, 3, scope=0, flags=8),
            assign("app_label", {"s": "books"}, 8, scope=3, flags=8),
        ],
    }
}


def test_relations_follow_django_for_m2m_to_own_name_concrete_parents_and_meta() -> None:
    graph = FactRepo(TEAMS).graph()

    relations = {(e.site.scope, e.attr("field")): e for e in graph.by_kind("relation")}
    accessors = {e.attr("field"): e.surface for e in graph.by_kind("reverse-accessor")}

    assert graph.failed_producers == {}
    assert relations[("Team", "friends")].attr("hidden") == "false"
    assert accessors["friends"] == "team_set"
    assert str(relations[("Staff", "group_ptr")].target) == "django.contrib.auth.models.Group"
    assert graph.symbols["shop.teams.Ledger"]["label"] == "books.Ledger"
    assert graph.symbols["shop.teams.Entry"]["label"] == "shop.Entry"


def test_a_model_that_cannot_be_read_is_an_unresolved_edge_and_the_others_still_count() -> None:
    broken = {
        "shop/teams.py": {
            **TEAMS["shop/teams.py"],
            "assigns": [{"s": 0, "c": 0, "op": "=", "t": ["friends"], "v": {"call": "ManyToManyField"}, "f": 8}],
        }
    }
    graph = FactRepo(broken).graph()

    failed = [e for e in graph.by_kind("relation") if e.attr("reason") == "producer-error"]

    assert graph.failed_producers == {}
    assert [(e.site.scope, e.resolution) for e in failed] == [("Team", Resolution.UNRESOLVED)]
    assert {e.attr("field") for e in graph.by_kind("relation")} >= {"order", "group_ptr"}


VIEWS = {
    "shop/views.py": {
        "imports": [import_from("shop.models", "Order"), import_from("billing.models", "Invoice", line=2)],
        "defs": [func_def("close", 3)],
        "refs": [
            [0, -1, 4, 9, 4, "Order", ".objects.filter()", "assign:qs"],
            [0, -1, 6, 9, 4, "Invoice", ".objects.filter()", "assign:qs"],
        ],
        "derived": [[0, -1, 5, 4, 4, "qs", ".update()", "expr", 0], [0, -1, 7, 4, 4, "qs", ".count()", "expr", 0]],
    }
}


def test_a_reassigned_local_hands_each_later_use_to_the_assignment_before_it() -> None:
    graph = FactRepo(VIEWS).graph()

    flow = {e.surface: e.attr("flow_shape") for e in graph.by_kind("reference") if e.site.module == "shop.views"}

    assert flow == {"shop.models.Order": "write(update)", "billing.models.Invoice": ""}


def test_finding_no_model_fails_the_model_producers_instead_of_reading_clean() -> None:
    no_models = {"shop/models.py": {"defs": [class_def("Order", [], 2)]}, "billing/models.py": {}}
    graph = FactRepo(no_models).graph()

    failed = {name: failure.reason for name, failure in graph.failed_producers.items()}

    assert failed["django.models@1"] == "NoModels: no first-party model found"
    assert failed["django.relations@1"] == "django.models@1 failed"
    assert "django.migrations@1" not in failed


def test_a_failed_migration_read_fails_the_symbols_that_carry_its_databases() -> None:
    graph = FactRepo().graph(routing=DatabaseRouting(Path("missing/db_routing.yaml")))

    failed = graph.failed_producers

    assert failed["django.migrations@1"].reason.startswith("FileNotFoundError")
    assert failed["django.symbols@1"].reason == "django.migrations@1 failed"
    assert failed["django.symbols@1"].kinds == MODEL_KINDS
