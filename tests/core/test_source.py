from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest

from hoglint.facts import FACT_KINDS, FactNeeds
from hoglint.plugins import PackReader, PackSink, ProducerFailure
from hoglint.source import GraphSource, run_packs
from rules.test_compare import git_in, write_files

DECLARED = FactNeeds(kinds=frozenset({"imports"}), watch_callees=frozenset({"connect"}))


class UndeclaredPack:
    name = "undeclared"
    allowed_keys: frozenset[str] = frozenset()
    edge_kinds: frozenset[str] = frozenset()

    def produce(self, section: Mapping[str, Any], reader: PackReader, sink: PackSink, root: Path) -> None:
        return None


class DeclaredPack(UndeclaredPack):
    name = "declared"

    def fact_needs(self, section: Mapping[str, Any]) -> FactNeeds:
        return DECLARED


@pytest.mark.parametrize(
    "packs, all_kinds",
    [
        ([], False),
        ([DeclaredPack()], False),
        ([DeclaredPack(), UndeclaredPack()], True),
    ],
)
def test_fact_needs_count_a_pack_without_a_declaration_as_needing_every_kind(packs: list, all_kinds: bool) -> None:
    needs = GraphSource.fact_needs([(pack, {}) for pack in packs])

    if not packs:
        assert needs == FactNeeds()
    assert (needs.kinds >= FACT_KINDS) is all_kinds
    assert (DECLARED.watch_callees <= needs.watch_callees) is bool(packs)


class ReadingPack(UndeclaredPack):
    """Reads its routing file, and the `also` file when the section names one."""

    name = "reading"
    policy_file_keys = ("routing",)

    def __init__(self) -> None:
        self.read: list[str] = []

    def produce(self, section: Mapping[str, Any], reader: PackReader, sink: PackSink, root: Path) -> None:
        self.read.append((root / section["routing"]).read_text())
        if "also" in section:
            self.read.append((root / section["also"]).read_text())


class FailureSink:
    def __init__(self) -> None:
        self.failed: list[str] = []

    def fail(self, producer: str, failure: ProducerFailure) -> None:
        self.failed.append(failure.reason)


@pytest.mark.parametrize("snapshot", ["working tree", "git tree"])
@pytest.mark.parametrize("anchored", [False, True])
@pytest.mark.parametrize("reads_more, fails", [(False, False), (True, True)])
def test_a_pack_reads_only_the_policy_files_it_names_from_the_runs_snapshot(
    tmp_path: Path, snapshot: str, anchored: bool, reads_more: bool, fails: bool
) -> None:
    write_files(tmp_path, {"conf/routing.yaml": "routes", "conf/other.yaml": "other"})
    git_in(tmp_path, "init", "-q")
    git_in(tmp_path, "add", "-A")
    tree = git_in(tmp_path, "write-tree").strip()
    # On disk only: git status cannot see an edit behind a skip-worktree bit, so no snapshot holds it.
    git_in(tmp_path, "update-index", "--skip-worktree", "conf/routing.yaml")
    (tmp_path / "conf/routing.yaml").write_text("edited")
    policy = tmp_path / "policy"
    write_files(policy, {"routing.yaml": "anchored routes"})
    routing = str(policy / "routing.yaml") if anchored else "conf/routing.yaml"
    section = {"routing": routing, **({"also": "conf/other.yaml"} if reads_more else {})}
    pack, sink = ReadingPack(), FailureSink()
    listing = GraphSource.listing_of(tmp_path, tree if snapshot == "git tree" else "")
    run_packs([(pack, section)], cast(PackReader, None), cast(PackSink, sink), listing)
    assert pack.read == ["anchored routes" if anchored else "routes"]
    assert bool(sink.failed) is fails, sink.failed
