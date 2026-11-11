from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from hoglint import facts
from hoglint.adapters.turbo import TaskTable, TurboAdapter, TurboInputs
from hoglint.facts import SitePackagesFiles, site_packages_files
from hoglint.ledger.cache import OFF_SWITCH, AnswerStore
from hoglint.ledger.inputs import InputPatterns, TreeFiles
from hoglint.ledger.listing import TreeListing
from hoglint.plugins import PLUGIN_ROOTS

from rules.test_compare import git_in, write_files

PACKAGES = ("alpha", "beta", "gamma")


def damage(entry: Path, how: str) -> None:
    if how == "truncated":
        entry.write_bytes(entry.read_bytes()[:-20])
    elif how == "garbage":
        entry.write_bytes(b"\x00not an answer\xff" * 40)
    elif how == "missing":
        entry.unlink()
    elif how == "body changed":
        data = entry.read_bytes()
        entry.write_bytes(data.replace(b"alpha/core.py", b"alpha/kore.py"))


def entries(cache: Path, kind: str) -> list[Path]:
    return sorted((cache / "hoglint" / "answers" / kind).glob("*.json"))


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    site = tmp_path / "site"
    for path in ("alpha/__init__.py", "alpha/core.py", "alpha/sub/deep/x.py", "alpha/data.txt", "beta/__init__.py"):
        (site / path).parent.mkdir(parents=True, exist_ok=True)
        (site / path).write_text("")
    monkeypatch.setattr(facts, "site_packages", lambda: site)
    monkeypatch.setattr(facts, "SETTLED_NS", 0)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv(OFF_SWITCH, raising=False)
    return site


def test_the_stored_site_packages_listing_is_the_walk_and_is_read_back(site: Path, tmp_path: Path) -> None:
    walked = site_packages_files(PACKAGES)
    assert SitePackagesFiles.of(PACKAGES) == walked
    [entry] = entries(tmp_path / "cache", "site-packages")
    assert AnswerStore("site-packages").get(entry.stem) is not None
    assert list(SitePackagesFiles.of(PACKAGES).items()) == list(walked.items())


@pytest.mark.parametrize("how", ["truncated", "garbage", "missing", "body changed"])
def test_a_damaged_site_packages_entry_is_a_miss_and_is_replaced(site: Path, tmp_path: Path, how: str) -> None:
    walked = site_packages_files(PACKAGES)
    SitePackagesFiles.of(PACKAGES)
    [entry] = entries(tmp_path / "cache", "site-packages")
    damage(entry, how)
    assert SitePackagesFiles.of(PACKAGES) == walked
    assert AnswerStore("site-packages").get(entry.stem) is not None


