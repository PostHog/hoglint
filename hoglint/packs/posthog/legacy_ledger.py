"""Renders hoglint edges in the line format of products/model_crossing_uses_baseline.txt.

A parity adapter: it proves the edges carry everything today's crossings scanner reads. The
policy filters here (owner by `products/<name>/`, watched classes, allowed shapes) are today's
scanner's, copied so the lines match; hoglint's rules replace them at cutover.
"""

from __future__ import annotations

import sys
import json
import tomllib
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from hoglint.core.analysis import AnalysisOptions
from hoglint.core.config import CoreConfig
from hoglint.core.harness import ProducerPlan, ProducerStep
from hoglint.core.modules import FirstParty
from hoglint.core.reader import BuildReader
from hoglint.core.records import sequence_items, str_literal
from hoglint.model import Context, Edge, Graph, Resolution
from hoglint.native.analysis import NativeAnalysis
from hoglint.native.build import NativeBuild
from hoglint.native.graph import NativeGraph, NativeSink
from hoglint.native.queries import NativePackQueries
from hoglint.packs.django.config import SECTION, DjangoPackConfig
from hoglint.packs.django.pack import DjangoPack
from hoglint.packs.posthog.drives import UNKNOWN_EXECUTION
from hoglint.packs.posthog.facade import FACADE_KINDS, FACADE_PRODUCER, FacadeFacts
from hoglint.packs.posthog.layout import CORE, backend_product, backend_rel, product_of_module, product_of_path
from hoglint.packs.posthog.pack import POSTHOG_PACK

ISOLATION_PATH = "tools/hogli-commands/hogli_commands/product/isolation.py"
BASELINE_PATH = "products/model_crossing_uses_baseline.txt"
NOT_ISOLATED_PATH = "products/isolation_baseline.txt"
GARAGE_PREFIXES = (
    "backend/hogql_queries/",
    "backend/max_tools.py",
    "backend/temporal/",
    "backend/tasks.py",
    "backend/tasks/",
)
MODEL_SURFACE_PREFIXES = ("backend/models/", "backend/models.py", "backend/migrations/")
CAPABILITY_SUBMODULES = frozenset({"dags", "hogql", "max_tools", "models", "queries", "tasks", "temporal"})
NON_CAPABILITY_STEMS = frozenset({"contracts", "enums", "testing"})
UNTYPED_SUBJECT_PARAMS = frozenset({"request", "team", "user"})
LIBRARIES = (
    (("django.db.models", "django.http"), "django"),
    (("rest_framework",), "rest_framework"),
    (("typing", "typing_extensions"), "typing"),
)
APPROVED_WIRING_BASES = frozenset(
    {"ee.hogai.tool.MaxTool", "posthog.hogql_queries.query_runner.QueryRunner", "temporalio.worker.Interceptor"}
)
APPROVED_WIRING_DECORATORS = frozenset({"celery.shared_task", "temporalio.activity.defn", "temporalio.workflow.defn"})
CARVE_OUTS = frozenset(
    {("customer_analytics", "TeamCustomerAnalyticsConfig"), ("tasks", "Task"), ("workflows", "TeamWorkflowsConfig")}
)
REEXPORT_DUNDERS = frozenset({"__getattr__", "__dir__"})
ANY = ("typing", "Any")
ALLOWED_SHAPES = ("annotation", "exception", "nested-class-attr", "_meta", "scalar-chain", "subquery")
OUT_OF_SCOPE = frozenset({Context.TEST, Context.MIGRATION})
ROOT_CONFIG = "hoglint.toml"
# The roots today's scanner treats as repo code.
FIRST_PARTY = ("posthog", "ee", "products", "common")
REPO = FirstParty(FIRST_PARTY)
EXTERNAL_PACKAGES = (
    "axes",
    "corsheaders",
    "django",
    "django_admin_inline_paginator",
    "django_filters",
    "django_linear_migrations",
    "django_otp",
    "django_structlog",
    "drf_spectacular",
    "drf_spectacular_sidecar",
    "loginas",
    "oauth2_provider",
    "rest_framework",
    "social_django",
    "social_core",
    "two_factor",
    "whitenoise",
)
# Today's scanner reads every file, evals included, so the parity run excludes nothing.
PARITY_CONFIG = CoreConfig(
    first_party=FIRST_PARTY,
    external_packages=EXTERNAL_PACKAGES,
    migration_paths=("**/migrations/**",),
)


