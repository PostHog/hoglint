"""The native evaluator (src/link/rules) on one small repo: hand-written rules for the corners where
the meaning is irregular, and seeded generated ones, held to stored runs (`stored_rows.json`,
`generated_runs.jsonl`)."""

from __future__ import annotations

import gc
import os
import ast
import json
import random
import hashlib
import subprocess
import dataclasses
from array import array
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from hoglint.config import CONFIG_FILE, Config, ConfigError, load_config
from hoglint.config.model import RuleSpec
from hoglint.config.schema import EVERY_RULE_READS, RULE_KEYS, unread_keys
from hoglint.core.harness import Output, ProducerPlan, ProducerStep
from hoglint.labels import Labeler
from hoglint.ledger.errors import EngineError
from hoglint.ledger.scan import group_triples, source_labels
from hoglint.model import Context, Edge, EntityId, Graph, Resolution, Site
from hoglint.native.plan import RulePlan
from hoglint.native.rules import NativeRuleContext
from hoglint.native.source import NativeGraphSource
from hoglint.plugins import REGISTRY, PackReader, PackSink, Refinement
from hoglint.rules.base import subjects_of
from hoglint.rules.engine import RULE_TYPES, Engine, primitive_of
from hoglint.rules.exports import ExportPrimitive, SlotRule
from hoglint.selectors import Selector

from native.require import native_module

SECTION = "flows"
PRODUCER = "flows.{}@1"
SLOTS = "products.billing.backend.facade.slots"
NAMESPACE = "products.alerts.backend.queries"


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
        into_namespace = False
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
                if not into_namespace:
                    out.append(Flows.into_namespace(site))
                    into_namespace = True
        return out

    @staticmethod
    def into_namespace(site: Site) -> Edge:
        """An edge to a directory with no `__init__.py`: no graph module, but under a product."""
        return Edge(
            "uses",
            site,
            NAMESPACE,
            (EntityId("python", NAMESPACE, ""),),
            Resolution.RESOLVED,
            frozenset({Context.PROD}),
            PRODUCER.format("flow"),
        )

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
    # A directory with no `__init__.py`: its module is a graph module, the directory is none.
    "products/alerts/backend/queries/runner.py": "def run():\n    pass\n",
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
        self.graph: Graph = NativeGraphSource(work).build(repo, load_config(repo, repo / CONFIG_FILE))
        gc.enable()

    def config(self, rules: str, base: str = CONFIG) -> Config:
        path = self.root / "config" / CONFIG_FILE
        path.parent.mkdir(exist_ok=True)
        path.write_text(base + rules)
        return load_config(self.repo, path)

    def context(self, config: Config) -> NativeRuleContext:
        labeler: Labeler = Engine(config, self.graph, self.repo).labeler
        labeler.find_conflicts()
        return NativeRuleContext(config, self.graph, labeler, self.repo)

    def rows(self, config: Config, name: str) -> list[tuple[Any, ...]]:
        """What rule `name` reports."""
        spec = config.rules[name]
        return run_rows(primitive_of(spec)(self.context(config), spec).evaluate())


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    return World(tmp_path_factory.mktemp("evaluator"))


def run_rows(run: Any) -> list[tuple[Any, ...]]:
    findings = [
        (f.identity, f.message, tuple(e.handle for e in f.witnesses), f.ledgerable, f.unknown) for f in run.findings
    ]
    incomplete = [(i.key, i.reason, i.subjects, i.edge.handle if i.edge is not None else None) for i in run.incomplete]
    return [*findings, *incomplete]


def group_name(name: str) -> str:
    """A level's group as stored: a group of a set field, named `frozenset({...})`, is stored as
    its sorted items in braces."""
    if not name.startswith("frozenset("):
        return name
    items = ast.literal_eval(name.removeprefix("frozenset(").removesuffix(")"))
    return "{" + ", ".join(sorted(items)) + "}"


def run_lines(world: World, at: int, base: str, rules: str) -> list[list[Any]]:
    """A whole run of one config as stored lines: each rule's findings (identity, message, flags,
    subjects) and incomplete items (key, reason, subjects), the levels, or the config error."""
    try:
        result = Engine(world.config(rules, base), world.graph, world.repo).evaluate()
    except ConfigError as error:
        # A problem names the config file, which sits in this run's temporary directory.
        root = str(world.root.resolve())
        return [["error", at, sorted(problem.replace(root, "<root>") for problem in error.problems)]]
    out: list[list[Any]] = []
    for rule, run in result.runs.items():
        for finding in run.findings:
            identity, flags = list(finding.identity), [finding.ledgerable, finding.unknown]
            out.append(["finding", at, rule, identity, finding.message, *flags, list(finding.subjects)])
        out.extend(["incomplete", at, rule, item.key, item.reason, list(item.subjects)] for item in run.incomplete)
    for name, level in result.levels.items():
        passing = sorted(group_name(group) for group in level.passing)
        failing = {group_name(group): reasons for group, reasons in level.failing.items()}
        out.append(["level", at, name, passing, dict(sorted(failing.items()))])
    return out


# The whole runs of the hand-written cases, as `run_lines` gives them, by case.
# `HOGLINT_REFRESH_GENERATED=1` rewrites the cases a run meets; review the diff.
STORED_ROWS = Path(__file__).parent / "stored_rows.json"


