"""The Isolated level with no `claimed_by` at all (`derive_unclaimed`), as the PostHog monorepo runs
it: every product is judged, a product that passes gets the test skip, and the ledger's level lines
are the ratchet (CASES.md `LR`).

(a) `check --write-ledger` writes a level line for each product that passes, and none for one that
    fails.
(b) A product that stops passing leaves a stale level line: check says it needs a person, and
    `ledger shrink` refuses to drop it.
(c) A registry whose module lacks the attribute in some products (PostHog's
    `load_product_modules("activity_log_config")`, attribute `FIELD_EXCLUSIONS`) still watches each
    module, and raises no problem.
(d) An enum member a registration point reads is no registered list, also when the enum's module
    looks a library up in `sys.modules` (the `: items` false fail on PostHog's
    `posthog.errors.QueryErrorCategory`)."""

from __future__ import annotations

import io
import json
import shutil
import contextlib
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from hoglint.cli import Commands
from hoglint.ledger.listing import WorktreeStatus
from native.require import native_module

from model_repo.derive import derive_unclaimed
from model_repo.harness import PRESET, PRESET_IN_TREE, copy_tree, stub_site

REGISTRY = """
[registries.activity_log_config]
loader    = "posthog.products.load_product_modules"
module    = "products/{product}/backend/activity_log_config.py"
attribute = "FIELD_EXCLUSIONS"
lifecycle = "on-demand"
"""
WORKER = "posthog/management/commands/start_temporal_worker.py"
FILES = {
    # (c) one product binds the attribute, one does not; core loads both by name.
    "products/depot/backend/activity_log_config.py": 'FIELD_EXCLUSIONS = {"bin": ["note"]}\n',
    "products/shelf/backend/activity_log_config.py": "def describe(change):\n    return str(change)\n",
    "posthog/models/activity_log_configs.py": (
        "from posthog.products import load_product_modules\n\n\n"
        "def field_exclusions():\n"
        "    found = {}\n"
        '    for module in load_product_modules("activity_log_config"):\n'
        '        found.update(getattr(module, "FIELD_EXCLUSIONS", {}))\n'
        "    return found\n"
    ),
    # (d) an enum whose module looks a library up in `sys.modules`, as posthog/errors.py does.
    "posthog/query_errors.py": (
        "import sys\nfrom enum import StrEnum\n\n\n"
        "class QueryErrorCategory(StrEnum):\n"
        '    USER_ERROR = "user_error"\n    RATE_LIMITED = "rate_limited"\n\n\n'
        "def rebuild(err):\n"
        '    exceptions = sys.modules.get("clickhouse_connect.driver.exceptions")\n'
        "    if exceptions is None or not isinstance(err, (exceptions.DatabaseError, exceptions.StreamFailureError)):\n"
        "        return None\n    return err\n"
    ),
}
# (d) the registration point reads the members, as the query runner does.
WORKER_READS = (
    "\nfrom posthog.query_errors import QueryErrorCategory\n\n\n"
    "def _classify(category):\n"
    "    if category in (QueryErrorCategory.USER_ERROR, QueryErrorCategory.RATE_LIMITED):\n"
    "        return QueryErrorCategory.USER_ERROR, 1\n"
    "    return QueryErrorCategory.RATE_LIMITED, 2\n"
)


def break_depot(repo: Path) -> None:
    """(b) depot folds its contracts module into its api: a facade needs a contracts module, so
    depot no longer passes Isolated (the `level-lost` hook scenario's change)."""
    facade = repo / "products/depot/backend/facade"
    contracts = (facade / "contracts.py").read_text()
    (facade / "contracts.py").unlink()
    api = facade / "api.py"
    api.write_text(api.read_text().replace("from products.depot.backend.facade.contracts import BinCount\n", contracts))


def git(repo: Path, *args: str) -> None:
    command = ["git", "-C", str(repo), "-c", "commit.gpgsign=false", "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*command, *args], check=True, capture_output=True)


def run(repo: Path, work: Path, action: Callable[[Commands], int]) -> tuple[int, str]:
    """One command in this process, as the CLI runs it: its exit code and what it printed."""
    WorktreeStatus.taken.pop(repo.resolve(), None)
    work.mkdir(parents=True, exist_ok=True)
    commands = Commands(repo, work)
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), commands.stores.as_current():
            code = action(commands)
    finally:
        commands.snapshots.close()
    return code, out.getvalue()


