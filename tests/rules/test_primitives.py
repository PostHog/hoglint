from __future__ import annotations

import sys
import json
import tomllib
import datetime
import subprocess
from pathlib import Path

import pytest

from hoglint.config import Config
from hoglint.ledger.file import LedgerFile
from hoglint.model import Context, FindingStatus, Graph, HandOut, IncompleteCause, Resolution
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


CYCLE = """
[roles]
group = "package"

[rules.no-cycles]
primitive = "cycle"
view = "packages"
select = "src.tier == 'product' and dst.tier == 'product'"
identity = "package-pair"
growth_key = ["src.package", "dst.package"]
"""


@pytest.mark.parametrize(
    "rules, rule_id, src",
    [(EDGE_RULE, "no-product-from-core", "core.api"), (CYCLE, "no-cycles", "products.billing.logic")],
)
@pytest.mark.parametrize(
    "mode, ledgerable, incomplete",
    [("finding", True, 0), ("block", False, 0), ("incomplete", None, 1)],
)
def test_on_unresolved_modes(
    tmp_path: Path, rules: str, rule_id: str, src: str, mode: str, ledgerable: bool | None, incomplete: int
) -> None:
    graph = base_graph().edge("import", src, None, "products.gone.x", resolution=Resolution.UNRESOLVED).graph
    result = run(tmp_path, rules + f'on_unresolved = "{mode}"\n', graph)
    rule_run = result.runs[rule_id]
    assert len(rule_run.incomplete) == incomplete
    if ledgerable is not None:
        assert [f.ledgerable for f in rule_run.findings] == [ledgerable]


def test_an_edge_with_its_own_src_label_is_judged_on_its_own(tmp_path: Path) -> None:
    graph = (
        base_graph()
        .edge("import", "core.api", "products.billing.models.Invoice", attrs=(("src.tier", "product"),))
        .edge("import", "core.api", "products.billing.logic.charge")
        .graph
    )
    result = run(tmp_path, EDGE_RULE, graph)
    assert identities(result, "no-product-from-core") == [("core.api", "products.billing.logic")]


def test_a_kind_marked_expected_that_matches_nothing_makes_its_rules_incomplete(tmp_path: Path) -> None:
    graph = (
        GraphBuilder()
        .module("core.api", "products.billing.models")
        .edge("reference", "core.api", "products.billing.models")
        .graph
    )
    expected = FIELDS.replace(
        'subclass_of = "django.db.models.Model" }', 'subclass_of = "django.db.models.Model", expected = true }'
    )
    config = config_from(tmp_path, expected + USE_SHAPE + EDGE_RULE)
    result = Engine(config, graph, tmp_path).evaluate()
    assert [item.cause for item in result.runs["model-crossings"].incomplete] == [IncompleteCause.EXTRACTOR]
    assert result.runs["no-product-from-core"].incomplete == []
    assert result.exit_code() == 2


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


def test_an_edge_with_its_own_src_label_meets_the_audience_on_its_own(tmp_path: Path) -> None:
    graph = (
        base_graph()
        .edge("import", "core.views", "products.billing.logic.charge", line=1)
        .edge("import", "core.views", "products.billing.logic.charge", line=2, attrs=(("src.tier", "product"),))
        .graph
    )
    rules = ENTRYPOINT.replace("audience = \"src.module == 'core.views'\"", "audience = \"src.tier == 'product'\"")
    findings = run(tmp_path, rules, graph).runs["public-surface"].findings
    assert [finding.site.line for finding in findings if finding.site is not None] == [1]


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


FLOW_SHAPE = """
[rules.model-sites]
primitive = "use-shape"
kinds = ["reference"]
select = "dst.kind == 'model'"
allow_shapes = ["annotation"]
flow = "def-use"
identity = "site"
growth_key = ["src.module"]
"""


def test_use_shape_with_flow_keeps_the_graph_edge_and_its_ordinal(tmp_path: Path) -> None:
    builder = base_graph()
    for line in (1, 2):
        builder.edge(
            "reference",
            "core.api",
            "products.billing.models.Invoice",
            shape="instance-single",
            attrs=(("flow_shape", "write(save)"),),
            line=line,
        )
    graph = builder.graph
    findings = run(tmp_path, FLOW_SHAPE, graph).runs["model-sites"].findings
    assert sorted(f.identity[-1] for f in findings) == ["0", "1"]
    assert all(any(f.witnesses[0] is edge for edge in graph.edges) for f in findings)
    assert {f.message.rsplit(" as ", 1)[1] for f in findings} == {"write(save)"}


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