def assert_stored(world: World, name: str, rules: str, base: str = CONFIG) -> None:
    """A whole run of `rules` over `base` gives the lines stored for case `name`."""
    found = json.loads(json.dumps(run_lines(world, 0, base, rules), ensure_ascii=False))
    stored = json.loads(STORED_ROWS.read_text()) if STORED_ROWS.exists() else {}
    if os.environ.get("HOGLINT_REFRESH_GENERATED"):
        stored[name] = found
        STORED_ROWS.write_text(json.dumps(stored, indent=1, ensure_ascii=False, sort_keys=True) + "\n")
    assert found == stored.get(name)


# (edge id, destination, judgment name, identity, source module, destination module, words): the
# words are the source symbol, destination symbol, label and unknown refs (`Words`).
JudgedRow = tuple[int, int, str, list[str], str, str | None, tuple[str | None, str | None, str, str]]


def judged_rows(columns: tuple[Any, ...]) -> list[JudgedRow]:
    """`Rules.judge`'s columns as rows."""
    names: list[str] = columns[2]
    identities: list[list[str]] = columns[4]
    modules: list[str] = columns[6]
    texts: list[str] = columns[9][0]
    edges, targets, judgment_at, identity_at, src_at, dst_at = (array("I", columns[at]) for at in (0, 1, 3, 5, 7, 8))
    words = [array("I", column) for column in columns[9][1:]]
    place = native_module().NO_PLACE

    def text(at: int) -> str | None:
        return texts[at] if at != place else None

    return [
        (
            edges[at],
            targets[at],
            names[judgment_at[at]],
            identities[identity_at[at]],
            modules[src_at[at]],
            modules[dst_at[at]] if dst_at[at] != place else None,
            (text(words[0][at]), text(words[1][at]), texts[words[2][at]], texts[words[3][at]]),
        )
        for at in range(len(edges))
    ]


def judged_columns(rows: list[JudgedRow]) -> tuple[Any, ...]:
    """Rows back as `Rules.judge`'s columns."""
    place = native_module().NO_PLACE
    names: dict[str, int] = {}
    identities: dict[tuple[str, ...], int] = {}
    modules: dict[str, int] = {}
    texts: dict[str, int] = {}

    def at(found: dict[Any, int], value: Any) -> int:
        return found.setdefault(value, len(found))

    columns = [array("I") for _ in range(10)]
    for edge, target, name, identity, src, dst, words in rows:
        columns[0].append(edge)
        columns[1].append(target)
        columns[2].append(at(names, name))
        columns[3].append(at(identities, tuple(identity)))
        columns[4].append(at(modules, src))
        columns[5].append(at(modules, dst) if dst is not None else place)
        for column, word in zip(columns[6:], words):
            column.append(at(texts, word) if word is not None else place)
    edges, targets, judgment_at, identity_at, src_at, dst_at = (column.tobytes() for column in columns[:6])
    distinct = [list(identity) for identity in identities]
    word_columns = (list(texts), *(column.tobytes() for column in columns[6:]))
    return edges, targets, list(names), judgment_at, distinct, identity_at, list(modules), src_at, dst_at, word_columns


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


def test_a_directory_without_init_reads_the_labels_its_path_gets_in_both_engines(world: World) -> None:
    rules = rule_table(
        "namespace",
        {
            **EDGE,
            "kinds": ["uses"],
            "select": f"dst.module == '{NAMESPACE}'",
            "identity": ["dst.module", "dst.package", "dst.tier", "dst.zone", "dst.stage"],
        },
    )
    assert_stored(world, "namespace", rules)
    config = world.config(rules)
    native = world.context(config)
    found = primitive_of(config.rules["namespace"])(native, config.rules["namespace"]).evaluate()
    # the directory sits under products/alerts: the product's labels, and a zone from its path; the
    # unresolved `uses` edge reads unknown everywhere
    assert {finding.identity for finding in found.findings} == {
        (NAMESPACE, "alerts", "product", "backend", "-"),
        ("?", "?", "?", "?", "?"),
    }


@pytest.mark.parametrize("name", sorted(CORNERS))
def test_the_evaluator_judges_each_irregular_corner_as_the_rule_code_does(world: World, name: str) -> None:
    assert_stored(world, f"corner {name}", rule_table(name, CORNERS[name]))


