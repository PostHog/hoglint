from __future__ import annotations

import json
import datetime
from pathlib import Path

import pytest

from hoglint.config import Config
from hoglint.ledger.file import LedgerFile
from hoglint.model import Context, FindingStatus, Graph, Resolution
from hoglint.rules.engine import Engine, RunResult
from hoglint.rules.exceptions import ExceptionsFile

from rules.support import GraphBuilder, config_from

FIELDS = """
schema = 1
extends = ["hoglint:django"]

[fields]
package = { type = "id", required_on = ["products/**", "core/**"] }
tier = { type = "enum", values = ["core", "product"], required_on = ["products/**", "core/**"] }
zone = { type = "zone" }

[[assign]]
match = "products/{product}/**"
set = { package = "{product}", tier = "product" }

[[assign]]
match = "core/**"
set = { package = "core", tier = "core" }

[zones]
within = "products/{product}"
facade = { paths = ["facade/**"] }
contracts = { parent = "facade", paths = ["facade/contracts.py"] }
logic = { paths = ["logic.py"] }
models = { paths = ["models.py"] }

[kinds]
model = { subclass_of = "django.db.models.Model" }

[identities]
crossing = ["dst.symbol", "src.module", "edge.label"]
"""


def run(
    tmp_path: Path, rules: str, graph: Graph, ledger: LedgerFile | None = None, exceptions: str | None = None
) -> RunResult:
    config: Config = config_from(tmp_path, FIELDS + rules)
    parsed = None
    if exceptions is not None:
        import tomllib  # noqa: PLC0415 — only this helper reads exceptions

        parsed = ExceptionsFile.parse(tomllib.loads(exceptions), config)
    return Engine(config, graph, tmp_path, today=datetime.date(2026, 11, 9)).evaluate(ledger, parsed)


def messages(result: RunResult, rule: str) -> list[str]:
    return [f.message for f in result.runs[rule].findings]


def identities(result: RunResult, rule: str) -> list[tuple[str, ...]]:
    return [f.identity for f in result.runs[rule].findings]


def base_graph() -> GraphBuilder:
    return (
        GraphBuilder()
        .module(
            "products.billing.facade.api",
            "products.billing.facade.contracts",
            "products.billing.logic",
            "products.billing.models",
            "products.billing.scripts",
            "products.alerts.logic",
            "core.api",
            "core.views",
            "tools.thing",
        )
        .symbol("products.billing.models.Invoice")
        .symbol("products.billing.logic.charge", "def")
        .symbol("products.billing.facade.api.charge", "def")
        .edge(
            "inherit",
            "products.billing.models",
            "django.db.models.Model",
            attrs=(("class", "Invoice"),),
            resolution=Resolution.EXTERNAL,
        )
    )


EDGE_RULE = """
[rules.no-product-from-core]
primitive = "edge"
kinds = ["import"]
from = "src.tier == 'core'"
deny = "dst.tier == 'product'"
identity = "module-pair"
growth_key = ["src.module", "dst.module"]
"""


def test_edge_rule_unknown_never_escapes_a_deny(tmp_path: Path) -> None:
    graph = (
        base_graph()
        .module("products.ghost")
        .edge("import", "core.api", "products.billing.logic.charge")
        .edge("import", "core.api", "tools.thing")
        .edge("import", "core.views", None, "products.gone.x", resolution=Resolution.UNRESOLVED)
        # products/ghost.py is inside the tier domain but no glob captures it: unknown
        .edge("import", "core.api", "products.ghost")
        .graph
    )
    result = run(tmp_path, EDGE_RULE, graph)
    found = identities(result, "no-product-from-core")
    assert ("core.api", "products.billing.logic") in found
    assert ("core.api", "tools.thing") not in found  # tools is outside the domain: a known none
    assert ("core.views", "?") in found  # unresolved: on_unresolved = finding
    ghost = [f for f in result.runs["no-product-from-core"].findings if f.identity == ("core.api", "products.ghost")]
    assert ghost and ghost[0].unknown