REACH_START = """
[rules.named-contracts]
primitive = "reach"
view = "file-imports"
from = "src.name == 'contracts'"
deny = "dst.external == 'django'"
identity = "module-pair"
growth_key = ["src.module"]

[rules.imports-only]
primitive = "reach"
view = "file-imports"
from = "src.name == 'contracts'"
select = "edge.kind == 'import'"
deny = "dst.external == 'django'"
identity = "module-pair"
growth_key = ["src.module"]
"""


def test_reach_starts_from_the_module_and_follows_only_selected_edges(tmp_path: Path) -> None:
    graph = (
        base_graph()
        # a function-local import: the module, not this edge's scope, decides whether the search starts
        .edge(
            "import", "products.billing.facade.contracts", "django.http", scope="helper", resolution=Resolution.EXTERNAL
        )
        .edge("dotted-path", "products.billing.facade.contracts", "products.billing.logic")
        .edge("import", "products.billing.logic", "django.db.models", resolution=Resolution.EXTERNAL)
        .graph
    )
    result = run(tmp_path, REACH_START, graph)
    contracts = "products.billing.facade.contracts"
    assert sorted(identities(result, "named-contracts")) == [
        (contracts, "django.db.models"),
        (contracts, "django.http"),
    ]
    assert identities(result, "imports-only") == [(contracts, "django.http")]


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
    assert len(result.runs["facade-signature"].findings) == 2
    found = {f.identity: (f.unknown, f.message) for f in result.runs["facade-signature"].findings}
    assert found == {
        ("charge", "return", "products.billing.models.Invoice"): (
            False,
            "products.billing.facade.api.charge exposes products.billing.models.Invoice in return",
        ),
        ("charge", "param:payload", "typing.Any"): (
            True,
            "products.billing.facade.api.charge: param:payload has no usable type (typing.Any)",
        ),
    }


ADAPTER = """
[shapes.adapter]
allow = ["class"]
deny = ["model"]

[exports.adapter]
within = "products/{product}"
paths = ["presentation/**"]
subclass_of = ["products.billing.presentation.mixins.BillingMixin"]
shape = "adapter"

[rules.export-shape]
primitive = "export"
identity = "module-symbol"
growth_key = ["dst.symbol"]

[rules.public-surface]
primitive = "entrypoint"
kinds = ["import"]
select = "src.package != dst.package and dst.tier == 'product'"
identity = "module-symbol"
growth_key = ["dst.symbol"]
"""


def test_export_by_base_class_hands_out_only_the_adapters(tmp_path: Path) -> None:
    graph = (
        base_graph()
        .module("products.billing.presentation.mixins", "products.billing.presentation.views")
        .symbol("products.billing.presentation.mixins.BillingMixin")
        .symbol("products.billing.presentation.views.InvoiceViewSet")
        .symbol("products.billing.presentation.views.render", "def")
        .edge(
            "inherit",
            "products.billing.presentation.views",
            "products.billing.presentation.mixins.BillingMixin",
            attrs=(("class", "InvoiceViewSet"),),
        )
        # views uses the model internally; importing it does not export it
        .edge("import", "products.billing.presentation.views", "products.billing.models.Invoice")
        .edge("import", "core.views", "products.billing.presentation.mixins.BillingMixin")
        .edge("import", "core.views", "products.billing.presentation.views.InvoiceViewSet")
        .edge("import", "core.views", "products.billing.presentation.views.render")
        .graph
    )
    result = run(tmp_path, ADAPTER, graph)
    assert identities(result, "export-shape") == []
    assert identities(result, "public-surface") == [
        ("products.billing.presentation.views.render", "core.views", "import")
    ]


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


# `products.billing.facade.api` hands out the `Invoice` it imports.
BINDS_INVOICE = (("binds", "Invoice"),)
HANDS_OUT_INVOICE = HandOut(frozenset({"Invoice"}), frozenset(), False)


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
        .edge("import", "products.billing.facade.api", "products.billing.models.Invoice", attrs=BINDS_INVOICE)
        .graph
    )
    graph.hand_outs["products.billing.facade.api"] = HANDS_OUT_INVOICE
    result = run(tmp_path, EXPORT, graph)
    found = sorted(identities(result, "export-shape"))
    assert found == [
        ("products.billing.models.Invoice", "products.billing.facade.api", "export-denied(model)"),
        ("products.billing.models.Invoice", "products.billing.facade.contracts", "export-denied(model)"),
    ]


