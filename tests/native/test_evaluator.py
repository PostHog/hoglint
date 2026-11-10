"""The native evaluator (src/link/rules) against the Python rule code, per edge, on one small repo:
hand-written rules for the corners where the meaning is irregular, and seeded generated ones."""

from __future__ import annotations

import gc
import random
import subprocess
import dataclasses
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from hoglint.config import CONFIG_FILE, Config, ConfigError, load_config
from hoglint.core.harness import Output, ProducerPlan, ProducerStep
from hoglint.labels import Labeler
from hoglint.ledger.errors import EngineError
from hoglint.model import Context, Edge, EntityId, Graph, Resolution, Site
from hoglint.native.plan import RulePlan
from hoglint.native.rules import NativeRuleContext
from hoglint.native.source import graph_source
from hoglint.plugins import REGISTRY, PackReader, PackSink, Refinement
from hoglint.rules import engine
from hoglint.rules.base import EdgeRule, Judgment, RuleContext, project
from hoglint.rules.engine import Engine, primitive_of
from hoglint.selectors import State

from native.require import native_module

SECTION = "flows"
PRODUCER = "flows.{}@1"
SLOTS = "products.billing.backend.facade.slots"


class Flows:
    """A test pack: the flow-refined shapes a def-use analysis writes, edges with several targets
    or with their own endpoint labels, an edge it hands over twice (its ordinal is its last
    row's), symbol facts, and signature slots as a facade pack writes them: one a core edge names
    too, one it does not, one in a private callable and one with no annotation."""

    name = SECTION
    allowed_keys = frozenset()
    edge_kinds = frozenset({"reference", "uses", "exposes"})

    def produce(self, section: Mapping[str, Any], reader: PackReader, sink: PackSink, root: Path) -> None:
        ProducerPlan(
            [
                ProducerStep(PRODUCER.format("flow"), frozenset({"reference", "uses"}), lambda: self.run(reader)),
                ProducerStep(PRODUCER.format("slots"), frozenset({"exposes"}), lambda: self.slots(reader)),
            ]
        ).run(sink)
        sink.add_symbols({"products.billing.backend.models.Invoice": {"decl": "class", "database": "billing"}})

    @staticmethod
    def slots(reader: PackReader) -> list[Output]:
        out: list[Output] = []
        invoice = EntityId("python", "products.billing.backend.models", "Invoice")
        for edge in reader.edges_to("exposes", [invoice]):
            if edge.site.module != SLOTS:
                continue
            producer = PRODUCER.format("slots")
            out.append(dataclasses.replace(edge, producer=producer, handle=-1))
            named = edge.attr("callable")
            for callable_name, slot, annotation in (
                (named, "param:copy", ""),
                (f"_{named}", "param:copy", ""),
                (named, "param:untyped", "missing"),
            ):
                attrs = (
                    ("callable", callable_name),
                    ("slot", slot),
                    *((("annotation", annotation),) if annotation else ()),
                )
                out.append(dataclasses.replace(edge, producer=producer, attrs=attrs, handle=-1))
        return out

    @staticmethod
    def run(reader: PackReader) -> list[Output]:
        out: list[Output] = []
        for edge in reader.edges_spelled("reference", ["Invoice"]):
            if edge.site.module == "core.a":
                out.append(Refinement(edge.handle, "instance-many", "filter", (("flow_shape", "write(delete)"),)))
            elif edge.site.module == "core.b":
                out.append(Refinement(edge.handle, None, None, (("flow_shape", "write(save)"),)))
            site = edge.site
            uses = Flows.uses(site, edge.surface)
            out.append(uses)
            if site.module == "core.a":
                out.extend([Flows.uses(site, edge.surface), uses])
        return out

    @staticmethod
    def uses(site: Site, surface: str) -> Edge:
        return Edge(
            "uses",
            site,
            surface,
            (
                EntityId("python", "products.billing.backend.models", "Invoice"),
                EntityId("python", "products.alerts.backend.facade.api", "notify"),
            ),
            Resolution.RESOLVED,
            frozenset({Context.PROD}),
            PRODUCER.format("flow"),
            via=(
                EntityId("python", "products.alerts.backend.facade", "notify"),
                EntityId("python", "nowhere.at", "all"),
            ),
            attrs=(("src.tier", "product"), ("dst.zone", "facade")) if site.module == "core.b" else (),
        )


if SECTION not in REGISTRY.packs:
    REGISTRY.add_pack(Flows())


CONFIG = """
schema = 1

[discovery]
source_roots = ["."]
first_party = ["core", "products", "packages"]

[vocabulary]
sections = ["flows"]
edge_kinds = ["uses"]
use_shapes = ["write", "instance-many"]

[flows]

[fields]
package = { type = "id", required_on = ["products/**", "core/**"] }
tier = { type = "enum", values = ["core", "product", "leaf"], required_on = ["products/**"] }
zone = { type = "zone" }
owners = { type = "set" }
testcode = { type = "bool" }
stage = { type = "enum", values = ["proto"], required_on = "name == 'legacy'" }
database = { type = "string", on = "symbol", required_on = "kind == 'model'" }
silo = { type = "enum", values = ["cell"], on = "symbol" }

[[assign]]
match = "products/{product}/**"
set = { package = "{product}", tier = "product", owners = "team-{product}" }

[[assign]]
match = "core/**"
set = { package = "core", tier = "core", owners = ["team-core", "team-devex"] }

[[assign]]
match = "core/legacy.py"
set = { stage = "proto" }

[[assign]]
match = "packages/{pkg}/**"
set = { package = "{pkg}", tier = "leaf" }

[[assign]]
match = ["**/tests/**", "**/test_*.py"]
set = { testcode = true }

[[assign]]
symbols = { decorated_by = "products.billing.backend.models.cell" }
set = { silo = "cell" }

[zones]
within = "products/{product}"
default = "internal"
backend = { paths = ["backend/**"] }
facade = { parent = "backend", paths = ["backend/facade/**"] }
contracts = { parent = "facade", paths = ["backend/facade/contracts.py"] }
presentation = { parent = "backend", paths = ["backend/presentation/**"] }
models = { parent = "backend", paths = ["backend/models.py"] }
shared = { paths = ["backend/shared.py"] }
special = { paths = ["backend/shared.py"], override = true }

[kinds]
model = { subclass_of = "products.billing.backend.models.Base" }

[shapes.contract]
data = ["frozen-dataclass", "enum"]
behavior = { kind = "abc", where = "tier == 'core'" }
deny = ["model"]

[exports.facade]
within = "products/{product}"
paths = ["backend/facade/**"]
shape = "contract"

[exports.adapter]
within = "products/{product}"
paths = ["backend/presentation/**"]
subclass_of = ["products.billing.backend.presentation.mixins.Mixin"]
audience = "src.tier == 'core'"
shape = "contract"

[registries.routes]
loader = "core.loader.load"
module = "products/{product}/backend/routes.py"
attribute = "ROUTES"
lifecycle = "setup"
"""

