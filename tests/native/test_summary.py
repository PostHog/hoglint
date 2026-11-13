from __future__ import annotations

import gc
import weakref
import subprocess
from pathlib import Path
from typing import Any

import pytest

from hoglint.cli import Commands, Run, Runner
from hoglint.config import CONFIG_FILE, load_config
from hoglint.ledger.envelope import Envelope
from hoglint.ledger.scan import ScanSummary
from hoglint.model import FindingStatus, Graph
from hoglint.native.rules import Rejudged
from hoglint.native.source import batch_mode, graph_source
from hoglint.rules.engine import Engine
from rules.test_attribution import DYNAMIC_CONFIG, LOADER, SECOND_LOADER
from rules.test_compare import git_in, write_files

from native.require import native_module
from native.test_parity import OWNERS_REPO

ENVELOPE = Envelope("t" * 40, "engine", "policy", "framework")
# The owners repo with its rule in the ledger, so a run that writes its own ledger accepts all.
LEDGERED_REPO = {
    **OWNERS_REPO,
    "hoglint.toml": OWNERS_REPO["hoglint.toml"].replace("ledger = false\n", 'growth_key = ["src.package"]\n')
    + """
[levels.isolated]
group_by = "package"
requires = ["core-to-product"]
strict = ["core-to-product"]
""",
}


def summary(repo: Path, work: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """The summary of a run whose ledger accepts every finding, and whose strict level they all
    block. Grouped findings must not be judged again to judge the level or store the summary."""
    config = load_config(repo, repo / CONFIG_FILE)
    work.mkdir()
    graph = graph_source("native", work).build(repo, config)

    def judged_again(_: Rejudged) -> None:
        raise AssertionError("an accepted grouped finding was judged again for a level or a summary")

    with monkeypatch.context() as patched:
        patched.setattr(Rejudged, "get", judged_again)
        result = Engine(config, graph, repo).evaluate(rewrite_ledger=True)
        made = ScanSummary.from_run(result, graph, ENVELOPE, roots=(repo,)).to_json()
    assert {finding.status for finding in result.findings()} == {FindingStatus.ACCEPTED}
    assert set(result.levels["isolated"].failing) == {"billing", "core"}
    return made


def test_levels_and_summaries_of_accepted_grouped_findings_judge_no_edge_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    native_module()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    repo = tmp_path / "repo"
    for path, text in LEDGERED_REPO.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    try:
        summary(repo, tmp_path / "work", monkeypatch)
    finally:
        gc.enable()


def test_an_inline_baseline_frees_its_graph_before_the_staged_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    native_module()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    repo = tmp_path / "repo"
    write_files(repo, {"hoglint.toml": DYNAMIC_CONFIG, "core/__init__.py": "", "core/loader.py": LOADER})
    git_in(repo, "init", "-q")
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-qm", "one computed import")
    (repo / "core/loader.py").write_text(SECOND_LOADER)
    git_in(repo, "add", "-A")
    graphs: list[weakref.ref[Graph]] = []
    run = Runner.run

    def keep_a_weak_reference(self: Runner, *args: Any, **kwargs: Any) -> Run:
        made = run(self, *args, **kwargs)
        graphs.append(weakref.ref(made.graph))
        return made

    monkeypatch.setattr(Runner, "run", keep_a_weak_reference)
    (tmp_path / "work").mkdir()
    commands = Commands(repo, tmp_path / "work", "native")
    batch_mode("native")
    try:
        tree = commands.snapshots.write_tree()
        baseline = commands.baselines.prepare(commands.snapshots.policy_of(tree), tree, staged=True)
        assert baseline.against.endswith("computed and stored)") and graphs[0]() is None
    finally:
        gc.enable()
        commands.snapshots.close()