def test_site_packages_lookups_at_once_agree(site: Path, tmp_path: Path) -> None:
    walked = site_packages_files(PACKAGES)
    found: list[dict[str, str]] = []
    threads = [threading.Thread(target=lambda: found.append(SitePackagesFiles.of(PACKAGES))) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert found == [walked] * 8
    [entry] = entries(tmp_path / "cache", "site-packages")
    assert AnswerStore("site-packages").get(entry.stem) is not None


@pytest.mark.parametrize(
    "change",
    ["add a file deep down", "remove a file", "rename a file", "add a directory", "a package root appears"],
)
def test_a_site_packages_change_is_seen(site: Path, tmp_path: Path, change: str) -> None:
    SitePackagesFiles.of(PACKAGES)
    assert entries(tmp_path / "cache", "site-packages")
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
    assert SitePackagesFiles.of(PACKAGES) == site_packages_files(PACKAGES)
    assert SitePackagesFiles.of(PACKAGES) == site_packages_files(PACKAGES)


def test_a_fresh_directory_or_the_off_switch_stores_nothing(
    site: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(OFF_SWITCH, "1")
    assert SitePackagesFiles.of(PACKAGES) == site_packages_files(PACKAGES)
    assert entries(tmp_path / "cache", "site-packages") == []
    monkeypatch.delenv(OFF_SWITCH)
    monkeypatch.setattr(facts, "SETTLED_NS", 60 * 10**9)
    assert SitePackagesFiles.of(PACKAGES) == site_packages_files(PACKAGES)
    assert entries(tmp_path / "cache", "site-packages") == []


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
ASKED = ["products/a/facade/api.py", "products/a/logic.py", "products/b/backend/x.py", "products/c/backend/y.py"]
OPTIONS = {"task": "check"}


class TurboRepo:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.repo = tmp_path / "repo"
        write_files(self.repo, TURBO)
        git_in(self.repo, "init", "-q")

    def run_root(self) -> Path:
        git_in(self.repo, "add", "-A")
        tree = git_in(self.repo, "write-tree").strip()
        work = self.tmp_path / "work"
        work.mkdir(exist_ok=True)
        declared = InputPatterns(TurboAdapter().inputs(OPTIONS))
        return TreeFiles(TreeListing.of(self.repo, tree), work, InputPatterns(["**"]), declared).run_root()

    @staticmethod
    def answers(handle: TurboInputs) -> list[tuple[bool, str]]:
        return [handle.covered(path) for path in ASKED]

    def fresh(self, run_root: Path) -> TurboInputs:
        return TurboInputs(PLUGIN_ROOTS.of(run_root, TurboAdapter(), OPTIONS), "check")


@pytest.fixture
def turbo_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TurboRepo:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv(OFF_SWITCH, raising=False)
    return TurboRepo(tmp_path)


def test_the_stored_turbo_table_answers_as_the_files_do(turbo_repo: TurboRepo, tmp_path: Path) -> None:
    run_root = turbo_repo.run_root()
    fresh = turbo_repo.fresh(run_root)
    assert TurboAdapter().open_run(OPTIONS, run_root).table == fresh.table
    [entry] = entries(tmp_path / "cache", "turbo")
    stored = TurboAdapter().open_run(OPTIONS, run_root)
    assert stored.table == fresh.table
    assert turbo_repo.answers(stored) == turbo_repo.answers(fresh)
    assert AnswerStore("turbo").get(entry.stem) is not None


@pytest.mark.parametrize("how", ["truncated", "garbage", "missing", "body changed"])
def test_a_damaged_turbo_entry_is_a_miss_and_is_replaced(turbo_repo: TurboRepo, tmp_path: Path, how: str) -> None:
    run_root = turbo_repo.run_root()
    TurboAdapter().open_run(OPTIONS, run_root)
    [entry] = entries(tmp_path / "cache", "turbo")
    if how == "body changed":
        entry.write_bytes(entry.read_bytes().replace(b"products/a", b"products/z"))
    else:
        damage(entry, how)
    assert TurboAdapter().open_run(OPTIONS, run_root).table == turbo_repo.fresh(run_root).table
    assert AnswerStore("turbo").get(entry.stem) is not None


def test_turbo_lookups_at_once_agree(turbo_repo: TurboRepo, tmp_path: Path) -> None:
    run_root = turbo_repo.run_root()
    found: list[TaskTable] = []
    threads = [
        threading.Thread(target=lambda: found.append(TurboAdapter().open_run(OPTIONS, run_root).table))
        for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert found == [turbo_repo.fresh(run_root).table] * 8
    [entry] = entries(tmp_path / "cache", "turbo")
    assert AnswerStore("turbo").get(entry.stem) is not None


@pytest.mark.parametrize(
    "path, content",
    [
        ("products/c/package.json", json.dumps({"name": "c", "scripts": {"check": "true"}})),
        ("products/a/turbo.json", json.dumps({"extends": ["//"], "tasks": {"check": {"inputs": ["**"]}}})),
        ("turbo.json", json.dumps({"tasks": {"check": {"inputs": ["logic.py"]}}})),
        ("products/d/package.json", json.dumps({"name": "d", "scripts": {"check": "true"}})),
    ],
)
def test_a_turbo_input_change_is_seen(turbo_repo: TurboRepo, tmp_path: Path, path: str, content: str) -> None:
    before = TurboAdapter().open_run(OPTIONS, turbo_repo.run_root())
    assert entries(tmp_path / "cache", "turbo")
    write_files(turbo_repo.repo, {path: content})
    run_root = turbo_repo.run_root()
    after = TurboAdapter().open_run(OPTIONS, run_root)
    assert after.table == turbo_repo.fresh(run_root).table
    assert after.table != before.table


def test_the_off_switch_stores_no_turbo_table(
    turbo_repo: TurboRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(OFF_SWITCH, "1")
    run_root = turbo_repo.run_root()
    assert TurboAdapter().open_run(OPTIONS, run_root).table == turbo_repo.fresh(run_root).table
    assert entries(tmp_path / "cache", "turbo") == []
