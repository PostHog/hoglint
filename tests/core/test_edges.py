from __future__ import annotations

import pytest

from hoglint.core.producers import use_position
from hoglint.core.symbols import FUNC, TC_ELSE, TRY, TYPE_ONLY
from hoglint.model import Context, Resolution

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
        FileFacts(VIEWS)
        .import_from("checkout.facade", "charge")
        .import_from("checkout.compat", "models")
        .import_from("checkout.lazy", "refund")
        .import_from("checkout.missing", "thing")
        .import_module("requests")
        .import_from("too_far", "x", level=3)
        .import_module("nspkg.sub")
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
        ("web/a.py", TC_ELSE, {Context.PROD, Context.TYPE_ONLY}),
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
        (None, "base:0", "base"),
        (None, "for-iter", "iterate"),
        (None, "assign-target", "target"),
    ],
)
def test_use_positions_are_neutral(next_token: str | None, up: str, position: str) -> None:
    assert use_position(next_token, up) == position
