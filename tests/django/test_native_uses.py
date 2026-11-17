"""`django.uses` in the link (D49) on hand-written facts: the shape of every use, the locals it
follows, and the files it cannot read."""

from __future__ import annotations

import gc
from array import array
from pathlib import Path

import pytest

from core.support import Linked, func_def, import_row
from django_facts import APPS, DJANGO, FIRST_PARTY, FactRepo
from hoglint.core.config import CoreConfig
from hoglint.core.harness import ProducerPlan, ProducerStep
from hoglint.core.memo import PackMemo
from hoglint.core.reader import BuildReader
from hoglint.model import Graph
from hoglint.packs.django.config import DjangoPackConfig
from hoglint.packs.django.native_uses import NativeUses, file_id
from hoglint.packs.django.pack import USES_KINDS, DjangoPack, DjangoRun
from hoglint.packs.django.shapes import VOCABULARY
from hoglint.packs.posthog.pack import PosthogPack
from hoglint.plugins import PackSink, Refinement
from test_drives import repo as drives_repo

# One use per row: (model, chain, up) and the shape the link gives it: (shape, detail, flow shape).
# Rows run in scope 0 of shop/views.py, one line each from line 3.
SHAPES = [
    ("Order", ".objects.select_for_update().count()", "expr", "lock", "", ""),
    ("Order", ".objects.filter().update()", "expr", "write", "update", ""),
    ("Order", ".objects.filter().count()", "return", "scalar-chain", "count", ""),
    ("Order", ".objects.filter().first()", "assign:o", "instance-single", "", "write(delete)"),
    ("Order", ".all_teams.all()", "assign:qs", "instance-many", "all", "write(update)"),
    ("Order", "._default_manager.get()", "expr", "instance-single", "", ""),
    ("Order", "._meta.default_manager.all()", "expr", "instance-many", "all", ""),
    ("Order", "._meta.model.objects.exists()", "expr", "scalar-chain", "exists", ""),
    ("Order", "._meta.managers", "expr", "other", "Attribute:_meta.managers", ""),
    ("Order", "._meta", "expr", "_meta", "", ""),
    ("Order", ".objects.filter()", "arg:1:Prefetch", "prefetch", "", ""),
    ("Order", ".objects.filter()", "arg:0:models.Exists", "subquery", "Exists", ""),
    ("Order", ".objects.all()", "kw:queryset:serializers.PrimaryKeyRelatedField", "drf-field-queryset", "", ""),
    ("Order", ".objects.filter()[]", "expr", "instance-many", "filter", ""),
    ("Order", ".DoesNotExist", "except", "exception", "", ""),
    ("Order", ".Meta", "expr", "nested-class-attr", "Meta", ""),
    ("Order", ".pk", "expr", "other", "Attribute:pk", ""),
    ("Order", "", "param-ann:o", "annotation", "", ""),
    ("Order", "[]", "returns", "annotation", "", ""),
    ("Order", "()", "assign:obj", "construct", "", ""),
    ("Order", "[]", "expr", "in-collection", "", ""),
    ("Order", "", "elt:list", "in-collection", "", ""),
    ("Order", "", "arg:1:isinstance", "isinstance", "", ""),
    ("Order", "", "arg:0:cast", "passed-as-class", "cast", ""),
    ("Order", "", "arg:0:?Call", "passed-as-class", "?", ""),
    ("Order", "", "assign:model", "drf-model-serializer", "", ""),
    ("Order", "", "kw:model:Meta", "drf-model-serializer", "", ""),
    ("Order", "", "return", "other", "Return", ""),
    ("Order", "", "comp:elt", "other", "ListComp", ""),
    ("Order", "", "?Call.func", "other", "Call", ""),
    ("Order", ".objects.filter().values_list().first()", "return", "scalar-chain", "values_list", ""),
    ("Order", ".objects.values().filter().update()", "expr", "write", "update", ""),
    ("Order", "", "kw:sender:receiver", "sender", "", ""),
    ("Order", "", "bin:| bin:| assign:Target", "annotation", "", ""),
    ("Order", "", "bin:| typealias:Target", "annotation", "", ""),
    ("Order", "", "bin:| arg:1:isinstance", "other", "BinOp", ""),
    ("Order", "", "bin:| elt:tuple slice assign:Cache", "annotation", "", ""),
    ("Order", ".objects.filter().order_by()[].values()", "assign:rows", "scalar-chain", "values", ""),
    ("Invoice", ".objects.filter()", "await assign:qs", "instance-many", "filter", "lock"),
    ("Invoice", ".objects.all()", "assign:later", "instance-many", "all", ""),
]
# Later uses of the locals above: (line, root, chain, up). `o` flows into a delete; `qs` into an
# update before line 33 reassigns it, and into a lock after, through `qs2`, a local assigned from
# it; `later` only into a read, which changes nothing.
DERIVED = [
    (20, "qs", ".update()", "expr"),
    (40, "o", ".delete()", "expr"),
    (42, "qs", ".filter()", "assign:qs2"),
    (43, "qs2", ".select_for_update().first()", "expr"),
    (44, "later", ".count()", "expr"),
]


