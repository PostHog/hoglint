"""The native evaluator (src/link/rules) against the Python rule code, per edge, on one small repo:
hand-written rules for the corners where the meaning is irregular, and seeded generated ones."""

from __future__ import annotations

import gc
import json
import random
import subprocess
import dataclasses
from array import array
from collections import Counter
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from hoglint.config import CONFIG_FILE, Config, ConfigError, load_config
from hoglint.config.model import RuleSpec
from hoglint.config.schema import EVERY_RULE_READS, RULE_KEYS, unread_keys
from hoglint.core.harness import Output, ProducerPlan, ProducerStep
from hoglint.labels import Labeler
from hoglint.ledger.errors import EngineError
from hoglint.model import Context, Edge, EntityId, Graph, Resolution, Site
from hoglint.native.plan import RulePlan
from hoglint.native.rules import NativeRuleContext
from hoglint.native.source import graph_source
from hoglint.plugins import REGISTRY, PackReader, PackSink, Refinement
from hoglint.rules import engine
from hoglint.rules.base import EdgeRule, Judgment, RuleContext, project, subjects_of
from hoglint.rules.engine import RULE_TYPES, Engine, primitive_of

from native.require import native_module

SECTION = "flows"
PRODUCER = "flows.{}@1"
SLOTS = "products.billing.backend.facade.slots"


class Flows:
    """A test pack: the flow-refined shapes a def-use analysis writes, edges with several targets
    or with their own endpoint labels, an edge it hands over twice (its ordinal is its last
    row's), an unresolved edge whose surface holds a control character, symbol facts, and
    signature slots as a facade pack writes them: one a core edge names too, one it does not, one
    in a private callable and one with no annotation."""

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
                unresolved = dataclasses.replace(uses, surface=f"{edge.surface}\x07", resolution=Resolution.UNRESOLVED)
                out.append(dataclasses.replace(unresolved, targets=(), via=()))
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
        "from products.billing.backend import nowhere as nowhere\nfrom products.billing.backend.models import Invoice, Loose\n\n\n"
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


JudgedRow = tuple[int, int, str, list[str] | None, str, str | None]
# The judgments whose rows carry the rule's identity (`Judgment::has_identity`).
IDENTIFIED = (Judgment.VIOLATION.value, Judgment.UNKNOWN_TYPE.value)


def judged_rows(columns: tuple[Any, ...]) -> list[JudgedRow]:
    """`Rules.judge`'s columns as rows: (edge id, destination, judgment name, identity, source
    module, destination module)."""
    names: list[str] = columns[2]
    identities: list[list[str]] = columns[4]
    modules: list[str] = columns[6]
    edges, targets, judgment_at, identity_at, src_at, dst_at = (array("I", columns[at]) for at in (0, 1, 3, 5, 7, 8))
    place = native_module().NO_PLACE
    return [
        (
            edges[at],
            targets[at],
            names[judgment_at[at]],
            identities[identity_at[at]] if identity_at[at] != place else [],
            modules[src_at[at]],
            modules[dst_at[at]] if dst_at[at] != place else None,
        )
        for at in range(len(edges))
    ]


def judged_columns(rows: list[JudgedRow]) -> tuple[Any, ...]:
    """Rows back as `Rules.judge`'s columns."""
    place = native_module().NO_PLACE
    names: dict[str, int] = {}
    identities: dict[tuple[str, ...], int] = {}
    modules: dict[str, int] = {}

    def at(found: dict[Any, int], value: Any) -> int:
        return found.setdefault(value, len(found))

    columns = [array("I") for _ in range(6)]
    for edge, target, name, identity, src, dst in rows:
        columns[0].append(edge)
        columns[1].append(target)
        columns[2].append(at(names, name))
        columns[3].append(at(identities, tuple(identity)) if name in IDENTIFIED and identity is not None else place)
        columns[4].append(at(modules, src))
        columns[5].append(at(modules, dst) if dst is not None else place)
    edges, targets, judgment_at, identity_at, src_at, dst_at = (column.tobytes() for column in columns)
    distinct = [list(identity) for identity in identities]
    return edges, targets, list(names), judgment_at, distinct, identity_at, list(modules), src_at, dst_at