def level_lines(repo: Path) -> set[str]:
    """The products the ledger holds a level line for."""
    lines = (repo / "hoglint.ledger").read_text().splitlines()
    return {line.split("\t")[1] for line in lines if line.startswith("level:isolated\t")}


@pytest.fixture(scope="module")
def unclaimed(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Path, Path, dict[str, Any]]]:
    """The model repository with the unclaimed preset, (c) and (d) laid, its ledger written by
    `check --write-ledger` and committed, and the answer of `watched --level isolated`."""
    native_module()
    root = tmp_path_factory.mktemp("unclaimed")
    repo = root / "repo"
    with pytest.MonkeyPatch.context() as patch, stub_site():
        patch.setenv("XDG_CACHE_HOME", str(root / "cache"))
        copy_tree(repo)
        (repo / PRESET_IN_TREE).write_text(derive_unclaimed(PRESET.read_text()) + REGISTRY)
        for path, text in FILES.items():
            (repo / path).parent.mkdir(parents=True, exist_ok=True)
            (repo / path).write_text(text)
        worker = repo / WORKER
        worker.write_text(worker.read_text() + WORKER_READS)
        (repo / "hoglint.exceptions.toml").write_text("schema = 1\n")
        git(repo, "add", "-A")
        code, out = run(repo, root / "write", lambda c: c.check(False, True, False, True))
        assert code == 0, out
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "base")
        code, out = run(repo, root / "watched", lambda c: c.watched("isolated", None, None))
        assert code == 0, out
        yield repo, root, json.loads(out)


def test_a_passing_product_gets_its_level_line(unclaimed: tuple[Path, Path, dict[str, Any]]) -> None:
    repo, _, answer = unclaimed
    passing = {name for name, group in answer["groups"].items() if group["passes"]}
    assert "depot" in passing
    assert level_lines(repo) == passing


def test_a_registry_module_without_its_attribute_is_watched(unclaimed: tuple[Path, Path, dict[str, Any]]) -> None:
    _, _, answer = unclaimed
    assert answer["complete"], answer["problems"]
    for product in ("depot", "shelf"):
        watched = answer["groups"][product]["watched"]
        path = f"products/{product}/backend/activity_log_config.py"
        assert path in watched, sorted(watched)
        assert "a registry loads this module by name" in watched[path]
    reasons = [text for group in answer["groups"].values() for text in group["reasons"]]
    assert not [text for text in reasons if "activity_log_config" in text]


def test_an_enum_member_a_registration_point_reads_is_no_registered_list(
    unclaimed: tuple[Path, Path, dict[str, Any]],
) -> None:
    _, _, answer = unclaimed
    assert answer["complete"], answer["problems"]
    assert not [text for text in answer["problems"] if "QueryErrorCategory" in text]


def test_a_product_that_stops_passing_needs_a_person(
    unclaimed: tuple[Path, Path, dict[str, Any]], tmp_path: Path
) -> None:
    base, root, _ = unclaimed
    repo = tmp_path / "repo"
    shutil.copytree(base, repo)
    break_depot(repo)
    git(repo, "add", "-A")
    with pytest.MonkeyPatch.context() as patch, stub_site():
        patch.setenv("XDG_CACHE_HOME", str(root / "cache"))
        code, out = run(repo, tmp_path / "check", lambda c: c.check(False, False, False, True))
        assert code != 0, out
        assert "level:isolated" in out and "depot" in out, out
        assert "needs a person" in out and "fix (safe): run `hoglint ledger shrink`" not in out, out
        code, out = run(repo, tmp_path / "shrink", lambda c: c.shrink_ledger())
        assert code == 1, out
        assert "depot" in level_lines(repo), out
        code, out = run(repo, tmp_path / "drop", lambda c: c.shrink_ledger(drop_levels=True))
        assert code == 0, out
        assert "depot" not in level_lines(repo), out
