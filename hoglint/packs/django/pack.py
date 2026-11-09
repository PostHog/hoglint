"""The Django pack: every Django-specific producer, run over one snapshot into a Graph."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from hoglint.core.analysis import AnalysisResult
from hoglint.core.build import CoreBuild
from hoglint.core.harness import ProducerPlan, ProducerStep
from hoglint.facts import FactNeeds
from hoglint.model import Edge, Graph
from hoglint.packs.django.apps import AppRegistry
from hoglint.packs.django.config import ALLOWED_KEYS, SECTION, DjangoPackConfig
from hoglint.packs.django.installs import INSTALLS_PRODUCER, InstallsProducer
from hoglint.packs.django.loaders import LOADS_PRODUCER, AppLoads
from hoglint.packs.django.migrations import (
    MIGRATION_PRODUCER,
    AliasProjection,
    MigrationEdges,
    MigrationGraph,
    MigrationReader,
)
from hoglint.packs.django.models import ModelRegistry, RelationBuilder
from hoglint.packs.django.relations import RELATIONS_PRODUCER, ModelFacts, RelationEdges
from hoglint.packs.django.signals import SIGNALS_PRODUCER, SignalProducer
from hoglint.packs.django.uses import GET_MODEL_PRODUCER, USES_PRODUCER, GetModelProducer, UseProducer
from hoglint.plugins import ProducerFailure

APPS_PRODUCER = "django.apps@1"
MODELS_PRODUCER = "django.models@1"
SYMBOLS_PRODUCER = "django.symbols@1"
MODEL_KINDS = frozenset({"relation", "reverse-accessor", "model-label", "reference", "signal"})
EDGE_KINDS = frozenset({*MODEL_KINDS, "migration-parent", "installs", "loads"})
FACT_NEEDS = FactNeeds(
    kinds=frozenset({"imports", "defs", "assigns", "refs", "derived", "uses", "calls", "callees"}),
    watch_callees=frozenset({"get_model", "setdefault", "append", "connect"}),
)


class NoModels(LookupError):
    """No first-party model found: the registry read nothing, which is never a clean answer."""


class DjangoRun:
    """One run of the pack's producers. Each step stores what later steps read."""

    def __init__(self, build: CoreBuild, config: DjangoPackConfig, repo_root: Path, graph: Graph) -> None:
        self.build = build
        self.config = config
        self.repo_root = repo_root
        self.graph = graph
        self.files = [
            scope
            for scope in build.files
            if scope.record.get("status") == "ok" and build.first_party.contains(scope.module)
        ]
        self.apps: AppRegistry | None = None
        self.registry: ModelRegistry | None = None
        self.migrations: MigrationGraph | None = None

    def read_apps(self) -> list[Edge]:
        self.apps = AppRegistry(self.build, self.config)
        return []

    def read_models(self) -> list[Edge]:
        assert self.apps is not None
        self.registry = ModelRegistry(self.build, self.apps)
        self.registry.discover()
        if not self.registry.models:
            raise NoModels("no first-party model found")
        failures = self.registry.failures
        if failures:
            reason = f"{len(failures)} classes unread, first {failures[0]}"
            self.graph.failed_producers[MODELS_PRODUCER] = ProducerFailure(reason, MODEL_KINDS)
        return []

    def migration_edges(self) -> list[Edge]:
        assert self.apps is not None
        projection = AliasProjection(self.repo_root, self.config.routing)
        self.migrations = MigrationReader(self.build, self.apps, self.config).read(projection)
        return MigrationEdges(self.migrations, self.build.external).produce()

    def relation_edges(self) -> list[Edge]:
        assert self.registry is not None
        builder = RelationBuilder(self.registry, self.config)
        return RelationEdges(self.registry, builder, self.build.index.path_of).produce()

    def use_edges(self) -> list[Edge]:
        assert self.registry is not None
        return UseProducer(self.build, self.registry).produce(self.files, self.graph.by_kind("reference"))

    def get_model_edges(self) -> list[Edge]:
        assert self.registry is not None
        return GetModelProducer(self.build, self.registry).produce(self.files)

    def signal_edges(self) -> list[Edge]:
        assert self.registry is not None
        return SignalProducer(self.build, self.registry, self.config).produce(self.files)

    def installs_edges(self) -> list[Edge]:
        return InstallsProducer(self.build, self.config.hooks).produce(self.files)

    def load_edges(self) -> list[Edge]:
        assert self.apps is not None
        return AppLoads(self.build, self.apps).produce()

    def symbols(self) -> list[Edge]:
        assert self.registry is not None and self.apps is not None
        databases = self.migrations.aliases if self.migrations is not None else {}
        self.graph.symbols.update(ModelFacts(self.registry, self.apps).symbols(databases))
        return []

    def plan(self) -> ProducerPlan:
        return ProducerPlan(
            [
                ProducerStep(APPS_PRODUCER, EDGE_KINDS, self.read_apps),
                ProducerStep(MODELS_PRODUCER, MODEL_KINDS, self.read_models, (APPS_PRODUCER,)),
                ProducerStep(
                    MIGRATION_PRODUCER, frozenset({"migration-parent"}), self.migration_edges, (APPS_PRODUCER,)
                ),
                ProducerStep(
                    RELATIONS_PRODUCER,
                    frozenset({"relation", "reverse-accessor", "model-label"}),
                    self.relation_edges,
                    (MODELS_PRODUCER,),
                ),
                ProducerStep(USES_PRODUCER, frozenset({"reference"}), self.use_edges, (MODELS_PRODUCER,)),
                ProducerStep(GET_MODEL_PRODUCER, frozenset({"model-label"}), self.get_model_edges, (MODELS_PRODUCER,)),
                ProducerStep(SIGNALS_PRODUCER, frozenset({"signal"}), self.signal_edges, (MODELS_PRODUCER,)),
                ProducerStep(INSTALLS_PRODUCER, frozenset({"installs"}), self.installs_edges),
                ProducerStep(LOADS_PRODUCER, frozenset({"loads"}), self.load_edges, (APPS_PRODUCER,)),
                ProducerStep(SYMBOLS_PRODUCER, frozenset(), self.symbols, (MODELS_PRODUCER,)),
            ]
        )


class DjangoPack:
    """The `[django]` section's pack. Core reference edges must be in the graph first."""

    name = SECTION
    allowed_keys = ALLOWED_KEYS
    edge_kinds = EDGE_KINDS

    def fact_needs(self, section: Mapping[str, Any]) -> FactNeeds:
        return FACT_NEEDS

    def produce(self, section: Mapping[str, Any], core: AnalysisResult, graph: Graph, root: Path) -> None:
        self.run(core.build, DjangoPackConfig.from_section(section), graph, root)

    @staticmethod
    def run(build: CoreBuild, config: DjangoPackConfig, graph: Graph, root: Path) -> None:
        DjangoRun(build, config, root, graph).plan().run(graph)


DJANGO_PACK = DjangoPack()
