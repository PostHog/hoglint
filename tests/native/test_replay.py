"""The replay over a small git repo: each check runs in its own process, as on a real sample."""

from __future__ import annotations

import os
import json
import datetime
import subprocess
from pathlib import Path

import pytest

from hoglint.native.replay import MODES, Checker, Pair, PairResult, Replay, Verdict

from native.require import native_module
from native.test_parity import OWNERS_REPO

# The child moves core/b.py's import to another module: its edges and its finding change, and
# nothing else does.
CHILD_EDITS = {"core/b.py": "from products.billing.models import Invoice\n"}


def write_tree(repo: Path, files: dict[str, str]) -> str:
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    return subprocess.run(
        ["git", "-C", str(repo), "write-tree"], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture(scope="module")
def pair(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Pair]:
    repo = tmp_path_factory.mktemp("replay") / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    parent = write_tree(repo, OWNERS_REPO)
    child = write_tree(repo, CHILD_EDITS)
    return repo, Pair("move-import", parent, child)


def replay(tmp_path: Path, pair: tuple[Path, Pair], mode: str, damages: tuple[str, ...] = ()) -> PairResult:
    native_module()
    os.environ.setdefault("PYTHONHASHSEED", "0")
    repo, found = pair
    checker = Checker(repo, "native", MODES[mode](), tmp_path / "work", datetime.date(2026, 1, 1))
    return Replay(checker, damages).run(found)


@pytest.mark.parametrize(
    ("mode", "damages", "equal"),
    [
        ("today", (), True),
        ("planted-miss", (), False),
        ("today", ("garbage-summaries", "garbage-inputs", "two-children-at-once"), True),
    ],
)
def test_a_replay_reports_a_pair_equal_only_when_every_check_reports_the_same(
    tmp_path: Path, pair: tuple[Path, Pair], mode: str, damages: tuple[str, ...], equal: bool
) -> None:
    result = replay(tmp_path, pair, mode, damages)
    assert result.equal is equal, result.line()
    labels = [verdict.label.split(" ")[0] for verdict in result.verdicts]
    assert labels[:2] == ["child", "back"]
    assert len(labels) == 2 + len(damages) + ("two-children-at-once" in damages)
    if not equal:
        child = result.verdicts[0]
        assert [diff.name for diff in child.diffs if not diff.equal] == ["edges"]


def test_the_truth_set_holds_the_file_whose_rows_changed_and_the_commit_predicts_it(
    tmp_path: Path, pair: tuple[Path, Pair]
) -> None:
    prediction = replay(tmp_path, pair, "today").prediction
    assert prediction is not None
    assert prediction.truth == {"core/b.py"}
    assert prediction.predicted == {"core/b.py"}
    assert not prediction.missed


def test_two_checks_of_different_engines_are_never_compared(tmp_path: Path) -> None:
    for side, engine in (("left", "native-aaaa"), ("right", "native-bbbb")):
        (tmp_path / side).mkdir()
        (tmp_path / side / "check.json").write_text(json.dumps({"engine": engine}))
        (tmp_path / side / "check-exit.jsonl").write_text("[0]\n")
    verdict = Verdict.of("child", tmp_path / "left", tmp_path / "right")
    assert not verdict.equal
    assert "different engines" in verdict.short()


def test_the_certificate_predicts_from_the_parents_traces_and_reports_each_gap(
    tmp_path: Path, pair: tuple[Path, Pair]
) -> None:
    result = replay(tmp_path, pair, "traces")
    prediction = result.prediction
    assert prediction is not None
    assert prediction.truth == {"core/b.py"}
    assert "core/b.py" in prediction.predicted
    assert not prediction.missed
    assert [note for note in result.notes if note.startswith("gaps ")] == ["gaps none"]
