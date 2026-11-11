"""The replay over a small git repo: each check runs in its own process, as on a real sample."""

from __future__ import annotations

import os
import json
import datetime
import subprocess
from pathlib import Path

import pytest

from hoglint.native.replay import (
    CHECK_OUTPUTS,
    COLD_SWITCHES,
    MODES,
    Checker,
    CheckDump,
    Compared,
    Pair,
    PairResult,
    PairsReport,
    PredictionScore,
    Replay,
    TruthItem,
    certify,
    check_env,
    compare,
    labelled,
)

from native.require import native_module
from native.test_parity import OWNERS_REPO

# The child moves core/b.py's import to another module: its edges and its finding change, and
# nothing else does.
CHILD_EDITS = {"core/b.py": "from products.billing.models import Invoice\n"}
# A Django repo whose models module gains a second model in the child: the pack memo's item for
# that module must run again, or the registry misses the new model.
MODEL = "from django.db import models\n\n\nclass Invoice(models.Model):\n    pass\n"
DJANGO_REPO = {
    "hoglint.toml": (
        'schema = 1\nextends = ["hoglint:django"]\n\n[discovery]\nsource_roots = ["."]\n'
        'first_party = ["app", "proj", "django"]\n\n[django]\nsettings = "proj.settings"\n'
    ),
    "proj/__init__.py": "",
    "proj/settings.py": 'INSTALLED_APPS = ["app"]\n',
    "app/__init__.py": "",
    "app/models.py": MODEL,
    "django/__init__.py": "",
    "django/db/__init__.py": "",
    "django/db/models/__init__.py": "from django.db.models.base import Model\n",
    "django/db/models/base.py": "class Model:\n    pass\n",
}
MODEL_EDITS = {"app/models.py": MODEL + "\n\nclass Refund(models.Model):\n    pass\n"}


def write_tree(repo: Path, files: dict[str, str]) -> str:
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    return subprocess.run(
        ["git", "-C", str(repo), "write-tree"], check=True, capture_output=True, text=True
    ).stdout.strip()


def git_pair(root: Path, name: str, files: dict[str, str], edits: dict[str, str]) -> tuple[Path, Pair]:
    repo = root / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    parent = write_tree(repo, files)
    child = write_tree(repo, edits)
    return repo, Pair(name, parent, child)


@pytest.fixture(scope="module")
def pair(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Pair]:
    return git_pair(tmp_path_factory.mktemp("replay"), "move-import", OWNERS_REPO, CHILD_EDITS)


@pytest.fixture(scope="module")
def model_pair(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Pair]:
    return git_pair(tmp_path_factory.mktemp("replay-django"), "gain-model", DJANGO_REPO, MODEL_EDITS)


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
    assert result.ok is equal, result.line()
    labels = [verdict.label.split(" ")[0] for verdict in result.verdicts]
    assert labels[:2] == ["child", "back"]
    assert len(labels) == 2 + len(damages) + ("two-children-at-once" in damages)
    if not equal:
        child = result.verdicts[0]
        assert isinstance(child, Compared)
        assert [diff.name for diff in child.diffs if not diff.equal] == ["edges"]


@pytest.mark.parametrize(("mode", "equal"), [("today", True), ("pack-memo-miss", False)])
def test_a_pack_memo_that_forgets_its_reads_makes_the_replay_fail(
    tmp_path: Path, model_pair: tuple[Path, Pair], mode: str, equal: bool
) -> None:
    result = replay(tmp_path, model_pair, mode)
    assert result.ok is equal, result.line()


def test_the_truth_set_holds_the_file_whose_rows_changed_and_the_commit_predicts_it(
    tmp_path: Path, pair: tuple[Path, Pair]
) -> None:
    prediction = replay(tmp_path, pair, "today").prediction
    assert prediction is not None
    assert prediction.truth == {"core/b.py"}
    assert prediction.predicted == {"core/b.py"}
    assert not prediction.missed


@pytest.mark.parametrize("what", ["", "3 damaged"])
def test_two_checks_of_different_engines_are_never_compared(tmp_path: Path, what: str) -> None:
    for side, engine in (("left", "native-aaaa"), ("right", "native-bbbb")):
        (tmp_path / side).mkdir()
        (tmp_path / side / "check.json").write_text(json.dumps({"engine": engine}))
        (tmp_path / side / "check-exit.jsonl").write_text("[0]\n")
    left, right = CheckDump.load(tmp_path / "left"), CheckDump.load(tmp_path / "right")
    verdict = Replay.verdict(labelled("garbage-inputs", what), left, right)
    assert not verdict.ok
    assert "different engines" in verdict.short()
    assert not PairResult(Pair("p", "a", "b"), [verdict]).ok


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