def is_model_module(module: str) -> bool:
    parts = module.split(".")
    return len(parts) >= 4 and parts[0] == "products" and parts[2:4] == ["backend", "models"]


@dataclass(frozen=True)
class CrossingsPolicy:
    """Today's watched classes: the product models that may cross in instance-free shapes."""

    model_crossings: frozenset[tuple[str, str]]

    @classmethod
    def from_facts(cls, build: NativeBuild) -> CrossingsPolicy:
        record = build.store.records[ISOLATION_PATH]
        for row in record.get("assigns", ()):
            if row["s"] != -1 or "MODEL_CROSSINGS" not in row["t"]:
                continue
            value = row["v"]["args"][0] if "call" in row["v"] else row["v"]
            pairs = set()
            for elt in sequence_items(value) or ():
                product, name = (str_literal(part) for part in elt["tuple"])
                if product is not None and name is not None:
                    pairs.add((product, name))
            return cls(frozenset(pairs))
        raise LookupError(f"MODEL_CROSSINGS not found in {ISOLATION_PATH}")


class LegacyLedger:
    def __init__(self, graph: Graph, policy: CrossingsPolicy) -> None:
        self.graph = graph
        self.policy = policy

    @staticmethod
    def owner_label(model_id: str, model_label: str) -> str:
        module = model_id.rpartition(".")[0]
        owner = product_of_module(module)
        return owner if owner != CORE else model_label.split(".")[0]

    def reverse_accessors(self) -> set[str]:
        out = set()
        for edge in self.graph.by_kind("reverse-accessor"):
            target = edge.target
            if edge.resolution != Resolution.RESOLVED or target is None or not edge.attr("target_label"):
                continue
            model = edge.attr("model")
            if product_of_module(model.rpartition(".")[0]) == product_of_module(target.module):
                continue
            owner = self.owner_label(model, edge.attr("model_label"))
            target_owner = self.owner_label(str(target), edge.attr("target_label"))
            out.add(
                f"{target_owner}.{target.symbol} {owner}.{model.rpartition('.')[2]}.{edge.attr('field')} "
                f"reverse-accessor({edge.surface}) 1"
            )
        return out

    def watched_label(self, edge: Edge) -> str | None:
        target = edge.target
        if target is None or not is_model_module(target.module):
            return None
        product = product_of_module(target.module)
        if (product, target.symbol) not in self.policy.model_crossings:
            return None
        return f"{product}.{target.symbol}"

    def in_scope(self, edge: Edge, product: str) -> bool:
        if edge.contexts & OUT_OF_SCOPE:
            return False
        return product_of_path(edge.site.path) != product

    def shapes(self) -> Counter[tuple[str, str, str]]:
        counts: Counter[tuple[str, str, str]] = Counter()
        for edge in self.graph.by_kind("reference"):
            if edge.resolution != Resolution.RESOLVED or not edge.shape:
                continue
            label = self.watched_label(edge)
            if label is None or not self.in_scope(edge, label.split(".")[0]):
                continue
            if edge.shape.startswith(ALLOWED_SHAPES):
                continue
            kind = f"{edge.shape}({edge.shape_detail})" if edge.shape_detail else edge.shape
            counts[(label, edge.site.module, kind)] += 1
        return counts

    def get_model(self) -> Counter[tuple[str, str, str]]:
        counts: Counter[tuple[str, str, str]] = Counter()
        for edge in self.graph.by_kind("model-label"):
            target = edge.target
            if edge.shape != "get_model" or edge.resolution != Resolution.RESOLVED or target is None:
                continue
            product = product_of_module(target.module)
            if product == CORE or not self.in_scope(edge, product):
                continue
            counts[(f"{product}.{target.symbol}", edge.site.module, "get_model")] += 1
        return counts

    def drives(self) -> Counter[tuple[str, str, str]]:
        """Drives that execute. Today's scanner reads a walk its depth limit stops as none; hoglint
        keeps it as an unknown drive, which is no line here."""
        counts: Counter[tuple[str, str, str]] = Counter()
        for edge in self.graph.by_kind("drives"):
            if edge.attr("execution") in ("none", UNKNOWN_EXECUTION):
                continue
            counts[(edge.attr("location"), edge.site.module, f"drives({edge.shape_detail})")] += 1
        return counts

    def lines(self) -> list[str]:
        counts = self.shapes() + self.get_model() + self.drives()
        out = set(self.reverse_accessors())
        out.update(f"{target} {module} {kind} {n}" for (target, module, kind), n in counts.items())
        return sorted(out)


