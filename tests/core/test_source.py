from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest

from hoglint.config import load_config
from hoglint.config.model import Config
from hoglint.facts import FACT_KINDS, FactNeeds
from hoglint.native.config import link_config
from hoglint.native.source import NativeGraphSource
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


def export_config(root: Path, paths: str) -> Config:
    write_files(
        root,
        {
            "hoglint.toml": (
                'schema = 1\n\n[discovery]\nsource_roots = ["."]\n\n[shapes.loose]\nallow = ["class"]\n\n'
                f'[exports.api]\nwithin = "app"\npaths = ["{paths}"]\nshape = "loose"\n'
            )
        },
    )
    return load_config(root)


def test_the_pack_memo_key_holds_the_policy_file_of_the_snapshot_and_every_config_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    write_files(repo, {"conf/routing.yaml": "first"})
    git_in(repo, "init", "-q")
    git_in(repo, "add", "-A")
    first = git_in(repo, "write-tree").strip()
    (repo / "conf/routing.yaml").write_text("second")
    git_in(repo, "add", "-A")
    second = git_in(repo, "write-tree").strip()
    (repo / "conf/routing.yaml").write_text("on disk only")
    monkeypatch.chdir(repo)
    packs = [(ReadingPack(), {"routing": "conf/routing.yaml"})]
    api, facade = export_config(tmp_path / "api", "api/**"), export_config(tmp_path / "facade", "facade/**")
    (tmp_path / "work").mkdir()
    source = NativeGraphSource(tmp_path / "work")

    def key(config: Config, tree: str) -> str:
        return source.memo_config(config, cast(Any, packs), GraphSource.listing_of(repo, tree))

    assert key(api, first) == key(api, first)
    assert key(api, first) != key(api, second)
    assert key(api, first) != key(facade, first)


PINNED_CONFIG = """schema = 1

[discovery]
source_roots = ["."]
dataclass_decorators = "pkg.frozen"

[shapes.loose]
allow = ["class"]

[shapes.typed]
allow = ["class"]
signatures = true

[exports.api]
within = "app"
paths = ["api/**"]
shape = "loose"

[exports.typed]
within = "lib"
paths = ["typed.py", "more/**"]
shape = "typed"
"""
# Run summaries key on this text, so a change to how the graph key is built must not move it.
PINNED_KEY = (
    '{"contract_surfaces": [["lib", ["typed.py", "more/**"]]], '
    '"export_surfaces": [["app", ["api/**"]], ["lib", ["typed.py", "more/**"]]], '
    '"sections": {"discovery": {"dataclass_decorators": "pkg.frozen", "source_roots": ["."]}}}'
)


def test_the_graph_key_text_of_a_fixed_config_does_not_move(tmp_path: Path) -> None:
    write_files(tmp_path, {"hoglint.toml": PINNED_CONFIG})
    assert GraphSource.key(Path("/repo"), load_config(tmp_path), True) == ("/repo:staged", PINNED_KEY)


def test_a_bare_string_and_a_list_in_discovery_parse_to_one_value_on_every_side(tmp_path: Path) -> None:
    parsed = []
    for name, value in (("bare", '"pkg.frozen"'), ("listed", '["pkg.frozen"]')):
        discovery = f"[discovery]\ndataclass_decorators = {value}\nfrozen_dataclass_decorators = {value}\n"
        write_files(tmp_path / name, {"hoglint.toml": f"schema = 1\n\n{discovery}"})
        core = GraphSource.core_config(load_config(tmp_path / name))
        parsed.append((core, core.dataclass_kinds, link_config(core, (), {})))
    assert parsed[0] == parsed[1]
    assert parsed[0][1].make == parsed[0][1].freeze == frozenset({"pkg.frozen"})
