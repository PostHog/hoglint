"""Limit 12 (the owner, Nov 24; `codex-limit12-answer.md`, option (a); CASES.md `L12`): a
registered def or class item with decorators is read as itself only when its outermost decorator
is Temporal's own `defn` (a library binding, bare or called) and every decorator under it is one
of the preset's wrappers in its recorded form (`name()` for a factory). Each first-party wrapper's
file is watched with the item, and the item is a limit 12 site with its resolved stack. Any other
decorated item is unread, so its product fails the level.

Recheck 9's three family 7 rows (a first-party decorator that returns BFlow on a def and on a
class, and a first-party `activity.defn` that does the same) each fail the level, as do a wrapper
in the wrong form, a wrapper the preset does not list and a wrapper name the item's module binds
again. The twins pass and watch the wrapper's file."""

from __future__ import annotations

from typing import Any

import pytest

from model_repo.shapes_round7_cases import case
from model_repo.test_watched_shapes import WORKER, answer_of, registered

HEAD = "from products.{p}.backend.temporal.a import AFlow\nfrom products.{p}.backend.temporal.b import BFlow\n\n"
UTILS = "posthog/temporal/common/utils.py"
SCOPED = "posthog/temporal/common/scoped.py"
OTHER = "posthog/temporal/common/retries.py"
WRAPS = (
    "    @functools.wraps(func)\n    async def wrapper(*args, **kwargs):\n"
    "        return await func(*args, **kwargs)\n\n    return wrapper\n"
)
CORE = {
    UTILS: "import functools\n\n\ndef close_db_connections(func):\n" + WRAPS,
    SCOPED: "import functools\n\n\ndef scoped_temporal(name=None):\n    def decorator(func):\n"
    + "".join(f"    {line}\n" if line else "\n" for line in WRAPS.splitlines())
    + "\n    return decorator\n",
    OTHER: "import functools\n\n\ndef retried(func):\n" + WRAPS,
}

CASES: dict[str, dict[str, Any]] = {}


def add(p: str, body: str, outcome: str, note: str, acts: str = "", files: dict[str, str] | None = None) -> None:
    """A facade that binds WORKFLOWS in `body`; `acts` is a product module of activities."""
    laid = dict(files or {})
    laid["backend/facade/temporal.py"] = HEAD + body
    probes: tuple[str, ...] = ("a",)
    if acts:
        laid["backend/temporal/acts.py"] = acts
        probes = ("a", "acts")
    core = {WORKER: registered(p), **CORE}
    CASES[p] = {**case(p, "", outcome, note, files=laid, probes=probes), "core": core}


ACT = "from temporalio import activity\n\n{imports}\n\n{decorators}\nasync def run_thing() -> None:\n    return None\n"
USE_ACT = "from products.{p}.backend.temporal.acts import run_thing\n\nWORKFLOWS = [AFlow, run_thing]\n"


def act(imports: str, decorators: str) -> str:
    return ACT.format(imports=imports, decorators=decorators)


# Recheck 9's family 7 rows, renamed.
add(
    "l12_decorated_def_item_f",
    "\ndef _d(f):\n    return BFlow\n\n\n@_d\ndef _wf():\n    pass\n\n\nWORKFLOWS = [AFlow, _wf]\n",
    "unread",
    "family 7: a first-party decorator returns BFlow for a def item",
)
add(
    "l12_decorated_class_item_f",
    "\ndef _d(c):\n    return BFlow\n\n\n@_d\nclass _Wf:\n    pass\n\n\nWORKFLOWS = [AFlow, _Wf]\n",
    "unread",
    "family 7: a first-party decorator returns BFlow for a class item",
)
add(
    "l12_activity_fake_defn_item",
    "from products.{p}.backend.temporal.mix import activity\n\n\n@activity.defn\ndef _wf():\n    pass\n\n\n"
    "WORKFLOWS = [AFlow, _wf]\n",
    "unread",
    "family 7: a first-party class named activity whose defn returns BFlow",
    files={
        "backend/temporal/mix.py": "from products.{p}.backend.temporal.b import BFlow\n\n\n"
        "class activity:  # noqa: N801\n    @staticmethod\n    def defn(f):\n        return BFlow\n",
    },
)
# Stacks outside the limit.
add(
    "l12_wrapper_wrong_form",
    USE_ACT,
    "unread",
    "a factory the preset records as called, used bare",
    acts=act("from posthog.temporal.common.scoped import scoped_temporal", "@activity.defn\n@scoped_temporal"),
)
add(
    "l12_wrapper_unlisted",
    USE_ACT,
    "unread",
    "a first-party wrapper the preset does not list",
    acts=act("from posthog.temporal.common.retries import retried", "@activity.defn\n@retried"),
)
add(
    "l12_wrapper_rebound",
    USE_ACT,
    "unread",
    "the wrapper's name bound again in the item's module",
    acts=act(
        "from posthog.temporal.common.utils import close_db_connections\n\n\n"
        "def _swap(func):\n    return func\n\n\nclose_db_connections = _swap",
        "@activity.defn\n@close_db_connections",
    ),
)
# Twins: inside the limit.
add(
    "l12_ok_defn_only",
    "from temporalio import workflow\n\n\n@workflow.defn(name='x')\nclass _Wf:\n    pass\n\n\nWORKFLOWS = [AFlow, _Wf]\n",
    "watched",
    "harmless: Temporal's own defn, called, alone",
)
add(
    "l12_ok_wrapper",
    USE_ACT,
    "watched",
    "harmless: Temporal's defn over a listed first-party wrapper",
    acts=act("from posthog.temporal.common.utils import close_db_connections", "@activity.defn\n@close_db_connections"),
)
add(
    "l12_ok_factory",
    USE_ACT,
    "watched",
    "harmless: Temporal's defn over a listed factory, called",
    acts=act(
        "import posthog.temporal.common.scoped as sc",
        "@activity.defn(name='run')\n@sc.scoped_temporal('team')",
    ),
)