def isolated_products(not_isolated_text: str, facade_products: set[str]) -> frozenset[str]:
    """Products at the Isolated rung: every product with a facade that the not-isolated list omits."""
    not_isolated = {
        line.strip() for line in not_isolated_text.splitlines() if line.strip() and not line.startswith("#")
    }
    return frozenset(facade_products - not_isolated)


class FacadeLedger:
    """facade-returns, facade-accepts, facade-logic and facade-wiring lines from facade facts."""

    def __init__(self, graph: Graph, policy: CrossingsPolicy, wiring_products: frozenset[str] | None) -> None:
        self.graph = graph
        self.policy = policy
        self.wiring_products = wiring_products
        self.by_module: dict[str, dict[str, list[Edge]]] = defaultdict(lambda: defaultdict(list))
        for edge in graph.by_kind("export", "exposes", "declares"):
            if edge.producer == FACADE_PRODUCER:
                self.by_module[edge.site.module][edge.kind].append(edge)
        self.class_edges: dict[str, dict[str, list[Edge]]] = defaultdict(lambda: defaultdict(list))
        for edge in graph.by_kind("inherit", "decorate"):
            if edge.producer == FACADE_PRODUCER:
                self.class_edges[f"{edge.site.module}.{edge.site.scope}"][edge.kind].append(edge)

    def is_model(self, qualified: str) -> bool:
        facts = self.graph.symbols.get(qualified, {})
        return facts.get("kind") == "model" and facts.get("abstract") != "true"

    def denied(self, edge: Edge, product: str) -> tuple[str, str] | None:
        surface, origin, origin_kind = edge.surface, edge.attr("origin"), edge.attr("origin_kind")
        if origin_kind == "external" or not REPO.contains(surface):
            q = origin if origin_kind == "external" else surface
            for prefixes, library in LIBRARIES:
                if any(q == p or q.startswith(p + ".") for p in prefixes):
                    name = q.rpartition(".")[2]
                    if library == "typing":
                        return ANY if name == "Any" else None
                    return (library, name)
            return None
        if origin_kind != "class":
            return None
        owner = backend_product(origin)
        if owner is None or not self.is_model(origin):
            return None
        rel = surface.rpartition(".")[0].split(".")[3:]
        if rel[:1] == ["facade"]:
            rel = rel[1:]
        if not rel or rel[0] != "models":
            return None
        name = origin.rpartition(".")[2]
        if owner == product and ((product, name) in CARVE_OUTS or (product, name) in self.policy.model_crossings):
            return None
        return (owner, name)

    def reexport_passes(self, edge: Edge, product: str) -> bool:
        origin = edge.attr("reexport")
        origin_module = origin.rpartition(".")[0]
        if edge.site.scope.startswith("_") or edge.attr("defined_here") == "true":
            return False
        rel = edge.attr("rel")
        if not rel or rel.startswith("backend/facade/") or backend_product(origin) != product:
            return False
        if backend_rel(origin_module).startswith("backend/facade/") or origin_module.split(".")[3:4] == ["facade"]:
            return False
        return int(edge.attr("hops")) <= 6

    def signature_lines(self, module: str, product: str, counts: Counter[str]) -> None:
        slots: dict[tuple[str, str, str], list[Edge]] = defaultdict(list)
        first_reexport: dict[tuple[str, str], str] = {}
        for edge in self.by_module[module]["exposes"]:
            reexport = edge.attr("reexport")
            if reexport:
                if not self.reexport_passes(edge, product):
                    continue
                key = (edge.site.scope, reexport)
                first = first_reexport.setdefault(key, edge.attr("signature"))
                if first != edge.attr("signature"):
                    continue
            slots[(edge.site.scope, edge.attr("signature"), edge.attr("slot"))].append(edge)
        for (symbol, _, slot), edges in slots.items():
            found: dict[tuple[str, str], Edge] = {}
            for edge in edges:
                if not edge.surface:
                    continue
                if REPO.contains(edge.surface) and backend_product(edge.surface) is None:
                    continue
                denied = self.denied(edge, product)
                if denied is not None:
                    found.setdefault(denied, edge)
            for (source, name), edge in found.items():
                if slot == "return":
                    if (source, name) == ANY and edge.attr("whole_any") != "true":
                        continue
                    counts[f"{source}.{name} {module}.{symbol} facade-returns"] += 1
                    continue
                param = slot.split(":", 1)[1]
                if (source, name) == ANY and param not in UNTYPED_SUBJECT_PARAMS:
                    continue
                counts[f"{source}.{name} {module}.{symbol} facade-accepts({param})"] += 1

    def handed(self, module: str, product: str) -> list[Edge]:
        return [
            e
            for e in self.by_module[module]["export"]
            if not e.attr("member_of") and e.attr("rel") and backend_product(e.surface) == product
        ]

    @staticmethod
    def key_of(path: str) -> str:
        return path.split("/", 4)[4]

    def is_capability(self, key: str, handed: list[Edge]) -> bool:
        if "/" not in key:
            stem = key.removesuffix(".py")
            if stem in CAPABILITY_SUBMODULES:
                return True
            if stem.startswith("api") or stem in NON_CAPABILITY_STEMS:
                return False
        return any(e.attr("rel").startswith((*GARAGE_PREFIXES, *MODEL_SURFACE_PREFIXES)) for e in handed)

    def logic_lines(self, module: str, product: str, key: str, handed: list[Edge], counts: Counter[str]) -> None:
        if not self.is_capability(key, handed):
            return
        bodies = 0
        for edge in self.by_module[module]["declares"]:
            if edge.site.scope in REEXPORT_DUNDERS:
                continue
            if edge.attr("wiring_decorator") == "true" or edge.attr("stub") != "true":
                bodies += 1
        if bodies:
            counts[f"{product}:backend/facade/{key} {module} facade-logic"] += bodies

    @staticmethod
    def approved(edge: Edge, approved: frozenset[str]) -> bool:
        names = {edge.surface, edge.attr("origin"), *(str(hop) for hop in edge.via)}
        return bool(names & approved)

    def bases_verdict(self, qualified: str, seen: frozenset[str]) -> str:
        verdicts = []
        inherits = sorted(self.class_edges[qualified]["inherit"], key=lambda e: int(e.attr("position")))
        for edge in inherits:
            if not edge.surface:
                verdicts.append("unresolved")
                continue
            if self.approved(edge, APPROVED_WIRING_BASES):
                return "approved"
            origin, kind = edge.attr("origin"), edge.attr("origin_kind")
            if kind == "class":
                if origin in seen:
                    verdicts.append("unresolved")
                    continue
                verdicts.append(self.bases_verdict(origin, seen | {origin}))
            elif kind == "external":
                verdicts.append("unapproved")
            else:
                verdicts.append("unresolved")
        if "approved" in verdicts:
            return "approved"
        if "unresolved" in verdicts:
            return "unresolved"
        return "unapproved"

    def wiring_verdict(self, qualified: str) -> str:
        if any(self.approved(e, APPROVED_WIRING_DECORATORS) for e in self.class_edges[qualified]["decorate"]):
            return "approved"
        return self.bases_verdict(qualified, frozenset({qualified}))

    def wiring_lines(self, module: str, product: str, handed: list[Edge], counts: Counter[str]) -> None:
        if self.wiring_products is not None and product not in self.wiring_products:
            return
        found: dict[str, str] = {}
        members = [e for e in self.by_module[module]["export"] if e.attr("member_of")]
        for edge in handed:
            if not edge.attr("rel").startswith(GARAGE_PREFIXES):
                continue
            bound = edge.attr("bound")
            if edge.attr("origin_kind") == "class":
                exported = {bound: edge.attr("origin")}
            elif edge.attr("members") == "unknown":
                found[bound] = "unresolved"
                continue
            else:
                exported = {
                    m.attr("bound"): m.attr("origin")
                    for m in members
                    if m.attr("member_of") == bound
                    and m.attr("rel") == edge.attr("rel")
                    and m.attr("origin_kind") == "class"
                }
            for name, qualified in exported.items():
                verdict = self.wiring_verdict(qualified)
                if verdict != "approved":
                    found[name] = verdict
        for name in found:
            counts[f"{product}.{name} {module} facade-wiring"] += 1

    def lines(self) -> list[str]:
        counts: Counter[str] = Counter()
        for module in sorted(self.by_module):
            product = backend_product(module)
            edges = self.by_module[module]
            any_edge = next(iter(edges["declares"] or edges["export"] or edges["exposes"]), None)
            if product is None or any_edge is None:
                continue
            key = self.key_of(any_edge.site.path)
            handed = self.handed(module, product)
            self.signature_lines(module, product, counts)
            self.logic_lines(module, product, key, handed, counts)
            if key not in ("contracts.py", "enums.py"):
                self.wiring_lines(module, product, handed, counts)
        return [f"{line} {n}" for line, n in counts.items()]


