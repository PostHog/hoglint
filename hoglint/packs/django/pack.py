"""The Django pack: every Django-specific producer, run over one snapshot into a pack sink."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from hoglint.core.harness import Output, ProducerPlan, ProducerStep
from hoglint.core.sites import FileScope
from hoglint.facts import FactNeeds
from hoglint.ledger.store import cache_root
from hoglint.model import Edge
from hoglint.native.graph import NativeSink
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
    SummaryCache,
)
from hoglint.packs.django.models import ModelRegistry, RelationBuilder
from hoglint.packs.django.native_uses import NativeUses
from hoglint.packs.django.relations import RELATIONS_PRODUCER, ModelFacts, RelationEdges
from hoglint.packs.django.signals import SIGNALS_PRODUCER, SignalProducer
from hoglint.packs.django.uses import GET_MODEL_PRODUCER, USES_PRODUCER, GetModelProducer, UseProducer
from hoglint.plugins import PackReader, PackSink, ProducerFailure

APPS_PRODUCER = "django.apps@1"
MODELS_PRODUCER = "django.models@1"
SYMBOLS_PRODUCER = "django.symbols@1"
MODEL_KINDS = frozenset({"relation", "reverse-accessor", "model-label", "reference", "signal"})
USES_KINDS = frozenset({"reference"})
EDGE_KINDS = frozenset({*MODEL_KINDS, "migration-parent", "installs", "loads"})
FACT_NEEDS = FactNeeds(
    kinds=frozenset({"imports", "defs", "assigns", "refs", "derived", "uses", "calls", "callees"}),
    watch_callees=frozenset({"get_model", "setdefault", "append", "connect"}),
)


class NoModels(LookupError):
    """No first-party model found: the registry read nothing, which is never a clean answer."""


class DjangoRun:
    """One run of the pack's producers. Each step stores what later steps read."""

    def __init__(self, reader: PackReader, config: DjangoPackConfig, repo_root: Path, sink: PackSink) -> None:
        self.reader = reader
        self.config = config
        self.repo_root = repo_root
        self.sink = sink
        self.files: list[FileScope] = []
        self.apps: AppRegistry | None = None
        self.registry: ModelRegistry | None = None
        self.migrations: MigrationGraph | None = None

    def scan_roots(self, apps: AppRegistry) -> tuple[str, ...]:
        """The configured roots, else the top-level packages of the first-party installed apps."""
        if self.config.scan_roots:
            return self.config.scan_roots
        first_party = self.reader.first_party
        return tuple(sorted({app.name.partition(".")[0] for app in apps.apps if first_party.contains(app.name)}))

    def read_apps(self) -> list[Edge]:
        self.apps = AppRegistry(self.reader, self.config)
        roots = self.scan_roots(self.apps)
        self.files = [
            scope
            for scope in self.reader.files
            if scope.record.get("status") == "ok"
            and self.reader.first_party.contains(scope.module)
            and any(scope.module == root or scope.module.startswith(root + ".") for root in roots)
        ]
        return []

    def read_models(self) -> list[Edge]:
        assert self.apps is not None
        self.registry = ModelRegistry(self.reader, self.apps)
        self.registry.discover(self.files)
        if not self.registry.models:
            raise NoModels("no first-party model found")
        failures = self.registry.failures
        if failures:
            reason = f"{len(failures)} classes unread, first {failures[0]}"
            self.sink.fail(MODELS_PRODUCER, ProducerFailure(reason, MODEL_KINDS))
        return []

    def migration_edges(self) -> list[Edge]:
        assert self.apps is not None
        projection = AliasProjection(self.repo_root, self.config.routing)
        cache = SummaryCache(cache_root() / "packs")
        self.migrations = MigrationReader(self.reader, self.apps, self.config, cache).read(projection)
        return MigrationEdges(self.migrations, self.reader.external).produce()

    def relation_edges(self) -> list[Edge]:
        assert self.registry is not None
        builder = RelationBuilder(self.registry, self.config)
        return RelationEdges(self.registry, builder, self.reader.index.path_of).produce()

    def use_edges(self) -> Sequence[Output]:
        """On the native engine the link judges the uses (D49); `UseProducer` is the meaning."""
        assert self.registry is not None
        producer = UseProducer(self.reader, self.registry)
        if isinstance(self.sink, NativeSink):
            return NativeUses(producer, self.sink).produce(self.files, USES_KINDS)
        return producer.produce(self.files)

    def get_model_edges(self) -> list[Edge]:
        assert self.registry is not None
        return GetModelProducer(self.reader, self.registry).produce(self.files)

    def signal_edges(self) -> list[Edge]:
        assert self.registry is not None
        return SignalProducer(self.reader, self.registry, self.config).produce(self.files)

    def installs_edges(self) -> list[Edge]:
        return InstallsProducer(self.reader, self.config.hooks).produce(self.files)

    def load_edges(self) -> list[Edge]:
        assert self.apps is not None
        return AppLoads(self.reader, self.apps).produce()

    def symbols(self) -> list[Edge]:
        """Model and app symbols. Rules on model edges read them through `dst.kind`, so a failure
        here makes those edge kinds incomplete."""
        assert self.registry is not None and self.apps is not None and self.migrations is not None
        self.sink.add_symbols(ModelFacts(self.registry, self.apps).symbols(self.migrations.aliases))
        return []

    def plan(self) -> ProducerPlan:
        return ProducerPlan(
            [
                ProducerStep(APPS_PRODUCER, frozenset(), self.read_apps),
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
                ProducerStep(USES_PRODUCER, USES_KINDS, self.use_edges, (MODELS_PRODUCER,)),
                ProducerStep(GET_MODEL_PRODUCER, frozenset({"model-label"}), self.get_model_edges, (MODELS_PRODUCER,)),
                ProducerStep(SIGNALS_PRODUCER, frozenset({"signal"}), self.signal_edges, (MODELS_PRODUCER,)),
                ProducerStep(INSTALLS_PRODUCER, frozenset({"installs"}), self.installs_edges, (APPS_PRODUCER,)),
                ProducerStep(LOADS_PRODUCER, frozenset({"loads"}), self.load_edges, (APPS_PRODUCER,)),
                ProducerStep(SYMBOLS_PRODUCER, MODEL_KINDS, self.symbols, (MODELS_PRODUCER, MIGRATION_PRODUCER)),
            ]
        )


class DjangoPack:
    """The `[django]` section's pack. Core reference edges must be in the graph first."""

    name = SECTION
    allowed_keys = ALLOWED_KEYS
    edge_kinds = EDGE_KINDS
    policy_file_keys = ("db_routing",)  # keys that name policy data files; see hoglint/ledger/envelope.py

    def fact_needs(self, section: Mapping[str, Any]) -> FactNeeds:
        return FACT_NEEDS

    def produce(self, section: Mapping[str, Any], reader: PackReader, sink: PackSink, root: Path) -> None:
        self.run(reader, DjangoPackConfig.from_section(section), sink, root)

    @staticmethod
    def run(reader: PackReader, config: DjangoPackConfig, sink: PackSink, root: Path) -> None:
        DjangoRun(reader, config, root, sink).plan().run(sink)


DJANGO_PACK = DjangoPack()
