"""Limit sites (t30, Nov 24; CASES.md `LS`): each place where the watched reading applies a written
limit, and so knowingly does not follow the code, is listed as {limit, site, what} in the watched
answer and in check's stored summary. `compare` shows a site the base does not have for a person's
review and never fails for it; a site that only moves to another line stays quiet.

Each site has its limit, file and line, enclosing def, normalized operation and one sentence;
`compare` matches by limit, file, def and operation, never by line. The base holds one site, a
`sys.path` change in a function (limit 11). The head moves it two lines down and spells `sys`
through an alias, which is the same site, and adds two: the module `builtins` handed on in a
function body (limit 9, as live's `dir(builtins)`) and a `del sys.modules[...]` (limit 10)."""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from hoglint.cli import Commands
from hoglint.compare import LIMITS_TITLE
from hoglint.config import CONFIG_FILE, load_config
from hoglint.ledger.scan import ScanSummary
from native.require import native_module

from model_repo.harness import copy_tree, stub_site
from model_repo.test_hook_output import FAILING_CLAIMS, compare, git, run, write

PATHS = "products/depot/backend/logic/plugin_paths.py"
PATHS_TEXT = "import sys\n\n\ndef add_plugin_path(path: str) -> None:\n    sys.path.insert(0, path)\n"
NAMES = "products/depot/backend/logic/name_tools.py"
NAMES_TEXT = (
    "import builtins\nimport sys\n\n\n"
    "def builtin_names() -> list[str]:\n    return dir(builtins)\n\n\n"
    "def forget(name: str) -> None:\n    del sys.modules[name]\n"
)
# The head's spelling of the base's site: two lines down, `sys` through an alias.
PATHS_MOVED = (
    "# moved down two lines\n\nimport sys as system\n\n\n"
    "def add_plugin_path(path: str) -> None:\n    system.path.insert(0, path)\n"
)
SYS_PATH = {
    "limit": "limit-11",
    "site": f"{PATHS}:5",
    "def": "add_plugin_path",
    "operation": "`sys.path.insert()`",
    "what": "the reading keeps the repository layout whatever `sys.path` holds",
}
BUILTINS = {
    "limit": "limit-9",
    "site": f"{NAMES}:6",
    "def": "builtin_names",
    "operation": "`builtins` to `dir()` argument 0",
    "what": "the module `builtins` handed on in a function body: what the recipient does with it is not followed",
}
DEL_MODULES = {
    "limit": "limit-10",
    "site": f"{NAMES}:10",
    "def": "forget",
    "operation": "`del sys.modules[...]`",
    "what": "a removal or restoration of a `sys.modules` entry is not read",
}


def mine(limits: list[dict[str, str]]) -> list[dict[str, str]]:
    """The sites of the files this case lays: the model repository's own may hold others."""
    return [site for site in limits if site["site"].startswith((PATHS, NAMES))]


@pytest.fixture(scope="module")
def repos(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Path, Path]]:
    """The base (claims that fail dropped, ledger written, one limit site) and the head on top of
    it, both committed; the head's branch is checked out."""
    native_module()
    root = tmp_path_factory.mktemp("limit-sites")
    base = root / "base"
    with pytest.MonkeyPatch.context() as patch, stub_site():
        patch.setenv("XDG_CACHE_HOME", str(root / "cache"))
        copy_tree(base)
        for product in FAILING_CLAIMS:
            (base / "products" / product / "turbo.json").unlink()
        write(base, "hoglint.exceptions.toml", "schema = 1\n")
        write(base, PATHS, PATHS_TEXT)
        assert run(base, root / "base-write", lambda c: c.check(False, True, False, True)).startswith("exit 0")
        git(base, "add", "-A")
        git(base, "commit", "-qm", "base")
        git(base, "branch", "base")
        head = root / "head"
        shutil.copytree(base, head, symlinks=True)
        write(head, PATHS, PATHS_MOVED)
        write(head, NAMES, NAMES_TEXT)
        git(head, "add", "-A")
        git(head, "commit", "-qm", "head")
        yield base, head


def answer(repo: Path, work: Path) -> dict[str, Any]:
    out = run(repo, work, lambda c: c.watched("isolated", None, None))
    return json.loads(out.split("\n", 1)[1])


def stored(repo: Path, work: Path) -> ScanSummary:
    """The summary `check` stores for the tree."""
    found: list[ScanSummary] = []

    def action(commands: Commands) -> int:
        assert commands.check(False, False, False, True) == 0
        tree = commands.worktree_tree(load_config(repo, repo / CONFIG_FILE))
        key, _ = commands.engine.envelope_at(repo, repo, tree)
        summary = commands.summaries.get(key)
        assert summary is not None
        found.append(summary)
        return 0

    run(repo, work, action)
    return found[0]


def test_the_watched_answer_lists_each_limit_site(repos: tuple[Path, Path], tmp_path: Path) -> None:
    base, head = repos
    with pytest.MonkeyPatch.context() as patch, stub_site():
        patch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        assert mine(answer(base, tmp_path / "base")["limits"]) == [SYS_PATH]
        moved = dict(SYS_PATH, site=f"{PATHS}:7")
        assert mine(answer(head, tmp_path / "head")["limits"]) == [BUILTINS, DEL_MODULES, moved]


def test_check_stores_the_limit_sites(repos: tuple[Path, Path], tmp_path: Path) -> None:
    _, head = repos
    with pytest.MonkeyPatch.context() as patch, stub_site():
        patch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        summary = stored(head, tmp_path / "check")
    sites = mine([site.to_json() for site in summary.limits])
    assert len(sites) == 3
    assert ScanSummary.from_json(summary.to_json()).limits == summary.limits


def test_compare_shows_a_new_site_for_review_and_keeps_a_moved_one_quiet(
    repos: tuple[Path, Path], tmp_path: Path
) -> None:
    _, head = repos
    with pytest.MonkeyPatch.context() as patch, stub_site():
        patch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        out = run(head, tmp_path / "compare", compare)
    assert out.startswith("exit 0"), out
    assert f"{LIMITS_TITLE} (2)" in out, out
    for site in (BUILTINS, DEL_MODULES):
        row = f"  {site['limit']} {site['site']} in {site['def']}: {site['operation']}: {site['what']}"
        assert row in out, out
    assert PATHS not in out, out
