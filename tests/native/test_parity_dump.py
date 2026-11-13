from __future__ import annotations

import gc
import json
import subprocess
import tracemalloc
from pathlib import Path

import pytest

from hoglint.ledger.scan import ScanSummary
from hoglint.native.normal import FIELDS
from hoglint.native.parity import LIST_OUTPUTS, Dump, compare_dumps

from native.require import native_module
from native.test_parity import OWNERS_REPO


def test_a_dump_fails_when_its_stored_summary_cannot_be_built(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    native_module()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    repo = tmp_path / "repo"
    for path, text in OWNERS_REPO.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)

    def changed_call_shape(*args: object, **kwargs: object) -> ScanSummary:
        raise TypeError("from_run() got an unexpected keyword argument")

    monkeypatch.setattr(ScanSummary, "from_run", changed_call_shape)
    out, work = tmp_path / "out", tmp_path / "work"
    work.mkdir()
    try:
        with pytest.raises(TypeError):
            Dump(repo, out, work).run()
    finally:
        gc.enable()
    assert not (out / "stored-summary.jsonl").exists()


def write_dump(root: Path, rows: list[list[object]]) -> None:
    root.mkdir()
    for name in LIST_OUTPUTS:
        with open(root / f"{name}.jsonl", "w") as handle:
            handle.writelines(f"{json.dumps(row)}\n" for row in rows)
    (root / "levels.json").write_text("{}")


def test_two_dumps_compare_a_row_at_a_time(tmp_path: Path) -> None:
    rows: list[list[object]] = [[n, f"module.{n}"] for n in range(50_000)]
    # rows 10 and 11 swap places, row 12 changes
    changed = [*rows[:10], rows[11], rows[10], [12, "moved"], *rows[13:]]
    write_dump(tmp_path / "left", rows)
    write_dump(tmp_path / "right", changed)
    tracemalloc.start()
    diffs = compare_dumps(tmp_path / "left", tmp_path / "right")
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    edges = diffs[0]
    assert (edges.left, edges.right, edges.same_position, edges.missing, edges.extra) == (50_000, 50_000, 49_997, 1, 1)
    assert edges.fields == ((FIELDS[1], 3), (FIELDS[0], 2))
    assert [left for left, _ in edges.examples] == [[10, "module.10"], [11, "module.11"], [12, "module.12"]]
    # a table held whole takes megabytes here; a row at a time, a few kilobytes
    assert peak < 1_000_000, peak
