"""Both engines over small repos, compared output by output as `hoglint.native.parity` does."""

from __future__ import annotations

import gc
import os
import subprocess
from pathlib import Path

import pytest

from hoglint.ledger.envelope import EngineError
from hoglint.native.parity import Dump, compare_dumps, read_lines
from hoglint.native.rules import Rejudged
from hoglint.native.source import ENGINES
from hoglint.rules.base import EdgeRule, Judgment, context_class
from hoglint.rules.engine import Engine, primitive_of
from rules.support import GraphBuilder, config_from

from native.require import native_module

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


# One presentation module whose classes and function differ only in their bases: an export that
# decides by class must keep them apart when the native engine classes destinations.
ADAPTER_REPO = {
    "hoglint.toml": """
schema = 1

[discovery]
source_roots = ["."]
first_party = ["core", "products"]

[fields]
package = { type = "id" }
tier = { type = "enum", values = ["core", "product"] }

[[assign]]
match = "products/{product}/**"
set = { package = "{product}", tier = "product" }

[[assign]]
match = "core/**"
set = { package = "core", tier = "core" }

[shapes.adapter]
allow = ["class"]

[exports.adapter]
within = "products/{product}"
paths = ["presentation/**"]
subclass_of = ["products.billing.presentation.mixins.BillingMixin"]
shape = "adapter"

[rules.public-surface]
primitive = "entrypoint"
kinds = ["import"]
select = "src.package != dst.package and dst.tier == 'product'"
identity = ["dst.symbol"]
ledger = false
""",
    "core/__init__.py": "",
    "core/views.py": "from products.billing.presentation.views import InvoiceViewSet, Plain, render\n",
    "products/__init__.py": "",
    "products/billing/__init__.py": "",
    "products/billing/presentation/__init__.py": "",
    "products/billing/presentation/mixins.py": "class BillingMixin:\n    pass\n",
    "products/billing/presentation/views.py": (
        "from products.billing.presentation.mixins import BillingMixin\n\n\n"
        "class InvoiceViewSet(BillingMixin):\n    pass\n\n\n"
        "class Plain:\n    pass\n\n\n"
        "def render():\n    pass\n"
    ),
}


def dumps(
    tmp_path: Path, sources: dict[str, str], monkeypatch: pytest.MonkeyPatch, unreadable: tuple[str, ...] = ()
) -> dict[str, Path]:
    native_module()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    repo = tmp_path / "repo"
    for path, text in sources.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    for path in unreadable:
        (repo / path).chmod(0)
        if os.access(repo / path, os.R_OK):
            pytest.skip("this user reads files whatever their mode (root)")
    out = {}
    try:
        for engine in ENGINES:
            out[engine] = tmp_path / engine
            work = tmp_path / f"work-{engine}"
            work.mkdir()
            Dump(repo, engine, out[engine], work).run()
    finally:
        gc.enable()
        for path in unreadable:
            (repo / path).chmod(0o644)
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


def test_a_source_no_engine_can_read_is_the_same_incomplete_item_in_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = dumps(tmp_path, OWNERS_REPO, monkeypatch, unreadable=("core/a.py",))
    assert [diff.render() for diff in compare_dumps(out["python"], out["native"]) if not diff.equal] == []
    reason = "core/a.py could not be read, so its facts are missing"
    assert ["source:unreadable", "core/a.py", reason, [], "extractor"] in read_lines(out["native"] / "incomplete.jsonl")


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


def test_an_export_that_decides_by_class_keeps_the_classes_of_one_module_apart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = dumps(tmp_path, ADAPTER_REPO, monkeypatch)
    assert [diff.render() for diff in compare_dumps(out["python"], out["native"]) if not diff.equal] == []
    flagged = [row[1] for row in read_lines(out["native"] / "findings.jsonl") if row[0] == "public-surface"]
    assert sorted(flagged) == [
        ["products.billing.presentation.views.Plain"],
        ["products.billing.presentation.views.render"],
    ]


# Two core modules import a product name that resolves and one that does not, with equal labels,
# so each pair is one group: a grouped finding keeps the flags its judgment sets.
BLOCK_REPO = {
    **OWNERS_REPO,
    "hoglint.toml": OWNERS_REPO["hoglint.toml"].replace(
        "[rules.core-to-product]",
        """[rules.core-imports]
primitive = "edge"
kinds = ["import"]
from = "src.tier == 'core'"
identity = ["src.module", "edge.surface"]
growth_key = ["edge.surface"]
on_unresolved = "block"

[rules.core-to-product]""",
    ),
    "core/a.py": "from products.billing.models import Invoice\nfrom products.billing.gone import Lost\n",
    "core/b.py": "from products.billing.logic import charge\nfrom products.billing.gone import Lost\n",
}


def test_grouped_findings_keep_the_flags_their_judgment_sets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = dumps(tmp_path, BLOCK_REPO, monkeypatch)
    assert [diff.render() for diff in compare_dumps(out["python"], out["native"]) if not diff.equal] == []
    flags = {
        (row[1][0], row[1][1]): (row[9], row[10])
        for row in read_lines(out["native"] / "findings.jsonl")
        if row[0] == "core-imports"
    }
    assert flags == {
        ("core.a", "products.billing.models.Invoice"): (True, False),
        ("core.b", "products.billing.logic.charge"): (True, False),
        ("core.a", "products.billing.gone.Lost"): (False, True),
        ("core.b", "products.billing.gone.Lost"): (False, True),
    }
