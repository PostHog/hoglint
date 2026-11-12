"""The replay over a small git repo: each check runs in its own process, as on a real sample."""

from __future__ import annotations

import os
import re
import json
import datetime
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest

import hoglint
from hoglint.ledger.cache import STORES, Store, StoreList
from hoglint.native.parity import RowDiff
from hoglint.native.predict import Prediction
from hoglint.native.replay import (
    CHECK_OUTPUTS,
    COLD,
    COLD_SWITCHES,
    DAMAGED,
    MODES,
    REUSE_SWITCHES,
    STORE_RULES,
    WARM,
    CheckDump,
    Checker,
    Compared,
    Mode,
    Pair,
    PairResult,
    PairsReport,
    PredictionScore,
    Replay,
    Side,
    States,
    StoreRules,
    Traces,
    TruthItem,
    certify,
    check_env,
    compare,
    damages_of,
    labelled,
    pick_damages,
)
from rules.test_compare import git_in, write_files

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


def write_tree(repo: Path, files: Mapping[str, str | None]) -> str:
    """The tree of `files` over the last one; None deletes a file."""
    write_files(repo, {path: text for path, text in files.items() if text is not None})
    for path, text in files.items():
        if text is None:
            (repo / path).unlink()
    git_in(repo, "add", ".")
    return git_in(repo, "write-tree").strip()


def git_pair(root: Path, name: str, files: dict[str, str], edits: Mapping[str, str | None]) -> tuple[Path, Pair]:
    repo = root / "repo"
    repo.mkdir()
    git_in(repo, "init", "-q")
    parent = write_tree(repo, files)
    child = write_tree(repo, edits)
    return repo, Pair(name, parent, child)


@pytest.fixture(scope="module")
def pair(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Pair]:
    return git_pair(tmp_path_factory.mktemp("replay"), "move-import", OWNERS_REPO, CHILD_EDITS)


@pytest.fixture(scope="module")
def model_pair(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Pair]:
    return git_pair(tmp_path_factory.mktemp("replay-django"), "gain-model", DJANGO_REPO, MODEL_EDITS)


def replay(tmp_path: Path, pair: tuple[Path, Pair], mode: str | Mode, damages: tuple[str, ...] = ()) -> PairResult:
    native_module()
    os.environ.setdefault("PYTHONHASHSEED", "0")
    repo, found = pair
    picked = MODES[mode]() if isinstance(mode, str) else mode
    checker = Checker(repo, "native", picked, tmp_path / "work", datetime.date(2026, 1, 1))
    return Replay(checker, pick_damages(",".join(damages))).run(found)


@pytest.mark.parametrize(
    ("mode", "damages", "equal"),
    [
        ("today", (), True),
        ("planted-miss", (), False),
        ("today", ("garbage-summaries", "garbage-inputs", "garbage-site-packages", "two-children-at-once"), True),
    ],
)
def test_a_replay_reports_a_pair_equal_only_when_every_check_reports_the_same(
    tmp_path: Path, pair: tuple[Path, Pair], mode: str, damages: tuple[str, ...], equal: bool
) -> None:
    result = replay(tmp_path, pair, mode, damages)
    assert result.ok is equal, result.line()
    labels = [verdict.label.split(" ")[0] for verdict in result.verdicts]
    assert labels[:2] == ["child", "back"]
    # A store damage has a verdict for the damaged check and one for the check after it.
    stores = [name for name in damages if name != "two-children-at-once"]
    assert len(labels) == 2 + 2 * len(stores) + 2 * ("two-children-at-once" in damages)
    assert all(f"{name}-after" in labels for name in stores)
    if not equal:
        child = result.verdicts[0]
        assert isinstance(child, Compared)
        assert [diff.name for diff in child.diffs if not diff.equal] == ["edges"]