def views() -> dict[str, dict]:
    refs = [[0, -1, line, 4, 4, model, chain, up] for line, (model, chain, up, *_) in enumerate(SHAPES, start=3)]
    derived = [[0, -1, line, 4, 4, root, chain, up, 0] for line, root, chain, up in DERIVED]
    return {
        "shop/views.py": {
            "imports": [import_row("shop.models", "Order"), import_row("billing.models", "Invoice", line=2)],
            "defs": [func_def("close", 3)],
            "refs": refs,
            "derived": derived,
        }
    }


def unreadable(name: str, ref: list, derived: list) -> dict[str, dict]:
    return {
        f"shop/{name}.py": {
            "imports": [import_row("shop.models", "Order")],
            "defs": [func_def("run", 2)],
            "refs": [[0, -1, 3, 4, 4, "Order", *ref]],
            "derived": derived,
        }
    }


# Files the shape code cannot read: a derived row that does not parse as one, a derived row whose
# deriving scope is not a def of the file, and a call step without its callee.
UNREADABLE = {
    **unreadable("short_row", [".objects.all()", "assign:qs"], [[0, -1, 4, 4, 4, "qs", ".update()", "expr"]]),
    **unreadable("lost_scope", [".objects.all()", "assign:qs"], [[0, -1, 4, 4, 4, "qs", ".update()", "expr", 7]]),
    **unreadable("bare_call", ["", "arg:1"], []),
}


def linked(*groups: dict[str, dict], first_party: tuple[str, ...] = FIRST_PARTY) -> Linked:
    """The Django fixture and `groups`, linked by the native engine."""
    return FactRepo(*groups).link(CoreConfig(first_party=first_party))


def django_run(reader: BuildReader, sink: PackSink) -> DjangoRun:
    """The pack up to its model registry, which `django.uses` reads."""
    run = DjangoRun(reader, DjangoPackConfig(settings_modules=("proj.settings",)), Path("."), sink)
    run.read_apps()
    run.read_models()
    return run


def test_the_link_gives_every_use_its_shape_and_fails_the_files_it_cannot_read() -> None:
    made = linked(DJANGO, APPS, views(), UNREADABLE)
    graph, reader, sink = made.graph, made.reader(), made.sink()
    run = django_run(reader, sink)
    assert run.registry is not None
    staged = graph.link.django_uses(
        [file_id(scope) for scope in run.files], [(m.module, m.symbol) for m in run.registry.models], VOCABULARY
    )
    ids, shapes, details, attrs = graph.link.staged_columns(staged)
    lines = {edge.handle: edge.site.line for edge in graph.edges}
    found = [
        (lines[handle], shape, detail, dict(pairs).get("flow_shape", ""))
        for handle, shape, detail, pairs in zip(array("I", ids), shapes, details, attrs)
    ]
    assert sorted(found) == [(line, *row[3:]) for line, row in enumerate(SHAPES, start=3)]

    paths = graph.link.paths()
    assert sorted(paths[file] for file in staged.failed()) == sorted(UNREADABLE)
    failures = NativeUses(reader, run.registry, sink).produce(run.files, USES_KINDS)
    assert sorted(edge.site.path for edge in failures) == sorted(UNREADABLE)
    assert sorted(edge.attr("error") for edge in failures) == [
        "IndexError: list index out of range",
        "IndexError: list index out of range",
        "ValueError: not enough values to unpack (expected 9, got 8)",
    ]


def test_a_later_producer_meets_the_fields_the_link_producer_set() -> None:
    made = linked(DJANGO, APPS, views())
    graph = made.graph
    DjangoPack.run(made.reader(), DjangoPackConfig(settings_modules=("proj.settings",)), made.sink(), Path("."))
    uses = graph.by_kind("reference")
    flowing = next(edge for edge in uses if edge.attr("flow_shape") == "write(delete)")
    locked = next(edge for edge in uses if edge.shape == "lock")

    def relabel() -> list[Refinement]:
        return [
            Refinement(locked.handle, "lock", ""),
            Refinement(flowing.handle, "lock", "", (("flow_shape", "lock"),)),
        ]

    ProducerPlan([ProducerStep("test.relabel@1", USES_KINDS, relabel)]).run(made.sink())
    reason = graph.failed_producers["test.relabel@1"].reason
    assert reason.startswith("sets shape of edge ")
    assert reason.endswith(" (django.uses@1 set 'instance-single') and 2 more apart")


def both_packs(memo: PackMemo | None) -> Graph:
    """The Django pack and the PostHog drives over one fixture."""
    group = drives_repo().records
    made = linked(group, first_party=(*FIRST_PARTY, "posthog", "products", "common"))
    reader = made.reader(memo)
    DjangoPack.run(reader, DjangoPackConfig(settings_modules=("proj.settings",)), made.sink(), Path("."))
    PosthogPack.run(reader, made.sink())
    if memo is not None:
        memo.flush()
    return made.graph


@pytest.mark.parametrize("memo", [False, True])
def test_the_packs_leave_no_reference_cycle_behind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memo: bool) -> None:
    # A native run turns the cyclic collector off, so a cycle keeps the pack objects until exit.
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    gc.collect()
    gc.disable()
    try:
        graph = both_packs(PackMemo(None, "test") if memo else None)
        gc.set_debug(gc.DEBUG_SAVEALL)
        gc.collect()
        left = sorted(
            {type(found).__qualname__ for found in gc.garbage if type(found).__module__.startswith("hoglint")}
        )
    finally:
        gc.set_debug(0)
        gc.garbage.clear()
        gc.enable()
    assert graph.edges
    assert left == []