# The export rule's corners, stored: per finding its identity, message and unknown flag, per
# incomplete item its key and reason.
EXPORT_CORNER_ROWS = {
    "export-shapes": (
        [
            (
                (
                    "products.billing.backend.facade.slots",
                    "products.billing.backend.nowhere",
                    "unresolved-import",
                    "billing",
                    "product",
                    "-",
                ),
                "products.billing.backend.facade.slots exports products.billing.backend.nowhere: unresolved-import",
                True,
            ),
            (
                (
                    "products.billing.backend.facade.slots",
                    "products.billing.backend.facade.slots.Desk",
                    "export-shape(class)",
                    "billing",
                    "product",
                    "-",
                ),
                "products.billing.backend.facade.slots exports products.billing.backend.facade.slots.Desk: export-shape(class)",
                False,
            ),
            (
                (
                    "products.billing.backend.facade.slots",
                    "products.billing.backend.facade.slots.Draft",
                    "export-shape(class)",
                    "billing",
                    "product",
                    "-",
                ),
                "products.billing.backend.facade.slots exports products.billing.backend.facade.slots.Draft: export-shape(class)",
                False,
            ),
            (
                (
                    "products.billing.backend.facade.slots",
                    "products.billing.backend.models.Invoice",
                    "export-denied(model)",
                    "billing",
                    "product",
                    "-",
                ),
                "products.billing.backend.facade.slots exports products.billing.backend.facade.slots.Note -> products.billing.backend.models.Invoice: export-denied(model)",
                False,
            ),
            (
                (
                    "products.billing.backend.facade.slots",
                    "products.billing.backend.facade.slots.Note",
                    "export-unknown",
                    "billing",
                    "product",
                    "-",
                ),
                "products.billing.backend.facade.slots exports products.billing.backend.facade.slots.Note: export-unknown",
                True,
            ),
            (
                (
                    "products.billing.backend.facade.slots",
                    "products.billing.backend.facade.slots.Port",
                    "export-shape(class)",
                    "billing",
                    "product",
                    "-",
                ),
                "products.billing.backend.facade.slots exports products.billing.backend.facade.slots.Port: export-shape(class)",
                False,
            ),
            (
                ("products.billing.backend.facade.slots", "typing.Any", "export-unknown", "-", "product", "-"),
                "products.billing.backend.facade.slots exports products.billing.backend.facade.slots.charge -> typing.Any: export-unknown",
                True,
            ),
            (
                (
                    "products.billing.backend.facade.slots",
                    "products.billing.backend.models.Loose",
                    "export-unknown",
                    "billing",
                    "product",
                    "-",
                ),
                "products.billing.backend.facade.slots exports products.billing.backend.facade.slots.charge -> products.billing.backend.models.Loose: export-unknown",
                True,
            ),
            (
                (
                    "products.billing.backend.facade.slots",
                    "products.billing.backend.facade.slots.charge",
                    "export-unknown",
                    "billing",
                    "product",
                    "-",
                ),
                "products.billing.backend.facade.slots exports products.billing.backend.facade.slots.charge: export-unknown",
                True,
            ),
            (
                (
                    "products.billing.backend.presentation.mixins",
                    "products.billing.backend.presentation.mixins.Mixin",
                    "export-shape(class)",
                    "billing",
                    "product",
                    "-",
                ),
                "products.billing.backend.presentation.mixins exports products.billing.backend.presentation.mixins.Mixin: export-shape(class)",
                False,
            ),
            (
                (
                    "products.billing.backend.presentation.views",
                    "products.billing.backend.presentation.views.View",
                    "export-shape(class)",
                    "billing",
                    "product",
                    "-",
                ),
                "products.billing.backend.presentation.views exports products.billing.backend.presentation.views.View: export-shape(class)",
                False,
            ),
        ],
        [],
    ),
    "export-unknowns-as-incomplete": (
        [
            (
                ("products.billing.backend.facade.slots.Desk",),
                "products.billing.backend.facade.slots exports products.billing.backend.facade.slots.Desk: export-shape(class)",
                False,
            ),
            (
                ("products.billing.backend.facade.slots.Draft",),
                "products.billing.backend.facade.slots exports products.billing.backend.facade.slots.Draft: export-shape(class)",
                False,
            ),
            (
                ("products.billing.backend.models.Invoice",),
                "products.billing.backend.facade.slots exports products.billing.backend.facade.slots.Note -> products.billing.backend.models.Invoice: export-denied(model)",
                False,
            ),
            (
                ("products.billing.backend.facade.slots.Port",),
                "products.billing.backend.facade.slots exports products.billing.backend.facade.slots.Port: export-shape(class)",
                False,
            ),
        ],
        [
            (
                "products.billing.backend.facade.slots|products.billing.backend.nowhere",
                "'products.billing.backend.nowhere' did not resolve",
            ),
            (
                "products.billing.backend.facade.slots|products.billing.backend.facade.slots.Note",
                "products.billing.backend.facade.slots.Note: param:untyped has no annotation",
            ),
            (
                "products.billing.backend.facade.slots|typing.Any",
                "products.billing.backend.facade.slots.charge -> typing.Any: a type hoglint reads as unknown",
            ),
            (
                "products.billing.backend.facade.slots|products.billing.backend.models.Loose",
                "products.billing.backend.facade.slots.charge -> products.billing.backend.models.Loose: its bases did not all resolve",
            ),
            (
                "products.billing.backend.facade.slots|products.billing.backend.facade.slots.charge",
                "products.billing.backend.facade.slots.charge: param:untyped has no annotation",
            ),
        ],
    ),
}


def export_rows(world: World, name: str, fields: dict[str, Any]) -> tuple[list[tuple[Any, ...]], list[tuple[str, str]]]:
    config = world.config(rule_table(name, fields))
    native = world.context(config)
    spec = config.rules[name]
    run = primitive_of(spec)(native, spec).evaluate()
    findings = [(tuple(f.identity), f.message, f.unknown) for f in run.findings]
    return findings, [(item.key, item.reason) for item in run.incomplete]


@pytest.mark.parametrize("name", sorted(EXPORT_CORNER_ROWS))
def test_an_export_corner_gives_its_stored_rows(world: World, name: str) -> None:
    findings, incomplete = EXPORT_CORNER_ROWS[name]
    assert export_rows(world, name, CORNERS[name]) == (findings, incomplete)