@pytest.mark.parametrize(
    "mode, ledgerable, incomplete",
    [("finding", True, 0), ("block", False, 0), ("incomplete", None, 1)],
)
def test_on_unresolved_modes(tmp_path: Path, mode: str, ledgerable: bool | None, incomplete: int) -> None:
    graph = base_graph().edge("import", "core.api", None, "products.gone.x", resolution=Resolution.UNRESOLVED).graph
    result = run(tmp_path, EDGE_RULE + f'on_unresolved = "{mode}"\n', graph)
    rule_run = result.runs["no-product-from-core"]
    assert len(rule_run.incomplete) == incomplete
    if ledgerable is not None:
        assert [f.ledgerable for f in rule_run.findings] == [ledgerable]


def test_unknown_selection_makes_the_rule_incomplete(tmp_path: Path) -> None:
    graph = base_graph().module("products.loose").edge("import", "products.loose", "core.api").graph
    rules = EDGE_RULE.replace("from = \"src.tier == 'core'\"", "from = \"src.package != 'billing'\"")
    result = run(tmp_path, rules, graph)
    assert [item.key for item in result.runs["no-product-from-core"].incomplete] == ["products.loose|import|core.api"]
    assert result.exit_code() == 0  # inherited incompleteness is reported, not blocking


ENTRYPOINT = """
[shapes.contract]
data = ["frozen-dataclass"]
deny = ["model"]

[exports.facade]
within = "products/{product}"
paths = ["facade/**"]
shape = "contract"

[exports.admin]
within = "products/{product}"
paths = ["logic.py"]
audience = "src.module == 'core.views'"
shape = "contract"

[rules.public-surface]
primitive = "entrypoint"
kinds = ["import"]
select = "src.package != dst.package and dst.tier == 'product'"
identity = "module-symbol"
growth_key = ["dst.symbol"]
"""


def test_entrypoint_reads_the_surface_and_the_audience(tmp_path: Path) -> None:
    graph = (
        base_graph()
        .edge(
            "import",
            "core.api",
            "products.billing.logic.charge",
            "products.billing.facade.api.charge",
            via=("products.billing.facade.api.charge",),
        )
        .edge("import", "core.api", "products.billing.logic.charge")
        .edge("import", "core.views", "products.billing.logic.charge")
        .edge("import", "products.billing.logic", "products.billing.models.Invoice")
        .graph
    )
    result = run(tmp_path, ENTRYPOINT, graph)
    assert identities(result, "public-surface") == [("products.billing.logic.charge", "core.api", "import")]


USE_SHAPE = """
[rules.model-crossings]
primitive = "use-shape"
kinds = ["reference"]
select = "dst.kind == 'model' and src.package != dst.package"
allow_shapes = ["annotation", "isinstance"]
identity = "crossing"
counted = true
growth_key = ["dst.symbol", "edge.label"]
"""


def test_use_shape_counts_per_identity_and_ledger_accepts(tmp_path: Path) -> None:
    builder = base_graph()
    for line in (1, 2):
        builder.edge(
            "reference",
            "core.api",
            "products.billing.models.Invoice",
            shape="instance-many",
            detail="filter",
            line=line,
        )
    builder.edge("reference", "core.api", "products.billing.models.Invoice", shape="annotation")
    builder.edge("reference", "products.billing.logic", "products.billing.models.Invoice", shape="write", detail="save")
    graph = builder.graph
    key = ("model-crossings", ("products.billing.models.Invoice", "core.api", "instance-many(filter)"))
    result = run(tmp_path, USE_SHAPE, graph)
    assert result.scan == {key: 2}
    ledger = LedgerFile(schema=1, policy=result.config.policy_hash, lines={key: 2}, exists=True)
    accepted = run(tmp_path, USE_SHAPE, graph, ledger)
    assert {f.status for f in accepted.runs["model-crossings"].findings} == {FindingStatus.ACCEPTED}
    assert accepted.equality is not None and accepted.equality.equal and accepted.exit_code() == 0
    shrunk = LedgerFile(schema=1, policy=result.config.policy_hash, lines={key: 3}, exists=True)
    stale = run(tmp_path, USE_SHAPE, graph, shrunk)
    assert stale.equality is not None and [d.key for d in stale.equality.stale] == [key]
    assert stale.exit_code() == 1


REACH = """
[rules.contracts-no-django]
primitive = "reach"
view = "file-imports"
from = "src.zone == 'contracts'"
deny = "dst.external == 'django'"
contexts = { exclude = ["type_only"] }
on_unresolved = "incomplete"
identity = "module-pair"
growth_key = ["src.module"]
"""


