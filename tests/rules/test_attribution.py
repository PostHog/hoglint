from __future__ import annotations

import json
import base64
import shutil
import hashlib
import datetime
import dataclasses
import multiprocessing
from pathlib import Path
from typing import Any

import pytest

from core.support import set_modified
from hoglint.cli import Commands, Snapshots
from hoglint.config import load_config
from hoglint.facts import find_binary
from hoglint.labels import ProviderRunner
from hoglint.ledger import store as store_module
from hoglint.ledger.cache import RunStores
from hoglint.ledger.couplings import CouplingScan
from hoglint.ledger.envelope import (
    ENGINE,
    FACTS,
    NATIVE,
    PROTOCOL,
    SECTIONS,
    BinaryIds,
    EngineError,
    EngineSources,
    Envelope,
    Framework,
    PolicyFiles,
    policy_digest,
)
from hoglint.ledger.equality import EqualityResult, LineDiff
from hoglint.ledger.file import LedgerFile
from hoglint.ledger.inputs import SOURCE_INPUTS, InputDigests, InputPatterns, RunInputs, TreeFiles
from hoglint.ledger.listing import TreeListing, WorktreeListing, WorktreeStatus, read_blobs
from hoglint.ledger.obligations import Attribution, Inherited, Standing, attribute, rows
from hoglint.ledger.records import FindingRecord, IncompleteRecord, ShownRecord, StoredFindings
from hoglint.ledger.scan import ProtocolError, ScanSummary
from hoglint.ledger.store import CacheRoot, SummaryStore
from hoglint.model import FindingStatus, IncompleteCause, IncompleteKind
from hoglint.packs.posthog.owners import OWNERS_YAML
from hoglint.report import View
from hoglint.rules.engine import Engine
from hoglint.rules.exceptions import ExceptionsFile
from hoglint.source import GraphSource
from native.require import native_required

from rules.support import GraphBuilder, config_from, native_graph, write_config
from rules.test_compare import CONFIG, FILES, git_in, has_extractor, hoglint, needs_extractor, write_files

DYNAMIC = ("dynamic-imports", "app.loader|dynamic-import|", "dynamic-import '' did not resolve")
OTHER_REASON = ("dynamic-imports", "app.loader|dynamic-import|", "selection reads an unknown value")
INTRODUCED, AMBIGUOUS, INHERITED = Attribution.INTRODUCED, Attribution.AMBIGUOUS, Attribution.INHERITED


def records(obligations: dict[tuple[str, str, str], list[str]]) -> list[IncompleteRecord]:
    return [
        IncompleteRecord(rule, key, reason, IncompleteCause.INHERITED, (), mark, IncompleteKind.NOT_MODELLED, "", 0)
        for (rule, key, reason), marks in obligations.items()
        for mark in marks
    ]


@pytest.mark.parametrize(
    "base, head, labels",
    [
        # the same unknown, moved lines or not: inherited
        ({DYNAMIC: ["f1"]}, {DYNAMIC: ["f1"]}, [INHERITED]),
        # a second computed import in the same module: the key holds one more item (adversary F8)
        ({DYNAMIC: ["f1"]}, {DYNAMIC: ["f1", "f1"]}, [INHERITED, INTRODUCED]),
        # one unknown replaced by another under the same obligation: nothing tells which is which
        ({DYNAMIC: ["f1"]}, {DYNAMIC: ["f2"]}, [AMBIGUOUS]),
        # another obligation under a key the base already has
        ({DYNAMIC: ["f1"]}, {DYNAMIC: ["f1"], OTHER_REASON: ["f1"]}, [INHERITED, INTRODUCED]),
        # unknowns the change removed never block
        ({DYNAMIC: ["f1", "f2"]}, {DYNAMIC: ["f2"]}, [INHERITED]),
    ],
)
def test_incomplete_items_are_attributed_by_count_and_fingerprint_through_serialization(
    base: dict, head: dict, labels: list[Attribution]
) -> None:
    stored = ScanSummary.from_json(json.loads(json.dumps(ScanSummary(incomplete=records(base)).to_json())))
    assert [label for label, _ in rows(attribute(head, stored.obligations()))] == labels


CROSSING = ("public-surface", ("products.billing.logic.price", "core.api.badge", "import"))
STALE = LineDiff(("public-surface", ("products.billing.logic.total", "core.api.report", "import")), 1, 2)


def standing(new: int, stale: list[LineDiff]) -> Standing:
    """A summary with `new` new findings of `CROSSING` and the `stale` lines, through serialization."""
    finding = FindingRecord(*CROSSING, FindingStatus.NEW, None, (), "core.api.badge", (), True, False)
    summary = ScanSummary(findings=StoredFindings.of([finding] * new), equality=EqualityResult(stale=stale))
    summary.new_rows = summary.findings.new_rows()
    return Standing.of(ScanSummary.from_json(json.loads(json.dumps(summary.to_json()))))


@pytest.mark.parametrize(
    "head, staged, keys, lines",
    [
        # what HEAD has already: inherited
        (standing(1, [STALE]), standing(1, [STALE]), {CROSSING}, {STALE}),
        # a second use of the same name: the count rises, so the key blocks as without HEAD
        (standing(1, []), standing(2, []), set(), set()),
        # the change fixes one of HEAD's two, and the ledger still records more
        (standing(2, []), standing(1, []), {CROSSING}, set()),
        # the change fixes one more finding of a line that is stale at HEAD: the line moves, so it blocks
        (standing(0, [STALE]), standing(0, [dataclasses.replace(STALE, scanned=0)]), set(), set()),
    ],
)
def test_a_commit_inherits_new_findings_and_stale_lines_by_key_and_count(
    head: Standing, staged: Standing, keys: set, lines: set
) -> None:
    inherited = Inherited.of(staged, head)
    assert (set(inherited.keys), set(inherited.lines)) == (keys, lines)


STORED = Envelope("tree1", "python-e1", "p1", "f1")