def missed_prediction() -> PredictionScore:
    item = TruthItem("edges", "imports", "core/t.py", frozenset({"core/t.py"}))
    return PredictionScore(frozenset({"core/t.py"}), frozenset(), (item,), (item,))


def test_a_certificate_whose_prediction_missed_a_file_fails_the_pair(capsys: pytest.CaptureFixture[str]) -> None:
    result = PairResult(Pair("p", "a", "b"), [certify(missed_prediction())], missed_prediction())
    assert not result.ok
    assert result.status() == "MISSED"
    report = PairsReport()
    report.add(result)
    assert report.close() is False
    assert "p  MISSED  prediction: MISSED (1 files" in capsys.readouterr().out
    held = PredictionScore(frozenset({"core/t.py"}), frozenset({"core/t.py"}), (), ())
    assert PairResult(Pair("p", "a", "b"), [certify(held)]).status() == "HELD"
    assert PairResult(Pair("p", "a", "b"), [certify(None)]).status() == "NO PREDICTION"


def test_a_replay_that_compared_no_pair_fails() -> None:
    assert PairsReport().close() is False


def test_a_check_takes_no_reuse_switch_from_the_callers_shell() -> None:
    caller = {"PATH": "/bin", "HOGLINT_PACK_MEMO": "off", "HOGLINT_NO_ANSWER_CACHE": "1", "HOGLINT_LINK_TRACE": "1"}
    assert check_env(caller, {}) == {"PATH": "/bin"}
    assert check_env(caller, COLD_SWITCHES) == {"PATH": "/bin", **COLD_SWITCHES}


def write_check(side: Path, meta: dict[str, object], graph: bool) -> Path:
    side.mkdir()
    (side / "check.json").write_text(json.dumps({"engine": "native-a", **meta}))
    for table in CHECK_OUTPUTS:
        (side / f"{table}.jsonl").write_text("[0]\n")
    if graph:
        (side / "modules.jsonl").write_text('["a", "a.py"]\n')
    return side


OPEN = {"answers": True, "summaries": True, "pack_memo": ["open"]}


@pytest.mark.parametrize(
    ("meta", "graph", "word"),
    [
        ({"memo": False, "reuse": OPEN}, True, ""),
        ({"memo": False, "reuse": {**OPEN, "pack_memo": ["unused"]}}, True, ""),
        ({"memo": False, "reuse": {**OPEN, "pack_memo": ["off"]}}, True, "NO REUSE"),
        ({"memo": False, "reuse": {**OPEN, "pack_memo": ["no store"]}}, True, "NO REUSE"),
        ({"memo": False, "reuse": {**OPEN, "pack_memo": ["failed"]}}, True, "NO REUSE"),
        ({"memo": False, "reuse": {**OPEN, "answers": False}}, True, "NO REUSE"),
        ({"memo": False, "reuse": OPEN}, False, "NO GRAPH TABLES"),
        ({"memo": True, "reuse": {**OPEN, "pack_memo": []}}, False, ""),
    ],
)
def test_a_warm_check_must_show_that_it_reused_stored_work(
    tmp_path: Path, meta: dict[str, object], graph: bool, word: str
) -> None:
    cold = write_check(tmp_path / "cold", {"memo": False}, graph=True)
    warm = write_check(tmp_path / "warm", meta, graph)
    verdict = compare("child", CheckDump.load(cold), CheckDump.load(warm), warm=True)
    assert verdict.word == word, verdict.short()
    assert verdict.ok is (word == "")
    assert compare("child", CheckDump.load(cold), CheckDump.load(warm)).word == ""


@pytest.mark.parametrize("warm", [False, True])
def test_a_side_without_its_check_tables_is_not_compared(tmp_path: Path, warm: bool) -> None:
    cold = write_check(tmp_path / "cold", {"memo": False}, graph=True)
    other = write_check(tmp_path / "other", {"memo": False, "reuse": OPEN}, graph=True)
    (other / "check-summary.jsonl").unlink()
    verdict = compare("child", CheckDump.load(cold), CheckDump.load(other), warm=warm)
    assert verdict.word == "NOT COMPARED"
    assert not PairResult(Pair("p", "a", "b"), [verdict]).ok


def test_a_warm_child_served_by_the_run_memo_is_not_printed_equal(tmp_path: Path) -> None:
    cold = write_check(tmp_path / "cold", {"memo": False}, graph=True)
    warm = write_check(tmp_path / "warm", {"memo": True}, graph=False)
    result = PairResult(Pair("p", "a", "b"), [compare("child", CheckDump.load(cold), CheckDump.load(warm), warm=True)])
    assert result.ok
    assert result.status() == "MEMO"
    assert "served by the run memo (3 tables compared" in result.line()