def test_reach_follows_chains_and_skips_type_only(tmp_path: Path) -> None:
    graph = (
        base_graph()
        .edge("import", "products.billing.facade.contracts", "products.billing.logic")
        .edge("import", "products.billing.logic", "django.db.models", resolution=Resolution.EXTERNAL)
        .edge(
            "import",
            "products.billing.facade.contracts",
            "django.http",
            resolution=Resolution.EXTERNAL,
            contexts=(Context.TYPE_ONLY,),
        )
        .graph
    )
    result = run(tmp_path, REACH, graph)
    assert identities(result, "contracts-no-django") == [("products.billing.facade.contracts", "django.db.models")]
    assert len(result.runs["contracts-no-django"].findings[0].witnesses) == 2


CYCLE = """
[rules.no-cycles]
primitive = "cycle"
view = "packages"
select = "src.tier == 'product' and dst.tier == 'product'"
identity = "package-pair"
growth_key = ["src.package", "dst.package"]
"""


def test_cycle_reports_every_pair_inside_a_component(tmp_path: Path) -> None:
    graph = (
        base_graph()
        .edge("import", "products.billing.logic", "products.alerts.logic")
        .edge("reference", "products.alerts.logic", "products.billing.models.Invoice")
        .edge("import", "products.billing.logic", "core.api")
        .graph
    )
    result = run(tmp_path, CYCLE, graph)
    assert sorted(identities(result, "no-cycles")) == [("alerts", "billing"), ("billing", "alerts")]


SIGNATURE = """
[rules.facade-signature]
primitive = "signature"
select = "src.zone == 'facade'"
deny = "dst.kind == 'model' or dst.symbol in ['django.db.models.QuerySet']"
identity = ["edge.callable", "edge.slot", "dst.symbol"]
growth_key = ["edge.callable"]
"""


def test_signature_denies_models_and_reads_any_as_unknown(tmp_path: Path) -> None:
    def expose(builder: GraphBuilder, target: str, slot: str, resolution: Resolution = Resolution.RESOLVED) -> None:
        attrs = (("callable", "charge"), ("slot", slot))
        builder.edge("exposes", "products.billing.facade.api", target, resolution=resolution, attrs=attrs)

    builder = base_graph()
    expose(builder, "products.billing.models.Invoice", "return")
    expose(builder, "typing.Any", "param:payload", Resolution.EXTERNAL)
    expose(builder, "builtins.int", "param:amount", Resolution.EXTERNAL)
    result = run(tmp_path, SIGNATURE, builder.graph)
    found = {f.identity: f.unknown for f in result.runs["facade-signature"].findings}
    assert found == {
        ("charge", "return", "products.billing.models.Invoice"): False,
        ("charge", "param:payload", "typing.Any"): True,
    }


EXPORT = """
[shapes.contract]
data = ["frozen-dataclass"]
deny = ["model"]

[exports.facade]
within = "products/{product}"
paths = ["facade/**"]
shape = "contract"

[rules.export-shape]
primitive = "export"
identity = "module-symbol"
growth_key = ["dst.symbol"]
"""


def test_export_shape_is_checked_through_fields(tmp_path: Path) -> None:
    graph = (
        base_graph()
        .symbol("products.billing.facade.contracts.Result")
        .edge(
            "decorate",
            "products.billing.facade.contracts",
            "dataclasses.dataclass",
            resolution=Resolution.EXTERNAL,
            attrs=(("decorated", "Result"), ("frozen", "1")),
        )
        .edge(
            "exposes",
            "products.billing.facade.contracts",
            "products.billing.models.Invoice",
            attrs=(("callable", "Result.__init__"), ("slot", "field:invoice")),
        )
        .edge("import", "products.billing.facade.api", "products.billing.models.Invoice")
        .graph
    )
    result = run(tmp_path, EXPORT, graph)
    found = sorted(identities(result, "export-shape"))
    assert found == [
        ("products.billing.models.Invoice", "products.billing.facade.api", "export-denied(model)"),
        ("products.billing.models.Invoice", "products.billing.facade.contracts", "export-denied(model)"),
    ]


