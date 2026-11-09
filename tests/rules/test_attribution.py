from __future__ import annotations

import os
import gzip
import json
import hashlib
import datetime
from pathlib import Path

import pytest

from hoglint.facts import Extractor
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
    PolicyFiles,
    policy_digest,
)
from hoglint.ledger.obligations import Attribution, attribute, rows
from hoglint.ledger.records import IncompleteRecord
from hoglint.ledger.scan import ScanSummary
from hoglint.ledger.store import CacheRoot, SummaryStore
from hoglint.model import IncompleteCause
from hoglint.rules.engine import Engine
from hoglint.rules.exceptions import ExceptionsFile

from rules.support import GraphBuilder, config_from
from rules.test_compare import CONFIG, FILES, git_in, has_extractor, hoglint, write_files

DYNAMIC = ("dynamic-imports", "app.loader|dynamic-import|", "dynamic-import '' did not resolve")
OTHER_REASON = ("dynamic-imports", "app.loader|dynamic-import|", "selection reads an unknown value")
INTRODUCED, AMBIGUOUS, INHERITED = Attribution.INTRODUCED, Attribution.AMBIGUOUS, Attribution.INHERITED


def records(obligations: dict[tuple[str, str, str], list[str]]) -> list[IncompleteRecord]:
    return [
        IncompleteRecord(rule, key, reason, IncompleteCause.INHERITED, (), mark)
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
        path.write_bytes(gzip.compress(content.encode()))
    found = store.get(asked)
    assert (found is not None and found.covers(scope) and found.obligations() == {DYNAMIC: ["f1"]}) is hit


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
    result = Engine(anchored, graph, code_root, today=datetime.date(2026, 11, 9)).evaluate(None, exceptions)
    summary = ScanSummary.from_run(result, graph, Envelope(), roots=(policy_root, code_root), exceptions=exceptions)
    assert str(tmp_path) not in json.dumps(summary.to_json())
    assert [f.message for f in summary.findings] == ["app.a app.b has no classification in policy/flags.toml"]
    assert summary.exceptions.expired(datetime.date(2025, 12, 31)) == []
    assert summary.exceptions.expired(datetime.date(2026, 1, 2)) == [("2026-01-01", "exception #0 (flags)")]


DAY = 86_400.0
NOW = 100 * DAY


@pytest.mark.parametrize(
    "files, kept",
    [
        # a file older than the age bound goes, a fresh one stays
        ({"facts/v3-old/a.json": (31, 10), "summaries/e1/t1/p-f.json": (1, 10)}, ["summaries/e1/t1/p-f.json"]),
        # over the size bound the oldest go first
        ({"a": (3, 60), "b": (2, 60), "c": (1, 60)}, ["b", "c"]),
    ],
)
def test_the_cache_root_stays_within_its_age_and_size(tmp_path: Path, files: dict, kept: list[str]) -> None:
    for name, (age_days, size) in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
        os.utime(path, (NOW - age_days * DAY, NOW - age_days * DAY))
    CacheRoot(tmp_path, max_bytes=150).prune(now=NOW)
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()) == kept


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


def built_before_its_sources(section: str) -> bool:
    """Whether the extractor binary is older than a source of the section, so its id cannot match yet."""
    sources = EngineSources.read(CRATE)
    if sources is None or not has_extractor() or not Extractor.find_binary().is_file():
        return True
    built = Extractor.find_binary().stat().st_mtime
    return any((CRATE / rel).stat().st_mtime > built for rel in sources.files(section))


@pytest.mark.parametrize("section", SECTIONS)
def test_the_extractor_reports_the_ids_of_its_sources(section: str) -> None:
    if built_before_its_sources(section):
        pytest.skip(f"no extractor binary built after the last [{section}] source edit")
    try:
        reported = BinaryIds.of(Extractor.find_binary(), section)
    except EngineError:
        pytest.skip(f"the extractor binary does not report its [{section}] id yet")
    sources = EngineSources.read(CRATE)
    assert sources is not None and reported == sources.digest(section)


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


@pytest.mark.skipif(not has_extractor(), reason="needs the hoglint-facts binary")
def test_staged_check_blocks_only_what_it_introduced_against_head_and_chains_its_summary(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    write_files(repo, {"hoglint.toml": DYNAMIC_CONFIG, "core/__init__.py": "", "core/loader.py": LOADER})
    git_in(repo, "init", "-q")
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-qm", "one computed import")

    inherited = hoglint(repo, "check", "--index")
    assert inherited.returncode == 0, inherited.stdout + inherited.stderr
    assert "this run): 0 introduced, 0 ambiguous, 1 inherited" in inherited.stdout

    (repo / "core/loader.py").write_text(SECOND_LOADER)
    git_in(repo, "add", "-A")
    introduced = hoglint(repo, "check", "--index")
    assert introduced.returncode == 2, introduced.stdout + introduced.stderr
    assert "stored summary): 1 introduced, 0 ambiguous, 1 inherited" in introduced.stdout

    git_in(repo, "commit", "-qm", "a second computed import")
    (repo / "core/__init__.py").write_text("X = 1\n")
    git_in(repo, "add", "-A")
    chained = hoglint(repo, "check", "--index")
    assert chained.returncode == 0, chained.stdout + chained.stderr
    assert "stored summary): 0 introduced, 0 ambiguous, 2 inherited" in chained.stdout


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


@pytest.mark.skipif(not has_extractor(), reason="needs the hoglint-facts binary")
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


@pytest.mark.skipif(not has_extractor(), reason="needs the hoglint-facts binary")
def test_a_side_input_the_tree_does_not_hold_keeps_the_summary_out_of_the_cache(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    write_files(repo, {**FILES, "turbo.json": '{"tasks": {}}\n'})
    git_in(repo, "init", "-q")
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-qm", "base")
    (repo / "turbo.json").write_text('{"tasks": {"contract-check": {}}}\n')
    staged = hoglint(repo, "check", "--index")
    assert "not stored: turbo.json differs from the tree" in staged.stderr
    assert not list((tmp_path / "repo-cache" / "hoglint" / "summaries").rglob("*.json.gz"))
