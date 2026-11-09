from __future__ import annotations

import pytest

from core.support import Repo, class_def, func_def, import_row
from hoglint.core.config import CoreConfig
from hoglint.model import Graph, Resolution
from hoglint.native.queries import PackQueries
from hoglint.presets_py.posthog.drives import DISPATCH_CALLS, MAX_CALL_DEPTH, DrivesProducer

DISPATCH = sorted(DISPATCH_CALLS)[0]
HELPERS = MAX_CALL_DEPTH + 1  # h1 -> ... -> h<last> -> dispatcher: one hop past the limit from h1
MIDDLE = 5


def callee_row(scope: int, callee: str, line: int) -> list:
    return [scope, -1, callee, 1, line, 0]


def name_row(scope: int, value: str, line: int) -> list:
    return [scope, -1, value, 1, line, 0]


def calling_test(helper: str) -> dict:
    """A test whose one method names the query kind and calls `helper`."""
    return {
        "imports": [import_row("common.helpers", helper, line=1)],
        "defs": [class_def("TestTrends", [], 2), func_def("test_trends", 3, scope=0, flags=8)],
        "names": [name_row(1, "TrendsQuery", 4)],
        "callees": [callee_row(1, helper, 4)],
    }


def repo() -> Repo:
    """A query kind dispatched to the `charts` product, a chain of helpers that ends in the
    dispatcher, and two tests that call into that chain: one from its start, past the depth
    limit, and one from its middle, within it."""
    helpers = [func_def(f"h{i}", i) for i in range(1, HELPERS + 1)]
    calls = [callee_row(i - 1, f"h{i + 1}", i) for i in range(1, HELPERS)]
    calls.append(callee_row(HELPERS - 1, DISPATCH, HELPERS))
    return Repo(
        {
            "posthog/schema_enums.py": {},
            "posthog/hogql_queries/query_runner.py": {
                "branches": [[-1, -1, 1, 0, 0, 3, {"cmp": "kind", "cs": [{"s": "TrendsQuery"}]}]],
                "imports": [[-1, 2, 0, 0, "products.charts.backend.hogql_queries.trends", 0, "TrendsRunner", None]],
            },
            "products/charts/backend/hogql_queries/trends.py": {"defs": [class_def("TrendsRunner", [], 1)]},
            "common/helpers.py": {"defs": helpers, "callees": calls},
            "posthog/test/test_from_the_start.py": calling_test("h1"),
            "posthog/test/test_from_the_middle.py": calling_test(f"h{MIDDLE}"),
        }
    )


@pytest.mark.parametrize("shuffle_seed", [0, 1])
def test_a_walk_the_depth_limit_stops_is_unknown_whichever_test_walks_first(shuffle_seed: int) -> None:
    build = repo().build(CoreConfig(first_party=("posthog", "products", "common")))

    edges = DrivesProducer(build, PackQueries.of(build, Graph()), shuffle_seed).produce()

    assert [(e.site.module, e.attr("execution"), e.resolution, e.attr("chain").count(">")) for e in edges] == [
        ("posthog.test.test_from_the_middle", "via-helper", Resolution.RESOLVED, HELPERS - MIDDLE),
        ("posthog.test.test_from_the_start", "unknown", Resolution.UNRESOLVED, 0),
    ]