@pytest.mark.parametrize(
    ("mode", "damages", "equal"),
    [("today", (), True), ("pack-memo-miss", (), False), ("today", ("remove-memo-parts", "stray-memo-key"), True)],
)
def test_a_pack_memo_that_forgets_its_reads_or_stays_broken_makes_the_replay_fail(
    tmp_path: Path, model_pair: tuple[Path, Pair], mode: str, damages: tuple[str, ...], equal: bool
) -> None:
    result = replay(tmp_path, model_pair, mode, damages)
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
    left, right = CheckDump.load(tmp_path / "left", COLD), CheckDump.load(tmp_path / "right", DAMAGED)
    verdict = compare(labelled("garbage-inputs", what), left, right)
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


NAMED_REPO = {
    "hoglint.toml": """
schema = 1

[discovery]
source_roots = ["."]
first_party = ["core"]

[fields]
seed = { type = "bool" }
gate = { type = "bool", on = "symbol" }
owners = { type = "set" }

[providers.codeowners]
use = "codeowners"
file = "CODEOWNERS"
sets = ["owners"]

[[assign]]
match = "core/seed.py"
set = { seed = true }

[[assign]]
symbols = { names = ["core.seed.Gate"] }
set = { gate = true }
""",
    "CODEOWNERS": "core/ @team-a\n",
    "core/__init__.py": "",
    "core/seed.py": "class Gate:\n    pass\n",
    "core/use.py": "from core.seed import Gate\n",
}
# Each child makes an item of a rule that every run makes from the config and the whole tree: the
# item belongs to no file, so no predicted file covers it.
RUN_LEVEL_CHANGES: dict[str, tuple[dict[str, str | None], str]] = {
    "a file a literal assignment path names is deleted": ({"core/seed.py": None}, "config:stale-name"),
    "a symbol an assignment names is renamed": ({"core/seed.py": "class Door:\n    pass\n"}, "config:stale-name"),
    "the file a provider reads is deleted": ({"CODEOWNERS": None}, "provider:codeowners"),
}


@pytest.fixture(scope="module", params=sorted(RUN_LEVEL_CHANGES))
def run_level_pair(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Pair]:
    edits, _ = RUN_LEVEL_CHANGES[request.param]
    return git_pair(tmp_path_factory.mktemp("replay-run-level"), request.param, NAMED_REPO, edits)


def run_level_rule(pair: tuple[Path, Pair]) -> str:
    return RUN_LEVEL_CHANGES[pair[1].name][1]


def test_a_warm_check_makes_a_run_level_item_again_that_no_changed_file_holds(
    tmp_path: Path, run_level_pair: tuple[Path, Pair]
) -> None:
    result = replay(tmp_path, run_level_pair, "today")
    assert result.status() == "EQUAL", result.line()
    assert result.prediction is not None
    assert run_level_rule(run_level_pair) in {item.rule for item in result.prediction.missed}


# The certificate with the run-level rules left out of what reruns, as a predictor that lost them would.
class WithoutRunLevelRules(Traces):
    def predict(self, changed: set[str], dumps: tuple[Path, Path]) -> Prediction:
        found = super().predict(changed, dumps)
        assert self.pair is not None
        return replace(found, rerun=found.rerun - set(self.pair.config.run_level))


@pytest.mark.parametrize(("mode", "word"), [(Traces, "HELD"), (WithoutRunLevelRules, "MISSED")])
def test_the_certificate_reruns_the_rules_every_run_makes_from_the_whole_tree(
    tmp_path: Path, run_level_pair: tuple[Path, Pair], mode: type[Traces], word: str
) -> None:
    result = replay(tmp_path, run_level_pair, mode())
    assert result.status() == word, result.line()
    if word == "MISSED":
        assert result.prediction is not None
        assert {item.rule for item in result.prediction.missed} == {run_level_rule(run_level_pair)}


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


# Switches that pick a binary or a thread count: none turns a store or a recording on or off.
NOT_REUSE_SWITCHES = {"HOGLINT_BASE_FACTS_BIN", "HOGLINT_FACTS_BIN", "HOGLINT_NATIVE_DIR", "HOGLINT_THREADS"}