@pytest.mark.parametrize("contexts, reported", [("", False), ("contexts = { exclude = [] }\n", True)])
def test_export_skips_test_imports_by_default_and_an_empty_exclude_keeps_them(
    tmp_path: Path, contexts: str, reported: bool
) -> None:
    graph = (
        base_graph()
        .edge(
            "import",
            "products.billing.facade.api",
            "products.billing.models.Invoice",
            contexts=(Context.TEST,),
            attrs=BINDS_INVOICE,
        )
        .graph
    )
    graph.hand_outs["products.billing.facade.api"] = HANDS_OUT_INVOICE
    result = run(tmp_path, EXPORT + contexts, graph)
    assert bool(identities(result, "export-shape")) is reported


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


def turbo_package(root: Path, directory: str, turbo: dict[str, object] | None, script: str = "contract-check") -> None:
    """A workspace package that runs `script`, with an optional package turbo.json."""
    (root / directory).mkdir(parents=True, exist_ok=True)
    (root / directory / "package.json").write_text(json.dumps({"name": directory, "scripts": {script: "true"}}))
    if turbo is not None:
        (root / directory / "turbo.json").write_text(json.dumps(turbo))


def test_coverage_reads_turbo_inputs_and_layout_selects_entities(tmp_path: Path) -> None:
    turbo_package(tmp_path, "products/billing", {"tasks": {"contract-check": {"inputs": ["facade/**"]}}})
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


COVER_EDGES = """
[rules.wiring-watched]
primitive = "coverage"
covers = ["edge.kind == 'dotted-path' and edge.context == 'test' and src.package != dst.package: dst"]
inputs = { adapter = "turbo", task = "contract-check" }
identity = "entity"
growth_key = ["subject"]
"""


def test_coverage_selects_each_edge_by_its_own_contexts(tmp_path: Path) -> None:
    turbo_package(tmp_path, "products/billing", {"tasks": {"contract-check": {"inputs": ["facade/**"]}}})
    graph = (
        base_graph()
        .edge("dotted-path", "core.api", "products.billing.models")
        .edge("dotted-path", "core.api", "products.billing.logic", contexts=(Context.TEST,))
        .edge("dotted-path", "core.views", None, "products.billing.gone", resolution=Resolution.UNRESOLVED)
        .edge(
            "dotted-path",
            "core.views",
            None,
            "products.billing.lost",
            resolution=Resolution.UNRESOLVED,
            contexts=(Context.TEST,),
        )
        .graph
    )
    result = run(tmp_path, COVER_EDGES, graph)
    assert identities(result, "wiring-watched") == [("products/billing/logic.py",)]
    assert [item.key for item in result.runs["wiring-watched"].incomplete] == ["core.views|products.billing.lost"]


COVER_A_REACH = """
[rules.request-path]
primitive = "reach"
view = "file-imports"
from = "src.module == 'core.api'"
deny = "dst.tier == 'product'"
on_unresolved = "incomplete"
identity = "module-pair"
report = "advisory"
ledger = false

[rules.wiring-watched]
primitive = "coverage"
covers = ["rules.request-path.dst.path"]
inputs = { adapter = "turbo", task = "contract-check" }
identity = "entity"
growth_key = ["subject"]
"""


def test_coverage_cannot_tell_what_a_rule_it_reads_cannot_tell(tmp_path: Path) -> None:
    turbo_package(tmp_path, "products/billing", {"tasks": {"contract-check": {"inputs": ["facade/**"]}}})
    graph = (
        base_graph()
        .edge("import", "core.api", "products.billing.logic")
        .edge("import", "core.api", None, "products.billing.gone", resolution=Resolution.UNRESOLVED)
        .graph
    )
    result = run(tmp_path, COVER_A_REACH, graph)
    assert identities(result, "wiring-watched") == [("products/billing/logic.py",)]
    assert [item.reason for item in result.runs["wiring-watched"].incomplete] == [
        "rule request-path cannot tell: import 'products.billing.gone' did not resolve"
    ]