@pytest.mark.parametrize(
    "asked, scope, content, hit",
    [
        (STORED, frozenset(), None, True),
        # any other envelope part is another summary
        (Envelope("tree2", "python-e1", "p1", "f1"), frozenset(), None, False),
        (Envelope("tree1", "python-e2", "p1", "f1"), frozenset(), None, False),
        (Envelope("tree1", "python-e1", "p2", "f1"), frozenset(), None, False),
        (Envelope("tree1", "python-e1", "p1", "f2"), frozenset(), None, False),
        # couplings cut to another rename scope cannot admit this one's moves
        (STORED, frozenset({"app.moved"}), None, False),
        # a corrupt file or another protocol is a miss, never a result
        (STORED, frozenset(), "{not json", False),
        (STORED, frozenset(), json.dumps({"protocol": 0}), False),
        (STORED, frozenset(), json.dumps({"protocol": 1, "obligations": 7}), False),
    ],
)
def test_a_stored_summary_serves_only_its_exact_envelope(
    tmp_path: Path, asked: Envelope, scope: frozenset[str], content: str | None, hit: bool
) -> None:
    store = SummaryStore(tmp_path)
    path = store.put(STORED, ScanSummary(envelope=STORED, incomplete=records({DYNAMIC: ["f1"]})))
    assert path is not None
    if content is not None:
        path.write_text(content)
    found = store.get(asked)
    assert (found is not None and found.covers(scope) and found.obligations() == {DYNAMIC: ["f1"]}) is hit


@pytest.mark.parametrize(
    "row",
    [
        ["r", ["a"], "new", None, [], "", []],
        ["r", ["a"], "forgotten", None, [], "", [], True, False],
        ["r", ["a"], "new", None, [["import", "a.py", 1]], "", [], True, False],
        ["r", ["a"], "new", None, [["import", "a.py", 1, 0, "a", 5]], "", [], True, False],
    ],
    ids=["short row", "unknown status", "short witness", "witness targets not a list"],
)
def test_a_malformed_findings_row_is_a_miss_not_a_crash(tmp_path: Path, row: list[Any]) -> None:
    summary = ScanSummary(envelope=STORED, findings=StoredFindings([row]))
    assert SummaryStore(tmp_path).put(STORED, summary) is not None
    assert SummaryStore(tmp_path).get(STORED) is None
    summary.write(tmp_path / "summary.zst")
    with pytest.raises(ProtocolError):
        ScanSummary.read(tmp_path / "summary.zst")


def test_a_reader_loads_only_the_sections_it_asks_for_and_equal_sections_are_stored_once(tmp_path: Path) -> None:
    store = SummaryStore(tmp_path)
    base = ScanSummary(envelope=STORED, incomplete=records({DYNAMIC: ["f1"]}), modules={"app.a": "app/a.py"})
    staged = dataclasses.replace(base, envelope=dataclasses.replace(STORED, inputs="inputs2"))
    store.put(base.envelope, base)
    sections = sorted((tmp_path / "summaries").rglob("*.zst"))
    store.put(staged.envelope, staged)
    assert sorted((tmp_path / "summaries").rglob("*.zst")) == sections
    incompletes = store.get(STORED, frozenset({"envelope", "incompletes"}))
    assert incompletes is not None and incompletes.obligations() == {DYNAMIC: ["f1"]} and not incompletes.modules
    for section in sections:
        section.write_bytes(b"not zstd")
    # a store keeps what it decoded, so only another process reads the corrupt files
    assert SummaryStore(tmp_path).get(STORED, frozenset({"envelope", "incompletes"})) is None


CLASSIFY = """
schema = 1

[rules.flags]
primitive = "inventory"
kinds = ["import"]
classify = "policy/flags.toml"
identity = "module-pair"
growth_key = ["src.module"]
"""


@pytest.mark.parametrize("code_text, read_from_policy_root", [("a = 1", False), ("a = 2", True)])
def test_a_policy_data_file_belongs_to_the_policy(tmp_path: Path, code_text: str, read_from_policy_root: bool) -> None:
    policy_root, code_root = tmp_path / "policy", tmp_path / "code"
    config = config_from(policy_root, CLASSIFY, {"policy/flags.toml": "a = 1"})
    write_files(code_root, {"policy/flags.toml": code_text})
    anchored = PolicyFiles(config).anchored(policy_root, code_root)
    assert (anchored.rules["flags"].classify == str(policy_root / "policy/flags.toml")) is read_from_policy_root
    before = policy_digest(config, [], [], policy_root)
    (policy_root / "policy/flags.toml").write_text("a = 3")
    assert policy_digest(config, [], [], policy_root) != before


def test_a_summary_holds_no_absolute_path_and_no_day(tmp_path: Path) -> None:
    policy_root, code_root = tmp_path / "policy", tmp_path / "code"
    config = config_from(policy_root, CLASSIFY, {"policy/flags.toml": "entry = []"})
    write_files(code_root, {"policy/flags.toml": "entry = [{ identity = ['x'] }]"})
    anchored = PolicyFiles(config).anchored(policy_root, code_root)
    graph = GraphBuilder().module("app.a", "app.b").edge("import", "app.a", "app.b").graph
    entry = {"rule": "flags", "match": {"src.module": "gone"}, "reason": "r", "owners": ["t"], "scope": "prod"}
    exceptions = ExceptionsFile.parse({"schema": 1, "exception": [{**entry, "review_by": "2026-01-01"}]}, config)
    result = Engine(anchored, native_graph(graph), code_root, today=datetime.date(2026, 11, 9)).evaluate(
        None, exceptions
    )
    summary = ScanSummary.from_run(result, graph, Envelope(), roots=(policy_root, code_root), exceptions=exceptions)
    assert str(tmp_path) not in json.dumps(summary.to_json())
    shown = [f.shown for f in summary.findings]
    assert [s.message for s in shown if s is not None] == ["app.a app.b has no classification in policy/flags.toml"]
    assert None not in shown
    assert summary.exceptions.expired(datetime.date(2025, 12, 31)) == []
    assert summary.exceptions.expired(datetime.date(2026, 1, 2)) == [("2026-01-01", "exception #0 (flags)")]


CROSS = """
schema = 1

[fields]
package = { type = "id" }

[[assign]]
match = "products/{product}/**"
set = { package = "{product}" }

[[assign]]
match = "core/**"
set = { package = "core" }

[rules.cross]
primitive = "edge"
kinds = ["import"]
select = "src.package != dst.package"
identity = ["src.module", "dst.module"]
growth_key = ["src.module"]

[levels.isolated]
group_by = "package"
requires = ["cross"]
"""


