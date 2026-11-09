from __future__ import annotations

import gc
import subprocess
from pathlib import Path

import pytest

from hoglint.config import CONFIG_FILE, load_config
from hoglint.ledger.envelope import Envelope
from hoglint.ledger.scan import ScanSummary
from hoglint.model import FindingStatus
from hoglint.native import bridge
from hoglint.native.rules import Rejudged
from hoglint.native.source import ENGINES, graph_source
from hoglint.rules.engine import Engine

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


def summary(repo: Path, engine: str, work: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """The summary of a run whose ledger accepts every finding, and whose strict level they all
    block. Grouped findings must not be judged again to judge the level or store the summary."""
    config = load_config(repo, repo / CONFIG_FILE)
    work.mkdir()
    graph = graph_source(engine, work).build(repo, config)

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
    try:
        bridge.load()
    except ImportError:
        pytest.skip("hoglint_native is not built (tools/hoglint/bin/build)")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    repo = tmp_path / "repo"
    for path, text in LEDGERED_REPO.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    try:
        made = {engine: summary(repo, engine, tmp_path / f"work-{engine}", monkeypatch) for engine in ENGINES}
    finally:
        gc.enable()
    assert made["native"] == made["python"]
