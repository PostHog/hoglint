from __future__ import annotations

import os
import json
import time
import sqlite3
import datetime
import subprocess
from pathlib import Path
from typing import Any

import pytest

from hoglint.core.config import CoreConfig
from hoglint.native.analysis import LITE_KINDS
from hoglint.native.config import link_config
from hoglint.native.factdb import EPOCH, MAX_AGE_DAYS, FactDatabase, files_under
from hoglint.native.snapshot import BlobSnapshot

from native.require import native_module

TODAY = datetime.date(2026, 11, 9)
DAY = 86_400


def make_repo(root: Path) -> Path:
    repo = root / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "__init__.py").write_text("")
    (repo / "app" / "models.py").write_text("class Thing:\n    pass\n\nclass Other:\n    pass\n")
    (repo / "app" / "views.py").write_text("from app.models import Thing\n\nTHING = Thing()\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    return repo


def fact_database(native: Any, root: Path) -> FactDatabase:
    return FactDatabase(root / "cache" / "facts", native.FACTS_ID, native.FACT_LAYOUT)


def link(native: Any, snapshot: BlobSnapshot, database: FactDatabase | None) -> tuple[Any, tuple[int, int]]:
    paths = sorted(snapshot.blobs)
    blobs = [(path, *snapshot.blobs[path]) for path in paths]
    extract = {"first_party": ["app"], "kinds": [], "lite_kinds": list(LITE_KINDS)}
    config = link_config(CoreConfig(), tuple(paths), {})
    db = str(database.path) if database is not None else None
    return native.link_blobs(str(snapshot.repo), blobs, [], json.dumps(extract), json.dumps(config), db, 1)


def imports(linked: Any) -> list[str]:
    return sorted(symbol for row in linked.edges(linked.ids_of_kind("import")) for _, symbol in row[7])


def records(linked: Any) -> list[list[bytes | None]]:
    """Every record's JSON text, key by key, as the link reads it back."""
    return [[linked.raw(file, key) for key in linked.keys(file)] for file in range(len(linked.paths()))]


def test_a_second_run_reads_every_record_from_the_database(tmp_path: Path) -> None:
    native = native_module()
    repo = make_repo(tmp_path)
    # Two files with one blob: the first run stores the entry once.
    (repo / "app" / "copy.py").write_text((repo / "app" / "models.py").read_text())
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    database = fact_database(native, tmp_path)
    first, first_counts = link(native, BlobSnapshot.index(repo), database)
    second, second_counts = link(native, BlobSnapshot.index(repo), database)
    assert (first_counts, second_counts) == ((0, 4), (4, 0))
    assert records(second) == records(first)
    assert second.edges(second.ids_of_kind("import")) == first.edges(first.ids_of_kind("import"))


def test_a_working_tree_file_that_changes_during_the_run_is_never_stored(tmp_path: Path) -> None:
    native = native_module()
    repo = make_repo(tmp_path)
    database = fact_database(native, tmp_path)
    views = repo / "app" / "views.py"
    views.write_text("from app.models import Thing\n")
    snapshot = BlobSnapshot.worktree(repo)
    views.write_text("from app.models import Other\n")
    changed, _ = link(native, snapshot, database)
    assert (imports(changed), changed.drifted()) == (["Other"], ["app/views.py"])
    views.write_text("from app.models import Thing\n")
    again, counts = link(native, BlobSnapshot.worktree(repo), database)
    assert (imports(again), again.drifted(), counts) == (["Thing"], [], (2, 1))


@pytest.mark.parametrize("damage", ["entry gone, id reused", "record does not decompress"])
def test_a_record_the_database_cannot_give_is_extracted_again(tmp_path: Path, damage: str) -> None:
    native = native_module()
    repo = make_repo(tmp_path)
    database = fact_database(native, tmp_path)
    link(native, BlobSnapshot.index(repo), database)
    run, counts = link(native, BlobSnapshot.index(repo), database)
    fresh, _ = link(native, BlobSnapshot.index(repo), None)
    with sqlite3.connect(database.path) as conn:
        newest, length = conn.execute("SELECT id, json_len FROM keys ORDER BY id DESC LIMIT 1").fetchone()
        if damage == "record does not decompress":
            conn.execute("UPDATE records SET json = ? WHERE id = ?", (b"\xff" * 64, newest))
        else:
            record = bytes(conn.execute("SELECT json FROM records WHERE id = ?", (newest,)).fetchone()[0])
            FactDatabase.delete(conn, [newest])
            # Another blob's entry takes the freed id and has a record of the same length.
            body = conn.execute("SELECT body FROM facts LIMIT 1").fetchone()[0]
            reused = conn.execute("INSERT INTO facts (body) VALUES (?)", (body,)).lastrowid
            conn.execute("INSERT INTO records (id, json) VALUES (?, ?)", (reused, record))
            conn.execute("INSERT INTO keys VALUES ('config', 'another', ?, 0, ?, 1)", (reused, length))
    assert counts == (3, 0)
    assert records(run) == records(fresh)


def test_a_damaged_database_is_moved_aside_and_the_run_goes_on(tmp_path: Path) -> None:
    native = native_module()
    repo = make_repo(tmp_path)
    database = fact_database(native, tmp_path)
    database.directory.mkdir(parents=True)
    database.path.write_bytes(b"not a database" * 512)
    run, counts = link(native, BlobSnapshot.index(repo), database)
    assert (imports(run), counts) == (["Thing"], (0, 3))
    aside = [path.name for path in database.directory.iterdir() if ".corrupt-" in path.name]
    assert (len(aside), database.path.exists()) == (1, False)
    assert database.open_for_run(TODAY)


def stored_ids(database: FactDatabase) -> list[int]:
    with sqlite3.connect(database.path) as conn:
        return [entry_id for (entry_id,) in conn.execute("SELECT id FROM keys ORDER BY id")]


def test_the_daily_prune_drops_unread_entries_then_other_files_then_old_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    native = native_module()
    repo = make_repo(tmp_path)
    database = fact_database(native, tmp_path)
    link(native, BlobSnapshot.index(repo), database)
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
        free = conn.execute("PRAGMA freelist_count").fetchone()[0]
    assert (stored_ids(database), bodies, free) == ([], 0, 0)


def other_database(directory: Path, name: str, opened_days_ago: float) -> list[Path]:
    """Another facts id's database unit, last opened `opened_days_ago`."""
    files = [directory / f"{name}{suffix}" for suffix in ("", "-wal", "-shm", ".opened")]
    for path in files:
        path.write_bytes(b"x" * 1024)
    stamp = time.time() - opened_days_ago * DAY
    os.utime(files[-1], (stamp, stamp))
    return files


@pytest.mark.parametrize(
    "opened_days_ago, max_bytes, deleted",
    [
        # over the size bound, a database some run opened today stays whole
        (0.1, 0, False),
        # over the size bound, one no run opened since yesterday goes whole
        (2, 0, True),
        # within the size bound, only one no run opened for 30 days goes
        (2, None, False),
        (31, None, True),
    ],
)
def test_another_database_goes_only_whole_and_only_when_no_run_has_it_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, opened_days_ago: float, max_bytes: int | None, deleted: bool
) -> None:
    native = native_module()
    database = fact_database(native, tmp_path)
    database.directory.mkdir(parents=True)
    files = other_database(database.directory, "0123456789abcdef-l2.sqlite", opened_days_ago)
    if max_bytes is not None:
        monkeypatch.setattr("hoglint.native.factdb.MAX_BYTES", max_bytes)
    database.prune(TODAY)
    assert [path.exists() for path in files] == [not deleted] * len(files)
