from __future__ import annotations

from pathlib import Path

import pytest

from hoglint.cli import Commands, GraphSource
from hoglint.config import Config
from hoglint.model import Graph
from hoglint.rules.base import RuleContext
from hoglint.rules.engine import Engine

from rules.support import GraphBuilder, write_config

CONFIG = """
schema = 1
extends = ["hoglint:django"]

[fields]
package = { type = "id", required_on = ["products/**"] }

[[assign]]
match = "products/{product}/**"
set = { package = "{product}" }

[rules.crossings]
primitive = "edge"
kinds = ["import"]
select = "src.package != dst.package"
identity = "module-pair"
growth_key = ["src.module"]

[rules.unresolved-anything]
primitive = "edge"
select = "edge.resolution == 'unresolved'"
ledger = false

[levels.isolated]
group_by = "package"
requires = ["crossings"]
"""


def graph() -> Graph:
    return (
        GraphBuilder()
        .module("products.billing.logic", "products.alerts.logic")
        .edge("import", "products.alerts.logic", "products.billing.logic")
        .graph
    )


@pytest.fixture
def commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Commands:
    built = graph()

    def build(self: GraphSource, code_root: Path, config: Config, staged: bool = False) -> Graph:
        return built

    monkeypatch.setattr(GraphSource, "build", build)
    write_config(tmp_path / "repo", CONFIG)
    (tmp_path / "work").mkdir()
    return Commands(tmp_path / "repo", tmp_path / "work")


def test_write_ledger_converges_in_one_run(commands: Commands) -> None:
    # the run that writes the ledger must already count the level lines that ledger makes pass
    assert commands.check(index=False, write_ledger=True, changed=False) == 0
    assert commands.check(index=False, write_ledger=False, changed=False) == 0


def test_explain_edge_lists_rules_that_read_every_edge_kind(
    commands: Commands, capsys: pytest.CaptureFixture[str]
) -> None:
    run = commands.runner.run(commands.repo)
    ctx = RuleContext(run.config, run.graph, Engine(run.config, run.graph).labeler)
    edge = run.graph.edges[0]
    assert commands.explain(f"edge:{edge.run_id(ctx.ordinal(edge))}") == 0
    assert "rule     unresolved-anything" in capsys.readouterr().out