# Relation R7 of the rule-half design: the order of `exports` changes no row, and `on_unknown` and
# `on_unresolved` change the form of an unknown row (a finding or an incomplete item), not its key.
def test_an_export_rule_gives_the_same_rows_in_any_export_order_and_report_mode(world: World) -> None:
    base = {"primitive": "export", "identity": ["src.module", "dst.symbol"], "ledger": False}
    forward, _ = export_rows(world, "forward", {**base, "exports": ["facade", "adapter"]})
    backward, _ = export_rows(world, "backward", {**base, "exports": ["adapter", "facade"]})
    assert sorted(forward) == sorted(backward) and forward
    incomplete_mode = {**base, "on_unknown": "incomplete", "on_unresolved": "incomplete"}
    found, items = export_rows(world, "quiet", incomplete_mode)
    assert [row for row in forward if not row[2]] == found
    assert sorted("|".join(identity) for identity, _, unknown in forward if unknown) == sorted(key for key, _ in items)


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
    assert_stored(world, "teams", rules, PROVIDED)
    config = world.config(rules, PROVIDED)
    native = world.context(config)
    run = primitive_of(config.rules["teams"])(native, config.rules["teams"]).evaluate()
    assert {finding.message.split(" closes")[0] for finding in run.findings} >= {
        "@team-alerts -> @team-billing",
        "@team-billing -> @team-alerts",
    }