def test_a_stored_summary_keeps_what_check_prints_and_names_other_findings_by_key(tmp_path: Path) -> None:
    config = config_from(tmp_path, CROSS)
    graph = (
        GraphBuilder()
        .module("core.a", "core.b", "products.billing.logic", "products.billing.models")
        .edge("import", "core.a", "products.billing.logic", line=3)
        .edge("import", "core.b", "products.billing.models", line=7)
        .graph
    )
    accepted = ("cross", ("core.a", "products.billing.logic"))
    ledger = LedgerFile(schema=1, policy=config.policy_hash, lines={accepted: 1}, exists=True)
    result = Engine(config, native_graph(graph), tmp_path).evaluate(ledger)
    envelope = Envelope("t" * 40, "engine", "policy", "framework")
    store = SummaryStore(tmp_path / "cache")
    store.put(envelope, ScanSummary.from_run(result, graph, envelope, roots=(tmp_path,)))
    stored = store.get(envelope, frozenset({"envelope", "findings", "levels"}))
    assert stored is not None
    records = {record.key: record for record in stored.findings}
    for finding in result.findings():
        shown = records[finding.ledger_key].shown
        if finding.status is not FindingStatus.NEW:
            assert shown is None, finding.ledger_key
            continue
        site = finding.site
        assert site is not None
        assert shown == ShownRecord(finding.message, site.path, site.line)
    assert {finding.status for finding in result.findings()} == {FindingStatus.NEW, FindingStatus.ACCEPTED}
    level = result.levels["isolated"]
    assert stored.levels["isolated"] == level.passing
    failing = stored.level_failures["isolated"]
    assert set(failing) == set(level.failing) == {"billing", "core"}
    new = {finding.ledger_key for finding in result.findings() if finding.status is FindingStatus.NEW}
    assert {(block.rule, block.identity) for blocks in failing.values() for block in blocks} == new


DAY = 86_400.0
NOW = 100 * DAY


@pytest.mark.parametrize(
    "files, kept",
    [
        # a file older than the age bound goes, a fresh one stays
        ({"bin/v3-old/a.json": (31, 10), "summaries/e1/t1/p-f.json": (1, 10)}, ["summaries/e1/t1/p-f.json"]),
        # over the size bound the oldest go first
        ({"a": (3, 60), "b": (2, 60), "c": (1, 60)}, ["b", "c"]),
        # the fact cache has its own pruner, which deletes a database only with its -wal
        (
            {"facts/x-l2.sqlite": (31, 100), "facts/x-l2.sqlite-wal": (31, 100), "a": (1, 10)},
            ["a", "facts/x-l2.sqlite", "facts/x-l2.sqlite-wal"],
        ),
    ],
)
def test_the_cache_root_stays_within_its_age_and_size(tmp_path: Path, files: dict, kept: list[str]) -> None:
    for name, (age_days, size) in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
        set_modified(path, NOW - age_days * DAY)
    CacheRoot(tmp_path, max_bytes=150).prune(now=NOW)
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()) == kept


PRUNERS = 6


def prune_after(start: Any, root: Path, today: datetime.date) -> bool:
    start.wait()
    return CacheRoot(root).prune_daily(today)


def test_of_processes_that_start_the_day_together_one_prunes(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    start = context.Manager().Barrier(PRUNERS)
    with context.Pool(PRUNERS) as pool:
        pruned = pool.starmap(prune_after, [(start, tmp_path, datetime.date(2026, 11, 10))] * PRUNERS)
    assert sorted(pruned) == [False] * (PRUNERS - 1) + [True]
    assert not CacheRoot(tmp_path).prune_daily(datetime.date(2026, 11, 10))
    assert CacheRoot(tmp_path).prune_daily(datetime.date(2026, 11, 11))
    # Runs either side of midnight: the later day's prune stands for the earlier day too.
    assert not CacheRoot(tmp_path).prune_daily(datetime.date(2026, 11, 10))
    assert not CacheRoot(tmp_path).prune_daily(datetime.date(2026, 11, 11))


def test_a_staged_check_needs_no_index_lock(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    write_files(repo, {"hoglint.toml": DYNAMIC_CONFIG, "core/__init__.py": "", "core/loader.py": LOADER})
    git_in(repo, "init", "-q")
    git_in(repo, "add", "-A")
    (repo / ".git" / "index.lock").write_text("")
    (tmp_path / "work").mkdir()
    tree = Snapshots(repo, tmp_path / "work").write_tree()
    (repo / ".git" / "index.lock").unlink()
    assert tree == git_in(repo, "write-tree").strip()


@pytest.mark.parametrize("fault", ["unwritable", "full"])
def test_a_cache_that_cannot_be_used_warns_once_and_changes_no_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], fault: str
) -> None:
    repo = tmp_path / "repo"
    write_files(repo, {"hoglint.toml": DYNAMIC_CONFIG, "core/__init__.py": "", "core/loader.py": LOADER})
    git_in(repo, "init", "-q")
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-qm", "one computed import")
    (repo / "core/loader.py").write_text(SECOND_LOADER)
    git_in(repo, "add", "-A")
    monkeypatch.setattr(
        GraphSource, "build", lambda *args, **kwargs: native_graph(GraphBuilder().module("core.loader").graph)
    )

    def check(cache: Path) -> int:
        monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
        (tmp_path / "work").mkdir(exist_ok=True)
        commands = Commands(repo, tmp_path / "work")
        try:
            return commands.check(index=True, write_ledger=False, changed=False)
        finally:
            commands.snapshots.close()

    healthy = check(tmp_path / "healthy")
    expected = capfd.readouterr().out
    monkeypatch.setattr(RunStores, "warned", set())
    broken = tmp_path / "broken"
    (broken / "hoglint").mkdir(parents=True)
    if fault == "unwritable":
        (broken / "hoglint").chmod(0o500)
    else:
        monkeypatch.setattr(store_module, "write_atomic", lambda *_: (_ for _ in ()).throw(OSError(28, "No space")))
    try:
        assert check(broken) == healthy
    finally:
        (broken / "hoglint").chmod(0o700)
    printed = capfd.readouterr()
    assert printed.out == expected
    assert printed.err.count("cannot use the cache") == 1, printed.err