SOURCES = {
    "CODEOWNERS": "products/billing/ @team-billing\nproducts/alerts/ @team-alerts\ncore/ @team-core\n",
    "policy/classified.toml": '[[entry]]\nidentity = ["core", "billing"]\n\n[[entry]]\nidentity = ["nobody", "nowhere"]\n',
    "core/__init__.py": "",
    "core/loader.py": "def load():\n    pass\n",
    "core/legacy.py": "import django.db\nfrom products.billing.backend.facade import api\n",
    "core/a.py": (
        "from products.billing.backend.models import Invoice\nfrom products.billing.backend import missing\n\n\n"
        "def f():\n    Invoice.objects.filter()\n"
    ),
    "core/b.py": "from products.billing.backend.models import Invoice\n\n\nclass B:\n    def g(self):\n        Invoice.objects.create()\n",
    "core/tests/test_a.py": "from products.alerts.backend.logic import run\nimport core.a\n",
    "packages/leaf/__init__.py": "from core.a import f\n",
    "products/__init__.py": "",
    "products/billing/__init__.py": "",
    "products/billing/backend/__init__.py": "",
    "products/billing/backend/models.py": (
        "def cell(cls):\n    return cls\n\n\nclass Base:\n    pass\n\n\n@cell\nclass Invoice(Base):\n    pass\n\n\n"
        "class Loose(Unknown):\n    pass\n"
    ),
    "products/billing/backend/shared.py": "from products.alerts.backend.facade.api import notify\n",
    "products/billing/backend/routes.py": "from products.billing.backend.models import Invoice\n",
    "products/billing/backend/facade/__init__.py": "",
    "products/billing/backend/facade/api.py": "from products.alerts.backend.logic import run\n\n\ndef charge():\n    run()\n",
    "products/billing/backend/facade/contracts.py": "import django.db.models\n",
    "products/billing/backend/facade/slots.py": (
        "import abc\nimport dataclasses\nimport enum\nfrom typing import Any\n\n"
        "from products.billing.backend import nowhere\nfrom products.billing.backend.models import Invoice, Loose\n\n\n"
        "@dataclasses.dataclass(frozen=True)\nclass Note:\n    invoice: Invoice\n    text: str\n\n\n"
        "@dataclasses.dataclass\nclass Draft:\n    note: Note\n\n\n"
        "class Level(enum.Enum):\n    LOW = 1\n\n\nclass Port(abc.ABC):\n    pass\n\n\n"
        "def charge(invoice: Invoice, note: Any) -> dict[str, Loose]:\n    pass\n\n\n"
        "def _hidden(invoice: Invoice) -> None:\n    pass\n\n\n"
        "class Desk:\n    def __call__(self, invoice: Invoice) -> Invoice:\n        pass\n\n"
        "    def _inner(self, invoice: Invoice) -> None:\n        pass\n"
    ),
    "products/billing/backend/presentation/__init__.py": "",
    "products/billing/backend/presentation/mixins.py": "class Mixin:\n    pass\n",
    "products/billing/backend/presentation/views.py": (
        "from products.billing.backend.presentation.mixins import Mixin\n\n\nclass View(Mixin):\n    pass\n\n\n"
        "class Plain:\n    pass\n"
    ),
    "products/alerts/__init__.py": "",
    "products/alerts/backend/__init__.py": "",
    "products/alerts/backend/logic.py": (
        "from products.billing.backend.presentation.views import View, Plain\nfrom products.billing.backend import facade\n\n\n"
        "def run():\n    facade.api.charge()\n"
    ),
    "products/alerts/backend/facade/__init__.py": "from products.alerts.backend.facade.api import notify\n",
    "products/alerts/jobs.py": (
        "from products.billing.backend.facade.api import charge\nfrom products.alerts.backend.facade import notify\n"
    ),
    "products/alerts/backend/facade/api.py": "from core.legacy import api\n\n\ndef notify():\n    pass\n",
    # A product whose directory and module name are not ASCII: `within` captures it.
    "products/é/__init__.py": "",
    "products/é/backend/__init__.py": "",
    "products/é/backend/models.py": "class Café:\n    pass\n",
    "products/é/jobs.py": "from products.é.backend.models import Café\nfrom products.billing.backend import facade\n",
}

JUDGMENTS = list(Judgment)


class World:
    """The fixture repo linked once, and a context per rule config over it."""

    def __init__(self, root: Path) -> None:
        native_module()
        self.root = root
        repo = root / "repo"
        for path, text in SOURCES.items():
            (repo / path).parent.mkdir(parents=True, exist_ok=True)
            (repo / path).write_text(text)
        (repo / CONFIG_FILE).write_text(CONFIG)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        self.repo = repo
        work = root / "work"
        work.mkdir()
        self.graph: Graph = graph_source("native", work).build(repo, load_config(repo, repo / CONFIG_FILE))
        gc.enable()

    def config(self, rules: str, base: str = CONFIG) -> Config:
        path = self.root / "config" / CONFIG_FILE
        path.parent.mkdir(exist_ok=True)
        path.write_text(base + rules)
        return load_config(self.repo, path)

    def contexts(self, config: Config) -> tuple[NativeRuleContext, RuleContext]:
        labeler: Labeler = Engine(config, self.graph, self.repo).labeler
        labeler.find_conflicts()
        native = NativeRuleContext(config, self.graph, labeler, self.repo)
        return native, RuleContext(config, self.graph, labeler, self.repo)


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    return World(tmp_path_factory.mktemp("evaluator"))


def python_rows(ctx: RuleContext, rule: EdgeRule) -> Counter[tuple[int, int, str, tuple[str, ...]]]:
    """(edge, destination, judgment, identity) of every destination `EdgeRule.decide` does not
    judge clean, by the Python rule code alone; only a violation's row and a signature slot of
    unknown type carry their identity."""
    out: Counter[tuple[int, int, str, tuple[str, ...]]] = Counter()
    for edge in ctx.graph.edges:
        if not rule.sees(edge):
            continue
        for target, env in enumerate(rule.envs_of(edge)):
            judgment = rule.decide(env)
            if judgment is Judgment.CLEAN:
                continue
            found = judgment in (Judgment.VIOLATION, Judgment.UNKNOWN_TYPE)
            identity = project(rule.spec.identity, env.resolve) if found else ()
            out[(edge.handle, target, judgment.value, identity)] += 1
    return out