def test_a_check_takes_no_reuse_switch_from_the_callers_shell() -> None:
    package = Path(hoglint.__file__).parent
    named = {match for path in package.rglob("*.py") for match in re.findall(r'"(HOGLINT_[A-Z_]+)"', path.read_text())}
    unknown = named - set(REUSE_SWITCHES) - NOT_REUSE_SWITCHES
    assert not unknown, "a new switch is a reuse switch (REUSE_SWITCHES) or not one (NOT_REUSE_SWITCHES)"
    caller = {"PATH": "/bin", **dict.fromkeys(REUSE_SWITCHES, "1")}
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


# What a warm check records: every store open or unused.
OPEN: dict[str, str] = {store.name: "open" for store in STORES.stores}
# What a cold check records: every store that is not shared off, and the summaries only written.
COLD_STATES: dict[str, str] = {
    **{store.name: "off" for store in STORES.stores if store.switch},
    **{store.name: "open" for store in STORES.stores if store.shared or not store.switch},
}
NO_READS = {"summaries": 0}
COLD_RECORD: dict[str, object] = {"memo": False, "stores": COLD_STATES, "reads": NO_READS}


@pytest.mark.parametrize(
    ("meta", "graph", "word"),
    [
        ({"memo": False, "stores": OPEN}, True, ""),
        ({"memo": False, "stores": {**OPEN, "pack-memo": "unused"}}, True, ""),
        ({"memo": False, "stores": {**OPEN, "pack-memo": "off"}}, True, "NO REUSE"),
        ({"memo": False, "stores": {**OPEN, "pack-memo": "failed"}}, True, "NO REUSE"),
        ({"memo": False, "stores": {**OPEN, "site-packages": "off"}}, True, "NO REUSE"),
        ({"memo": False, "stores": {**OPEN, "summaries": "failed"}}, True, "NO REUSE"),
        ({"memo": False}, True, "NO REUSE"),
        ({"memo": False, "stores": OPEN}, False, "NO GRAPH TABLES"),
        ({"memo": True}, False, ""),
    ],
)
def test_a_warm_check_must_show_that_it_reused_stored_work(
    tmp_path: Path, meta: dict[str, object], graph: bool, word: str
) -> None:
    cold = write_check(tmp_path / "cold", COLD_RECORD, graph=True)
    warm = write_check(tmp_path / "warm", meta, graph)
    verdict = compare("child", CheckDump.load(cold, COLD), CheckDump.load(warm, WARM))
    assert verdict.word == word, verdict.short()
    assert verdict.ok is (word == "")
    assert compare("child", CheckDump.load(cold, COLD), CheckDump.load(warm, DAMAGED)).word == ""


@pytest.mark.parametrize("side", [DAMAGED, WARM])
def test_a_side_without_its_check_tables_is_not_compared(tmp_path: Path, side: str) -> None:
    cold = write_check(tmp_path / "cold", COLD_RECORD, graph=True)
    other = write_check(tmp_path / "other", {"memo": False, "stores": OPEN}, graph=True)
    (other / "check-summary.jsonl").unlink()
    verdict = compare("child", CheckDump.load(cold, COLD), CheckDump.load(other, side))
    assert verdict.word == "NOT COMPARED"
    assert not PairResult(Pair("p", "a", "b"), [verdict]).ok


def test_a_warm_child_served_by_the_run_memo_is_not_printed_equal(tmp_path: Path) -> None:
    cold = write_check(tmp_path / "cold", COLD_RECORD, graph=True)
    warm = write_check(tmp_path / "warm", {"memo": True}, graph=False)
    result = PairResult(Pair("p", "a", "b"), [compare("child", CheckDump.load(cold, COLD), CheckDump.load(warm, WARM))])
    assert result.ok
    assert result.status() == "MEMO"
    assert "served by the run memo (3 tables compared" in result.line()


def test_a_side_that_reuses_stored_work_needs_a_state_a_parent_check_filled(tmp_path: Path) -> None:
    state = States(tmp_path).fresh("warm")
    with pytest.raises(TypeError):
        Side.warm(state)  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError):
        Side.damaged(state)  # ty: ignore[invalid-argument-type]
    cold = Side.cold(States(tmp_path), "cold")
    assert cold.env == COLD_SWITCHES
    with pytest.raises(ValueError):
        compare("child", CheckDump(tmp_path, {}, WARM), CheckDump(tmp_path, {}, COLD))


