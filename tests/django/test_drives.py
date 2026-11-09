from __future__ import annotations

from core.support import Repo, class_def, func_def, import_row
from hoglint.core.config import CoreConfig
from hoglint.model import Graph, Resolution
from hoglint.native.queries import PackQueries
from hoglint.presets_py.posthog.drives import DISPATCH_CALLS, MAX_CALL_DEPTH, DrivesProducer

DISPATCH = sorted(DISPATCH_CALLS)[0]
HELPERS = MAX_CALL_DEPTH + 1  # h1 -> ... -> h9 -> dispatcher: one hop past the limit from h1


def callee_row(scope: int, callee: str, line: int) -> list:
    return [scope, -1, callee, 1, line, 0]


def name_row(scope: int, value: str, line: int) -> list:
    return [scope, -1, value, 1, line, 0]


def repo() -> Repo:
    """A query kind dispatched to the `charts` product, a chain of helpers that ends in the
    dispatcher, and a test whose methods call into that chain from its start and its middle."""
    helpers = [func_def(f"h{i}", i) for i in range(1, HELPERS + 1)]
    calls = [callee_row(i - 1, f"h{i + 1}", i) for i in range(1, HELPERS)]
    calls.append(callee_row(HELPERS - 1, DISPATCH, HELPERS))
    deep, shallow = 1, 2
    return Repo(
        {
            "posthog/schema_enums.py": {},
            "posthog/hogql_queries/query_runner.py": {
                "branches": [[-1, -1, 1, 0, 0, 3, {"cmp": "kind", "cs": [{"s": "TrendsQuery"}]}]],
                "imports": [[-1, 2, 0, 0, "products.charts.backend.hogql_queries.trends", 0, "TrendsRunner", None]],
            },
            "products/charts/backend/hogql_queries/trends.py": {"defs": [class_def("TrendsRunner", [], 1)]},
            "common/helpers.py": {"defs": helpers, "callees": calls},
            "posthog/test/test_trends.py": {
                "imports": [import_row("common.helpers", "h1", line=1), import_row("common.helpers", "h5", line=2)],
                "defs": [
                    class_def("TestTrends", [], 3),
                    func_def("test_from_the_start", 4, scope=0, flags=8),
                    func_def("test_from_the_middle", 6, scope=0, flags=8),
                ],
                "names": [name_row(deep, "TrendsQuery", 5), name_row(shallow, "TrendsQuery", 7)],
                "callees": [callee_row(deep, "h1", 5), callee_row(shallow, "h5", 7)],
            },
        }
    )


def test_a_walk_the_depth_limit_stops_is_unknown_and_never_hides_a_shorter_walk() -> None:
    build = repo().build(CoreConfig(first_party=("posthog", "products", "common")))

    edges = DrivesProducer(build, PackQueries.of(build, Graph())).produce()

    assert {(e.site.scope, e.attr("execution"), e.resolution) for e in edges} == {
        ("TestTrends.test_from_the_start", "unknown", Resolution.UNRESOLVED),
        ("TestTrends.test_from_the_middle", "via-helper", Resolution.RESOLVED),
    }