def run_rows(run: Any) -> list[tuple[Any, ...]]:
    findings = [
        (f.identity, f.message, tuple(e.handle for e in f.witnesses), f.ledgerable, f.unknown) for f in run.findings
    ]
    incomplete = [(i.key, i.reason, i.subjects, i.edge.handle if i.edge is not None else None) for i in run.incomplete]
    return [*findings, *incomplete]


def differences(world: World, rules: str, base: str = CONFIG) -> list[str]:
    """Where the evaluator and the Python rule code differ for every rule of `rules`."""
    config = world.config(rules, base)
    native, python = world.contexts(config)
    out = []
    for spec in config.rules.values():
        mode = native.plan.mode(spec)
        rule = primitive_of(spec)(native, spec)
        index = native.plan.index[spec.id]
        # An inventory's rows keep the first destination of each identity; its run is compared.
        if mode == "judge" and spec.primitive != "inventory":
            assert isinstance(rule, EdgeRule)
            ours = Counter(
                (edge, target, JUDGMENTS[code].value, tuple(identity))
                for edge, target, code, identity, _, _ in native.evaluator.judge(index)
            )
            theirs = python_rows(python, rule)
            if ours != theirs:
                out.append(f"{spec.id}: rust only {sorted(ours - theirs)}, python only {sorted(theirs - ours)}")
        native.results[spec.id] = primitive_of(spec)(native, spec).evaluate()
        python.results[spec.id] = primitive_of(spec)(python, spec).evaluate()
        if run_rows(native.results[spec.id]) != run_rows(python.results[spec.id]):
            out.append(f"{spec.id}: the {mode} run differs from the Python run")
    return out


def rule_table(name: str, fields: dict[str, Any]) -> str:
    lines = [f"\n[rules.{name}]"]
    for key, value in fields.items():
        if isinstance(value, dict):
            inner = ", ".join(f"{k} = {render_toml(v)}" for k, v in value.items())
            lines.append(f"{key} = {{ {inner} }}")
        else:
            lines.append(f"{key} = {render_toml(value)}")
    return "\n".join(lines) + "\n"


