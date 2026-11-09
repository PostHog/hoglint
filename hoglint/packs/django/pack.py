"""The Django pack: every Django-specific producer, run over one snapshot into a Graph."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from hoglint.model import Edge, Graph
from hoglint.packs.django.apps import AppRegistry, SettingsReader
from hoglint.packs.django.config import DjangoPackConfig
from hoglint.packs.django.installs import InstallsProducer
from hoglint.packs.django.loaders import AppLoads
from hoglint.packs.django.migrations import AliasProjection, MigrationEdges, MigrationReader
from hoglint.packs.django.models import ModelRegistry, RelationBuilder
from hoglint.packs.django.relations import ModelFacts, RelationEdges
from hoglint.packs.django.signals import SignalProducer
from hoglint.packs.django.sites import first_party_records
from hoglint.packs.django.symbols import SymbolResolver
from hoglint.packs.django.uses import GetModelProducer, UseProducer
from hoglint.spike.core import Core

MODELS_PRODUCER = "django.models"
DEPENDENT_PRODUCERS = (
    "django.migrations",
    "django.relations",
    "django.uses",
    "django.model-labels",
    "django.signals",
    "django.installs",
    "django.loaders",
)


class DjangoProducers:
    """The producers that need the app registry and the model index."""

    def __init__(self, core: Core, config: DjangoPackConfig, repo_root: Path) -> None:
        self.core = core
        self.config = config
        self.repo_root = repo_root
        self.symbols = SymbolResolver(core)
        self.apps = AppRegistry(core, config)
        self.registry = ModelRegistry(core, self.apps, config)
        self.registry.discover()
        self.files = first_party_records(core, config.first_party)
        self.databases: dict[str, frozenset[str]] = {}

    def migrations(self) -> list[Edge]:
        projection = AliasProjection(self.repo_root, self.config.routing)
        migrations = MigrationReader(self.core, self.apps, self.config).read(projection)
        self.databases = migrations.aliases
        return MigrationEdges(migrations).produce()

    def relations(self) -> list[Edge]:
        builder = RelationBuilder(self.registry)
        return RelationEdges(self.registry, builder, self.core.index.path_of).produce()

    def references(self) -> list[Edge]:
        return UseProducer(self.core, self.symbols, self.registry).produce(self.files)

    def model_labels(self) -> list[Edge]:
        return GetModelProducer(self.core, self.registry).produce(self.files)

    def signals(self) -> list[Edge]:
        return SignalProducer(self.core, self.symbols, self.registry, self.config).produce(self.files)

    def installs(self) -> list[Edge]:
        return InstallsProducer(self.symbols, self.config.hooks, self.config.first_party).produce(self.files)

    def loads(self) -> list[Edge]:
        settings = SettingsReader(self.core).modules(self.config.settings_modules)
        return AppLoads(self.core, self.apps, settings, self.config.first_party).produce()

    def in_order(self) -> list[tuple[str, Callable[[], list[Edge]]]]:
        return [
            ("django.migrations", self.migrations),
            ("django.relations", self.relations),
            ("django.uses", self.references),
            ("django.model-labels", self.model_labels),
            ("django.signals", self.signals),
            ("django.installs", self.installs),
            ("django.loaders", self.loads),
        ]


class DjangoPack:
    """Runs the producers in dependency order. A producer that raises is recorded, never fatal."""

    def __init__(self, core: Core, config: DjangoPackConfig, repo_root: Path) -> None:
        self.core = core
        self.config = config
        self.repo_root = repo_root

    def produce(self, graph: Graph) -> DjangoProducers | None:
        try:
            producers = DjangoProducers(self.core, self.config, self.repo_root)
        except Exception as error:  # a broken producer makes its rules incomplete, it does not stop the run
            graph.failed_producers[MODELS_PRODUCER] = f"{type(error).__name__}: {error}"
            for name in DEPENDENT_PRODUCERS:
                graph.failed_producers[name] = f"{MODELS_PRODUCER} failed"
            return None
        for name, producer in producers.in_order():
            try:
                graph.edges.extend(producer())
            except Exception as error:
                graph.failed_producers[name] = f"{type(error).__name__}: {error}"
        graph.symbols.update(ModelFacts(producers.registry, producers.apps).symbols(producers.databases))
        return producers