def read_baseline(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip() and not line.startswith("#")]


@dataclass(frozen=True)
class ParityResult:
    baseline: frozenset[str]
    derived: frozenset[str]

    @property
    def missing(self) -> list[str]:
        return sorted(self.baseline - self.derived)

    @property
    def extra(self) -> list[str]:
        return sorted(self.derived - self.baseline)


class Parity:
    """Builds the graph with the Django pack and the PostHog producers, then diffs the ledger."""

    def __init__(self, repo_root: Path, build: NativeBuild) -> None:
        self.repo_root = repo_root
        self.build = build

    def produce(self, graph: NativeGraph) -> None:
        """The repo's own `[django]` table drives the Django pack, so parity checks the real config.
        The facade facts producer runs here only: a check gets its slots from core."""
        section = tomllib.loads((self.repo_root / ROOT_CONFIG).read_text()).get(SECTION, {})
        reader = BuildReader(self.build, NativePackQueries(self.build, graph, graph.link))
        sink = NativeSink(graph)
        DjangoPack.run(reader, DjangoPackConfig.from_section(section), sink, self.repo_root)
        POSTHOG_PACK.run(reader, sink)

        def facade() -> list[Edge]:
            return FacadeFacts(reader).produce()

        ProducerPlan([ProducerStep(FACADE_PRODUCER, FACADE_KINDS, facade)]).run(sink)

    def lines(self, graph: Graph) -> list[str]:
        policy = CrossingsPolicy.from_facts(self.build)
        declared = graph.by_kind("declares") + graph.by_kind("export")
        facade_products = {backend_product(e.site.module) for e in declared if e.producer == FACADE_PRODUCER}
        not_isolated = (self.repo_root / NOT_ISOLATED_PATH).read_text()
        wiring = isolated_products(not_isolated, {product for product in facade_products if product is not None})
        return sorted(set(LegacyLedger(graph, policy).lines()) | set(FacadeLedger(graph, policy, wiring).lines()))

    def compare(self, lines: list[str]) -> ParityResult:
        baseline = read_baseline((self.repo_root / BASELINE_PATH).read_text())
        return ParityResult(frozenset(baseline), frozenset(lines))


