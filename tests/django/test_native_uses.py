"""`django.uses` in the link (D49) against the Python producer, which stays the meaning, on
hand-written facts that both engines load: every shape the producer knows, the locals it follows,
and files it cannot read."""

from __future__ import annotations

import gc
import json
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from core.support import SITE, func_def, import_row
from django_facts import APPS, DJANGO, FIRST_PARTY, FactRepo
from hoglint.core.config import CoreConfig
from hoglint.core.harness import GraphSink, ProducerPlan, ProducerStep
from hoglint.core.memo import PackMemo
from hoglint.core.reader import BuildReader, python_pack_io
from hoglint.model import Edge, Graph
from hoglint.native.build import NativeBuild
from hoglint.native.config import link_config
from hoglint.native.graph import NativeGraph, NativeSink
from hoglint.native.queries import NativePackQueries
from hoglint.packs.django.config import DjangoPackConfig
from hoglint.packs.django.native_uses import NativeUses, file_id
from hoglint.packs.django.pack import USES_KINDS, DjangoPack, DjangoRun
from hoglint.packs.django.shapes import VOCABULARY
from hoglint.packs.django.uses import UseProducer
from hoglint.plugins import PackSink, Refinement, Refinements
from hoglint.presets_py.posthog.pack import PosthogPack
from native.require import native_module
from test_drives import repo as drives_repo