ENGINE_LIST = """# a test engine
[facts]
src/**/*.rs
!src/link/**
Cargo.toml
Cargo.lock

[native]
src/link/**
binding/**

[engine]
protocol 1
hoglint/**/*.py
"""
ENGINE_FILES = {
    "engine-sources.txt": ENGINE_LIST,
    "Cargo.toml": "[package]",
    "Cargo.lock": "v1",
    "src/lib.rs": "lib",
    "src/link/mod.rs": "link",
    "binding/Cargo.toml": "[binding]",
    "binding/src/lib.rs": "bind",
    "hoglint/cli.py": "cli",
    "tests/t.py": "not the engine",
    "src/notes.md": "not the engine",
    "target/debug/gen.rs": "build output",
    ".hidden/h.rs": "hidden",
}
# Each id's input spelled out from the engine-sources.txt header, so build.rs can check the same vectors.
FACTS_INPUT = b"Cargo.lock\0v1\0Cargo.toml\0[package]\0src/lib.rs\0lib\0"
NATIVE_INPUT = (
    b"Cargo.lock\0v1\0Cargo.toml\0[package]\0binding/Cargo.toml\0[binding]\0binding/src/lib.rs\0bind\0"
    b"src/lib.rs\0lib\0src/link/mod.rs\0link\0"
)
ENGINE_INPUT = (
    b"protocol 1\0"
    b"Cargo.lock\0v1\0Cargo.toml\0[package]\0binding/Cargo.toml\0[binding]\0binding/src/lib.rs\0bind\0"
    b"hoglint/cli.py\0cli\0src/lib.rs\0lib\0src/link/mod.rs\0link\0"
)


@pytest.mark.parametrize("section, spelled", [(FACTS, FACTS_INPUT), (NATIVE, NATIVE_INPUT), (ENGINE, ENGINE_INPUT)])
def test_each_id_is_the_one_engine_sources_txt_defines(tmp_path: Path, section: str, spelled: bytes) -> None:
    write_files(tmp_path, ENGINE_FILES)
    sources = EngineSources.read(tmp_path)
    assert sources is not None
    assert sources.digest(section) == hashlib.sha256(spelled).digest()[:8].hex()


@pytest.mark.parametrize(
    "edited, changed",
    [
        # a Python edit never asks for a rebuild and never re-extracts
        ("hoglint/cli.py", {ENGINE}),
        ("src/link/mod.rs", {NATIVE, ENGINE}),
        ("binding/src/lib.rs", {NATIVE, ENGINE}),
        ("src/lib.rs", {FACTS, NATIVE, ENGINE}),
    ],
)
def test_an_edit_changes_only_the_ids_whose_sources_it_touches(tmp_path: Path, edited: str, changed: set[str]) -> None:
    write_files(tmp_path, ENGINE_FILES)
    before = EngineSources.read(tmp_path)
    assert before is not None
    ids = {section: before.digest(section) for section in SECTIONS}
    (tmp_path / edited).write_text("edited")
    assert {section for section in SECTIONS if before.digest(section) != ids[section]} == changed


CRATE = Path(__file__).resolve().parents[2]


def test_the_engine_list_names_the_protocol_this_engine_reads() -> None:
    sources = EngineSources.read(CRATE)
    assert sources is not None and sources.protocol == str(PROTOCOL)


# Not [engine]: its sources include every Python file, so the id a binary embeds at build time is
# stale after any Python edit. The vectors in test_each_id_is_the_one_engine_sources_txt_defines,
# which src/sources.rs repeats, pin the [engine] algorithm in both languages.
@pytest.mark.parametrize("section", (FACTS, NATIVE))
def test_the_extractor_reports_the_ids_of_its_sources(section: str) -> None:
    sources = EngineSources.read(CRATE)
    assert sources is not None
    if not has_extractor() and not native_required():
        pytest.skip("no extractor binary is built")
    reported = BinaryIds.of(find_binary(), section)
    if reported != sources.digest(section) and not native_required():
        pytest.skip(f"the extractor binary was built from other [{section}] sources: rebuild it")
    assert reported == sources.digest(section)


USE_SHAPE = """
schema = 1
extends = ["hoglint:django"]

[fields]
package = { type = "id", required_on = ["products/**"] }

[[assign]]
match = "products/{product}/**"
set = { package = "{product}" }

[rules.model-crossings]
primitive = "use-shape"
kinds = ["reference"]
select = "src.package != dst.package"
allow_shapes = ["annotation"]
flow = "def-use"
identity = "module-symbol"
counted = true
growth_key = ["dst.symbol", "edge.label"]
"""


def test_couplings_carry_the_shape_the_rule_judges(tmp_path: Path) -> None:
    widget = "products.alpha.models.Widget"
    graph = (
        GraphBuilder()
        .module("products.alpha.models", "products.beta.old_name", "products.beta.other")
        .symbol(widget)
        .edge("reference", "products.beta.old_name", widget, shape="annotation")
        .edge(
            "reference",
            "products.beta.old_name",
            widget,
            shape="instance-single",
            attrs=(("flow_shape", "write(save)"),),
        )
        .edge("reference", "products.beta.other", widget, shape="annotation")
        .graph
    )
    graph = native_graph(graph)
    result = Engine(config_from(tmp_path, USE_SHAPE), graph, tmp_path).evaluate()
    couplings = CouplingScan(result, graph, frozenset({"products.beta.old_name"}))
    assert couplings.counts() == {
        ("model-crossings", "products.beta.old_name", widget, "reference", ("annotation",)): 1,
        ("model-crossings", "products.beta.old_name", widget, "reference", ("write(save)",)): 1,
    }
    assert list(couplings.witnesses().values()) == [
        [("model-crossings", "products.beta.old_name", widget, "reference", ("write(save)",))]
    ]


DYNAMIC_CONFIG = (
    CONFIG
    + """
[rules.dynamic-imports]
primitive = "edge"
kinds = ["dynamic-import"]
deny = "false"
on_unresolved = "incomplete"
ledger = false
"""
)
LOADER = "import importlib\n\n\ndef load(name):\n    return importlib.import_module(name)\n"
SECOND_LOADER = LOADER.replace("return", "importlib.import_module(name)\n    return")