@pytest.mark.parametrize(
    ("states", "reads", "memo"),
    [
        ({"site-packages": "open"}, NO_READS, False),
        ({"pack-memo": "open"}, NO_READS, False),
        ({"pack-memo": "failed"}, NO_READS, False),
        ({}, {"summaries": 1}, False),
        ({}, {}, False),
        ({}, NO_READS, True),
    ],
)
def test_a_cold_check_that_shared_a_store_is_not_compared(
    tmp_path: Path, states: dict[str, str], reads: dict[str, int], memo: bool
) -> None:
    record: dict[str, object] = {"memo": memo, "stores": {**COLD_STATES, **states}, "reads": reads}
    cold = write_check(tmp_path / "cold", record, graph=True)
    other = write_check(tmp_path / "other", COLD_RECORD, graph=True)
    verdict = compare("seed1-threads1", CheckDump.load(cold, COLD), CheckDump.load(other, COLD))
    assert verdict.word == "NOT COLD", verdict.short()
    result = PairResult(Pair("p", "a", "b"), [verdict])
    assert not result.ok
    assert result.status() == "NOT COLD"


def test_a_pair_without_a_verdict_its_mode_owes_fails() -> None:
    equal = Compared("child", (RowDiff("check-exit", 1, 1, 1, 0, 0, (), ()),), False)
    owed = MODES["today"].owes
    result = PairResult(Pair("p", "a", "b"), [equal], owed=owed)
    assert not result.ok
    assert result.status() == "NO VERDICT"
    assert "back: NO VERDICT" in result.line()
    assert not PairResult(Pair("p", "a", "b"), owed=owed).ok
    assert not PairResult(Pair("p", "a", "b"), owed=MODES["traces"].owes).ok
    assert not PairResult(Pair("p", "a", "b"), [Compared("child", (), False)]).ok


def test_a_laid_file_replaces_its_path_in_both_trees_and_keeps_the_rest(tmp_path: Path) -> None:
    repo, found = git_pair(tmp_path, "move-import", OWNERS_REPO, CHILD_EDITS)
    preset = tmp_path / "hoglint.toml"
    preset.write_text(OWNERS_REPO["hoglint.toml"] + "# laid\n")
    laid = found.laid(repo, {"hoglint.toml": preset})
    for tree, edits in ((laid.parent, {}), (laid.child, CHILD_EDITS)):
        assert git_in(repo, "show", f"{tree}:hoglint.toml") == preset.read_text()
        assert git_in(repo, "show", f"{tree}:core/b.py") == {**OWNERS_REPO, **edits}["core/b.py"]


def test_a_store_added_to_the_list_reaches_every_rule_of_the_replay() -> None:
    made_up = Store("made-up", "made-up", shared=False, warning="", switch="HOGLINT_MADE_UP", off="1")
    rules = StoreRules(StoreList((*STORES.stores, made_up), STORES.recordings))
    assert rules.cold_switches() == {**STORE_RULES.cold_switches(), "HOGLINT_MADE_UP": "1"}
    assert "HOGLINT_MADE_UP" in rules.reuse_switches()
    assert {"garbage-made-up", "empty-made-up", "truncate-made-up"} <= {damage.name for damage in damages_of(rules)}
    cold = {"memo": False, "stores": {**COLD_STATES, "made-up": "open"}, "reads": NO_READS}
    assert rules.cold_problem(cold) == "the made-up store was open"
    assert rules.cold_problem({**cold, "stores": {**COLD_STATES, "made-up": "off"}}) == ""
    assert rules.warm_problem({"memo": False, "stores": {**OPEN, "made-up": "off"}}) == "the made-up store was off"
    assert rules.warm_problem({"memo": False, "stores": OPEN}) == "the made-up store was not recorded"