def differences(world: World, rules: str, base: str = CONFIG) -> list[str]:
    """Where the evaluator and the Python rule code differ for every rule of `rules`."""
    config = world.config(rules, base)
    native, python = world.contexts(config)
    out = []
    for spec in config.rules.values():
        mode = primitive_of(spec).NATIVE_MODE
        rule = primitive_of(spec)(native, spec)
        index = native.plan.index[spec.id]
        # An inventory's rows keep the first destination of each identity; its run is compared.
        if mode == "judge" and spec.primitive != "inventory":
            assert isinstance(rule, EdgeRule)
            ours = Counter(
                (edge, target, name, tuple(identity))
                for edge, target, name, identity, _, _ in judged_rows(native.evaluator.judge(index))
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
    "uses-judged-at-their-import": {
        "primitive": "entrypoint",
        "kinds": ["import", "reference"],
        "select": "src.package != dst.package and dst.tier == 'product'",
        "deny": "dst.tier == 'product'",
        "uses_at_import": True,
        "identity": ["dst.symbol", "src.module", "edge.kind"],
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
    monkeypatch.setattr(RulePlan, "provided", lambda self, names: ([], []))
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


# A module that reaches a product's model as an attribute of the imported models module.
THROUGH_A_MODULE = "from products.billing.backend import models\n\n\ndef h():\n    models.Invoice.objects.all()\n"


@pytest.mark.parametrize(
    ("uses_at_import", "references"),
    [
        (
            False,
            {
                ("products.billing.backend.models.Invoice", "core.a"),
                ("products.billing.backend.models.Invoice", "core.b"),
                ("products.billing.backend.models.Invoice", "core.c"),
            },
        ),
        # A use of a name the module imports goes; a use through an imported module stays.
        (True, {("products.billing.backend.models.Invoice", "core.c")}),
    ],
)
def test_an_entrypoint_rule_judges_a_use_at_its_import_in_both_engines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, uses_at_import: bool, references: set[tuple[str, str]]
) -> None:
    monkeypatch.setitem(SOURCES, "core/c.py", THROUGH_A_MODULE)
    world = World(tmp_path)
    spec_table = {**CORNERS["uses-judged-at-their-import"], "uses_at_import": uses_at_import}
    config = world.config(rule_table("uses", spec_table))
    native, python = world.contexts(config)
    spec = config.rules["uses"]
    found = run_rows(primitive_of(spec)(native, spec).evaluate())
    assert found == run_rows(primitive_of(spec)(python, spec).evaluate())
    identities = {identity for identity, *_ in found}
    assert {(symbol, module) for symbol, module, kind in identities if kind == "reference"} == references
    assert ("products.billing.backend.models.Invoice", "core.a", "import") in identities


ADAPTER = 'subclass_of = ["products.billing.backend.presentation.mixins.Mixin"]\naudience = "src.tier == \'core\'"'


@pytest.mark.parametrize(
    ("members", "found_symbols"),
    [
        # By class: a class that inherits the listed base is a member too.
        (
            'subclass_of = ["products.billing.backend.presentation.mixins.Mixin"]',
            {"products.billing.backend.presentation.views.Plain"},
        ),
        # By name: only the listed symbols, not their subclasses.
        (
            'symbols = ["products.billing.backend.presentation.mixins.Mixin"]',
            {"products.billing.backend.presentation.views.Plain", "products.billing.backend.presentation.views.View"},
        ),
        (
            'symbols = ["products.billing.backend.presentation.views.Plain"]',
            {"products.billing.backend.presentation.views.View"},
        ),
    ],
)
def test_an_export_that_names_its_symbols_holds_them_and_not_their_subclasses_in_both_engines(
    world: World, members: str, found_symbols: set[str]
) -> None:
    base = CONFIG.replace(ADAPTER, f"{members}\naudience = \"src.tier == 'product'\"")
    assert base != CONFIG
    config = world.config(rule_table("doors", CORNERS["export-by-class-and-audience"]), base)
    native, python = world.contexts(config)
    spec = config.rules["doors"]
    found = run_rows(primitive_of(spec)(native, spec).evaluate())
    assert found == run_rows(primitive_of(spec)(python, spec).evaluate())
    presentation = {identity[0] for identity, *_ in found if ".presentation." in identity[0]}
    assert presentation == found_symbols


def test_an_export_cannot_name_its_symbols_and_decide_by_class(world: World) -> None:
    base = CONFIG.replace(ADAPTER, ADAPTER + '\nsymbols = ["products.billing.backend.presentation.mixins.Mixin"]')
    with pytest.raises(ConfigError, match="symbols"):
        world.config("", base)


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


# A rule mode: a primitive, or an inventory over edges or over entities, as its class states it
# (`RULE_TYPES`). Each draws its keys from the schema's list for its primitive (`RULE_KEYS`).
TYPES = {cls.MODE: cls for cls in RULE_TYPES}
MODES = {mode: cls.PRIMITIVE for mode, cls in TYPES.items()}
ENTITY_MODES = frozenset(mode for mode, cls in TYPES.items() if cls.ENTITIES)
# The key that makes a mode what it is, and keys a mode cannot load without.
MODE_KEYS = {mode: cls.MODE_KEY for mode, cls in TYPES.items() if cls.MODE_KEY}
NEEDED = {"coverage": frozenset({"covers", "inputs"})}
# Modes whose findings a coverage rule's `rules.<id>.<column>` cover may name.
NAMED_MODES = ["reach", "cycle", "export", "inventory-classify", "inventory-exactly-one", "edge"]
UNKNOWN_TYPES = ["typing.Any", "builtins.dict", "products.billing.backend.models.Loose"]
CONTEXT_PARTS = [{"exclude": ["test"]}, {"include": ["prod"]}, {"exclude": []}, {"include": ["test", "prod"]}]


class Tables:
    """Rule tables generated from the schema's key lists: in each mode, every key `RULE_KEYS`
    accepts for its primitive is set in some tables and left out of others, a key the mode
    never reads too (the load then refuses the table). A key with no value here stops the
    generator, so the schema cannot outgrow it. `drawn` records (mode, keys) of each table."""

    def __init__(self, rng: random.Random, refs: list[str] = REFS) -> None:
        self.rng = rng
        self.refs = refs
        self.drawn: list[tuple[str, frozenset[str]]] = []

    def selector(self, mode: str, key: str) -> str:
        rng = self.rng
        if mode in ENTITY_MODES and key != "from":
            return selector(rng, ENTITY_REFS)
        if key == "from" and mode == "reach":
            module_refs = [
                ref for ref in self.refs if ref.startswith("src.") and ref not in ("src.context", "src.exported")
            ]
            return selector(rng, module_refs)
        if key == "from":
            return selector(rng, [ref for ref in self.refs if not ref.startswith("dst.")])
        return selector(rng, self.refs)

    def identity(self, mode: str) -> list[str]:
        rng = self.rng
        if mode == "coverage":
            return rng.sample(["subject", "package", "tier", "zone", "path"], rng.randint(1, 2))
        if mode in ENTITY_MODES:
            return rng.sample(ENTITY_REFS, rng.randint(1, 2))
        if mode == "export":
            return rng.sample([*self.refs, "edge.label", "edge.kind"], rng.randint(1, 3))
        return rng.sample(self.refs, rng.randint(1, 3))

    def kinds(self, mode: str) -> list[str]:
        rng = self.rng
        if mode == "use-shape":
            return ["reference"] if rng.random() < 0.8 else ["reference", "uses"]
        if mode == "signature":
            return ["exposes"] if rng.random() < 0.8 else ["exposes", "import"]
        return rng.sample(["import", "reference", "uses", "declares", "inherit"], rng.randint(1, 3))

    def covers(self, named: str) -> list[str]:
        """An edge selection with a kind pin, one with none, one on contexts only, and the
        columns of the rule `named` (empty when there is none)."""
        rng = self.rng
        pool = [
            f"edge.kind in ['import', 'uses'] and {selector(rng, self.refs, 1)}: {rng.choice(['src', 'dst'])}",
            f"{selector(rng, self.refs, 1)}: {rng.choice(['src', 'dst'])}",
            "edge.context == 'test': dst",
        ]
        if named:
            pool += [f"rules.{named}.{column}" for column in ("dst.path", "src.path", "edge.surface", "dst.module")]
        return rng.sample(pool, rng.randint(1, 3))

    def value(self, mode: str, key: str, named: str) -> Any:
        rng = self.rng
        on_any = ["finding", "block", "incomplete"]
        makers = {
            "doc": lambda: "generated",
            "report": lambda: rng.choice(["blocking", "advisory"]),
            "ledger": lambda: rng.random() < 0.2,
            "identity": lambda: self.identity(mode),
            "counted": lambda: rng.random() < 0.5,
            "remedy": lambda: "go through the facade",
            "requires": lambda: ["flows.flow"],
            "on_unresolved": lambda: rng.choice(on_any),
            "on_unknown": lambda: rng.choice(on_any),
            "kinds": lambda: self.kinds(mode),
            "contexts": lambda: rng.choice(CONTEXT_PARTS),
            "view": lambda: rng.choice(["refs", "file-imports", "zones", "packages"]),
            "endpoint": lambda: rng.choice(["origin", "origin", "surface", "via"]),
            "from": lambda: self.selector(mode, "from"),
            "select": lambda: self.selector(mode, "select"),
            "deny": lambda: self.selector(mode, "deny"),
            "allow": lambda: self.selector(mode, "allow"),
            "require": lambda: self.selector(mode, "require"),
            "unless": lambda: self.selector(mode, "unless"),
            "flow": lambda: rng.choice(["def-use", "def-use", "syntax"]),
            "resolve_calls": lambda: rng.choice(["bare-name", "import"]),
            "uses_at_import": lambda: rng.random() < 0.5,
            "allow_shapes": lambda: rng.sample(["write", "write(save)", "instance-many", "call", "attribute"], 2),
            "deny_shapes": lambda: rng.sample(["write", "write(delete)", "write(save)", "instance-many"], 2),
            "unknown_types": lambda: rng.sample(UNKNOWN_TYPES, rng.randint(1, 2)),
            "exports": lambda: rng.sample(["facade", "adapter"], rng.randint(1, 2)),
            "on": lambda: rng.choice(["module", "symbol"]),
            "within": lambda: "products/{product}",
            "files": lambda: rng.sample(["backend/__init__.py", "backend/nothing.py"], rng.randint(1, 2)),
            "absent": lambda: rng.sample(["backend/models.py", "backend/legacy.py"], rng.randint(1, 2)),
            "registry": lambda: ["routes"],
            "covers": lambda: self.covers(named),
            "inputs": lambda: {"adapter": "globs", "paths": [rng.choice(["core/**", "products/**"])]},
            "classify": lambda: "policy/classified.toml",
            "exactly_one": lambda: rng.choice(
                [ref[4:] for ref in self.refs if ref in ("src.owners", "src.team")] + ["tier"]
            ),
        }
        return makers[key]()

    def chance(self, mode: str, key: str) -> float:
        if key == MODE_KEYS.get(mode) or key in NEEDED.get(mode, ()):
            return 1.0 if key in MODE_KEYS.values() else 0.95
        if key in unread_keys(TYPES[mode]):
            return 0.08
        return 0.85 if key == "identity" else 0.5

    def table(self, mode: str, named: str = "") -> dict[str, Any]:
        """A table of `mode`; a ledgered one mostly with the identity and growth key it needs."""
        rng = self.rng
        table: dict[str, Any] = {"primitive": MODES[mode]}
        for key in sorted(RULE_KEYS[MODES[mode]] - {"primitive", "growth_key"}):
            if rng.random() < self.chance(mode, key):
                table[key] = self.value(mode, key, named)
        if table.get("ledger", True) and "identity" not in table and rng.random() < 0.9:
            table["identity"] = self.identity(mode)
        if "identity" in table and rng.random() < 0.95:
            table["growth_key"] = table["identity"][:1]
        self.drawn.append((mode, frozenset(table)))
        return table

    def level(self, name: str) -> str:
        """A level that requires rule `name`, grouped by a field this config declares; a field a
        provider sets when one does."""
        rng = self.rng
        fields = ["package", "tier", "zone", "testcode", "owners"]
        fields += [ref[4:] for ref in self.refs if ref in ("src.team", "src.lead")] * 3
        lines = [f"\n[levels.{name}-level]", f'group_by = "{rng.choice(fields)}"', f'requires = ["{name}"]']
        if rng.random() < 0.5:
            lines.append(f"select = {render_toml(selector(rng, ENTITY_REFS, 1))}")
        if rng.random() < 0.3:
            lines.append(f'strict = ["{name}"]')
        return "\n".join(lines) + "\n"

    def rules(self, name: str, mode: str = "") -> str:
        """Rule `name` of `mode`, or of a mode drawn at random, as TOML: a coverage rule after
        the rule its covers may name, and sometimes a level that requires the rule."""
        rng = self.rng
        mode = mode or rng.choice(list(MODES))
        out = ""
        named = ""
        if mode == "coverage" and rng.random() < 0.7:
            named = f"{name}-named"
            out += rule_table(named, {**self.table(rng.choice(NAMED_MODES)), "ledger": False})
        out += rule_table(name, self.table(mode, named))
        if rng.random() < 0.3:
            out += self.level(name)
        return out


def schema_gaps(drawn: list[tuple[str, frozenset[str], bool]]) -> list[str]:
    """Each mode's keys a generated test left out of its reach: a key no table set, one every
    table set, an accepted key no loaded table held, or a key the mode never reads that loaded."""
    out = []
    for mode, primitive in MODES.items():
        tables = [(keys, loaded) for drawn_mode, keys, loaded in drawn if drawn_mode == mode]
        unread = unread_keys(TYPES[mode])
        for key in sorted(RULE_KEYS[primitive] - {"primitive"}):
            held = [loaded for keys, loaded in tables if key in keys]
            # The key that picks an inventory's mode is left out only in the other mode.
            if not held or (len(held) == len(tables) and key != MODE_KEYS.get(mode)):
                out.append(f"{mode}: `{key}` is set in {len(held)} of {len(tables)} tables")
            elif key in unread and any(held):
                out.append(f"{mode}: `{key}` loaded, though the mode never reads it")
            elif key not in unread and not any(held):
                out.append(f"{mode}: no table with `{key}` loaded")
    return out


def level_differences(world: World, config: Config, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Where the two engines differ on a whole evaluation's levels, or on the config error it
    stops with."""

    def outcome() -> list[str]:
        try:
            result = Engine(config, world.graph, world.repo).evaluate()
        except ConfigError as error:
            return sorted(error.problems)
        return [f"{name}: {level.passing} {dict(level.failing)}" for name, level in result.levels.items()]

    native = outcome()
    with monkeypatch.context() as patched:
        patched.setattr(engine, "context_class", lambda index: RuleContext)
        python = outcome()
    return [] if native == python else [f"levels: native {native}, python {python}"]


def test_generated_rules_are_judged_the_same_in_both(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    tables = Tables(random.Random(20261111))
    drawn: list[tuple[str, frozenset[str], bool]] = []
    failures = []
    # Every mode in turn, until a table of it loads: each mode gets as many judged configs.
    for mode in list(MODES) * 24:
        while True:
            before = len(tables.drawn)
            rules = tables.rules("generated", mode)
            try:
                config = world.config(rules)
            except ConfigError:
                drawn.extend((drawn_mode, keys, False) for drawn_mode, keys in tables.drawn[before:])
                continue
            drawn.extend((drawn_mode, keys, True) for drawn_mode, keys in tables.drawn[before:])
            break
        failures.extend(f"{found}\n{rules}" for found in differences(world, rules))
        if config.levels:
            failures.extend(f"{found}\n{rules}" for found in level_differences(world, config, monkeypatch))
    assert failures == []
    assert schema_gaps(drawn) == []


# A `RuleSpec` attribute and the config key it holds, where the two names differ.
SPEC_KEYS = {
    "from_": "from",
    "contexts_include": "contexts",
    "contexts_exclude": "contexts",
    "identity_name": "identity",
    "producer_options": "flow",
    "input": "inputs",
}


class Recording:
    """A `RuleSpec` that records the config key of every attribute read from it."""

    def __init__(self, spec: RuleSpec) -> None:
        self.spec = spec
        self.keys: set[str] = set()

    def __getattr__(self, name: str) -> Any:
        self.keys.add(SPEC_KEYS.get(name, name))
        return getattr(self.spec, name)


class KeysRead:
    """Per mode, the keys its rule class read in a run of the Python engine, and the keys the
    native plan read to plan it; both among the keys its primitive accepts and some rules do not
    read (`EVERY_RULE_READS` aside)."""

    def __init__(self) -> None:
        self.by_class: dict[str, set[str]] = {mode: set() for mode in TYPES}
        self.by_plan: dict[str, set[str]] = {mode: set() for mode in TYPES}

    def record(self, world: World, config: Config) -> None:
        native, python = world.contexts(config)
        for spec in config.rules.values():
            cls = primitive_of(spec)
            accepted = RULE_KEYS[cls.PRIMITIVE] - EVERY_RULE_READS
            recording: Any = Recording(spec)
            python.results[spec.id] = cls(python, recording).evaluate()
            self.by_class[cls.MODE] |= recording.keys & accepted
            planned: Any = Recording(spec)
            rule = cls(native, planned)
            self.by_class[cls.MODE] |= planned.keys & accepted
            planned.keys.clear()
            rule.plan()
            self.by_plan[cls.MODE] |= planned.keys & accepted

    def problems(self) -> list[str]:
        out = []
        for mode, cls in TYPES.items():
            read = self.by_class[mode]
            if read != cls.READS:
                out.append(
                    f"{mode}: reads {sorted(read - cls.READS)} undeclared, never reads {sorted(cls.READS - read)}"
                )
            if not self.by_plan[mode] <= read:
                out.append(f"{mode}: the plan reads {sorted(self.by_plan[mode] - read)}, which the rule does not")
            if not cls.READS <= RULE_KEYS[cls.PRIMITIVE]:
                out.append(f"{mode}: declares {sorted(cls.READS - RULE_KEYS[cls.PRIMITIVE])}, which the schema refuses")
        return out


def test_each_rule_type_reads_exactly_the_keys_it_declares(world: World) -> None:
    tables = Tables(random.Random(20261112))
    read = KeysRead()
    for mode in list(TYPES) * 24:
        while True:
            rules = tables.rules("generated", mode)
            try:
                config = world.config(rules)
            except ConfigError:
                continue
            break
        read.record(world, config)
    assert read.problems() == []


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
            rule = Tables(self.rng, refs).rules(f"generated{made}")
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
            rule = Tables(self.rng, self.refs).rules(f"generated{made}")
            try:
                world.config(out + rule, base)
            except ConfigError:
                continue
            out += rule
            made += 1
        return out


def test_generated_configs_are_judged_the_same_in_both(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    rng = random.Random(1111)
    judged = levels = 0
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
        config = world.config(rules, base)
        if config.levels:
            levels += 1
            found = level_differences(world, config, monkeypatch)
            failures.extend(f"{found}\n{base[len(CONFIG) :]}{rules}" for found in found)
    assert failures == []
    assert levels > 5


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


def test_a_surface_with_a_control_character_is_worded_once_for_both_engines(world: World) -> None:
    rules = rule_table(
        "unplaced",
        {
            "primitive": "coverage",
            "covers": ["edge.kind == 'uses': dst"],
            "inputs": {"adapter": "globs", "paths": ["core/**"]},
            "identity": ["subject"],
            "ledger": False,
        },
    )
    assert differences(world, rules) == []
    config = world.config(rules)
    native, _ = world.contexts(config)
    run = primitive_of(config.rules["unplaced"])(native, config.rules["unplaced"]).evaluate()
    assert "cannot place 'products.billing.backend.models.Invoice\\x07' for coverage" in [
        item.reason for item in run.incomplete
    ]


class Lying:
    """The evaluator, with the rows each call `changes` names returns changed by its change."""

    def __init__(self, inner: Any, changes: dict[str, Any]) -> None:
        self.inner = inner
        self.changes = changes

    def __getattr__(self, name: str) -> Any:
        found = getattr(self.inner, name)
        change = self.changes.get(name)
        if change is None:
            return found
        if name == "judge":
            return lambda *args: judged_columns(change(judged_rows(found(*args))))
        return lambda *args: change(list(found(*args)))


def lie(monkeypatch: pytest.MonkeyPatch, **changes: Any) -> None:
    made = NativeRuleContext.evaluator.func
    monkeypatch.setattr(
        NativeRuleContext,
        "evaluator",
        property(lambda ctx: ctx.__dict__.setdefault("lying", Lying(made(ctx), changes))),
    )


def test_a_run_that_writes_a_ledger_judges_every_evaluator_finding_again(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = world.config(rule_table("lied", {**EDGE, "kinds": ["import"], "deny": "dst.package == 'nowhere'"}))
    edge = next(edge for edge in world.graph.edges if edge.kind == "import" and edge.targets)
    row = (edge.handle, 0, Judgment.VIOLATION.value, ["a", "b"], edge.site.module, None)
    lie(monkeypatch, judge=lambda rows: [*rows, row])
    assert ("a", "b") in [f.identity for f in Engine(config, world.graph, world.repo).evaluate().findings()]
    with pytest.raises(EngineError, match="apart from the evaluator"):
        Engine(config, world.graph, world.repo).evaluate(rewrite_ledger=True)


def test_a_found_row_with_no_identity_is_an_engine_error(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    config = world.config(rule_table("lied", {**EDGE, "kinds": ["import"], "deny": "dst.package == 'billing'"}))
    lie(monkeypatch, judge=lambda rows: [(*rows[0][:3], None, *rows[0][4:]), *rows[1:]])
    with pytest.raises(EngineError, match="no identity"):
        Engine(config, world.graph, world.repo).evaluate()


@pytest.mark.parametrize(
    ("src", "dst", "subjects"),
    [("a", "b", ("a", "b")), ("a", "a", ("a",)), ("a", None, ("a",)), ("", "b", ("b",)), ("", None, ())],
)
def test_a_finding_names_its_source_and_another_destination_as_subjects(
    src: str, dst: str | None, subjects: tuple[str, ...]
) -> None:
    assert subjects_of(src, dst) == subjects


@pytest.mark.parametrize(
    ("rules", "lies", "name"),
    [
        (
            rule_table("lied", {**EDGE, "kinds": ["import"], "deny": "dst.package == 'billing'"}),
            {"judge": lambda rows: [(*rows[0][:2], "violated", *rows[0][3:]), *rows[1:]]},
            "violated",
        ),
        (
            rule_table("lied", CORNERS["cycle-between-packages"]),
            {"pairs": lambda rows: [(*rows[0][:2], "violated", *rows[0][3:]), *rows[1:]]},
            "violated",
        ),
        (
            rule_table("lied", CORNERS["reach-from-contracts"]),
            {"reach": lambda rows: [(*row[:3], "maybe" if row[3] is not None else None) for row in rows]},
            "maybe",
        ),
    ],
    ids=["judge", "pairs", "reach"],
)
def test_a_name_the_rule_code_does_not_know_stops_the_run(
    world: World, monkeypatch: pytest.MonkeyPatch, rules: str, lies: dict[str, Any], name: str
) -> None:
    config = world.config(rules)
    lie(monkeypatch, **lies)
    with pytest.raises(EngineError, match=f"'{name}' is not a valid"):
        Engine(config, world.graph, world.repo).evaluate()


def an_import(world: World) -> Edge:
    return next(edge for edge in world.graph.edges if edge.kind == "import" and edge.targets)


def made_up(text: str) -> str:
    return "made-up" if text == "billing" else text


def swapped(text: str, pair: tuple[str, str] = ("core", "billing")) -> str:
    """Two values of a label column the other way round: a column with the same values, on the
    wrong modules."""
    first, second = pair
    return second if text == first else first if text == second else text


def relabeled(change: Callable[[str], str]) -> Callable[[Any], Any]:
    """A lie of the `values` call: every value of a label column changed by `change`."""
    return lambda found: [[(kind, [change(item) for item in items]) for kind, items in found[0]], found[1]]


def made_up_label(changes: dict[str, Any]) -> dict[str, Any]:
    """A `package` column that says `made-up` where it says `billing`, and judge rows whose
    identities say the same: labels the evaluator computed, wrong on both sides."""
    return {
        **changes,
        "values": relabeled(made_up),
        "judge": lambda rows: [(*row[:3], [made_up(item) for item in row[3]], *row[4:]) for row in rows],
    }


COVERED_CORE = rule_table(
    "lied",
    {
        "primitive": "coverage",
        "covers": ["edge.kind in ['import'] and src.tier == 'core': dst"],
        "inputs": {"adapter": "globs", "paths": ["core/**"]},
        "ledger": False,
    },
)

# Per rule type, the rules and the lies of the evaluator's calls (each made from the fixture): a
# row with an identity the rule's own `project` does not give, a first row left out, or a made-up
# row: a label column, a reach path, a cycle pair, a coverage path, a level group.
LEDGER_LIES: dict[str, tuple[str, Any]] = {
    "identity": (
        rule_table("lied", {**EDGE, "kinds": ["import"], "deny": "dst.package == 'billing'"}),
        lambda world: {"judge": lambda rows: [(*rows[0][:3], ["made", "up"], *rows[0][4:]), *rows[1:]]},
    ),
    "label column on an unresolved row": (
        rule_table(
            "lied",
            {
                **EDGE,
                "kinds": ["uses"],
                "on_unresolved": "finding",
                "identity": ["src.package", "dst.symbol"],
                "ledger": True,
                "growth_key": ["src.package"],
            },
        ),
        lambda world: {"values": relabeled(swapped)},
    ),
    "source module": (
        rule_table("lied", {**EDGE, "kinds": ["import"], "deny": "dst.package == 'billing'"}),
        lambda world: {"judge": lambda rows: [(*rows[0][:4], "made.up", *rows[0][5:]), *rows[1:]]},
    ),
    "label column": (
        rule_table(
            "lied", {**EDGE, "kinds": ["import"], "deny": "dst.package != src.package", "identity": ["dst.package"]}
        ),
        lambda world: made_up_label({}),
    ),
    "export": (rule_table("lied", CORNERS["export-shapes"]), lambda world: {"export": lambda rows: rows[1:]}),
    "layout": (rule_table("lied", CORNERS["layout-on-modules"]), lambda world: {"entities": lambda rows: rows[1:]}),
    "entity-inventory": (
        rule_table("lied", CORNERS["inventory-exactly-one"]),
        lambda world: {"entities": lambda rows: rows[1:]},
    ),
    "inventory": (rule_table("lied", CORNERS["inventory-classify"]), lambda world: {"judge": lambda rows: rows[1:]}),
    "reach path": (
        rule_table("lied", CORNERS["reach-from-contracts"]),
        lambda world: {"reach": lambda rows: [*rows, ("denied", "core.a", [(an_import(world).handle, 0)], "true")]},
    ),
    "cycle pair": (
        rule_table("lied", CORNERS["cycle-between-packages"]),
        lambda world: {
            "pairs": lambda rows: [
                *rows,
                (an_import(world).handle, 0, Judgment.VIOLATION.value, ("made", "up")),
                (an_import(world).handle, 0, Judgment.VIOLATION.value, ("up", "made")),
            ]
        },
    ),
    "coverage path": (
        COVERED_CORE,
        lambda world: {"coverage": lambda found: [[*found[0], ("made/up.py", 0, None)], found[1]]},
    ),
}


@pytest.mark.parametrize("name", sorted(LEDGER_LIES))
def test_a_ledger_write_holds_every_rule_type_to_its_own_code(
    world: World, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    rules, lies = LEDGER_LIES[name]
    config = world.config(rules)
    assert Engine(config, world.graph, world.repo).evaluate(rewrite_ledger=True).runs["lied"].findings
    lie(monkeypatch, **lies(world))
    Engine(config, world.graph, world.repo).evaluate()
    with pytest.raises(EngineError, match="evaluator"):
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


def test_a_ledger_write_holds_a_level_to_the_groups_a_python_labeler_gives(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = world.config(LEVELS)
    Engine(config, world.graph, world.repo).evaluate(rewrite_ledger=True)
    lie(monkeypatch, level_groups=lambda groups: [*groups, "made-up"])
    assert any(
        "made-up" in level.passing for level in Engine(config, world.graph, world.repo).evaluate().levels.values()
    )
    with pytest.raises(EngineError, match="evaluator"):
        Engine(config, world.graph, world.repo).evaluate(rewrite_ledger=True)


def test_a_ledger_write_holds_a_level_to_the_groups_a_python_labeler_blocks(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def levels() -> dict[str, tuple[list[str], list[str]]]:
        result = Engine(config, world.graph, world.repo).evaluate()
        return {name: (level.passing, sorted(level.failing)) for name, level in result.levels.items()}

    rules = rule_table("crossing", {**EDGE, "kinds": ["import"], "deny": "dst.package == 'alerts'"})
    config = world.config(rules + '\n[levels.isolated]\ngroup_by = "package"\nrequires = ["crossing"]\n')
    Engine(config, world.graph, world.repo).evaluate(rewrite_ledger=True)
    honest = levels()
    # `billing` fails the level and `leaf` passes it.
    pair = ("billing", "leaf")
    lie(monkeypatch, values=relabeled(lambda item: swapped(item, pair)))
    lied = levels()
    assert lied != honest and {name: set(sum(groups, [])) for name, groups in lied.items()} == {
        name: set(sum(groups, [])) for name, groups in honest.items()
    }
    with pytest.raises(EngineError, match="evaluator"):
        Engine(config, world.graph, world.repo).evaluate(rewrite_ledger=True)


def problems(world: World, config: Config) -> list[str]:
    """The problems of the config error a whole evaluation stops with, or none."""
    try:
        Engine(config, world.graph, world.repo).evaluate()
    except ConfigError as error:
        return sorted(error.problems)
    return []


# A string field a provider sets and an assignment sets apart on `core/**`, which no rule reads.
LEAD_SET_TWO_WAYS = (
    CONFIG.replace("[fields]\n", '[fields]\nlead = { type = "string" }\n')
    + '\n[providers.codeowners]\nuse = "codeowners"\nfile = "CODEOWNERS"\nsets = ["lead"]\n'
    + '\n[[assign]]\nmatch = "core/**"\nset = { lead = "someone" }\n'
)


@pytest.mark.parametrize(
    "level",
    ['group_by = "lead"', 'group_by = "package"\nselect = "lead != \'nobody\'"'],
    ids=["group-by", "select"],
)
def test_a_conflict_only_a_level_meets_stops_both_engines_alike(
    world: World, monkeypatch: pytest.MonkeyPatch, level: str
) -> None:
    rules = LEVELS.replace('group_by = "zone"', level)
    config = world.config(rules, LEAD_SET_TWO_WAYS)
    native = problems(world, config)
    monkeypatch.setattr(engine, "context_class", lambda index: RuleContext)
    assert native == problems(world, config)
    assert native and all("field 'lead' set differently" in problem for problem in native)


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


# A provider-set field read in each role the evaluator compiles (D54), as (rules, config).
LEAD = PROVIDED.replace("[fields]\n", '[fields]\nlead = { type = "string" }\n').replace(
    'sets = ["team"]', 'sets = ["team", "lead"]'
)
COLUMN_ROLES = {
    "selector": (rule_table("read", {**EDGE, "select": "src.team == dst.team"}), PROVIDED),
    "identity": (rule_table("read", {**EDGE, "identity": ["src.module", "dst.team"]}), PROVIDED),
    "cycle group": (rule_table("read", TEAM_CYCLE), PROVIDED),
    "exactly_one": (
        rule_table("read", {"primitive": "inventory", "on": "module", "exactly_one": "team", "ledger": False}),
        PROVIDED,
    ),
    "level group_by": (
        LEVELS.replace('group_by = "zone"', 'group_by = "lead"').replace('group_by = "package"', 'group_by = "lead"'),
        LEAD,
    ),
    "cover column": (
        rule_table("crossing", {**EDGE, "kinds": ["import"]})
        + rule_table(
            "read",
            {
                "primitive": "coverage",
                "covers": ["rules.crossing.dst.lead"],
                "inputs": {"adapter": "globs", "paths": ["core/**"]},
                "ledger": False,
            },
        ),
        LEAD,
    ),
    "audience": (
        rule_table("read", CORNERS["export-by-class-and-audience"]),
        PROVIDED.replace("audience = \"src.tier == 'core'\"", "audience = \"src.team == '@team-core'\""),
    ),
    "domain": (
        rule_table("read", {**EDGE, "deny": "src.probe == 'x'"}),
        PROVIDED.replace(
            "[fields]\n", '[fields]\nprobe = { type = "string", required_on = "team == \'@team-core\'" }\n'
        ),
    ),
}


@pytest.mark.parametrize("role", sorted(COLUMN_ROLES))
def test_a_provider_field_read_in_each_role_gets_its_column(
    world: World, monkeypatch: pytest.MonkeyPatch, role: str
) -> None:
    rules, base = COLUMN_ROLES[role]
    config = world.config(rules, base)
    native, _ = world.contexts(config)
    reads = native_module().Rules.reads(native.native.native.link, json.dumps(native.plan.data()))
    assert set(reads) & {"team", "lead"}
    if config.levels:
        assert level_differences(world, config, monkeypatch) == []
    else:
        assert differences(world, rules, base) == []
