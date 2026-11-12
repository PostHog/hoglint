from __future__ import annotations

import json
import time
import sqlite3
import datetime
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from core.support import set_modified
from hoglint.core.config import CoreConfig
from hoglint.ledger.store import Walk
from hoglint.native.analysis import LITE_KINDS
from hoglint.native.config import link_config
from hoglint.native.factdb import EPOCH, MAX_AGE_DAYS, FactDatabase
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


def stored_record(conn: sqlite3.Connection, oid: str) -> bytes:
    query = "SELECT r.json FROM records r JOIN keys k ON k.id = r.id WHERE k.oid = ?"
    return bytes(conn.execute(query, (oid,)).fetchone()[0])


def replace_record(conn: sqlite3.Connection, oid: str, record: bytes) -> None:
    conn.execute("UPDATE records SET json = ? WHERE id = (SELECT id FROM keys WHERE oid = ?)", (record, oid))


def reuse_the_id(conn: sqlite3.Connection, oid: str) -> None:
    entry_id, length = conn.execute("SELECT id, json_len FROM keys WHERE oid = ?", (oid,)).fetchone()
    record = stored_record(conn, oid)
    FactDatabase.delete(conn, [entry_id])
    # Another blob's entry takes the freed id and has a record of the same length.
    body = conn.execute("SELECT body FROM facts LIMIT 1").fetchone()[0]
    reused = conn.execute("INSERT INTO facts (body) VALUES (?)", (body,)).lastrowid
    conn.execute("INSERT INTO records (id, json) VALUES (?, ?)", (reused, record))
    conn.execute("INSERT INTO keys VALUES ('config', 'another', ?, 0, ?, 1)", (reused, length))


def drop_the_record(conn: sqlite3.Connection, oid: str) -> None:
    conn.execute("DELETE FROM records WHERE id = (SELECT id FROM keys WHERE oid = ?)", (oid,))


def garble_the_record(conn: sqlite3.Connection, oid: str) -> None:
    replace_record(conn, oid, b"\xff" * 64)


def garble_the_frames(conn: sqlite3.Connection, oid: str) -> None:
    packed = stored_record(conn, oid)
    index_end = 4 + int.from_bytes(packed[:4], "little")
    replace_record(conn, oid, packed[:index_end] + b"\xff" * (len(packed) - index_end))


@pytest.mark.parametrize("damage", [reuse_the_id, drop_the_record, garble_the_record, garble_the_frames])
def test_a_record_the_database_cannot_give_is_extracted_again(
    tmp_path: Path, damage: Callable[[sqlite3.Connection, str], None]
) -> None:
    native = native_module()
    repo = make_repo(tmp_path)
    database = fact_database(native, tmp_path)
    link(native, BlobSnapshot.index(repo), database)
    run, counts = link(native, BlobSnapshot.index(repo), database)
    fresh, _ = link(native, BlobSnapshot.index(repo), None)
    with sqlite3.connect(database.path) as conn:
        (oid,) = conn.execute("SELECT oid FROM keys ORDER BY id DESC LIMIT 1").fetchone()
        damage(conn, oid)
    assert counts == (3, 0)
    assert records(run) == records(fresh)
    # After the next run the database holds the record as a clean one does: repaired, or stored anew.
    link(native, BlobSnapshot.index(repo), database)
    clean = fact_database(native, tmp_path / "clean")
    link(native, BlobSnapshot.index(repo), clean)
    with sqlite3.connect(database.path) as ours, sqlite3.connect(clean.path) as theirs:
        assert stored_record(ours, oid) == stored_record(theirs, oid)


@pytest.mark.parametrize("json_len", ["-1", str(1 << 62), "json_len + 1", "json_len - 1", "json_len / 2"])
def test_a_stored_length_that_is_not_the_records_is_a_miss(tmp_path: Path, json_len: str) -> None:
    native = native_module()
    repo = make_repo(tmp_path)
    database = fact_database(native, tmp_path)
    link(native, BlobSnapshot.index(repo), database)
    with sqlite3.connect(database.path) as conn:
        conn.execute(f"UPDATE keys SET json_len = {json_len} WHERE id = (SELECT max(id) FROM keys)")
    run, counts = link(native, BlobSnapshot.index(repo), database)
    fresh, _ = link(native, BlobSnapshot.index(repo), None)
    assert counts == (2, 1)
    assert records(run) == records(fresh)
    # The run stored the entry anew, so the next run reads every record from the cache.
    assert link(native, BlobSnapshot.index(repo), database)[1] == (3, 0)


@pytest.mark.parametrize("mark", [b"\x86\x00not text", b"", b"2026-11"], ids=["not-utf8", "empty", "truncated"])
def test_a_damaged_prune_mark_means_not_pruned_today(tmp_path: Path, mark: bytes) -> None:
    database = FactDatabase(tmp_path / "cache" / "facts", "facts-id", 1)
    database.directory.mkdir(parents=True)
    database.pruned_mark.write_bytes(mark)
    assert database.open_for_run(TODAY)
    assert database.pruned_mark.read_text() == TODAY.isoformat()


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


