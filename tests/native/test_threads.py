"""How many threads a run takes (D6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hoglint.core.analysis import AnalysisOptions
from hoglint.core.config import CoreConfig
from hoglint.native.analysis import NativeAnalysis


@pytest.mark.parametrize(
    ("staged", "override", "threads"),
    [
        (True, "", 1),  # pre-commit, and the baseline it starts, link on one thread
        (False, "", 0),  # every core
        (True, "3", 3),
    ],
)
def test_a_staged_run_takes_one_thread_unless_told_otherwise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, staged: bool, override: str, threads: int
) -> None:
    monkeypatch.setenv("HOGLINT_THREADS", override)
    analysis = NativeAnalysis(AnalysisOptions(repo=tmp_path, config=CoreConfig(), work_dir=tmp_path, staged=staged))
    assert analysis.threads() == threads
