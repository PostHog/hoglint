from __future__ import annotations

from collections.abc import Callable

import pytest

from core.support import Repo, class_def, func_def, import_row
from django_facts import FactRepo
from hoglint.core import memo
from hoglint.core.config import CoreConfig
from hoglint.core.harness import PRODUCER_ERROR
from hoglint.model import Graph, Resolution
from hoglint.packs.posthog.drives import DISPATCH_CALLS, MAX_CALL_DEPTH, DrivesProducer
from hoglint.packs.posthog.pack import PosthogPack

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
    linked = repo().link(CoreConfig(first_party=("posthog", "products", "common")))

    edges = DrivesProducer(linked.reader(), shuffle_seed).produce()

    found = [(e.site.module, e.attr("execution"), e.resolution, e.attr("chain").count(">")) for e in edges]
    assert sorted(found) == [
        ("posthog.test.test_from_the_middle", "via-helper", Resolution.RESOLVED, HELPERS - MIDDLE),
        ("posthog.test.test_from_the_start", "unknown", Resolution.UNRESOLVED, 0),
    ]


def django_run() -> Graph:
    return FactRepo().graph(hooks=("billing.hooks.register_refund_hook",))


def posthog_run() -> Graph:
    linked = repo().link(CoreConfig(first_party=("posthog", "products", "common")))
    PosthogPack.run(linked.reader(), linked.sink())
    return linked.graph


@pytest.mark.parametrize("run", [django_run, posthog_run])
def test_a_run_with_no_pack_memo_digests_no_answer_and_registers_none(
    run: Callable[[], Graph], monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_digest(text: str) -> str:
        raise AssertionError("a run with no pack memo digests an answer")

    monkeypatch.setattr(memo, "text_digest", no_digest)
    graph = run()
    assert graph.edges
    assert graph.failed_producers == {}
    assert [edge for edge in graph.edges if edge.attr("reason") == PRODUCER_ERROR] == []
    assert memo.NULL_TRACER.answers == {} and memo.NULL_TRACER.verifiers == {}