def render_toml(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(render_toml(item) for item in value) + "]"
    if isinstance(value, str) and "'" in value:
        return '"' + value.replace('"', '\\"') + '"'
    return f"'{value}'" if isinstance(value, str) else str(value)


EDGE = {
    "primitive": "edge",
    "kinds": ["import", "reference", "uses"],
    "identity": ["src.module", "dst.symbol"],
    "ledger": False,
}

SIGNATURE = {
    "primitive": "signature",
    "select": "src.zone == 'facade'",
    "deny": "dst.kind == 'model' or dst.symbol == 'builtins.dict'",
    "unknown_types": ["typing.Any"],
    "identity": ["src.symbol", "edge.slot", "dst.symbol"],
    "ledger": False,
}

CORNERS = {
    "zone-chain-against-a-scalar": {**EDGE, "deny": "dst.zone == 'backend'", "identity": ["dst.zone", "src.zone"]},
    "zone-against-a-zone": {**EDGE, "deny": "dst.zone == src.zone and dst.zone in ['facade']"},
    "set-against-a-member": {**EDGE, "deny": "src.owners == 'team-core' and src.owners != dst.owners"},
    "list-literal-against-a-scalar": {**EDGE, "deny": "dst.package == ['billing'] or dst.package in ['billing']"},
    "set-read-as-text": {**EDGE, "deny": "src.owners startswith 'team-core,' or dst.zone == src.owners"},
    "non-ascii-capture-under-within": {
        **EDGE,
        "select": "src.package == 'é' and dst.package == 'é'",
        "deny": "dst.zone == 'backend'",
        "identity": ["src.module", "dst.zone", "dst.package"],
    },
    "bool-rendering": {
        **EDGE,
        "deny": "src.testcode == true",
        "identity": ["src.testcode", "dst.exported", "src.owners"],
    },
    "read-endpoint-order": {
        **EDGE,
        "endpoint": "surface",
        "select": "dst.tier == 'product'",
        "identity": ["dst.symbol", "dst.registry", "dst.lifecycle", "dst.external", "dst.name", "dst.kind", "dst.decl"],
    },
    "unresolved-symbol-answers": {
        **EDGE,
        "on_unresolved": "finding",
        "identity": ["dst.symbol", "dst.module", "dst.tier", "dst.package"],
    },
    "endpoint-attrs": {
        **EDGE,
        "kinds": ["uses"],
        "select": "src.tier == 'product'",
        "identity": ["dst.zone", "src.tier"],
    },
    "source-path-is-the-site-path": {
        **EDGE,
        "deny": "src.path startswith 'core/'",
        "identity": ["src.path", "src.scope"],
    },
    "leaf-zones-with-one-override": {**EDGE, "from": "src.zone == 'special'", "identity": ["src.zone"]},
    "default-zone-under-within": {**EDGE, "from": "src.zone == 'internal'", "identity": ["src.zone", "dst.zone"]},
    "symbol-fields-and-domains": {
        **EDGE,
        "endpoint": "origin",
        "identity": ["dst.database", "dst.silo", "dst.stage", "src.stage"],
    },
    "export-by-class-and-audience": {
        "primitive": "entrypoint",
        "kinds": ["import"],
        "select": "src.package != dst.package",
        "identity": ["dst.symbol"],
        "ledger": False,
    },
    "contexts-and-ordinals": {
        **EDGE,
        "deny": "edge.context subset_of ['prod', 'test']",
        "identity": ["edge.context", "edge.ordinal", "edge.label"],
    },
    "via-destinations": {**EDGE, "endpoint": "via", "identity": ["dst.symbol", "dst.module"]},
    "flow-shape-use-shape": {
        "primitive": "use-shape",
        "flow": "def-use",
        "select": "edge.shape == 'write'",
        "deny_shapes": ["write"],
        "identity": ["src.module"],
        "ledger": False,
    },
    "signature-slots": SIGNATURE,
    "signature-unknown-as-incomplete": {**SIGNATURE, "on_unknown": "incomplete"},
    "signature-unknown-blocks": {
        **SIGNATURE,
        "on_unknown": "block",
        "on_unresolved": "finding",
        "ledger": True,
        "growth_key": ["dst.symbol"],
    },
    "export-shapes": {
        "primitive": "export",
        "identity": ["src.module", "dst.symbol", "edge.label", "dst.package", "src.tier", "edge.surface"],
        "ledger": False,
    },
    "export-unknowns-as-incomplete": {
        "primitive": "export",
        "exports": ["facade"],
        "on_unknown": "incomplete",
        "on_unresolved": "incomplete",
        "unknown_types": ["typing.Any"],
        "contexts": {"exclude": []},
        "identity": ["dst.symbol"],
        "ledger": False,
    },
    "layout-on-modules": {
        "primitive": "layout",
        "select": "tier == 'product' and zone != 'facade'",
        "require": "package in ['billing', 'core'] or subject startswith 'products.alerts'",
        "identity": ["subject", "package", "zone"],
        "ledger": False,
    },
    "layout-on-symbols": {
        "primitive": "layout",
        "on": "symbol",
        "select": "kind == 'model' or stage == 'proto'",
        "require": "database == 'billing'",
        "identity": ["subject", "database", "module"],
        "ledger": False,
    },
    "layout-roots-and-registries": {
        "primitive": "layout",
        "within": "products/{product}",
        "files": ["backend/__init__.py"],
        "registry": ["routes"],
        "identity": ["subject"],
        "ledger": False,
    },
    "inventory-exactly-one": {
        "primitive": "inventory",
        "on": "module",
        "exactly_one": "owners",
        "select": "tier != 'leaf' and stage != none",
        "identity": ["subject", "owners"],
        "ledger": False,
    },
    "inventory-classify": {
        "primitive": "inventory",
        "classify": "policy/classified.toml",
        "kinds": ["import", "uses"],
        "select": "src.package != dst.package",
        "on_unresolved": "finding",
        "identity": ["src.package", "dst.package"],
        "ledger": False,
    },
    "cycle-between-packages": {
        "primitive": "cycle",
        "kinds": ["import", "reference"],
        "identity": ["src.package", "dst.package"],
        "ledger": False,
    },
    "reach-from-contracts": {
        "primitive": "reach",
        "kinds": ["import"],
        "from": "src.zone == 'contracts' or src.package == 'core'",
        "deny": "dst.external == 'django'",
        "on_unresolved": "incomplete",
        "identity": ["src.module", "dst.module"],
        "ledger": False,
    },
}


@pytest.mark.parametrize("name", sorted(CORNERS))
def test_the_evaluator_judges_each_irregular_corner_as_the_rule_code_does(world: World, name: str) -> None:
    assert differences(world, rule_table(name, CORNERS[name])) == []


CONFLICTS = {
    "module-assignments": CONFIG.replace(
        '[[assign]]\nmatch = "core/legacy.py"',
        '[[assign]]\nmatch = "core/legacy.py"\nset = { tier = "leaf" }\n\n[[assign]]\nmatch = "core/legacy.py"',
    ),
    "leaf-zones": CONFIG.replace(
        'special = { paths = ["backend/shared.py"], override = true }', 'special = { paths = ["backend/shared.py"] }'
    ),
    "kinds": CONFIG.replace(
        'model = { subclass_of = "products.billing.backend.models.Base" }',
        'model = { subclass_of = "products.billing.backend.models.Base" }\nbase = { subclass_of = "products.billing.backend.models.Base" }',
    ),
    "symbol-assignments": CONFIG.replace(
        'values = ["cell"], on = "symbol"', 'values = ["cell", "control"], on = "symbol"'
    )
    + '\n[[assign]]\nsymbols = { decorated_by = "products.billing.backend.models.cell" }\nset = { silo = "control" }\n',
    "symbol-field-by-module-assignments": CONFIG.replace(
        'values = ["cell"], on = "symbol"', 'values = ["cell", "control"], on = "symbol"'
    )
    + '\n[[assign]]\nmatch = "core/**"\nset = { silo = "cell" }\n\n[[assign]]\nmatch = "core/a.py"\nset = { silo = "control" }\n',
}


@pytest.mark.parametrize("name", sorted(CONFLICTS))
def test_a_label_set_two_ways_stops_the_native_run_before_any_rule(world: World, name: str) -> None:
    config = world.config("", CONFLICTS[name])
    with pytest.raises(ConfigError):
        Engine(config, world.graph, world.repo).run_rules()


PROVIDED = (
    CONFIG.replace("[fields]\n", '[fields]\nteam = { type = "set" }\n')
    + '\n[providers.codeowners]\nuse = "codeowners"\nfile = "CODEOWNERS"\nsets = ["team"]\n\n[roles]\ngroup = "team"\n'
)
TEAM_CYCLE = {"primitive": "cycle", "view": "packages", "identity": ["src.module", "dst.module"], "ledger": False}


def test_a_cycle_over_a_group_a_provider_sets_finds_its_pairs_natively(world: World) -> None:
    rules = rule_table("teams", TEAM_CYCLE)
    assert differences(world, rules, PROVIDED) == []
    config = world.config(rules, PROVIDED)
    native, _ = world.contexts(config)
    run = primitive_of(config.rules["teams"])(native, config.rules["teams"]).evaluate()
    assert {finding.message.split(" closes")[0] for finding in run.findings} >= {
        "@team-alerts -> @team-billing",
        "@team-billing -> @team-alerts",
    }


def test_a_provider_field_read_without_its_column_stops_the_evaluator(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = world.config(rule_table("teams", TEAM_CYCLE), PROVIDED)
    native, _ = world.contexts(config)
    monkeypatch.setattr(RulePlan, "names_read", lambda self: set())
    with pytest.raises(EngineError, match="field team, which a provider sets"):
        assert native.evaluator


def test_a_plan_with_its_modules_in_another_order_stops_the_evaluator(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = world.config(rule_table("teams", TEAM_CYCLE), PROVIDED)
    native, _ = world.contexts(config)
    monkeypatch.setattr(RulePlan, "modules", lambda self: list(reversed(self.graph.modules)))
    with pytest.raises(EngineError, match="modules in another order"):
        assert native.evaluator


def test_a_capture_over_a_directory_that_is_not_ascii_gives_one_zone_in_both_engines(world: World) -> None:
    config = world.config(rule_table("ascii", CORNERS["non-ascii-capture-under-within"]))
    native, python = world.contexts(config)
    spec = config.rules["ascii"]
    found = run_rows(primitive_of(spec)(native, spec).evaluate())
    assert found == run_rows(primitive_of(spec)(python, spec).evaluate())
    assert {identity for identity, *_ in found} == {("products.é.jobs", "models", "é")}


def test_a_set_read_as_text_is_its_identity_column_in_both_engines(world: World) -> None:
    config = world.config(rule_table("text", CORNERS["set-read-as-text"]))
    native, python = world.contexts(config)
    spec = config.rules["text"]
    found = run_rows(primitive_of(spec)(native, spec).evaluate())
    assert found == run_rows(primitive_of(spec)(python, spec).evaluate())
    assert {identity[0] for identity, *_ in found} >= {"core.a", "core.legacy"}


class Refusing:
    """An evaluator that refuses every call, as one that meets a limit only it has would."""

    def __getattr__(self, name: str) -> Any:
        def refuse(*args: Any) -> Any:
            raise ValueError(f"{name} refused")

        return refuse


@pytest.mark.parametrize("primitive", ["edge", "use-shape", "cycle", "reach", "conflicts"])
def test_an_evaluator_error_is_an_engine_error(world: World, primitive: str) -> None:
    spec = {**CORNERS["reach-from-contracts"], "primitive": primitive} if primitive == "reach" else EDGE
    spec = CORNERS["flow-shape-use-shape"] if primitive == "use-shape" else spec
    spec = CORNERS["cycle-between-packages"] if primitive == "cycle" else spec
    config = world.config(rule_table("refused", spec))
    native, _ = world.contexts(config)
    native.__dict__["evaluator"] = Refusing()
    with pytest.raises(EngineError, match="refused"):
        if primitive == "conflicts":
            native.find_conflicts()
        else:
            primitive_of(config.rules["refused"])(native, config.rules["refused"]).evaluate()


def test_a_use_shape_rule_on_the_flow_shape_keeps_edges_whose_link_shape_differs(world: World) -> None:
    config = world.config(rule_table("flows", CORNERS["flow-shape-use-shape"]))
    native, _ = world.contexts(config)
    spec = config.rules["flows"]
    run = primitive_of(spec)(native, spec).evaluate()
    assert sorted(finding.identity for finding in run.findings) == [("core.a",), ("core.b",)]


REFS = [
    "src.package",
    "dst.package",
    "src.tier",
    "dst.tier",
    "src.zone",
    "dst.zone",
    "src.owners",
    "dst.owners",
    "src.testcode",
    "dst.testcode",
    "dst.stage",
    "dst.database",
    "dst.silo",
    "dst.kind",
    "src.kind",
    "dst.decl",
    "dst.exported",
    "src.exported",
    "dst.registry",
    "dst.lifecycle",
    "src.module",
    "dst.module",
    "dst.symbol",
    "src.symbol",
    "dst.name",
    "src.path",
    "src.scope",
    "dst.external",
    "src.subject",
    "dst.nothing",
    "edge.kind",
    "edge.shape",
    "edge.label",
    "edge.use",
    "edge.resolution",
    "edge.surface",
    "edge.producer",
    "edge.context",
    "edge.ordinal",
    "edge.flow_shape",
    "edge.slot",
    "edge.callable",
    "src.context",
]
ENTITY_REFS = [
    "package",
    "tier",
    "zone",
    "owners",
    "testcode",
    "stage",
    "database",
    "silo",
    "subject",
    "module",
    "symbol",
    "path",
    "name",
    "kind",
    "decl",
    "registry",
    "lifecycle",
]
LITERALS = [
    "'billing'",
    "'alerts'",
    "'core'",
    "'product'",
    "'leaf'",
    "'facade'",
    "'backend'",
    "'presentation'",
    "'internal'",
    "'team-core'",
    "'team-billing'",
    "'import'",
    "'reference'",
    "'exposes'",
    "'returns'",
    "'prod'",
    "'test'",
    "'write'",
    "'model'",
    "'cell'",
    "'products'",
    "'django'",
    "'0'",
    "true",
    "false",
    "none",
    "['billing', 'core']",
    "['facade', 'backend']",
]
OPS = ["==", "!=", "in", "not in", "startswith", "subset_of"]


def atom(rng: random.Random, refs: list[str]) -> str:
    left = rng.choice(refs)
    if rng.random() < 0.15:
        return left
    right = rng.choice(refs) if rng.random() < 0.3 else rng.choice(LITERALS)
    return f"{left} {rng.choice(OPS)} {right}"


def selector(rng: random.Random, refs: list[str], depth: int = 2) -> str:
    if depth == 0 or rng.random() < 0.4:
        return atom(rng, refs)
    joiner = rng.choice([" and ", " or "])
    parts = [selector(rng, refs, depth - 1) for _ in range(rng.randint(2, 3))]
    text = "(" + joiner.join(parts) + ")"
    return f"not {text}" if rng.random() < 0.2 else text


def generated(rng: random.Random, refs: list[str] = REFS) -> dict[str, Any]:
    primitive = rng.choice(
        ["edge", "edge", "entrypoint", "use-shape", "cycle", "signature", "export", "coverage", "entities"]
    )
    if primitive == "entities":
        table: dict[str, Any] = {
            "primitive": rng.choice(["layout", "inventory"]),
            "identity": rng.sample(ENTITY_REFS, rng.randint(1, 2)),
            "ledger": False,
        }
        if table["primitive"] == "inventory":
            table["exactly_one"] = rng.choice(["owners", "tier"])
        table["on"] = "symbol" if rng.random() < 0.4 else "module"
        if rng.random() < 0.8:
            table["select"] = selector(rng, ENTITY_REFS)
        if table["primitive"] == "layout":
            table["require"] = selector(rng, ENTITY_REFS)
        return table
    if primitive == "coverage":
        return {
            "primitive": "coverage",
            "covers": [f"{selector(rng, refs)}: {rng.choice(['src', 'dst'])}" for _ in range(rng.randint(1, 2))],
            "inputs": {"adapter": "globs", "paths": [rng.choice(["core/**", "products/**"])]},
            "identity": rng.sample(["subject", "package", "tier", "zone", "path"], rng.randint(1, 2)),
            "ledger": False,
        }
    if primitive == "export":
        return {
            "primitive": "export",
            "exports": rng.sample(["facade", "adapter"], rng.randint(1, 2)),
            "on_unknown": rng.choice(["finding", "block", "incomplete"]),
            "on_unresolved": rng.choice(["finding", "block", "incomplete"]),
            "unknown_types": rng.sample(["typing.Any", "builtins.dict", "products.billing.backend.models.Loose"], 1),
            "identity": rng.sample([*refs, "edge.label", "edge.kind"], rng.randint(1, 3)),
            "ledger": False,
            **({"contexts": {"exclude": ["test"]}} if rng.random() < 0.3 else {}),
        }
    spec: dict[str, Any] = {
        "primitive": primitive,
        "kinds": rng.sample(["import", "reference", "uses", "declares", "inherit"], rng.randint(1, 3)),
        "identity": rng.sample(refs, rng.randint(1, 3)),
        "ledger": False,
    }
    if primitive in ("edge", "entrypoint"):
        spec["endpoint"] = rng.choice(["origin", "origin", "surface", "via"])
    if primitive in ("edge", "entrypoint", "signature"):
        spec["on_unresolved"] = rng.choice(["finding", "block", "incomplete"])
    if primitive == "signature":
        spec["kinds"] = ["exposes"] if rng.random() < 0.8 else [*spec["kinds"], "exposes"]
        spec["on_unknown"] = rng.choice(["finding", "block", "incomplete"])
        spec["unknown_types"] = rng.sample(["typing.Any", "builtins.dict", "products.billing.backend.models.Loose"], 2)
    predicates = ("deny", "allow", "unless") if primitive in ("edge", "entrypoint") else ("deny", "unless")
    for key in ("from", "select", *predicates):
        if rng.random() < 0.45 and (primitive in ("edge", "entrypoint", "signature") or key in ("from", "select")):
            spec[key] = selector(rng, refs)
    if primitive == "use-shape":
        spec["deny_shapes"] = ["write"]
        if rng.random() < 0.5:
            spec["flow"] = "def-use"
    if rng.random() < 0.3:
        spec["contexts"] = {"exclude": ["test"]}
    return spec


def test_generated_rules_are_judged_the_same_in_both(world: World) -> None:
    rng = random.Random(20261110)
    judged = 0
    failures = []
    while judged < 60:
        rules = rule_table("generated", generated(rng))
        try:
            world.config(rules)
        except ConfigError:
            continue
        judged += 1
        failures.extend(f"{found}\n{rules}" for found in differences(world, rules))
    assert failures == []


TIERS = ["core", "product", "leaf"]
PATHS = ["core/legacy.py", "core/**", "products/billing/**", "products/alerts/**", "packages/**", "**/facade/**"]
FILES = ["core/legacy.py", "core/a.py", "products/alerts/backend/facade/api.py", "products/billing/backend/models.py"]
SILO = 'values = ["cell"], on = "symbol"'
TWO_SILOS = 'values = ["cell", "control"], on = "symbol"'


class Conflicting:
    """A config that sets labels two ways, built from the fixture's: each shape adds sources that
    may disagree on some module or symbol. Some are found before any rule runs, the rest where a
    rule reads them."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.base = CONFIG
        self.added: list[str] = []
        # Fields whose conflicts are met only where a rule reads them, which rules read more often.
        self.lazy: list[str] = []

    def assign(self, match: str, sets: str, extra: str = "") -> None:
        self.added.append(f'\n[[assign]]\nmatch = "{match}"\n{extra}set = {{ {sets} }}\n')

    def module_field(self) -> None:
        override = "override = true\n" if self.rng.random() < 0.2 else ""
        self.assign(self.rng.choice(PATHS), f'tier = "{self.rng.choice(TIERS)}"', override)

    def symbol_field_by_modules(self) -> None:
        self.base = self.base.replace(SILO, TWO_SILOS)
        for silo in ("cell", "control"):
            self.assign(self.rng.choice(PATHS), f'silo = "{silo}"')

    def zone_by_assignment(self) -> None:
        zone = self.rng.choice(["backend", "facade", "internal"])
        self.assign(self.rng.choice(["products/billing/backend/**", "products/**", "core/**"]), f'zone = "{zone}"')

    def leaf_zones(self) -> None:
        if self.rng.random() < 0.5:
            self.base = self.base.replace(
                'special = { paths = ["backend/shared.py"], override = true }',
                'special = { paths = ["backend/shared.py"] }',
            )
        else:
            self.base = self.base.replace("[kinds]\n", 'extra = { paths = ["backend/**"] }\n\n[kinds]\n')

    def kinds(self) -> None:
        test = self.rng.choice(
            [
                'subclass_of = "products.billing.backend.models.Base"',
                'decorated_by = "products.billing.backend.models.cell"',
            ]
        )
        self.base = self.base.replace("[kinds]\n", f"[kinds]\nother = {{ {test} }}\n")

    def symbol_assignments(self) -> None:
        self.base = self.base.replace(SILO, TWO_SILOS)
        match = f'match = "{self.rng.choice(FILES)}"\n' if self.rng.random() < 0.5 else ""
        self.added.append(
            f'\n[[assign]]\n{match}symbols = {{ decorated_by = "products.billing.backend.models.cell" }}\nset = {{ silo = "control" }}\n'
        )

    def symbols_of_a_file(self) -> None:
        self.base = self.base.replace(SILO, TWO_SILOS)
        path = self.rng.choice(FILES)
        for silo in ("cell", "control"):
            self.added.append(f'\n[[assign]]\nmatch = "{path}"\nsymbols = {{}}\nset = {{ silo = "{silo}" }}\n')
        self.lazy += ["dst.silo", "src.silo"]

    def provider(self) -> None:
        self.base = self.base.replace("[fields]\n", '[fields]\nlead = { type = "string" }\n')
        self.added.append('\n[providers.codeowners]\nuse = "codeowners"\nfile = "CODEOWNERS"\nsets = ["lead"]\n')
        self.assign(self.rng.choice(PATHS), 'lead = "someone"')
        self.lazy += ["src.lead", "dst.lead"]

    def config(self) -> str:
        shapes = [
            self.module_field,
            self.symbol_field_by_modules,
            self.zone_by_assignment,
            self.leaf_zones,
            self.kinds,
            self.symbol_assignments,
            self.symbols_of_a_file,
            self.provider,
        ]
        # The shapes met only where read are drawn more often: they need a rule that reads them.
        shapes += [self.symbols_of_a_file, self.provider] * 2
        for shape in dict.fromkeys(self.rng.sample(shapes, self.rng.randint(1, 2))):
            shape()
        return self.base + "".join(self.added)

    def rules(self, world: World, base: str) -> str:
        """One to three generated rules that load over `base`."""
        refs = REFS + self.lazy * 8
        out = ""
        made = 0
        wanted = self.rng.randint(1, 3)
        while made < wanted:
            rule = rule_table(f"generated{made}", generated(self.rng, refs))
            try:
                world.config(out + rule, base)
            except ConfigError:
                continue
            out += rule
            made += 1
        return out


def outcome(world: World, config: Config) -> list[str]:
    """The problems of the config error a whole run stops with, or none."""
    try:
        Engine(config, world.graph, world.repo).run_rules()
    except ConfigError as error:
        return sorted(error.problems)
    return []


def test_a_label_set_two_ways_stops_both_engines_alike(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    rng = random.Random(1110)
    stopped = judged = 0
    failures = []
    while judged < 150:
        conflicting = Conflicting(rng)
        text = conflicting.config()
        try:
            world.config("", text)
        except ConfigError:
            continue
        text += conflicting.rules(world, text)
        config = world.config("", text)
        judged += 1
        native = outcome(world, config)
        with monkeypatch.context() as patched:
            patched.setattr(engine, "context_class", lambda index: RuleContext)
            python = outcome(world, config)
        stopped += bool(python)
        if native != python:
            failures.append(f"native {native}\npython {python}\n{text}")
    assert failures == []
    assert stopped > judged // 3


def test_a_symbol_label_set_two_ways_outside_the_graphs_symbols_stops_the_run_where_read(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = CONFIG.replace(SILO, TWO_SILOS) + "".join(
        f'\n[[assign]]\nmatch = "core/legacy.py"\nsymbols = {{}}\nset = {{ silo = "{silo}" }}\n'
        for silo in ("cell", "control")
    )
    reads = rule_table("reads", {**EDGE, "kinds": ["import"], "endpoint": "surface", "deny": "dst.silo == 'cell'"})
    assert outcome(world, world.config("", base)) == []
    native = outcome(world, world.config(reads, base))
    assert [problem.partition(" by ")[0] for problem in native] == ["core.legacy.api: field 'silo' set differently"]
    monkeypatch.setattr(engine, "context_class", lambda index: RuleContext)
    assert outcome(world, world.config(reads, base)) == native


def test_an_edge_with_no_destination_reads_its_source_where_python_reads_it(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    lead = (
        CONFIG.replace("[fields]\n", '[fields]\nlead = { type = "string" }\n')
        + '\n[providers.codeowners]\nuse = "codeowners"\nfile = "CODEOWNERS"\nsets = ["lead"]\n'
        + '\n[[assign]]\nmatch = "products/billing/**"\nset = { lead = "someone" }\n'
    )
    reads = rule_table("via", {**EDGE, "endpoint": "via", "select": "src.scope startswith src.lead"})
    native = outcome(world, world.config(reads, lead))
    monkeypatch.setattr(engine, "context_class", lambda index: RuleContext)
    assert native == outcome(world, world.config(reads, lead))
    assert len(native) > 1


class Varied:
    """A config that differs from the fixture's outside `[rules]`: fields a provider sets (a set
    and a string, with or without a domain or an overriding assignment), `[roles]` with a group
    and an owner, symbol fields that module and `symbols = {}` assignments set, and a cycle over
    the packages view. Rules over it are generated."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.base = CONFIG
        self.added: list[str] = []
        self.provided: list[str] = []
        self.refs = list(REFS)
        self.group = ""

    def team(self) -> None:
        domain = ', required_on = ["products/**"]' if self.rng.random() < 0.5 else ""
        self.base = self.base.replace("[fields]\n", f'[fields]\nteam = {{ type = "set"{domain} }}\n')
        self.provided.append("team")
        self.refs += ["src.team", "dst.team"]

    def lead(self) -> None:
        self.base = self.base.replace("[fields]\n", '[fields]\nlead = { type = "string" }\n')
        self.provided.append("lead")
        if self.rng.random() < 0.5:
            match = self.rng.choice(PATHS)
            self.added.append(f'\n[[assign]]\nmatch = "{match}"\noverride = true\nset = {{ lead = "someone" }}\n')
        self.refs += ["src.lead", "dst.lead"]

    def roles(self) -> None:
        self.group = self.rng.choice(["package", "tier", "zone", *self.provided])
        owner = self.rng.choice(["owners", *(name for name in self.provided if name == "team")])
        self.added.append(f'\n[roles]\ngroup = "{self.group}"\nowner = "{owner}"\n')

    def symbol_labels(self) -> None:
        self.base = self.base.replace(SILO, TWO_SILOS)
        if self.rng.random() < 0.5:
            self.added.append(f'\n[[assign]]\nmatch = "{self.rng.choice(PATHS)}"\nset = {{ silo = "control" }}\n')
        else:
            path = self.rng.choice(FILES[:3])
            self.added.append(f'\n[[assign]]\nmatch = "{path}"\nsymbols = {{}}\nset = {{ silo = "control" }}\n')

    def config(self) -> str:
        shapes = [self.team, self.lead, self.symbol_labels]
        for shape in self.rng.sample(shapes, self.rng.randint(1, 3)):
            shape()
        if self.provided:
            sets = ", ".join(f'"{name}"' for name in self.provided)
            self.added.append(f'\n[providers.codeowners]\nuse = "codeowners"\nfile = "CODEOWNERS"\nsets = [{sets}]\n')
        if self.rng.random() < 0.7:
            self.roles()
        return self.base + "".join(self.added)

    def rules(self, world: World, base: str) -> str:
        """A cycle over the packages view when roles name a group, and generated rules that load."""
        out = rule_table("cycle", TEAM_CYCLE) if self.group else ""
        made = 0
        wanted = self.rng.randint(1, 2)
        while made < wanted:
            rule = rule_table(f"generated{made}", generated(self.rng, self.refs))
            try:
                world.config(out + rule, base)
            except ConfigError:
                continue
            out += rule
            made += 1
        return out


def test_generated_configs_are_judged_the_same_in_both(world: World) -> None:
    rng = random.Random(1111)
    judged = 0
    failures = []
    while judged < 60:
        varied = Varied(rng)
        base = varied.config()
        try:
            world.config("", base)
        except ConfigError:
            continue
        judged += 1
        rules = varied.rules(world, base)
        failures.extend(f"{found}\n{base[len(CONFIG) :]}{rules}" for found in differences(world, rules, base))
    assert failures == []


def test_labels_are_the_labelers(world: World) -> None:
    config = world.config("")
    native, _ = world.contexts(config)
    labeler = native.labeler
    for name, spec in config.fields.items():
        if spec.on != "module":
            continue
        values = native.evaluator.column(name)
        for module, value in zip(native.graph.modules, values, strict=True):
            found, _ = labeler.field_value(module, None, name)
            expected = "?" if found.state is State.UNKNOWN else "-" if found.state is State.NONE else None
            if expected is None:
                data = found.data
                expected = (
                    f"{data}|{','.join(found.chain)}"
                    if found.chain
                    else ("true" if data else "false")
                    if isinstance(data, bool)
                    else ",".join(sorted(data)) or "-"
                    if isinstance(data, frozenset)
                    else str(data)
                )
            assert (module, name, value) == (module, name, expected)


# A field whose domain reads a provider field that a provider and an assignment set apart on
# `core/**`: reading it there meets that conflict, as the Python labeler meets it.
DOMAIN_READS_A_CONFLICT = CONFIG.replace(
    "[fields]\n",
    '[fields]\nlead = { type = "string" }\nreview = { type = "string", required_on = "lead == \'someone\'" }\n',
) + (
    '\n[providers.codeowners]\nuse = "codeowners"\nfile = "CODEOWNERS"\nsets = ["lead"]\n'
    '\n[[assign]]\nmatch = "core/**"\nset = { lead = "someone" }\n'
)


@pytest.mark.parametrize("base", [CONFIG, DOMAIN_READS_A_CONFLICT], ids=["fixture", "domain-reads-a-conflict"])
def test_the_native_labeler_reads_what_the_python_labeler_reads(world: World, base: str) -> None:
    config = world.config("", base)
    native, _ = world.contexts(config)
    native.find_conflicts()
    python = Labeler(config, world.graph, world.repo, native.labeler.index)
    names = [name for name, spec in config.fields.items() if spec.on == "module"]
    for module in world.graph.modules:
        for name in names:
            assert native.labeler.field_value(module, None, name)[0] == python.field_value(module, None, name)[0]
        for name in ("registry", "lifecycle"):
            assert native.labeler.builtin(module, None, name) == python.builtin(module, None, name)
    assert sorted(native.labeler.conflicts) == sorted(python.conflicts)


COVERED = rule_table("named", {**EDGE, "kinds": ["import", "uses"], "deny": "dst.package != src.package"}) + rule_table(
    "covered",
    {
        "primitive": "coverage",
        "covers": [
            "rules.named.dst.path",
            "rules.named.edge.surface",
            "edge.kind in ['import', 'uses'] and src.package != dst.package: src",
            "edge.context == 'test' and dst.zone == 'facade': dst",
            "src.tier == 'core': dst",
        ],
        "inputs": {"adapter": "globs", "paths": ["core/**"]},
        "identity": ["subject", "package"],
        "ledger": False,
    },
)


def test_a_coverage_rule_needs_the_paths_the_python_rule_needs(world: World) -> None:
    assert differences(world, COVERED) == []
    config = world.config(COVERED)
    native, _ = world.contexts(config)
    native.results["named"] = primitive_of(config.rules["named"])(native, config.rules["named"]).evaluate()
    run = primitive_of(config.rules["covered"])(native, config.rules["covered"]).evaluate()
    assert len(run.findings) > 3 and run.incomplete


class Lying:
    """The evaluator, with a violation row added for an edge the rule judges clean."""

    def __init__(self, inner: Any, row: tuple[Any, ...]) -> None:
        self.inner = inner
        self.row = row

    def judge(self, rule: int) -> list[tuple[Any, ...]]:
        return [*self.inner.judge(rule), self.row]

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def test_a_run_that_writes_a_ledger_judges_every_evaluator_finding_again(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = world.config(rule_table("lied", {**EDGE, "kinds": ["import"], "deny": "dst.package == 'nowhere'"}))
    edge = next(edge for edge in world.graph.edges if edge.kind == "import" and edge.targets)
    row = (edge.handle, 0, JUDGMENTS.index(Judgment.VIOLATION), ["a", "b"], edge.site.module, None)
    made = NativeRuleContext.evaluator.func
    monkeypatch.setattr(
        NativeRuleContext, "evaluator", property(lambda ctx: ctx.__dict__.setdefault("lying", Lying(made(ctx), row)))
    )
    assert ("a", "b") in [f.identity for f in Engine(config, world.graph, world.repo).evaluate().findings()]
    with pytest.raises(EngineError, match="apart from the evaluator"):
        Engine(config, world.graph, world.repo).evaluate(rewrite_ledger=True)


LEVELS = (
    rule_table("crossing", {**EDGE, "kinds": ["import"], "deny": "dst.package != src.package"})
    + '\n[levels.isolated]\ngroup_by = "package"\nselect = "tier != \'leaf\'"\nrequires = ["crossing"]\n'
    + '\n[levels.zoned]\ngroup_by = "zone"\nrequires = ["crossing"]\nstrict = ["crossing"]\n'
)


def test_a_level_judges_the_groups_the_python_level_judges(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    def levels() -> dict[str, tuple[list[str], dict[str, list[str]]]]:
        result = Engine(config, world.graph, world.repo).evaluate()
        return {name: (level.passing, dict(level.failing)) for name, level in result.levels.items()}

    config = world.config(LEVELS)
    native = levels()
    monkeypatch.setattr(engine, "context_class", lambda index: RuleContext)
    assert native == levels()
    assert all(passing or failing for passing, failing in native.values())


EXPECTED_KINDS = CONFIG.replace(
    'model = { subclass_of = "products.billing.backend.models.Base" }',
    'model = { subclass_of = "products.billing.backend.models.Base", expected = true }\n'
    'ghost = { subclass_of = "nowhere.Ghost", expected = true }',
)


def test_an_expected_kind_no_symbol_holds_makes_a_rule_incomplete_in_both(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    rules = rule_table("kinds", {**EDGE, "deny": "dst.kind in ['ghost', 'model']"})
    config = world.config(rules, EXPECTED_KINDS)

    def reasons() -> list[str]:
        return [item.reason for item in Engine(config, world.graph, world.repo).evaluate().incomplete()]

    native = reasons()
    monkeypatch.setattr(engine, "context_class", lambda index: RuleContext)
    assert native == reasons()
    assert native == ["kind ghost matched no symbol, but [kinds] marks it expected"]


def test_a_provider_field_an_inventory_counts_or_a_level_groups_by_gets_its_column(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    lead = PROVIDED.replace("[fields]\n", '[fields]\nlead = { type = "string" }\n').replace(
        'sets = ["team"]', 'sets = ["team", "lead"]'
    )
    counted = rule_table("teams", {"primitive": "inventory", "on": "module", "exactly_one": "team", "ledger": False})
    assert differences(world, counted, lead) == []
    config = world.config(LEVELS.replace('group_by = "zone"', 'group_by = "lead"'), lead)

    def levels() -> dict[str, tuple[list[str], dict[str, list[str]]]]:
        result = Engine(config, world.graph, world.repo).evaluate()
        return {name: (level.passing, dict(level.failing)) for name, level in result.levels.items()}

    native = levels()
    monkeypatch.setattr(engine, "context_class", lambda index: RuleContext)
    assert native == levels()