LAYOUT_AND_COVERAGE = """
[rules.drives]
primitive = "edge"
kinds = ["drives"]
contexts = { include = ["test"] }
select = "src.package != dst.package"
identity = ["edge.location", "src.module"]
counted = true
growth_key = ["edge.location"]

[rules.wiring-watched]
primitive = "coverage"
covers = ["rules.drives.location"]
inputs = { adapter = "turbo", task = "contract-check" }
identity = "entity"
growth_key = ["subject"]

[rules.at-home]
primitive = "layout"
select = "tier == 'product' and not path startswith 'products/'"
identity = ["package", "subject"]
growth_key = ["package"]
"""


def test_coverage_reads_turbo_inputs_and_layout_selects_entities(tmp_path: Path) -> None:
    (tmp_path / "products" / "billing").mkdir(parents=True)
    (tmp_path / "products" / "billing" / "turbo.json").write_text(
        json.dumps({"tasks": {"contract-check": {"inputs": ["facade/**"]}}})
    )
    graph = (
        base_graph()
        .module("core.tests.test_billing")
        .edge(
            "drives",
            "core.tests.test_billing",
            "products.billing.logic",
            contexts=(Context.TEST,),
            attrs=(("location", "products/billing/logic.py"),),
        )
        .edge(
            "drives",
            "core.tests.test_billing",
            "products.billing.facade.api",
            contexts=(Context.TEST,),
            attrs=(("location", "products/billing/facade/api.py"),),
        )
        .graph
    )
    rules = LAYOUT_AND_COVERAGE.replace('match = "products/{product}/**"', "")
    result = run(tmp_path, rules, graph)
    assert identities(result, "wiring-watched") == [("products/billing/logic.py",)]
    assert identities(result, "at-home") == []


LEVEL = (
    USE_SHAPE
    + LAYOUT_AND_COVERAGE
    + """
[levels.isolated]
group_by = "package"
select = "tier == 'product'"
requires = ["model-crossings", "wiring-watched"]
strict = ["wiring-watched"]
"""
)


def test_level_lets_ledgered_debt_coexist_but_not_unknowns_or_unwatched_wiring(tmp_path: Path) -> None:
    (tmp_path / "products" / "billing").mkdir(parents=True)
    (tmp_path / "products" / "billing" / "turbo.json").write_text(
        json.dumps({"tasks": {"contract-check": {"inputs": ["**"]}}})
    )
    (tmp_path / "products" / "alerts").mkdir(parents=True)
    (tmp_path / "products" / "alerts" / "turbo.json").write_text(
        json.dumps({"tasks": {"contract-check": {"inputs": ["facade/**"]}}})
    )
    graph = (
        base_graph()
        .symbol("products.alerts.logic.Alert")
        .edge("reference", "core.api", "products.billing.models.Invoice", shape="write", detail="save")
        .edge(
            "drives",
            "core.api",
            "products.alerts.logic",
            contexts=(Context.TEST,),
            attrs=(("location", "products/alerts/logic.py"),),
        )
        .graph
    )
    first = run(tmp_path, LEVEL, graph)
    assert first.levels["isolated"].passing == []  # the crossing is new, not yet ledgered
    ledger = LedgerFile(schema=1, policy=first.config.policy_hash, lines=dict(first.scan), exists=True)
    second = run(tmp_path, LEVEL, graph, ledger)
    assert second.levels["isolated"].passing == ["billing"]
    assert "unwatched" in second.levels["isolated"].failing["alerts"][0]


EXCEPTIONS = """
schema = 1

[[exception]]
rule = "model-crossings"
match = { "dst.symbol" = "products.billing.models.Invoice", "src.module" = "core.api" }
reason = "placeholder"
owners = ["team-billing"]
scope = "prod"
review_by = "2026-01-01"

[[exception]]
rule = "model-crossings"
match = { "src.module" = "core.gone" }
reason = "placeholder"
owners = ["team-billing"]
scope = "prod"
"""


def test_exceptions_allow_and_stale_or_expired_entries_are_reported(tmp_path: Path) -> None:
    graph = (
        base_graph()
        .edge("reference", "core.api", "products.billing.models.Invoice", shape="write", detail="save")
        .graph
    )
    result = run(tmp_path, USE_SHAPE, graph, LedgerFile(), EXCEPTIONS)
    assert [f.status for f in result.runs["model-crossings"].findings] == [FindingStatus.ALLOWED]
    assert result.scan == {}
    assert [e.index for e in result.exceptions.stale] == [1]
    assert [e.index for e in result.exceptions.expired] == [0]
    assert result.exit_code() == 1  # the stale entry fails; the expired one only reports