def test_a_provider_field_read_without_its_column_stops_the_evaluator(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = world.config(rule_table("teams", TEAM_CYCLE), PROVIDED)
    native = world.context(config)
    monkeypatch.setattr(RulePlan, "provided", lambda self, names: ([], []))
    with pytest.raises(EngineError, match="field team, which a provider sets"):
        assert native.evaluator


def test_a_plan_with_its_modules_in_another_order_stops_the_evaluator(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = world.config(rule_table("teams", TEAM_CYCLE), PROVIDED)
    native = world.context(config)
    monkeypatch.setattr(RulePlan, "modules", lambda self: list(reversed(self.graph.modules)))
    with pytest.raises(EngineError, match="modules in another order"):
        assert native.evaluator


def test_a_capture_over_a_directory_that_is_not_ascii_gives_one_zone_in_both_engines(world: World) -> None:
    config = world.config(rule_table("ascii", CORNERS["non-ascii-capture-under-within"]))
    assert_stored(world, "ascii", rule_table("ascii", CORNERS["non-ascii-capture-under-within"]))
    found = world.rows(config, "ascii")
    assert {identity for identity, *_ in found} == {("products.é.jobs", "models", "é")}


def test_a_set_read_as_text_is_its_identity_column_in_both_engines(world: World) -> None:
    config = world.config(rule_table("text", CORNERS["set-read-as-text"]))
    assert_stored(world, "text", rule_table("text", CORNERS["set-read-as-text"]))
    found = world.rows(config, "text")
    assert {identity[0] for identity, *_ in found} >= {"core.a", "core.legacy"}


# A module that reaches a product's model as an attribute of the imported models module.
THROUGH_A_MODULE = "from products.billing.backend import models\n\n\ndef h():\n    models.Invoice.objects.all()\n"


INVOICE = "products.billing.backend.models.Invoice"
EVERY_USE = {(INVOICE, "core.a"), (INVOICE, "core.b"), (INVOICE, "core.c")}
JUDGED_AT_THE_IMPORT = CORNERS["uses-judged-at-their-import"]
# (rule keys over the corner, the uses reported, whether the import is reported). A use is left
# to its import only when the rule reports that import: a use the import's judgment does not
# cover, because the rule tells the two apart, is judged on its own.
USES_AT_IMPORT_CASES = {
    "every use, without the key": ({"uses_at_import": False}, EVERY_USE, True),
    # A use of a name the module imports goes; a use through an imported module stays.
    "the key": ({}, {(INVOICE, "core.c")}, True),
    "a deny that reads the edge kind": (
        {"deny": "dst.tier == 'product' and edge.kind == 'reference'"},
        EVERY_USE,
        False,
    ),
    "a select that reads the edge kind": (
        {"select": f"{JUDGED_AT_THE_IMPORT['select']} and edge.kind == 'reference'"},
        EVERY_USE,
        False,
    ),
    "an allow that reads the source symbol": (
        {"allow": "src.symbol == src.module"},
        EVERY_USE,
        False,
    ),
}


@pytest.mark.parametrize("case", USES_AT_IMPORT_CASES)
def test_an_entrypoint_rule_judges_a_use_at_its_import_in_both_engines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    keys, references, import_reported = USES_AT_IMPORT_CASES[case]
    monkeypatch.setitem(SOURCES, "core/c.py", THROUGH_A_MODULE)
    world = World(tmp_path)
    spec_table = {**JUDGED_AT_THE_IMPORT, **keys}
    if "allow" in keys:
        del spec_table["deny"]
    config = world.config(rule_table("uses", spec_table))
    assert_stored(world, f"uses at import: {case}", rule_table("uses", spec_table))
    found = world.rows(config, "uses")
    identities = {identity for identity, *_ in found}
    assert {(symbol, module) for symbol, module, kind in identities if kind == "reference"} == references
    assert ((INVOICE, "core.a", "import") in identities) is import_reported


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
    assert_stored(world, f"doors {members}", rule_table("doors", CORNERS["export-by-class-and-audience"]), base)
    found = world.rows(config, "doors")
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
    native = world.context(config)
    native.__dict__["evaluator"] = Refusing()
    with pytest.raises(EngineError, match="refused"):
        if primitive == "conflicts":
            native.find_conflicts()
        else:
            primitive_of(config.rules["refused"])(native, config.rules["refused"]).evaluate()


def test_a_use_shape_rule_on_the_flow_shape_keeps_edges_whose_link_shape_differs(world: World) -> None:
    config = world.config(rule_table("flows", CORNERS["flow-shape-use-shape"]))
    native = world.context(config)
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
        if key == "from" and mode in ("reach", "export"):
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
            "through_denied": lambda: rng.random() < 0.5,
            "allow_shapes": lambda: rng.sample(["write", "write(save)", "instance-many", "call", "attribute"], 2),
            "deny_shapes": lambda: rng.sample(["write", "write(delete)", "write(save)", "instance-many"], 2),
            "unknown_types": lambda: rng.sample(UNKNOWN_TYPES, rng.randint(1, 2)),
            "leave_slots_to": lambda: [named] if named else [],
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
        if mode == "export" and rng.random() < 0.7:
            named = f"{name}-owner"
            out += rule_table(named, {**self.table("signature"), "ledger": False})
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


def test_the_generated_rules_reach_every_key_of_the_schema(world: World) -> None:
    tables = Tables(random.Random(20261111))
    drawn: list[tuple[str, frozenset[str], bool]] = []
    # Every mode in turn, until a table of it loads, as the stored runs draw them.
    for mode in list(MODES) * 24:
        while True:
            before = len(tables.drawn)
            rules = tables.rules("generated", mode)
            try:
                world.config(rules)
            except ConfigError:
                drawn.extend((drawn_mode, keys, False) for drawn_mode, keys in tables.drawn[before:])
                continue
            drawn.extend((drawn_mode, keys, True) for drawn_mode, keys in tables.drawn[before:])
            break
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


# Rounds of one draw per rule type: at least the first, and at most the second while a declared key
# is still unread, so a new key needs no new seed.
KEY_ROUNDS, KEY_ROUNDS_MAX = 48, 480


class KeysRead:
    """Per mode, the keys its rule class read in its constructor, its plan and a native run whose
    every message is read, and the keys the native plan read to plan it; both among the keys its
    primitive accepts and some rules do not read (`EVERY_RULE_READS` aside)."""

    def __init__(self) -> None:
        self.by_class: dict[str, set[str]] = {mode: set() for mode in TYPES}
        self.by_plan: dict[str, set[str]] = {mode: set() for mode in TYPES}

    def record(self, world: World, config: Config) -> None:
        native = world.context(config)
        for spec in config.rules.values():
            cls = primitive_of(spec)
            accepted = RULE_KEYS[cls.PRIMITIVE] - EVERY_RULE_READS
            planned: Any = Recording(spec)
            rule = cls(native, planned)
            self.by_class[cls.MODE] |= planned.keys & accepted
            planned.keys.clear()
            rule.plan()
            self.by_plan[cls.MODE] |= planned.keys & accepted
            self.by_class[cls.MODE] |= planned.keys & accepted
            ran: Any = Recording(spec)
            run = native.results[spec.id] = cls(native, ran).evaluate()
            for finding in run.findings:
                assert finding.message is not None and finding.remedies is not None
            self.by_class[cls.MODE] |= ran.keys & accepted

    def every_key_read(self) -> bool:
        return all(cls.READS <= self.by_class[mode] for mode, cls in TYPES.items())

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
    tables = Tables(random.Random(20261113))
    read = KeysRead()
    # A key such as `on_unresolved` is read only when a drawn rule meets an unresolved edge, so
    # each type is drawn a number of rounds, then more until it has read every key it declares.
    for drawn in range(KEY_ROUNDS_MAX):
        if drawn >= KEY_ROUNDS and read.every_key_read():
            break
        for mode in TYPES:
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


def test_a_symbol_label_set_two_ways_outside_the_graphs_symbols_stops_the_run_where_read(world: World) -> None:
    base = CONFIG.replace(SILO, TWO_SILOS) + "".join(
        f'\n[[assign]]\nmatch = "core/legacy.py"\nsymbols = {{}}\nset = {{ silo = "{silo}" }}\n'
        for silo in ("cell", "control")
    )
    reads = rule_table("reads", {**EDGE, "kinds": ["import"], "endpoint": "surface", "deny": "dst.silo == 'cell'"})
    assert outcome(world, world.config("", base)) == []
    native = outcome(world, world.config(reads, base))
    assert [problem.partition(" by ")[0] for problem in native] == ["core.legacy.api: field 'silo' set differently"]
    assert_stored(world, "silo read", reads, base)


def test_an_edge_with_no_destination_reads_its_source(world: World) -> None:
    lead = (
        CONFIG.replace("[fields]\n", '[fields]\nlead = { type = "string" }\n')
        + '\n[providers.codeowners]\nuse = "codeowners"\nfile = "CODEOWNERS"\nsets = ["lead"]\n'
        + '\n[[assign]]\nmatch = "products/billing/**"\nset = { lead = "someone" }\n'
    )
    reads = rule_table("via", {**EDGE, "endpoint": "via", "select": "src.scope startswith src.lead"})
    native = outcome(world, world.config(reads, lead))
    assert_stored(world, "via", reads, lead)
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


# The runs of the generated configs, stored: one JSON list per line, a config's line (a digest of
# its base config and rules) before its rows. Rewrite it with `HOGLINT_REFRESH_GENERATED=1` after
# a change of the generators, and review the diff.
GENERATED_RUNS = Path(__file__).parent / "generated_runs.jsonl"


def generated_configs(world: World) -> Iterator[tuple[str, str]]:
    """(base config, rules) of every config the generated tests draw: the generated rules over the
    fixture's config, configs that differ outside `[rules]`, and configs that set a label two ways."""
    tables = Tables(random.Random(20261111))
    for mode in list(MODES) * 24:
        while True:
            rules = tables.rules("generated", mode)
            try:
                world.config(rules)
            except ConfigError:
                continue
            break
        yield CONFIG, rules
    rng = random.Random(1111)
    judged = 0
    while judged < 60:
        varied = Varied(rng)
        base = varied.config()
        try:
            world.config("", base)
        except ConfigError:
            continue
        judged += 1
        yield base, varied.rules(world, base)
    rng = random.Random(1110)
    judged = 0
    while judged < 150:
        conflicting = Conflicting(rng)
        text = conflicting.config()
        try:
            world.config("", text)
        except ConfigError:
            continue
        text += conflicting.rules(world, text)
        judged += 1
        yield text, ""


def config_digest(base: str, rules: str) -> str:
    return hashlib.blake2b((base + rules).encode(), digest_size=8).hexdigest()


def stored_runs() -> dict[int, tuple[str, list[list[Any]]]]:
    """The stored runs by config: its digest and its lines."""
    out: dict[int, tuple[str, list[list[Any]]]] = {}
    for text in GENERATED_RUNS.read_text().splitlines():
        line = json.loads(text)
        if line[0] == "config":
            out[line[1]] = (line[2], [])
        else:
            out[line[1]][1].append(line)
    return out


def test_a_generated_config_gives_its_stored_run(world: World) -> None:
    configs = list(generated_configs(world))
    if os.environ.get("HOGLINT_REFRESH_GENERATED"):
        lines: list[list[Any]] = []
        for at, (base, rules) in enumerate(configs):
            lines.append(["config", at, config_digest(base, rules)])
            lines.extend(run_lines(world, at, base, rules))
        GENERATED_RUNS.write_text("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines))
    stored = stored_runs()
    assert len(stored) == len(configs) == 24 * len(MODES) + 60 + 150
    moved = []
    for at, (base, rules) in enumerate(configs):
        digest, lines = stored[at]
        if digest != config_digest(base, rules):
            moved.append(f"config {at} is not the stored one: the generators changed")
            continue
        found = json.loads(json.dumps(run_lines(world, at, base, rules), ensure_ascii=False))
        if found != lines:
            first = next((a, b) for a, b in zip([*found, None], [*lines, None]) if a != b)
            moved.append(f"config {at}: given {first[0]}, stored {first[1]}")
    assert moved == []


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
    native = world.context(config)
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


def test_a_coverage_rule_gives_its_stored_paths(world: World) -> None:
    assert_stored(world, "covered", COVERED)
    config = world.config(COVERED)
    native = world.context(config)
    native.results["named"] = primitive_of(config.rules["named"])(native, config.rules["named"]).evaluate()
    run = primitive_of(config.rules["covered"])(native, config.rules["covered"]).evaluate()
    assert len(run.findings) > 3 and run.incomplete


MODULE_COVERS = rule_table(
    "module-covered",
    {
        "primitive": "coverage",
        "covers": [
            "zone == 'models' or (registry != none and tier == 'product'): module",
            "package == 'alerts' and tier == 'product': module",
        ],
        "inputs": {"adapter": "globs", "paths": ["products/billing/backend/models.py"]},
        "identity": ["subject"],
        "ledger": False,
    },
)


def test_a_module_cover_needs_the_file_of_each_module_an_entity_selection_keeps(world: World) -> None:
    config = world.config(MODULE_COVERS)
    native = world.context(config)
    run = primitive_of(config.rules["module-covered"])(native, config.rules["module-covered"]).evaluate()
    unwatched = {finding.identity[0]: finding.message for finding in run.findings}
    assert "products/billing/backend/models.py" not in unwatched
    assert (
        "zone == 'models' or (registry != none and tier == 'product'): module"
        in unwatched["products/billing/backend/routes.py"]
    )
    assert {"products/alerts/backend/logic.py", "products/alerts/jobs.py"} <= set(unwatched)
    assert not any(path.startswith("core/") for path in unwatched)
    # products/__init__.py is in the package field's domain, and nothing sets it there.
    unknown = [item for item in run.incomplete if item.subjects == ("products",)]
    assert [item.reason for item in unknown] == [
        "cannot tell whether `package == 'alerts' and tier == 'product': module` selects products"
    ]


def test_a_surface_with_a_control_character_is_worded_once(world: World) -> None:
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
    assert_stored(world, "unplaced", rules)
    config = world.config(rules)
    native = world.context(config)
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
            {"reach": lambda rows: [(*row[:3], "maybe" if row[3] is not None else None, row[4]) for row in rows]},
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


LEVELS = (
    rule_table("crossing", {**EDGE, "kinds": ["import"], "deny": "dst.package != src.package"})
    + '\n[levels.isolated]\ngroup_by = "package"\nselect = "tier != \'leaf\'"\nrequires = ["crossing"]\n'
    + '\n[levels.zoned]\ngroup_by = "zone"\nrequires = ["crossing"]\nstrict = ["crossing"]\n'
)


def test_a_level_gives_its_stored_groups(world: World) -> None:
    def levels() -> dict[str, tuple[list[str], dict[str, list[str]]]]:
        result = Engine(config, world.graph, world.repo).evaluate()
        return {name: (level.passing, dict(level.failing)) for name, level in result.levels.items()}

    config = world.config(LEVELS)
    native = levels()
    assert_stored(world, "levels", LEVELS)
    assert all(passing or failing for passing, failing in native.values())


# So many owners that a frozenset of them almost never iterates in sorted order, whatever the hash seed.
OWNERS = [f"team-{letter}" for letter in "zyxwvutsrqponmlkjihgfedcba"]


def test_a_level_grouped_by_a_set_field_names_a_group_by_its_sorted_items(world: World) -> None:
    base = CONFIG.replace('owners = ["team-core", "team-devex"]', f"owners = {json.dumps(OWNERS)}")
    rules = LEVELS + '\n[levels.owned]\ngroup_by = "owners"\nrequires = ["crossing"]\n'
    level = Engine(world.config(rules, base), world.graph, world.repo).evaluate().levels["owned"]
    items = ", ".join(repr(owner) for owner in sorted(OWNERS))
    assert f"frozenset({{{items}}})" in [*level.passing, *level.failing]


def test_the_scan_names_a_set_label_by_its_sorted_items(world: World) -> None:
    base = CONFIG.replace('owners = ["team-core", "team-devex"]', f"owners = {json.dumps(OWNERS)}")
    crossing = {**EDGE, "kinds": ["import"], "deny": "dst.package != src.package", "ledger": True}
    rules = rule_table("crossing", {**crossing, "growth_key": ["src.owners"]}) + '\n[roles]\ngroup = "owners"\n'
    result = Engine(world.config(rules, base), world.graph, world.repo).evaluate()
    owners = "frozenset({" + ", ".join(repr(owner) for owner in sorted(OWNERS)) + "})"
    assert owners in source_labels(result, {})["owners"].values()
    assert owners in {group for src, dst, _, _ in group_triples(result, world.graph) for group in (src, dst)}


def test_a_set_a_provider_gives_a_text_field_reads_as_its_sorted_items(world: World) -> None:
    teams = world.repo / "TEAMS"
    teams.write_text("products/billing/ " + " ".join(f"@{owner}" for owner in OWNERS) + "\n")
    base = CONFIG.replace("[fields]\n", '[fields]\nlead = { type = "string" }\n')
    base += '\n[providers.codeowners]\nuse = "codeowners"\nfile = "TEAMS"\nsets = ["lead"]\n'
    rules = LEVELS + '\n[levels.led]\ngroup_by = "lead"\nrequires = ["crossing"]\n'
    try:
        level = Engine(world.config(rules, base), world.graph, world.repo).evaluate().levels["led"]
    finally:
        teams.unlink()
    lead = "frozenset({" + ", ".join(repr(f"@{owner}") for owner in sorted(OWNERS)) + "})"
    assert lead in [*level.passing, *level.failing]


# Rows between two first-party packages, and rows inside one package across zones.
WITHIN_GROUP = rule_table(
    "crossing", {**EDGE, "kinds": ["import"], "deny": "dst.package != src.package and dst.external == none"}
) + rule_table("inside", {**EDGE, "kinds": ["import"], "deny": "dst.package == src.package and dst.zone != src.zone"})


@pytest.mark.parametrize("within_group", [False, True])
def test_a_level_reads_a_within_group_rule_only_inside_one_group(world: World, within_group: bool) -> None:
    level = '\n[levels.own]\ngroup_by = "package"\nrequires = ["crossing", "inside"]\nstrict = ["crossing", "inside"]\n'
    if within_group:
        level += 'within_group = ["crossing", "inside"]\n'
    result = Engine(world.config(WITHIN_GROUP + level), world.graph, world.repo).evaluate().levels["own"]
    reasons = [reason for found in result.failing.values() for reason in found]
    # A row whose destination did not resolve names one group: it still counts.
    known = {reason.split(":")[0] for reason in reasons if ": unknown: " not in reason}
    assert known == ({"inside"} if within_group else {"crossing", "inside"})
    assert any(reason.startswith("crossing: unknown: ") for reason in reasons)
    assert any(reason.startswith("inside: ") for reason in result.failing["billing"])


CLAIMED = (
    '\n[levels.own]\ngroup_by = "package"\nrequires = ["crossing"]\nstrict = ["crossing"]\n'
    'claimed_by = { adapter = "globs", narrowed = ["products/billing", "products/alerts/backend/facade"] }\n'
)


@pytest.mark.parametrize("rewrite_ledger", [False, True])
def test_a_group_that_claims_a_level_it_fails_fails_the_run_whatever_the_ledger(
    world: World, rewrite_ledger: bool
) -> None:
    config = world.config(WITHIN_GROUP + CLAIMED)
    result = Engine(config, world.graph, world.repo).evaluate(rewrite_ledger=rewrite_ledger)
    level = result.levels["own"]
    # One narrowed path of a package is enough for the package to claim the level.
    assert level.claims == {"alerts": "inputs.narrowed", "billing": "inputs.narrowed"}
    failed = dict(level.failed_claims())
    assert sorted(failed) == sorted(set(level.claims) - set(level.passing)) and failed
    assert all(failed[group] == level.failing[group] for group in failed)
    assert result.claims_failed()


def test_a_group_that_claims_a_level_it_passes_and_a_group_that_claims_nothing_fail_no_run(world: World) -> None:
    rules = rule_table("crossing", {**EDGE, "kinds": ["import"], "deny": "false"})
    claimed = CLAIMED.replace('"products/billing", ', "")
    result = Engine(world.config(rules + claimed), world.graph, world.repo).evaluate(rewrite_ledger=True)
    level = result.levels["own"]
    assert level.claims == {"alerts": "inputs.narrowed"} and "billing" in level.blocks
    assert level.failed_claims() == [] and not result.claims_failed()


def test_a_level_claimed_through_an_input_the_run_cannot_read_fails_the_run(world: World) -> None:
    config = world.config(WITHIN_GROUP + CLAIMED)
    result = Engine(config, world.graph, None).evaluate(rewrite_ledger=True)
    assert "no repo root" in result.levels["own"].claim_error and result.claims_failed()


FACADE_FILES = {
    "primitive": "layout",
    "within": "products/{product}",
    "files": [
        "backend/{facade,nowhere}",
        "backend/facade/{contracts.py,contracts}",
        "backend/facade/api.py: function",
        "backend/facade/__init__.py: function",
        "backend/presentation/views.py: class",
        "backend: function",
    ],
    "ledger": False,
}


def test_a_required_file_may_have_alternatives_and_name_what_it_defines(world: World) -> None:
    rows = world.rows(world.config(rule_table("facade-files", FACADE_FILES)), "facade-files")
    findings = sorted(message for _, message, _, _, _ in rows[: len(rows) - 3])
    assert findings == [
        "products/alerts lacks backend/facade/{contracts.py,contracts}",
        "products/alerts lacks backend/presentation/views.py",
        "products/alerts/backend/facade/__init__.py defines no function",
        "products/billing/backend/facade/__init__.py defines no function",
        "products/é lacks backend/facade/__init__.py",
        "products/é lacks backend/facade/api.py",
        "products/é lacks backend/facade/{contracts.py,contracts}",
        "products/é lacks backend/presentation/views.py",
        "products/é lacks backend/{facade,nowhere}",
    ]
    assert sorted(row[0] for row in rows[len(rows) - 3 :]) == [
        "products/alerts/backend",
        "products/billing/backend",
        "products/é/backend",
    ]


def test_a_level_reads_a_layout_root_without_init_as_its_own_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(SOURCES, "products/loose/tool.py", "def run():\n    pass\n")
    loose = World(tmp_path)
    rule = {"primitive": "layout", "within": "products/{product}", "files": ["backend/facade/api.py"], "ledger": False}
    level = '\n[levels.faced]\ngroup_by = "package"\nrequires = ["faced"]\nstrict = ["faced"]\n'
    result = Engine(loose.config(rule_table("faced", rule) + level), loose.graph, loose.repo).evaluate()
    assert result.levels["faced"].failing["loose"] == [
        "faced: strict, so the ledger does not excuse it: products/loose lacks backend/facade/api.py"
    ]


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
def test_a_conflict_only_a_level_meets_stops_the_run(world: World, level: str) -> None:
    rules = LEVELS.replace('group_by = "zone"', level)
    config = world.config(rules, LEAD_SET_TWO_WAYS)
    native = problems(world, config)
    assert_stored(world, f"level conflict {level}", rules, LEAD_SET_TWO_WAYS)
    assert native and all("field 'lead' set differently" in problem for problem in native)


EXPECTED_KINDS = CONFIG.replace(
    'model = { subclass_of = "products.billing.backend.models.Base" }',
    'model = { subclass_of = "products.billing.backend.models.Base", expected = true }\n'
    'ghost = { subclass_of = "nowhere.Ghost", expected = true }',
)


def test_an_expected_kind_no_symbol_holds_makes_a_rule_incomplete(world: World) -> None:
    rules = rule_table("kinds", {**EDGE, "deny": "dst.kind in ['ghost', 'model']"})
    config = world.config(rules, EXPECTED_KINDS)

    def reasons() -> list[str]:
        return [item.reason for item in Engine(config, world.graph, world.repo).evaluate().incomplete()]

    assert reasons() == ["kind ghost matched no symbol, but [kinds] marks it expected"]


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
def test_a_provider_field_read_in_each_role_gets_its_column(world: World, role: str) -> None:
    rules, base = COLUMN_ROLES[role]
    config = world.config(rules, base)
    native = world.context(config)
    reads = native_module().Rules.reads(native.native.native.link, json.dumps(native.plan.data()))
    assert set(reads) & {"team", "lead"}
    assert_stored(world, f"column role {role}", rules, base)


# An export rule's slot owners are selectors it compiles itself: a provider field one reads gets its
# column even where no signature rule reads that field in its own selection.
def test_a_provider_field_a_slot_owner_reads_gets_its_column(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    owner = Selector.parse("src.team == '@team-billing'").node
    named = SlotRule("seer", owner, None, "origin", frozenset(), frozenset())
    monkeypatch.setattr(ExportPrimitive, "named_slot_rules", lambda self: [named])
    export = {
        "primitive": "export",
        "leave_slots_to": ["seer"],
        "identity": ["src.module", "dst.symbol", "edge.label"],
    }
    rules = rule_table("read", {**export, "ledger": False}) + rule_table("seer", SIGNATURE)
    config = world.config(rules, PROVIDED)
    native = world.context(config)
    reads = native_module().Rules.reads(native.native.native.link, json.dumps(native.plan.data()))
    assert reads == ["team"]
    assert_stored(world, "slot owner column", rules, PROVIDED)
