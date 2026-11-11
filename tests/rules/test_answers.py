from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from hoglint import facts
from hoglint.adapters.turbo import TurboAdapter, TurboInputs
from hoglint.facts import SitePackagesFiles, site_packages_files
from hoglint.ledger.cache import OFF_SWITCH, AnswerStore
from hoglint.ledger.inputs import InputPatterns, TreeFiles
from hoglint.ledger.listing import TreeListing
from hoglint.plugins import PLUGIN_ROOTS

from rules.test_compare import git_in, write_files

PACKAGES = ("alpha", "beta", "gamma")


def damage(entry: Path, how: str, body: tuple[bytes, bytes]) -> None:
    """`body`: the bytes a "body changed" entry has replaced, and what replaces them."""
    if how == "truncated":
        entry.write_bytes(entry.read_bytes()[:-20])
    elif how == "garbage":
        entry.write_bytes(b"\x00not an answer\xff" * 40)
    elif how == "missing":
        entry.unlink()
    elif how == "body changed":
        entry.write_bytes(entry.read_bytes().replace(*body))


def entries(cache: Path, kind: str) -> list[Path]:
    return sorted((cache / "hoglint" / "answers" / kind).glob("*.json"))


SITE_FILES = ("alpha/__init__.py", "alpha/core.py", "alpha/sub/deep/x.py", "alpha/data.txt", "beta/__init__.py")
SITE_CHANGES = ("add a file deep down", "remove a file", "rename a file", "add a directory", "a package root appears")


class SiteCase:
    """The stored site-packages listing: an answer is the listing's items, in order."""

    kind = "site-packages"
    body = (b"alpha/core.py", b"alpha/kore.py")

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.site = tmp_path / "site"
        for path in SITE_FILES:
            (self.site / path).parent.mkdir(parents=True, exist_ok=True)
            (self.site / path).write_text("")
        monkeypatch.setattr(facts, "site_packages", lambda: self.site)
        monkeypatch.setattr(facts, "SETTLED_NS", 0)

    def lookup(self) -> object:
        return list(SitePackagesFiles.of(PACKAGES).items())

    def fresh(self) -> object:
        return list(site_packages_files(PACKAGES).items())

    def change(self, change: str) -> None:
        site = self.site
        if change == "add a file deep down":
            (site / "alpha/sub/deep/y.py").write_text("")
        elif change == "remove a file":
            (site / "alpha/core.py").unlink()
        elif change == "rename a file":
            (site / "alpha/core.py").rename(site / "alpha/kore.py")
        elif change == "add a directory":
            (site / "beta/new").mkdir()
            (site / "beta/new/z.py").write_text("")
        elif change == "a package root appears":
            (site / "gamma").mkdir()
            (site / "gamma/__init__.py").write_text("")


TURBO = {
    "turbo.json": json.dumps({"tasks": {"check": {"inputs": ["backend/**"]}}}),
    "products/a/package.json": json.dumps({"name": "a", "scripts": {"check": "true"}}),
    "products/a/turbo.json": json.dumps({"extends": ["//"], "tasks": {"check": {"inputs": ["facade/**"]}}}),
    "products/b/package.json": json.dumps({"name": "b", "scripts": {"check": "true"}}),
    "products/c/package.json": json.dumps({"name": "c", "scripts": {"lint": "true"}}),
    "products/a/facade/api.py": "",
    "products/a/logic.py": "",
    "products/b/backend/x.py": "",
    "products/c/backend/y.py": "",
}
TURBO_CHANGES = {
    "c gains the task": ("products/c/package.json", json.dumps({"name": "c", "scripts": {"check": "true"}})),
    "a widens its inputs": (
        "products/a/turbo.json",
        json.dumps({"extends": ["//"], "tasks": {"check": {"inputs": ["**"]}}}),
    ),
    "the root inputs change": ("turbo.json", json.dumps({"tasks": {"check": {"inputs": ["logic.py"]}}})),
    "a new package appears": ("products/d/package.json", json.dumps({"name": "d", "scripts": {"check": "true"}})),
}
ASKED = ["products/a/facade/api.py", "products/a/logic.py", "products/b/backend/x.py", "products/c/backend/y.py"]
OPTIONS = {"task": "check"}