def test_a_database_whose_newest_facts_row_was_lost_is_moved_aside_and_stores_again(tmp_path: Path) -> None:
    native = native_module()
    repo = make_repo(tmp_path)
    database = fact_database(native, tmp_path)
    link(native, BlobSnapshot.index(repo), database)
    # The next entry's `facts` row takes the lost row's id, which a `records` row still holds.
    with sqlite3.connect(database.path) as conn:
        conn.execute("DELETE FROM facts WHERE id = (SELECT max(id) FROM facts)")
    (repo / "app" / "views.py").write_text("from app.models import Other\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    link(native, BlobSnapshot.index(repo), database)
    aside = [path.name for path in database.directory.iterdir() if ".corrupt-" in path.name]
    assert len(aside) == 1
    assert link(native, BlobSnapshot.index(repo), database)[1] == (0, 3)
    run, counts = link(native, BlobSnapshot.index(repo), database)
    assert (imports(run), counts) == (["Other"], (3, 0))


def stored_ids(database: FactDatabase) -> list[int]:
    with sqlite3.connect(database.path) as conn:
        return [entry_id for (entry_id,) in conn.execute("SELECT id FROM keys ORDER BY id")]


def test_the_daily_prune_drops_unread_entries_then_other_files_then_entries_not_read_today(
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

    root_bytes = sum(size for _, size, _ in Walk.of(database.directory.parent).files)
    monkeypatch.setattr("hoglint.native.factdb.MAX_BYTES", root_bytes - 1)
    database.prune(TODAY)
    assert (stored_ids(database), other.exists()) == (ids[1:], False)

    # Over the bound, rows read three days ago go; rows a run read today stay, so the root stays over it.
    with sqlite3.connect(database.path) as conn:
        conn.execute("UPDATE keys SET used = ? WHERE id = ?", (today - 3, ids[1]))
    monkeypatch.setattr("hoglint.native.factdb.MAX_BYTES", 0)
    database.prune(TODAY)
    with sqlite3.connect(database.path) as conn:
        bodies = conn.execute("SELECT count(*) FROM facts").fetchone()[0]
        free = conn.execute("PRAGMA freelist_count").fetchone()[0]
    assert (stored_ids(database), bodies, free) == (ids[2:], len(ids[2:]), 0)


def other_database(directory: Path, name: str, opened_days_ago: float) -> list[Path]:
    """Another facts id's database unit, last opened `opened_days_ago`."""
    files = [directory / f"{name}{suffix}" for suffix in ("", "-wal", "-shm", ".opened")]
    for path in files:
        path.write_bytes(b"x" * 1024)
    set_modified(files[-1], time.time() - opened_days_ago * DAY)
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


def test_only_the_newest_other_database_stays_unless_a_run_opened_one_today(tmp_path: Path) -> None:
    native = native_module()
    database = fact_database(native, tmp_path)
    database.directory.mkdir(parents=True)
    ages = {"today": 0.1, "also-today": 0.2, "two-days": 2, "three-days": 3}
    units = {name: other_database(database.directory, f"{name}-l9.sqlite", age) for name, age in ages.items()}
    database.prune(TODAY)
    kept = {name for name, files in units.items() if all(path.exists() for path in files)}
    gone = {name for name, files in units.items() if not any(path.exists() for path in files)}
    assert (kept, gone) == ({"today", "also-today"}, {"two-days", "three-days"})


def test_a_database_opened_after_the_prune_listed_it_stays(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    native = native_module()
    database = fact_database(native, tmp_path)
    database.directory.mkdir(parents=True)
    other_database(database.directory, "newer-l9.sqlite", 2)
    older = other_database(database.directory, "older-l9.sqlite", 3)
    listed = FactDatabase.others

    def others_then_opened(self: FactDatabase, files: list[tuple[float, int, Path]]) -> Any:
        found = listed(self, files)
        older[-1].touch()  # a run of the older build opens it right after the listing
        return found

    monkeypatch.setattr(FactDatabase, "others", others_then_opened)
    database.prune(TODAY)
    assert all(path.exists() for path in older)


def records_directory(facts: Path, name: str, written_days_ago: float) -> Path:
    """A directory of the Python engine's per-file records, last written `written_days_ago`."""
    directory = facts / name
    directory.mkdir()
    entry = directory / "0123.json"
    entry.write_bytes(b"{}")
    stamp = time.time() - written_days_ago * DAY
    set_modified(entry, stamp)
    set_modified(directory, stamp)
    return directory


def test_the_two_newest_record_namespaces_stay_whole_and_empty_directories_go(tmp_path: Path) -> None:
    native = native_module()
    database = fact_database(native, tmp_path)
    facts = database.directory
    facts.mkdir(parents=True)
    namespaces = {
        "v3-b01-full": 2,
        "v3-b01-lite": 2,
        "v3-b02-full": 3,
        "v3-b03-full": 4,
        "v3-b03-lite": 1.5,
    }
    for name, age in namespaces.items():
        records_directory(facts, name, age)
    (facts / "left-empty").mkdir()
    set_modified(facts / "left-empty", time.time() - 2 * DAY)
    (facts / "made-now").mkdir()
    database.prune(TODAY)
    assert sorted(path.name for path in facts.iterdir()) == [
        "made-now",
        "v3-b01-full",
        "v3-b01-lite",
        "v3-b03-full",
        "v3-b03-lite",
    ]