COVER_A_DIRECTORY = """
[rules.drives]
primitive = "edge"
kinds = ["drives"]
contexts = { include = ["test"] }
select = "src.package != dst.package"
identity = ["dst.module", "src.module"]
counted = true
growth_key = ["dst.module"]

[rules.wiring-watched]
primitive = "coverage"
covers = ["rules.drives.dst.path", "edge.kind == 'dotted-path' and edge.context == 'test': dst"]
inputs = { adapter = "turbo", task = "contract-check" }
identity = "entity"
growth_key = ["subject"]
"""


def directory_graph() -> GraphBuilder:
    """`products.billing.queries` is a directory without `__init__.py`: two files directly inside,
    a test and a package below it."""
    return (
        base_graph()
        .module(
            "core.tests.test_billing",
            "products.billing.queries.runner",
            "products.billing.queries.other",
            "products.billing.queries.tests.test_runner",
        )
        .package("products.billing.queries.nested")
    )


@pytest.mark.parametrize("kind", ["drives", "dotted-path"])
def test_a_directory_without_init_needs_each_file_directly_inside_it_watched(tmp_path: Path, kind: str) -> None:
    turbo_package(tmp_path, "products/billing", {"tasks": {"contract-check": {"inputs": ["queries/runner.py"]}}})
    graph = directory_graph().edge(
        kind, "core.tests.test_billing", "products.billing.queries", contexts=(Context.TEST,)
    )
    result = run(tmp_path, COVER_A_DIRECTORY, graph.graph)
    assert identities(result, "wiring-watched") == [("products/billing/queries/other.py",)]
    assert result.runs["wiring-watched"].incomplete == []


def test_a_destination_with_no_path_that_is_no_directory_is_incomplete(tmp_path: Path) -> None:
    turbo_package(tmp_path, "products/billing", {"tasks": {"contract-check": {"inputs": ["**"]}}})
    graph = directory_graph().edge(
        "dotted-path", "core.tests.test_billing", "nowhere.vanished", contexts=(Context.TEST,)
    )
    result = run(tmp_path, COVER_A_DIRECTORY, graph.graph)
    assert [item.reason for item in result.runs["wiring-watched"].incomplete] == [
        "cannot place 'nowhere.vanished' for coverage"
    ]


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
    turbo_package(tmp_path, "products/billing", {"tasks": {"contract-check": {"inputs": ["**"]}}})
    turbo_package(tmp_path, "products/alerts", {"tasks": {"contract-check": {"inputs": ["facade/**"]}}})
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
    assert "wiring-watched: strict" in second.levels["isolated"].failing["alerts"][0]


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
    builder = base_graph()
    for module in ("core.api", "core.views", "products.alerts.logic"):
        builder.edge("reference", module, "products.billing.models.Invoice", shape="write", detail="save")
    accepted = ("model-crossings", ("products.billing.models.Invoice", "core.views", "write(save)"))
    ledger = LedgerFile(schema=1, policy="", lines={accepted: 1}, exists=True)
    result = run(tmp_path, USE_SHAPE, builder.graph, ledger, EXCEPTIONS)
    table = result.runs["model-crossings"].findings
    # Each reader of a row's status (a row made, all rows, the rows of one status) agrees.
    made = [table[row].status for row in range(len(table))]
    assert sorted(made) == sorted(FindingStatus)
    assert table.statuses() == made
    assert {status: table.rows_with(status) for status in FindingStatus} == {
        status: [row for row, found in enumerate(made) if found is status] for status in FindingStatus
    }
    allowed = [f for f in table if f.status is FindingStatus.ALLOWED]
    assert [f.identity[1] for f in allowed] == ["core.api"]
    assert accepted in result.scan and not any(key[1][1] == "core.api" for key in result.scan)
    assert [e.index for e in result.exceptions.stale] == [1]
    assert [e.index for e in result.exceptions.expired] == [0]
    assert result.exit_code() == 1  # the stale entry fails; the expired one only reports


SETTING_ENTRIES = """
[rules.middleware]
primitive = "edge"
kinds = ["dotted-path"]
select = "edge.setting == 'MIDDLEWARE'"
deny = "true"
identity = ["dst.module"]
ledger = false
"""


