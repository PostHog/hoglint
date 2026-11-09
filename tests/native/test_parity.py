"""Both engines over small repos, compared output by output as `hoglint.native.parity` does."""

from __future__ import annotations

import gc
import subprocess
from pathlib import Path

import pytest

from hoglint.ledger.envelope import EngineError
from hoglint.native import bridge
from hoglint.native.parity import Dump, compare_dumps, read_lines
from hoglint.native.rules import Rejudged
from hoglint.native.source import ENGINES
from hoglint.rules.base import EdgeRule, Judgment, context_class
from hoglint.rules.engine import Engine, primitive_of
from rules.support import GraphBuilder, config_from

# Two core modules whose labels are equal for everything the rule reads, so the native engine
# judges them as one group; each has its own owners.
OWNERS_REPO = {
    "hoglint.toml": """
schema = 1

[discovery]
source_roots = ["."]
first_party = ["core", "products"]

[fields]
package = { type = "id" }
tier = { type = "enum", values = ["core", "product"] }
owners = { type = "set" }

[roles]
owner = "owners"

[[assign]]
match = "products/{product}/**"
set = { package = "{product}", tier = "product" }

[[assign]]
match = "core/**"
set = { package = "core", tier = "core" }

[[assign]]
match = "core/a.py"
set = { owners = "team-a" }

[[assign]]
match = "core/b.py"
set = { owners = "team-b" }

[rules.core-to-product]
primitive = "edge"
kinds = ["import"]
from = "src.tier == 'core'"
deny = "dst.tier == 'product'"
identity = "package-pair"
ledger = false
""",
    "core/__init__.py": "",
    "core/a.py": "from products.billing.models import Invoice\n",
    "core/b.py": "from products.billing.logic import charge\n",
    "products/__init__.py": "",
    "products/billing/__init__.py": "",
    "products/billing/logic.py": "def charge():\n    pass\n",
    "products/billing/models.py": "class Invoice:\n    pass\n",
}


def dumps(tmp_path: Path, sources: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    try:
        bridge.load()
    except ImportError:
        pytest.skip("hoglint_native is not built (tools/hoglint/bin/build)")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    repo = tmp_path / "repo"
    for path, text in sources.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    out = {}
    try:
        for engine in ENGINES:
            out[engine] = tmp_path / engine
            work = tmp_path / f"work-{engine}"
            work.mkdir()
            Dump(repo, engine, out[engine], work).run()
    finally:
        gc.enable()
    return out


def test_grouped_findings_take_owners_and_subjects_from_their_own_edge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = dumps(tmp_path, OWNERS_REPO, monkeypatch)
    assert [diff.render() for diff in compare_dumps(out["python"], out["native"]) if not diff.equal] == []
    findings = read_lines(out["native"] / "findings.jsonl")
    owners = {row[3][1]: (row[6], row[8]) for row in findings}
    assert owners == {
        "core.a": (["team-a"], ["core.a", "products.billing.models"]),
        "core.b": (["team-b"], ["core.b", "products.billing.logic"]),
    }


def test_a_finding_judged_again_apart_from_its_group_is_an_engine_error(tmp_path: Path) -> None:
    config = config_from(tmp_path, OWNERS_REPO["hoglint.toml"])
    built = (
        GraphBuilder()
        .module("core.a", "products.billing.models")
        .edge("import", "core.a", "products.billing.models.Invoice")
        .graph
    )
    engine = Engine(config, built, tmp_path)
    ctx = context_class(engine.labeler.index)(config, built, engine.labeler, tmp_path)
    spec = config.rules["core-to-product"]
    rule = primitive_of(spec)(ctx, spec)
    assert isinstance(rule, EdgeRule)
    edge = built.edges[0]
    assert Rejudged(rule, edge, Judgment.VIOLATION).get().message
    with pytest.raises(EngineError):
        Rejudged(rule, edge, Judgment.UNKNOWN_VIOLATION).get()
