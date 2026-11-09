from __future__ import annotations

import pytest

from hoglint.core.build import CoreBuild
from hoglint.core.config import CoreConfig
from hoglint.core.producers import BuildContext, Producer, use_position
from hoglint.core.symbols import FUNC, TC_ELSE, TRY, TYPE_ONLY
from hoglint.facts import FactStore
from hoglint.model import Context, Graph, Resolution
from hoglint.outputs.tach_map import TachMap
from hoglint.plugins import ProducerFailure

from core.support import FileFacts, build, edges_of

VIEWS = "web/views.py"


def repo() -> list[FileFacts]:
    return [
        FileFacts("checkout/__init__.py"),
        FileFacts("checkout/logic.py").define("charge").define("refund"),
        FileFacts("checkout/facade/__init__.py").import_from("api", "charge", level=1),
        FileFacts("checkout/facade/api.py").import_from("checkout.logic", "charge"),
        FileFacts("checkout/compat.py").import_from("django.db", "models"),
        FileFacts("checkout/lazy.py")
        .define("__getattr__")
        .assign("_BASE", {"s": "checkout."})
        .assign("_LAZY", {"dict": [[{"s": "refund"}, {"s": "logic"}]]}),
        FileFacts("nspkg/sub/mod.py"),
        FileFacts("checkout/optional.py")
        .import_from("checkout.logic", "refund", flags=TRY)
        .assign("refund", None, flags=TRY),
        FileFacts(VIEWS)
        .import_from("checkout.facade", "charge")
        .import_from("checkout.compat", "models")
        .import_from("checkout.lazy", "refund")
        .import_from("checkout.missing", "thing")
        .import_module("requests")
        .import_from("too_far", "x", level=3)
        .import_module("nspkg.sub")
        .import_from("checkout.optional", "refund")
        .ref("charge", chain="()", up="expr")
        .string("checkout.facade.api.charge", up="arg:0:mock.patch decorator")
        .string("checkout.logic.gone", up="expr")
        .string("checkout.team", up="kw:to:ForeignKey"),
    ]


@pytest.mark.parametrize(
    "surface, resolution, origin, via",
    [
        ("checkout.facade.charge", Resolution.RESOLVED, "checkout.logic.charge", ["checkout.facade.api.charge"]),
        ("checkout.compat.models", Resolution.EXTERNAL, "django.db.models", []),
        ("checkout.lazy.refund", Resolution.RESOLVED, "checkout.logic.refund", []),
        ("checkout.missing.thing", Resolution.UNRESOLVED, None, []),
        ("requests", Resolution.EXTERNAL, "requests", []),
        ("...too_far.x", Resolution.UNRESOLVED, None, []),
        ("nspkg.sub", Resolution.RESOLVED, "nspkg.sub", []),
        ("checkout.optional.refund", Resolution.RESOLVED, "checkout.logic.refund", []),
    ],
)
def test_import_edges_keep_surface_via_and_origin(
    surface: str, resolution: Resolution, origin: str | None, via: list[str]
) -> None:
    edges = {edge.surface: edge for edge in edges_of(build(repo()), "import", VIEWS)}
    edge = edges[surface]
    assert edge.resolution == resolution
    assert [str(t) for t in edge.targets] == ([origin] if origin else [])
    assert [str(hop) for hop in edge.via] == via


def test_reference_names_surface_and_origin_with_a_use_position() -> None:
    [edge] = edges_of(build(repo()), "reference", VIEWS)
    assert (edge.surface, str(edge.target), edge.use) == ("checkout.facade.charge", "checkout.logic.charge", "call")


@pytest.mark.parametrize(
    "surface, resolution, role",
    [
        ("checkout.facade.api.charge", Resolution.RESOLVED, "patch-target"),
        ("checkout.logic.gone", Resolution.UNRESOLVED, "literal"),
    ],
)
def test_dotted_path_strings(surface: str, resolution: Resolution, role: str) -> None:
    edges = {edge.surface: edge for edge in edges_of(build(repo()), "dotted-path", VIEWS)}
    assert (edges[surface].resolution, edges[surface].attr("role")) == (resolution, role)
    assert "checkout.team" not in edges  # one dot and no match: a label, not a path