def test_an_attr_its_producer_could_not_tell_reads_unknown_not_absent(tmp_path: Path) -> None:
    graph = (
        base_graph()
        .edge("dotted-path", "core.api", "products.billing.logic", attrs=(("setting", "MIDDLEWARE"),))
        .edge("dotted-path", "core.api", "products.billing.models", attrs=(("unknown", "setting"),))
        .edge("dotted-path", "core.api", "products.alerts.logic")
        .graph
    )
    run_ = run(tmp_path, SETTING_ENTRIES, graph).runs["middleware"]
    assert [f.identity for f in run_.findings] == [("products.billing.logic",)]
    assert [item.reason for item in run_.incomplete] == ["selection reads unknown edge.setting"]


STALE_NAMES = """
[fields.crosses]
type = "bool"
on = "symbol"

[[assign]]
match = ["core/api.py", "core/gone.py", "core/*.py"]
set = { tier = "core" }

[[assign]]
symbols = { names = ["products.billing.models.Invoice", "core.api.Gone"] }
set = { crosses = true }
"""


def test_an_assignment_that_names_no_file_or_symbol_is_incomplete_not_silent(tmp_path: Path) -> None:
    result = run(tmp_path, STALE_NAMES, base_graph().graph)
    assert [(item.key, item.reason) for item in result.runs["config:stale-name"].incomplete] == [
        (
            "core/gone.py",
            "core/gone.py is named by [[assign]] core/api.py, core/gone.py, core/*.py  (hoglint.toml) but no file has it",
        ),
        ("core.api.Gone", "core.api.Gone is named by [[assign]] symbols  (hoglint.toml) but no symbol has it"),
    ]


EXPECTED_VALUES = """
[rules.middleware]
primitive = "edge"
kinds = ["dotted-path"]
select = "edge.setting in ['MIDDLEWARE', 'SESSION_ENGINE']"
deny = "true"
expect = { setting = ["MIDDLEWARE", "SESSION_ENGINE"] }
identity = ["dst.module"]
ledger = false
"""


def test_a_value_a_rule_expects_that_no_edge_carries_is_a_stale_name(tmp_path: Path) -> None:
    graph = base_graph().edge("dotted-path", "core.api", "products.billing.logic", attrs=(("setting", "MIDDLEWARE"),))
    result = run(tmp_path, EXPECTED_VALUES, graph.graph)
    assert [(item.key, item.reason) for item in result.runs["config:stale-name"].incomplete] == [
        ("SESSION_ENGINE", "SESSION_ENGINE is expected by rules.middleware on edge.setting but no edge carries it")
    ]


@pytest.mark.parametrize(
    "entry, problem",
    [
        ('match = "core.api"', "match must be a table"),
        ('match = { "src.module" = { nested = 1 } }', "match value for 'src.module'"),
        ('match = { "src.module" = "core.api" }\nreview_by = "soon"', "review_by must be a date"),
        ('match = { "src.module" = "core.api" }\nreview_by = 5', "review_by must be a date"),
    ],
)
def test_malformed_exceptions_are_reported_not_raised(tmp_path: Path, entry: str, problem: str) -> None:
    text = (
        f'schema = 1\n[[exception]]\nrule = "model-crossings"\nreason = "r"\nowners = ["t"]\nscope = "prod"\n{entry}\n'
    )
    graph = base_graph().edge("reference", "core.api", "products.billing.models.Invoice", shape="write").graph
    result = run(tmp_path, USE_SHAPE, graph, LedgerFile(), text)
    assert any(problem in line for line in result.exceptions.problems)
    assert result.exit_code() == 1


def test_exception_match_values_render_like_identities(tmp_path: Path) -> None:
    rules = """
[rules.unexported]
primitive = "edge"
kinds = ["import"]
select = "src.package != dst.package and dst.tier == 'product'"
identity = ["src.module", "dst.exported"]
growth_key = ["src.module"]
"""
    text = 'schema = 1\n[[exception]]\nrule = "unexported"\nmatch = { "dst.exported" = false }\nreason = "r"\nowners = "t"\nscope = "prod"\n'
    graph = base_graph().edge("import", "core.api", "products.billing.logic.charge").graph
    result = run(tmp_path, ENTRYPOINT + rules, graph, LedgerFile(), text)
    assert [f.status for f in result.runs["unexported"].findings] == [FindingStatus.ALLOWED]
    assert result.exceptions.stale == []


def test_the_rule_classes_need_no_native_shell() -> None:
    code = "import sys, hoglint.rules.engine; print(sorted(m for m in sys.modules if m.startswith('hoglint.native')))"
    found = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert found.stdout.strip() == "[]"
