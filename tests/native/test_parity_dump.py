from __future__ import annotations

import gc
import subprocess
from pathlib import Path

import pytest

from hoglint.ledger.scan import ScanSummary
from hoglint.native.parity import Dump

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
            Dump(repo, "native", out, work).run()
    finally:
        gc.enable()
    assert not (out / "stored-summary.jsonl").exists()