LOUD = {p: shape for p, shape in CASES.items() if shape["outcome"] == "unread"}
QUIET = {p: shape for p, shape in CASES.items() if shape["outcome"] == "watched"}
# What each twin's limit 12 site says: its stack, and the wrapper file it watches.
STACKS = {
    "l12_ok_defn_only": ("_Wf", "`@temporalio.workflow.defn`", None),
    "l12_ok_wrapper": (
        "run_thing",
        "`@temporalio.activity.defn` over `@posthog.temporal.common.utils.close_db_connections`",
        UTILS,
    ),
    "l12_ok_factory": (
        "run_thing",
        "`@temporalio.activity.defn` over `@posthog.temporal.common.scoped.scoped_temporal()`",
        SCOPED,
    ),
}


@pytest.fixture(scope="module")
def quiet(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return answer_of(tmp_path_factory, QUIET)


@pytest.fixture(scope="module")
def loud(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return answer_of(tmp_path_factory, LOUD)


def test_the_twins_are_complete(quiet: dict[str, Any]) -> None:
    assert quiet["complete"], quiet["problems"]


@pytest.mark.parametrize("p", list(QUIET))
def test_a_stack_inside_the_limit_is_read_and_watches_its_wrapper(quiet: dict[str, Any], p: str) -> None:
    group = quiet["groups"][p]
    assert group["passes"], (CASES[p]["note"], group["reasons"])
    for probe in CASES[p]["probes"]:
        assert f"products/{p}/{probe}" in group["watched"], sorted(group["watched"])
    item, stack, wrapper = STACKS[p]
    path = f"products/{p}/backend/{'temporal/acts' if wrapper else 'facade/temporal'}.py"
    sites = [site for site in quiet["limits"] if site["limit"] == "limit-12" and site["site"].startswith(path)]
    assert [(site["def"], site["operation"]) for site in sites] == [(item, stack)], quiet["limits"]
    if wrapper is not None:
        assert group["watched"].get(wrapper, "").startswith("limit-12: a wrapper of the registered item"), sorted(
            group["watched"]
        )


@pytest.mark.parametrize("p", list(LOUD))
def test_a_stack_outside_the_limit_fails_the_level(loud: dict[str, Any], p: str) -> None:
    group = loud["groups"][p]
    assert not group["passes"], CASES[p]["note"]
    assert any(CASES[p]["reason"] in text for text in group["reasons"]), group["reasons"]


FAMILY_7 = ("l12_decorated_def_item_f", "l12_decorated_class_item_f", "l12_activity_fake_defn_item")


@pytest.mark.parametrize("p", FAMILY_7)
def test_each_family_7_row_alone_blocks_the_skip(tmp_path_factory: pytest.TempPathFactory, p: str) -> None:
    """Each row alone, so no other row's change can be what fails it: the substituted file (BFlow's)
    is watched by nothing else, and the level fails for the list."""
    found = answer_of(tmp_path_factory, {p: CASES[p]})
    group = found["groups"][p]
    assert not group["passes"], CASES[p]["note"]
    assert any(CASES[p]["reason"] in text for text in group["reasons"]), group["reasons"]
