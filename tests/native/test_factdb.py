from __future__ import annotations

import json
import sqlite3
import datetime
import subprocess
from pathlib import Path
from typing import Any

import pytest

from hoglint.core.config import CoreConfig
from hoglint.native import bridge
from hoglint.native.analysis import LITE_KINDS
from hoglint.native.config import link_config
from hoglint.native.factdb import EPOCH, MAX_AGE_DAYS, FactDatabase, files_under
from hoglint.native.snapshot import BlobSnapshot

TODAY = datetime.date(2026, 11, 9)


def native_module() -> Any:
    try:
        return bridge.load()
    except ImportError:
        pytest.skip("hoglint_native is not built (tools/hoglint/bin/build)")


def make_repo(root: Path) -> Path:
    repo = root / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "__init__.py").write_text("")
    (repo / "app" / "models.py").write_text("class Thing:\n    pass\n")
    (repo / "app" / "views.py").write_text("from app.models import Thing\n\nTHING = Thing()\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    return repo


def link(native: Any, repo: Path, database: FactDatabase) -> tuple[Any, tuple[int, int]]:
    snapshot = BlobSnapshot.index(repo)
    paths = sorted(snapshot.blobs)
    blobs = [(path, *snapshot.blobs[path]) for path in paths]
    extract = {"first_party": ["app"], "kinds": [], "lite_kinds": list(LITE_KINDS)}
    config = link_config(CoreConfig(), tuple(paths), {})
    return native.link_blobs(str(repo), blobs, [], json.dumps(extract), json.dumps(config), str(database.path), 1)


def test_a_second_run_reads_every_record_from_the_database(tmp_path: Path) -> None:
    native = native_module()
    repo = make_repo(tmp_path)
    database = FactDatabase(tmp_path / "cache" / "facts", native.FACTS_ID)
    first, first_counts = link(native, repo, database)
    second, second_counts = link(native, repo, database)
    assert (first_counts, second_counts) == ((0, 3), (3, 0))
    assert bytes(memoryview(second.text())) == bytes(memoryview(first.text()))
    assert second.edges(second.ids_of_kind("import")) == first.edges(first.ids_of_kind("import"))


def stored_ids(database: FactDatabase) -> list[int]:
    with sqlite3.connect(database.path) as conn:
        return [entry_id for (entry_id,) in conn.execute("SELECT id FROM keys ORDER BY id")]


def test_the_daily_prune_drops_unread_entries_then_other_files_then_old_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    native = native_module()
    repo = make_repo(tmp_path)
    database = FactDatabase(tmp_path / "cache" / "facts", native.FACTS_ID)
    link(native, repo, database)
    ids = stored_ids(database)
    today = (TODAY - EPOCH).days
    with sqlite3.connect(database.path) as conn:
        conn.execute("UPDATE keys SET used = ?", (today,))
        conn.execute("UPDATE keys SET used = ? WHERE id = ?", (today - MAX_AGE_DAYS - 1, ids[0]))
    other = database.directory / "older-engine" / "entry.json"
    other.parent.mkdir()
    other.write_bytes(b"x" * 4096)

    assert database.prune_daily(TODAY)
    assert not database.prune_daily(TODAY)
    assert (stored_ids(database), other.exists()) == (ids[1:], True)

    root_bytes = sum(size for _, size, _ in files_under(database.directory.parent))
    monkeypatch.setattr("hoglint.native.factdb.MAX_BYTES", root_bytes - 1)
    database.prune(TODAY)
    assert (stored_ids(database), other.exists()) == (ids[1:], False)

    monkeypatch.setattr("hoglint.native.factdb.MAX_BYTES", 0)
    database.prune(TODAY)
    with sqlite3.connect(database.path) as conn:
        bodies = conn.execute("SELECT count(*) FROM facts").fetchone()[0]
    assert (stored_ids(database), bodies) == ([], 0)