# One use per row: (model, chain, up). Rows run in scope 0 of shop/views.py, one line each.
SHAPES = [
    ("Order", ".objects.select_for_update().count()", "expr"),
    ("Order", ".objects.filter().update()", "expr"),
    ("Order", ".objects.filter().count()", "return"),
    ("Order", ".objects.filter().first()", "assign:o"),
    ("Order", ".all_teams.all()", "assign:qs"),
    ("Order", "._default_manager.get()", "expr"),
    ("Order", "._meta.default_manager.all()", "expr"),
    ("Order", "._meta.model.objects.exists()", "expr"),
    ("Order", "._meta.managers", "expr"),
    ("Order", "._meta", "expr"),
    ("Order", ".objects.filter()", "arg:1:Prefetch"),
    ("Order", ".objects.filter()", "arg:0:models.Exists"),
    ("Order", ".objects.all()", "kw:queryset:serializers.PrimaryKeyRelatedField"),
    ("Order", ".objects.filter()[]", "expr"),
    ("Order", ".DoesNotExist", "except"),
    ("Order", ".Meta", "expr"),
    ("Order", ".pk", "expr"),
    ("Order", "", "param-ann:o"),
    ("Order", "[]", "returns"),
    ("Order", "()", "assign:obj"),
    ("Order", "[]", "expr"),
    ("Order", "", "elt:list"),
    ("Order", "", "arg:1:isinstance"),
    ("Order", "", "arg:0:cast"),
    ("Order", "", "arg:0:?Call"),
    ("Order", "", "assign:model"),
    ("Order", "", "kw:model:Meta"),
    ("Order", "", "return"),
    ("Order", "", "comp:elt"),
    ("Order", "", "?Call.func"),
    ("Order", ".objects.filter().values_list().first()", "return"),
    ("Order", ".objects.values().filter().update()", "expr"),
    ("Order", "", "kw:sender:receiver"),
    ("Order", "", "bin:| bin:| assign:Target"),
    ("Order", "", "bin:| typealias:Target"),
    ("Order", "", "bin:| arg:1:isinstance"),
    ("Order", "", "bin:| elt:tuple slice assign:Cache"),
    ("Order", ".objects.filter().order_by()[].values()", "assign:rows"),
    ("Invoice", ".objects.filter()", "await assign:qs"),
    ("Invoice", ".objects.all()", "assign:later"),
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
    refs = [[0, -1, line, 4, 4, model, chain, up] for line, (model, chain, up) in enumerate(SHAPES, start=3)]
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


@dataclass(frozen=True)
class Engines:
    """One fact set loaded by both engines, with their pack readers and sinks."""

    python: tuple[Graph, BuildReader, GraphSink]
    native: tuple[NativeGraph, BuildReader, NativeSink]


def engines(
    tmp_path: Path, *groups: dict[str, dict], first_party: tuple[str, ...] = FIRST_PARTY, memo: PackMemo | None = None
) -> Engines:
    native = native_module()
    repo = FactRepo(*groups)
    config = CoreConfig(first_party=first_party)
    build = repo.build(config)
    graph = build.graph()
    python = (graph, *python_pack_io(build, graph))
    facts = tmp_path / "facts.jsonl"
    facts.write_text("".join(json.dumps(record) + "\n" for record in repo.records.values()))
    external = {path: path.removeprefix(SITE) for path in repo.records if path.startswith(SITE)}
    analyzed = tuple(path for path in repo.records if path not in external)
    link = native.link_jsonl(str(facts), json.dumps(link_config(config, analyzed, external)), 1)
    native_graph = NativeGraph(link, {})
    native_build = NativeBuild(config, link, external, native_graph.core.maker)
    native_graph.modules = native_build.index.repo_modules()
    if memo is not None:
        native_build.trace_with(memo.tracer)
    reader = BuildReader(native_build, NativePackQueries(native_build, native_graph, link), memo)
    return Engines(python, (native_graph, reader, NativeSink(native_graph)))


def django_run(reader: BuildReader, sink: PackSink) -> DjangoRun:
    """The pack up to its model registry, which `django.uses` reads."""
    run = DjangoRun(reader, DjangoPackConfig(settings_modules=("proj.settings",)), Path("."), sink)
    run.read_apps()
    run.read_models()
    return run


def rows(made: Refinements) -> list[tuple[Any, ...]]:
    return [(made.handles[at], made.shapes[at], made.details[at], tuple(made.attrs[at])) for at in range(len(made))]


def test_the_link_judges_every_use_as_the_python_producer_does(tmp_path: Path) -> None:
    graph, reader, sink = engines(tmp_path, DJANGO, APPS, views(), UNREADABLE).native
    run = django_run(reader, sink)
    assert run.registry is not None
    python = UseProducer(reader, run.registry).produce(run.files)
    staged = graph.link.django_uses(
        [file_id(scope) for scope in run.files], [(m.module, m.symbol) for m in run.registry.models], VOCABULARY
    )
    ids, shapes, details, attrs = graph.link.staged_columns(staged)
    native = Refinements(list(array("I", ids)), shapes, details, [tuple(pairs) for pairs in attrs])

    assert rows(native) == rows(Refinements.of(python))
    assert len(native) == len(SHAPES)
    assert {(shape, detail) for shape, detail in zip(native.shapes, native.details)} >= {
        ("lock", ""),
        ("write", "update"),
        ("scalar-chain", "count"),
        ("instance-single", ""),
        ("instance-many", "all"),
        ("scalar-chain", "exists"),
        ("other", "Attribute:_meta.managers"),
        ("_meta", ""),
        ("prefetch", ""),
        ("subquery", "Exists"),
        ("drf-field-queryset", ""),
        ("exception", ""),
        ("nested-class-attr", "Meta"),
        ("other", "Attribute:pk"),
        ("annotation", ""),
        ("construct", ""),
        ("in-collection", ""),
        ("isinstance", ""),
        ("passed-as-class", "cast"),
        ("passed-as-class", "?"),
        ("drf-model-serializer", ""),
        ("other", "Return"),
        ("other", "ListComp"),
        ("other", "Call"),
    }
    assert [dict(pairs).get("flow_shape") for pairs in native.attrs if pairs] == [
        "write(delete)",
        "write(update)",
        "lock",
    ]

    paths = graph.link.paths()
    assert sorted(paths[file] for file in staged.failed()) == sorted(UNREADABLE)
    failures = [edge for edge in python if isinstance(edge, Edge)]
    made = NativeUses(UseProducer(reader, run.registry), sink).produce(run.files, USES_KINDS)
    assert made == failures
    assert sorted(edge.attr("error") for edge in made) == [
        "IndexError: list index out of range",
        "IndexError: list index out of range",
        "ValueError: not enough values to unpack (expected 9, got 8)",
    ]


def test_a_later_producer_meets_the_fields_the_link_producer_set(tmp_path: Path) -> None:
    both = engines(tmp_path, DJANGO, APPS, views())
    failed: list[dict[str, str]] = []
    refined: list[list[tuple[str, str, tuple[tuple[str, str], ...]]]] = []
    for graph, reader, sink in (both.python, both.native):
        DjangoPack.run(reader, DjangoPackConfig(settings_modules=("proj.settings",)), sink, Path("."))
        uses = graph.by_kind("reference")
        flowing = next(edge for edge in uses if edge.attr("flow_shape") == "write(delete)")
        locked = next(edge for edge in uses if edge.shape == "lock")

        def relabel(flowing: Edge = flowing, locked: Edge = locked) -> list[Refinement]:
            return [
                Refinement(locked.handle, "lock", ""),
                Refinement(flowing.handle, "lock", "", (("flow_shape", "lock"),)),
            ]

        ProducerPlan([ProducerStep("test.relabel@1", USES_KINDS, relabel)]).run(sink)
        failed.append({name: failure.reason for name, failure in graph.failed_producers.items()})
        refined.append([(edge.shape, edge.shape_detail, edge.attrs) for edge in graph.by_kind("reference")])

    assert failed[0] == failed[1]
    assert refined[0] == refined[1]
    reason = failed[1]["test.relabel@1"]
    assert reason.startswith("sets shape of edge ")
    assert reason.endswith(" (django.uses@1 set 'instance-single') and 2 more apart")


def both_packs(tmp_path: Path, engine: str, memo: PackMemo | None) -> Graph:
    """The Django pack and the PostHog drives over one fixture, on one engine."""
    group = drives_repo().records
    made = engines(tmp_path, group, first_party=(*FIRST_PARTY, "posthog", "products", "common"), memo=memo)
    graph, reader, sink = made.python if engine == "python" else made.native
    DjangoPack.run(reader, DjangoPackConfig(settings_modules=("proj.settings",)), sink, Path("."))
    PosthogPack.run(reader, sink)
    if memo is not None:
        memo.flush()
    return graph


@pytest.mark.parametrize("engine, memo", [("python", False), ("native", False), ("native", True)])
def test_the_packs_leave_no_reference_cycle_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engine: str, memo: bool
) -> None:
    # A native run turns the cyclic collector off, so a cycle keeps the pack objects until exit.
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    gc.collect()
    gc.disable()
    try:
        graph = both_packs(tmp_path, engine, PackMemo(None, "test") if memo else None)
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
