from __future__ import annotations

import threading
from pathlib import Path

import pytest

from hoglint import facts
from hoglint.facts import SitePackagesFiles, site_packages_files
from hoglint.ledger.cache import OFF_SWITCH, AnswerStore

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