@pytest.mark.parametrize(
    "path, flags, expected",
    [
        ("web/a.py", 0, {Context.PROD}),
        ("web/a.py", TYPE_ONLY, {Context.PROD, Context.TYPE_ONLY}),
        ("web/a.py", TC_ELSE, {Context.PROD, Context.TYPE_CHECKING_ELSE}),
        ("web/a.py", FUNC, {Context.PROD, Context.FUNCTION_LOCAL}),
        ("web/a.py", TRY, {Context.PROD, Context.CONDITIONAL}),
        ("web/tests/test_a.py", 0, {Context.TEST}),
        ("web/a.pyi", 0, {Context.PROD, Context.TYPE_ONLY}),
    ],
)
def test_contexts_come_from_syntax_and_path(path: str, flags: int, expected: set[Context]) -> None:
    files = [
        FileFacts("checkout/logic.py").define("charge"),
        FileFacts(path).import_from("checkout.logic", "charge", flags=flags),
    ]
    [edge] = edges_of(build(files), "import", path)
    assert edge.contexts == expected


@pytest.mark.parametrize(
    "next_token, up, position",
    [
        ("()", "expr", "call"),
        ("[]", "return", "subscript"),
        ("objects", "assign:x", "attribute"),
        (None, "arg:1:isinstance if-test", "argument"),
        (None, "await return", "return"),
        (None, "assign:x", "store"),
        (None, "param-ann:x", "annotation"),
        (None, "returns", "annotation"),
        (None, "base:0", "base"),
        (None, "for-iter", "iterate"),
        (None, "assign-target", "target"),
    ],
)
def test_use_positions_are_neutral(next_token: str | None, up: str, position: str) -> None:
    assert use_position(next_token, up) == position


@pytest.mark.parametrize("kind", ["reference", "dotted-path", "dynamic-import"])
def test_row_flags_give_refs_strs_and_calls_their_statement_context(kind: str) -> None:
    files = [
        FileFacts("checkout/logic.py").define("charge"),
        FileFacts(VIEWS)
        .import_from("checkout.logic", "charge")
        .import_module("importlib")
        .ref("charge", chain="()", flags=TYPE_ONLY)
        .string("checkout.logic.charge", flags=TYPE_ONLY)
        .call("importlib.import_module", [{"s": "checkout.logic"}], [], flags=TYPE_ONLY),
    ]
    [edge] = edges_of(build(files), kind, VIEWS)
    assert edge.contexts == {Context.PROD, Context.TYPE_ONLY}


@pytest.mark.parametrize(
    "args, kws, surface, argument",
    [
        ([{"s": "checkout.logic"}], [], "checkout.logic", "literal"),
        ([], [["name", {"s": "checkout.logic"}]], "checkout.logic", "literal"),
        ([], [["package", {"s": "checkout"}]], "", "missing"),
        ([], [[None, "options"]], "", "missing"),
    ],
)
def test_dynamic_import_reads_the_module_name_argument(args: list, kws: list, surface: str, argument: str) -> None:
    files = [
        FileFacts("checkout/logic.py"),
        FileFacts(VIEWS).import_module("importlib").call("importlib.import_module", args, kws),
    ]
    [edge] = edges_of(build(files), "dynamic-import", VIEWS)
    assert (edge.surface, edge.attr("argument")) == (surface, argument)


@pytest.mark.parametrize(
    "facts, counted",
    [
        (FileFacts(VIEWS).import_from("checkout.logic", "charge", flags=TYPE_ONLY), False),
        (FileFacts(VIEWS).import_from("checkout.logic", "charge", flags=TC_ELSE), False),
        (FileFacts(VIEWS).import_from("checkout.logic", "charge", flags=TRY), True),
        (FileFacts(VIEWS).string("checkout.logic.charge", flags=TYPE_ONLY), True),
    ],
)
def test_tach_map_skips_imports_in_either_type_checking_branch(facts: FileFacts, counted: bool) -> None:
    files = [FileFacts("checkout/__init__.py"), FileFacts("checkout/logic.py").define("charge"), facts]
    graph = build(files)
    mapping = TachMap(graph, [f.record["path"] for f in files]).dependents()
    assert mapping == ({"checkout/logic.py": [VIEWS]} if counted else {})


class PartialProducer(Producer):
    """Records what a producer may record besides edges: a module and a partial failure."""

    kind = "partial"
    failure = ProducerFailure("one file unread", frozenset({"partial"}))

    def run(self, context: BuildContext, graph: Graph) -> None:
        graph.modules["generated.mod"] = "generated/mod.py"
        graph.failed_producers["partial@1"] = self.failure


def test_a_core_producer_keeps_the_modules_and_failures_it_records() -> None:
    graph = CoreBuild(CoreConfig(), FactStore()).graph((PartialProducer,))

    assert graph.modules["generated.mod"] == "generated/mod.py"
    assert graph.failed_producers == {"partial@1": PartialProducer.failure}