def main() -> None:
    """Usage: python -m hoglint.packs.posthog.legacy_ledger <repo_root> [<work_dir>]"""
    repo_root = Path(sys.argv[1]).resolve()
    work_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(tempfile.mkdtemp(prefix="hoglint-parity-"))
    analysis = NativeAnalysis(AnalysisOptions(repo=repo_root, config=PARITY_CONFIG, work_dir=work_dir)).run()
    parity = Parity(repo_root, analysis.build)
    graph = analysis.graph
    parity.produce(graph)
    result = parity.compare(parity.lines(graph))
    unresolved_receivers = [e for e in graph.by_kind("signal") if e.resolution == Resolution.UNRESOLVED]
    summary = {
        "baseline": len(result.baseline),
        "derived": len(result.derived),
        "equal": len(result.baseline & result.derived),
        "missing": result.missing,
        "extra": result.extra,
        "failed_producers": {name: failure.reason for name, failure in graph.failed_producers.items()},
        "edges": dict(sorted(Counter(f"{e.kind}:{e.resolution.value}" for e in graph.edges).items())),
        "signal_receivers_unresolved": len(unresolved_receivers),
        "signal_unresolved_reasons": dict(Counter(e.attr("reason") for e in unresolved_receivers)),
    }
    sys.stdout.write(json.dumps(summary, indent=1) + "\n")


if __name__ == "__main__":
    main()
