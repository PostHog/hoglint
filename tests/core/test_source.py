from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest

from hoglint.facts import FACT_KINDS, FactNeeds
from hoglint.plugins import PackReader, PackSink, ProducerFailure
from hoglint.source import GraphSource, run_packs

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


@pytest.mark.parametrize("anchored", [False, True])
@pytest.mark.parametrize("reads_more, fails", [(False, False), (True, True)])
def test_a_pack_reads_only_the_policy_files_it_names(
    tmp_path: Path, anchored: bool, reads_more: bool, fails: bool
) -> None:
    (tmp_path / "conf").mkdir()
    (tmp_path / "conf/routing.yaml").write_text("routes")
    (tmp_path / "conf/other.yaml").write_text("other")
    routing = str(tmp_path / "conf/routing.yaml") if anchored else "conf/routing.yaml"
    section = {"routing": routing, **({"also": "conf/other.yaml"} if reads_more else {})}
    pack, sink = ReadingPack(), FailureSink()
    run_packs([(pack, section)], cast(PackReader, None), cast(PackSink, sink), tmp_path)
    assert pack.read == ["routes"]
    assert bool(sink.failed) is fails, sink.failed