class TurboCase:
    """The stored turbo task table: an answer is the table and what it says of each asked path."""

    kind = "turbo"
    body = (b"products/a", b"products/z")

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.repo = tmp_path / "repo"
        write_files(self.repo, TURBO)
        git_in(self.repo, "init", "-q")
        self.run_root = self.write_run_root()

    def write_run_root(self) -> Path:
        git_in(self.repo, "add", "-A")
        tree = git_in(self.repo, "write-tree").strip()
        work = self.tmp_path / "work"
        work.mkdir(exist_ok=True)
        declared = InputPatterns(TurboAdapter().inputs(OPTIONS))
        return TreeFiles(TreeListing.of(self.repo, tree), work, InputPatterns(["**"]), declared).run_root()

    @staticmethod
    def answer(handle: TurboInputs) -> object:
        return handle.table, [handle.covered(path) for path in ASKED]

    def lookup(self) -> object:
        return self.answer(TurboAdapter().open_run(OPTIONS, self.run_root))

    def fresh(self) -> object:
        return self.answer(TurboInputs(PLUGIN_ROOTS.of(self.run_root, TurboAdapter(), OPTIONS), "check"))

    def change(self, change: str) -> None:
        path, content = TURBO_CHANGES[change]
        write_files(self.repo, {path: content})
        self.run_root = self.write_run_root()


StoreCase = SiteCase | TurboCase


@pytest.fixture(params=["site-packages", "turbo"])
def store(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StoreCase:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv(OFF_SWITCH, raising=False)
    if request.param == "site-packages":
        return SiteCase(tmp_path, monkeypatch)
    return TurboCase(tmp_path)


def test_a_stored_answer_is_the_fresh_one_and_is_read_back(store: StoreCase, tmp_path: Path) -> None:
    assert store.lookup() == store.fresh()
    assert len(entries(tmp_path / "cache", store.kind)) == 1
    assert store.lookup() == store.fresh()


@pytest.mark.parametrize("how", ["truncated", "garbage", "missing", "body changed"])
def test_a_damaged_entry_is_a_miss_and_is_replaced(store: StoreCase, tmp_path: Path, how: str) -> None:
    store.lookup()
    [entry] = entries(tmp_path / "cache", store.kind)
    damage(entry, how, store.body)
    assert store.lookup() == store.fresh()
    assert AnswerStore(store.kind).get(entry.stem) is not None


def test_lookups_at_once_agree_and_leave_one_whole_entry(store: StoreCase, tmp_path: Path) -> None:
    found: list[object] = []
    threads = [threading.Thread(target=lambda: found.append(store.lookup())) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert found == [store.fresh()] * 8
    [entry] = entries(tmp_path / "cache", store.kind)
    assert AnswerStore(store.kind).get(entry.stem) is not None


@pytest.mark.parametrize(
    "store, change",
    [("site-packages", change) for change in SITE_CHANGES] + [("turbo", change) for change in TURBO_CHANGES],
    indirect=["store"],
)
def test_a_change_is_seen(store: StoreCase, tmp_path: Path, change: str) -> None:
    before = store.lookup()
    assert entries(tmp_path / "cache", store.kind)
    store.change(change)
    assert store.lookup() == store.fresh()
    assert store.lookup() == store.fresh()
    assert store.lookup() != before


def test_the_off_switch_stores_nothing(store: StoreCase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OFF_SWITCH, "1")
    assert store.lookup() == store.fresh()
    assert entries(tmp_path / "cache", store.kind) == []


@pytest.mark.parametrize("store", ["site-packages"], indirect=True)
def test_a_fresh_site_packages_directory_stores_nothing(
    store: StoreCase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(facts, "SETTLED_NS", 60 * 10**9)
    assert store.lookup() == store.fresh()
    assert entries(tmp_path / "cache", store.kind) == []