@needs_extractor
def test_staged_check_blocks_only_what_it_introduced_against_head_and_chains_its_summary(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    write_files(repo, {"hoglint.toml": DYNAMIC_CONFIG, "core/__init__.py": "", "core/loader.py": LOADER})
    git_in(repo, "init", "-q")
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-qm", "one computed import")

    inherited = hoglint(repo, "check", "--index", "--all")
    assert inherited.returncode == 0, inherited.stdout + inherited.stderr
    assert "this run): 0 introduced, 0 ambiguous, 1 inherited" in inherited.stdout

    (repo / "core/loader.py").write_text(SECOND_LOADER)
    git_in(repo, "add", "-A")
    introduced = hoglint(repo, "check", "--index", "--all")
    assert introduced.returncode == 2, introduced.stdout + introduced.stderr
    assert "stored summary): 1 introduced, 0 ambiguous, 1 inherited" in introduced.stdout

    git_in(repo, "commit", "-qm", "a second computed import")
    (repo / "core/__init__.py").write_text("X = 1\n")
    git_in(repo, "add", "-A")
    chained = hoglint(repo, "check", "--index", "--all")
    assert chained.returncode == 0, chained.stdout + chained.stderr
    assert "stored summary): 0 introduced, 0 ambiguous, 2 inherited" in chained.stdout


# A literal path and a symbol the config writes out in full: a change that makes one name nothing
# leaves a config:stale-name item with no file behind it. Moving the file also loses the symbol.
NAMED_CONFIG = (
    CONFIG
    + """
[fields.seed]
type = "bool"

[fields.gate]
type = "bool"
on = "symbol"

[[assign]]
match = "core/seed.py"
set = { seed = true }

[[assign]]
symbols = { names = ["core.seed.Gate"] }
set = { gate = true }
"""
)
SEEDED = {"hoglint.toml": NAMED_CONFIG, "core/__init__.py": "", "core/seed.py": "class Gate:\n    pass\n"}
# (the child's edits, None deleting a file; the stored summary's count of new items)
STALE_CHANGES = {
    "the named file is deleted": ({"core/seed.py": None}, "2 introduced"),
    "the named file is moved": ({"core/seed.py": None, "core/moved.py": SEEDED["core/seed.py"]}, "2 introduced"),
    "the named symbol is renamed": ({"core/seed.py": "class Door:\n    pass\n"}, "1 introduced"),
}


def make_stale(repo: Path, change: str) -> None:
    edits, _ = STALE_CHANGES[change]
    for path, text in edits.items():
        if text is None:
            (repo / path).unlink()
        else:
            (repo / path).write_text(text)
    git_in(repo, "add", "-A")


@needs_extractor
@pytest.mark.parametrize("change", sorted(STALE_CHANGES))
def test_a_commit_that_makes_a_config_name_stale_is_blocked_as_introduced(tmp_path: Path, change: str) -> None:
    repo = tmp_path / "repo"
    write_files(repo, SEEDED)
    git_in(repo, "init", "-q")
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-qm", "the named file and symbol exist")
    clean = hoglint(repo, "check", "--index")
    assert clean.returncode == 0, clean.stdout + clean.stderr

    make_stale(repo, change)
    stale = hoglint(repo, "check", "--index", "--all")
    assert stale.returncode == 2, stale.stdout + stale.stderr
    assert f"stored summary): {STALE_CHANGES[change][1]}, 0 ambiguous\n" in stale.stdout
    assert "config:stale-name [introduced, stale-name]" in stale.stdout


def stored_incomplete_counts(cache: Path) -> dict[str, int]:
    """Inputs digest -> incomplete items of every stored summary."""
    store = SummaryStore(cache)
    counts = {}
    for manifest in sorted((cache / "summaries").rglob("*.json")):
        envelope = Envelope.from_json(json.loads(manifest.read_text())["sections"]["envelope"]["envelope"])
        summary = store.get(envelope, frozenset({"envelope", "incompletes"}))
        assert summary is not None, manifest
        counts[envelope.inputs] = len(summary.incomplete)
    return counts


@needs_extractor
def test_a_staged_check_reads_only_the_tree_it_captured_from_the_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    write_files(repo, {"hoglint.toml": DYNAMIC_CONFIG, "core/__init__.py": "", "core/loader.py": LOADER})
    git_in(repo, "init", "-q")
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-qm", "one computed import")
    (repo / "core/loader.py").write_text(SECOND_LOADER)
    git_in(repo, "add", "-A")
    policy_of = Snapshots.policy_of
    captured = []

    def stage_a_third_after_the_capture(self: Snapshots, tree: str) -> Path:
        captured.append(tree)
        (repo / "core/loader.py").write_text(
            SECOND_LOADER.replace("return", "importlib.import_module(name)\n    return")
        )
        git_in(repo, "add", "-A")
        return policy_of(self, tree)

    monkeypatch.setattr(Snapshots, "policy_of", stage_a_third_after_the_capture)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    (tmp_path / "work").mkdir()
    commands = Commands(repo, tmp_path / "work")
    try:
        assert commands.check(True, False, False, view=View("hoglint check --index", all=True)) == 2
    finally:
        commands.snapshots.close()
    assert "computed and stored): 1 introduced, 0 ambiguous, 1 inherited" in capfd.readouterr().out
    patterns = RunInputs(config_from(tmp_path / "policy", DYNAMIC_CONFIG)).patterns
    staged = InputDigests.compute(TreeListing.of(repo, captured[0]), patterns)
    counts = stored_incomplete_counts(tmp_path / "cache" / "hoglint")
    assert sorted(counts.values()) == [1, 2] and counts[staged] == 2


OWNED_CONFIG = (
    CONFIG.replace("[fields]", '[fields]\nowners = { type = "set" }')
    + """
[roles]
owner = "owners"

[providers.codeowners]
use = "codeowners"
file = "CODEOWNERS"
sets = ["owners"]
"""
)


@needs_extractor
def test_query_reads_owners_from_the_owners_files_not_from_the_summary(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    write_files(repo, {**FILES, "hoglint.toml": OWNED_CONFIG, "CODEOWNERS": "*.py @team-a\n"})
    git_in(repo, "init", "-q")
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-qm", "base")
    assert "@team-a  " in hoglint(repo, "query", "--by", "owners").stdout
    (repo / "CODEOWNERS").write_text("*.py @team-b\n")
    owned = hoglint(repo, "query", "--by", "owners").stdout
    assert "@team-b  " in owned and "@team-a" not in owned


PACKAGED_CONFIG = (
    CONFIG.replace('first_party = ["products", "core"]', 'first_party = ["products", "core", "scripts"]').replace(
        'growth_key = ["dst.symbol"]', 'growth_key = ["dst.symbol", "src.package"]'
    )
    + """
[[assign]]
match = "products/alerts/digest.py"
set = { package = "reports", tier = "product" }
override = true

[rules.billing-logic-uses]
primitive = "entrypoint"
kinds = ["import"]
select = "dst.package == 'billing'"
identity = "module-pair"
report = "advisory"
ledger = false
"""
)


@needs_extractor
def test_query_groups_by_the_package_label_of_the_source_module(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    use = "from products.billing.logic import charge\n"
    write_files(repo, {**FILES, "hoglint.toml": PACKAGED_CONFIG, "products/alerts/digest.py": use})
    git_in(repo, "init", "-q")
    assert hoglint(repo, "check", "--write-ledger", "--record-growth").returncode == 0
    write_files(repo, {"scripts/__init__.py": "", "scripts/seed.py": use})
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-qm", "base")
    # digest.py sits in alerts, and the override moves it to reports; scripts has no package.
    # billing's own uses come from no ledgered line, so only the finding labels their modules.
    # billing-logic-uses never blocks, so its rows are advisory; `new` holds only rows that block.
    groups = [
        {"key": "(no package)", "counts": {"advisory": {"billing-logic-uses": 1}, "new": {"public-surface": 1}}},
        {"key": "alerts", "counts": {"accepted": {"public-surface": 1}, "advisory": {"billing-logic-uses": 1}}},
        {"key": "billing", "counts": {"advisory": {"billing-logic-uses": 2}}},
        {"key": "reports", "counts": {"accepted": {"public-surface": 1}, "advisory": {"billing-logic-uses": 1}}},
    ]
    found = hoglint(repo, "query", "--by", "package", "--json").stdout
    assert found.endswith("}\n") and found.count("\n") == 1
    assert json.loads(found) == {"by": "package", "groups": groups}
    text = hoglint(repo, "query", "--by", "package").stdout
    assert "reports  2  (accepted:public-surface 1, advisory:billing-logic-uses 1)\n" in text


@needs_extractor
def test_a_file_no_rule_reads_never_keeps_a_staged_summary_from_the_next_commit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    write_files(repo, FILES)
    git_in(repo, "init", "-q")
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-qm", "base")
    (repo / "scratch-note.md").write_text("todo\n")
    (repo / "core/api.py").write_text("from products.billing.facade import charge\n\nX = 1\n")
    git_in(repo, "add", "core/api.py")
    hoglint(repo, "check", "--index")
    git_in(repo, "commit", "-qm", "one")
    (repo / "core/__init__.py").write_text("Y = 2\n")
    git_in(repo, "add", "core/__init__.py")
    assert "(tree " in (after := hoglint(repo, "check", "--index", "--all")).stdout
    assert "stored summary)" in after.stdout, after.stdout + after.stderr


@needs_extractor
def test_a_commit_that_changes_no_input_keeps_the_summary_key(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    write_files(repo, FILES)
    git_in(repo, "init", "-q")
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-qm", "base")
    keys = [hoglint(repo, "summary", "key").stdout]
    for path, text in (("README.md", "docs\n"), ("core/api.py", "from products.billing.facade import charge\nX = 1\n")):
        (repo / path).write_text(text)
        git_in(repo, "add", "-A")
        git_in(repo, "commit", "-qm", path)
        keys.append(hoglint(repo, "summary", "key").stdout)
    assert keys[0].startswith(f"hoglint-summary-p{PROTOCOL}-")
    assert keys[0] == keys[1] != keys[2]


@pytest.mark.parametrize(
    "stored",
    [b"\x86\x00not text", b"z" * 40, b"", b"0123456789abcdef0123"],
    ids=["not-utf8", "not-hex", "empty", "truncated"],
)
def test_a_stored_inputs_digest_that_is_not_one_is_computed_again_and_replaced(tmp_path: Path, stored: bytes) -> None:
    repo = tmp_path / "repo"
    write_files(repo, FILES)
    git_in(repo, "init", "-q")
    git_in(repo, "add", "-A")
    tree = git_in(repo, "write-tree").strip()
    patterns = InputPatterns(SOURCE_INPUTS)
    digest = InputDigests(tmp_path / "cache").of(repo, tree, patterns)
    (stored_file,) = (tmp_path / "cache" / "inputs").iterdir()
    stored_file.write_bytes(stored)
    assert InputDigests(tmp_path / "cache").of(repo, tree, patterns) == digest
    assert stored_file.read_text() == digest


COVERED = (
    CONFIG
    + """
[rules.wiring-watched]
primitive = "coverage"
covers = ["edge.kind == 'import' and src.package != dst.package: dst"]
inputs = { adapter = "turbo", task = "contract-check" }
identity = "entity"
growth_key = ["subject"]

[rules.readme]
primitive = "layout"
within = "products/{product}"
files = ["README.md"]
identity = "entity"
growth_key = ["subject"]
"""
)
WATCHED_PACKAGE = {
    "products/billing/package.json": '{"name": "billing", "scripts": {"contract-check": "true"}}',
    "products/billing/README.md": "billing\n",
}


def turbo_inputs(inputs: str) -> str:
    return json.dumps({"tasks": {"contract-check": {"inputs": [inputs]}}})


@needs_extractor
def test_a_staged_check_reads_side_inputs_from_the_index_not_the_working_tree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    write_files(repo, {**FILES, **WATCHED_PACKAGE, "hoglint.toml": COVERED})
    (repo / "products/billing/turbo.json").write_text(turbo_inputs("**"))
    git_in(repo, "init", "-q")
    git_in(repo, "add", "-A")
    (repo / "products/billing/turbo.json").write_text(turbo_inputs("facade.py"))
    (repo / "products/billing/README.md").unlink()
    differs = ("does not watch products/billing/logic.py", "products/billing lacks README.md")
    staged, working = hoglint(repo, "check", "--index", "--all").stdout, hoglint(repo, "check", "--all").stdout
    assert [line in staged for line in differs] == [False, False], staged
    assert [line in working for line in differs] == [True, True], working


@pytest.mark.parametrize(
    "path, held",
    [
        ("products/billing/README.md", True),  # a layout rule's file
        ("products/alerts/README.md", False),
        ("products/billing/package.json", True),  # the turbo adapter's input
        ("products/billing/logic.py", True),  # a source
        ("products/billing/notes.txt", None),  # nothing declares it, so no key covers the answer
    ],
)
def test_a_run_asks_whether_a_path_exists_only_for_a_path_its_key_covers(
    tmp_path: Path, path: str, held: bool | None
) -> None:
    repo = tmp_path / "repo"
    write_files(repo, {**FILES, **WATCHED_PACKAGE, "products/billing/notes.txt": "x\n"})
    git_in(repo, "init", "-q")
    git_in(repo, "add", "-A")
    tree = git_in(repo, "write-tree").strip()
    files = TreeFiles(
        TreeListing.of(repo, tree), tmp_path, RunInputs(config_from(tmp_path / "policy", COVERED)).patterns
    )
    if held is None:
        with pytest.raises(EngineError, match="no source or declared input pattern covers it"):
            files.holds(path)
        return
    assert files.holds(path) is held


GONE = (
    CONFIG
    + """
[rules.legacy-gone]
primitive = "layout"
within = "products/{product}"
absent = ["legacy"]
identity = "entity"
growth_key = ["subject"]
"""
)


def stored_manifests(cache: Path) -> list[str]:
    return sorted(str(path.relative_to(cache)) for path in cache.rglob("summaries/**/*.json"))


@needs_extractor
def test_the_working_tree_is_what_git_status_shows_and_only_a_tree_run_is_stored(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    write_files(
        repo, {**FILES, "hoglint.toml": GONE, ".gitignore": "__pycache__/\n", "products/billing/legacy/a.py": ""}
    )
    git_in(repo, "init", "-q")
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-qm", "base")
    git_in(repo, "rm", "-q", "products/billing/legacy/a.py")
    git_in(repo, "commit", "-qm", "drop legacy")
    # On disk but not in git's view: an ignored directory, an empty one, a skip-worktree edit.
    write_files(
        repo, {"products/billing/legacy/__pycache__/a.pyc": "", "core/api.py": "import products.billing.logic\n"}
    )
    (repo / "products/alerts/legacy").mkdir()
    git_in(repo, "update-index", "--skip-worktree", "core/api.py")
    cache = tmp_path / "repo-cache" / "hoglint"
    reports = []
    for args in (("check",), ("check", "--index")):
        shutil.rmtree(cache, ignore_errors=True)
        done = hoglint(repo, *args)
        printed = done.stdout.replace("this run", "HEAD").replace(" --index", "")
        reports.append((done.returncode, printed, stored_manifests(cache)))
    shutil.rmtree(cache, ignore_errors=True)
    hoglint(repo, "summary", "export", "--file", str(tmp_path / "summary"))
    assert reports[0][2] == reports[1][2], reports
    assert stored_manifests(cache) == reports[0][2] and len(reports[0][2]) == 1
    assert reports[0][0] == 1 and "blocked: 1 new finding." in reports[0][1] and "core.api" not in reports[0][1]
    # The index holds HEAD's tree, so the commit adds nothing: HEAD's finding is named, not blocked on.
    assert reports[1][0] == 0 and "Already at HEAD and not in step with the ledger: 1 finding." in reports[1][1]
    (repo / "products/alerts/new.py").write_text("X = 1\n")
    dirty = hoglint(repo, "check")
    assert "blocked: 1 new finding." in dirty.stdout and "core.api" not in dirty.stdout, dirty.stdout
    # A failed check says to fix or to stop and tell the person, never to rewrite the ledger.
    assert "Fix each finding, or stop and tell the person." in dirty.stdout and "write-ledger" not in dirty.stdout
    assert stored_manifests(cache) == reports[0][2]


LINKS = {
    "core/impl.py": "from products.billing.logic import charge\n",
    "core/link.py": "alias.py",
    "core/alias.py": "impl.py",
    "core/gone.py": "missing.py",
    "core/out.py": "../../outside.py",
    ".github/CODEOWNERS": "../CODEOWNERS",
}


@needs_extractor
def test_a_symlink_reads_as_its_target_inside_the_tree_and_as_unreadable_outside(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    config = OWNED_CONFIG.replace('file = "CODEOWNERS"', 'file = ".github/CODEOWNERS"')
    write_files(repo, {**FILES, "hoglint.toml": config, "CODEOWNERS": "*.py @team-a\n"})
    (tmp_path / "outside.py").write_text("from products.billing.logic import charge\n")
    (repo / ".github").mkdir()
    (repo / ".github/CODEOWNERS").symlink_to(LINKS[".github/CODEOWNERS"])
    git_in(repo, "init", "-q")
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-qm", "owners")
    # The code and its links are staged on top, so the hook judges their findings as the commit's.
    for path, target in LINKS.items():
        if path == "core/impl.py":
            (repo / path).write_text(target)
        elif path.startswith("core/"):
            (repo / path).parent.mkdir(parents=True, exist_ok=True)
            (repo / path).symlink_to(target)
    git_in(repo, "add", "-A")
    outputs = [hoglint(repo, *args, "--all").stdout for args in (("check",), ("check", "--index"))]
    # An untracked file makes `check` read the working tree, which follows the same links.
    (repo / "core/notes.py").write_text("")
    outputs.append(hoglint(repo, "check", "--all").stdout)
    for printed in outputs:
        assert "core.link names products.billing.logic.charge, which is not exported to it" in printed, printed
        assert "core/impl.py:1  [@team-a]" in printed
        assert (
            "source:unreadable: each of these 2 places has no facts, because hoglint cannot read the file.\n" in printed
        )
        for path in ("core/gone.py", "core/out.py"):
            assert f"  at {path}: {path} could not be read, so its facts are missing\n" in printed


SITE = {
    "django/__init__.py": "VERSION = 1",
    "django-6.1.3.dist-info/RECORD": "django/__init__.py,sha256=abc,11\n",
    "rest_framework/__init__.py": "",
    "djangorestframework-3.18.3.dist-info/top_level.txt": "rest_framework\n",
    "djangorestframework-3.18.3.dist-info/RECORD": "rest_framework/__init__.py,sha256=def,0\n",
}


@pytest.mark.parametrize(
    "edited, content, changes",
    [
        # an install or upgrade rewrites RECORD
        ("django-6.1.3.dist-info/RECORD", "django/__init__.py,sha256=xyz,11\n", True),
        ("djangorestframework-3.18.3.dist-info/RECORD", "", True),
        # sources are never read: only the metadata stands for them
        ("django/__init__.py", "VERSION = 2", False),
        # rows that depend on the install: a script's shebang names the venv, the installer adds its own
        (
            "django-6.1.3.dist-info/RECORD",
            "django/__init__.py,sha256=abc,11\n../../../bin/django-admin,sha256=venv,350\n"
            "django-6.1.3.dist-info/REQUESTED,sha256=e,0\ndjango/__pycache__/__init__.cpython-314.pyc,,\n",
            False,
        ),
    ],
)
def test_the_framework_digest_comes_from_distribution_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, edited: str, content: str, changes: bool
) -> None:
    write_files(tmp_path, SITE)
    monkeypatch.setattr(Framework, "site", staticmethod(lambda: tmp_path))
    before = Framework.digest(("django", "rest_framework"))
    (tmp_path / edited).write_text(content)
    assert (Framework.digest(("django", "rest_framework")) != before) is changes


def record_row(path: str, text: str) -> str:
    data = text.encode()
    return (
        f"{path},sha256={base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()},{len(data)}\n"
    )


def test_an_editable_install_and_a_wheel_of_the_same_code_have_one_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code = {"plugin_code/__init__.py": "X = 1\n", "plugin_code/rules/check.py": "def check():\n    pass\n"}
    rows = "".join(record_row(path, text) for path, text in code.items())
    write_files(tmp_path / "wheel", {**code, "plugin_code-1.0.dist-info/RECORD": rows})
    # an editable RECORD lists only the path hook; the code is wherever the import finds it
    write_files(
        tmp_path / "editable", {"plugin_code-1.0.dist-info/RECORD": "__editable__.plugin_code.pth,sha256=a,9\n"}
    )
    write_files(tmp_path / "checkout", code)
    monkeypatch.syspath_prepend(str(tmp_path / "checkout"))
    digests = []
    for site in ("wheel", "editable"):
        monkeypatch.setattr(Framework, "site", staticmethod(lambda site=site: tmp_path / site))
        digests.append(Framework.digest(("plugin_code",)))
    (tmp_path / "checkout/plugin_code/rules/check.py").write_text("def check():\n    return 1\n")
    assert digests[0] == digests[1] != Framework.digest(("plugin_code",))


PROVIDER_CONFIG = """
schema = 1
extends = ["hoglint:posthog"]

[discovery]
external = ["django"]

[fields]
owners = { type = "set" }

[providers.owners]
use = "owners-yaml"
sets = ["owners"]
"""


def test_a_provider_declares_the_distributions_it_imports_and_fails_without_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_config(tmp_path, write_config(tmp_path, PROVIDER_CONFIG))
    assert Framework.packages_of(config) == ("django", "owners_yaml")
    monkeypatch.setattr(Framework, "site", staticmethod(lambda: tmp_path))
    failures = ProviderRunner(config.providers.values(), tmp_path).failures
    assert failures == {"owners": "ModuleNotFoundError: no installed distribution ships owners_yaml"}


@pytest.mark.parametrize("files, fails", [(None, True), (["owners.yaml", "team.yaml"], False)])
def test_the_owners_provider_reads_only_the_ownership_files_it_declares(
    tmp_path: Path, files: list[str] | None, fails: bool
) -> None:
    write_files(tmp_path, {"owners.yaml": "version: 1\nowners: [team-a]\nalias_files: [team.yaml]\n"})
    options = {} if files is None else {"files": files}
    assert OWNERS_YAML.inputs(options) == tuple(f"**/{name}" for name in files or ("owners.yaml", "product.yaml"))
    if fails:
        with pytest.raises(ValueError, match="names ownership files team.yaml"):
            OWNERS_YAML.open(options, tmp_path)
        return
    assert OWNERS_YAML.values(OWNERS_YAML.open(options, tmp_path), "app/a.py")["owners"].value == {"team-a"}


ENTRY = "N... 100644 100644 100644 1111111 2222222"
INPUTS = InputPatterns((*SOURCE_INPUTS, "**/turbo.json", "hoglint.ledger"))


@pytest.mark.parametrize(
    "records, drift",
    [
        # no rule reads it
        (["? notes/todo.md", f"1 .M {ENTRY} conf/settings.toml"], []),
        ([f"1 .M {ENTRY} products/turbo.json"], ["products/turbo.json"]),
        # staged only: still not HEAD's
        ([f"1 M. {ENTRY} hoglint.ledger"], ["hoglint.ledger"]),
        ([f"2 R. {ENTRY} R100 conf/new.py", "conf/old.py"], ["conf/new.py", "conf/old.py"]),
        ([f"1 .M {ENTRY} app/a.py", "? app/b.pyi"], ["app/a.py", "app/b.pyi"]),
        # a directory name may hold a newline, and its sources are still inputs
        (["? notes\nold/a.py"], ["notes\nold/a.py"]),
    ],
)
def test_a_working_tree_holds_heads_inputs_unless_a_declared_input_differs(
    tmp_path: Path, records: list[str], drift: list[str]
) -> None:
    assert WorktreeStatus(tmp_path, records).drift(INPUTS.matches) == drift


LINES = "a = 1\nb = 2\nc = 3\nd = 4\n"


@pytest.mark.parametrize(
    "steps, paths",
    [
        # old file deleted, new one known by intent-to-add: status says `.R`, the index holds the old path
        ([("mv", "a.py", "b.py"), ("git", "add", "-N", "b.py")], ["b.py"]),
        ([("git", "mv", "a.py", "b.py")], ["b.py"]),
        # a copy (`.C`) keeps the path it was copied from
        ([("edit", "a.py"), ("cp", "a.py", "c.py"), ("git", "add", "-N", "c.py")], ["a.py", "c.py"]),
    ],
    ids=["rename in the working tree", "rename in the index", "copy in the working tree"],
)
def test_the_working_tree_listing_follows_what_a_rename_or_copy_record_says(
    tmp_path: Path, steps: list[tuple[str, ...]], paths: list[str]
) -> None:
    write_files(tmp_path, {"a.py": LINES})
    git_in(tmp_path, "init", "-q")
    git_in(tmp_path, "config", "status.renames", "copies")
    git_in(tmp_path, "add", "-A")
    git_in(tmp_path, "commit", "-qm", "base")
    for step in steps:
        if step[0] == "git":
            git_in(tmp_path, *step[1:])
        elif step[0] == "mv":
            (tmp_path / step[1]).rename(tmp_path / step[2])
        elif step[0] == "cp":
            shutil.copyfile(tmp_path / step[1], tmp_path / step[2])
        else:
            (tmp_path / step[1]).write_text(LINES + "e = 5\n")
    assert sorted(WorktreeListing.of(tmp_path).paths) == paths


def test_a_blob_git_does_not_have_is_an_engine_error_that_names_the_path(tmp_path: Path) -> None:
    write_files(tmp_path, {"a.py": LINES})
    git_in(tmp_path, "init", "-q")
    present = git_in(tmp_path, "hash-object", "-w", "a.py").strip()
    lost = "1" * 40  # a partial clone or a pruned object
    with pytest.raises(EngineError, match=f"git has no object {lost} for b.py"):
        read_blobs(tmp_path, {"a.py": present, "b.py": lost})
