"""The Django pack: every Django-specific producer, run over one snapshot into a pack sink."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from hoglint.core.harness import Output, ProducerPlan, ProducerStep
from hoglint.core.memo import ItemOutput, ItemRun, Items
from hoglint.core.sites import FileScope
from hoglint.facts import FactNeeds
from hoglint.ledger.store import cache_root
from hoglint.model import Edge
from hoglint.native.graph import NativeSink
from hoglint.packs.django.apps import AppReader, AppRegistry
from hoglint.packs.django.config import ALLOWED_KEYS, SECTION, DjangoPackConfig
from hoglint.packs.django.installs import INSTALLS_PRODUCER, InstallsItems, InstallsProducer
from hoglint.packs.django.loaders import LOADS_PRODUCER, AppLoads
from hoglint.packs.django.migrations import (
    MIGRATION_PRODUCER,
    AliasProjection,
    MigrationEdges,
    MigrationReader,
    SummaryCache,
)
from hoglint.packs.django.models import ModelItems, ModelRegistry, RelationBuilder
from hoglint.packs.django.native_uses import NativeUses
from hoglint.packs.django.relations import RELATIONS_PRODUCER, ModelFacts, RelationEdges, RelationItems
from hoglint.packs.django.signals import SIGNALS_PRODUCER, SignalItems, SignalProducer
from hoglint.packs.django.uses import GET_MODEL_PRODUCER, USES_PRODUCER, GetModelItems, GetModelProducer, UseProducer
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


class AppItems(Items):
    """`django.apps`: the app registry as one fold. The files later steps scan come after it."""

    def __init__(self, run: DjangoRun) -> None:
        self.run = run

    def folds(self) -> list[str]:
        return ["apps"]

    def run_item(self, key: str) -> ItemOutput:
        return ItemOutput([], AppReader(self.run.reader).read(self.run.config).data())

    def take(self, key: str, data: Any) -> None:
        self.run.apps = AppRegistry.restore(data, self.run.reader.tracer)
        self.run.apps.answer_reads()

    def finish(self) -> Iterable[Output]:
        self.run.files = self.run.scan_files()
        return ()


class ModelStepItems(ModelItems):
    """Model discovery of a run: no model at all fails the step, an unread class fails the
    producer while the other models count."""

    def __init__(self, run: DjangoRun) -> None:
        assert run.registry is not None
        super().__init__(run.registry, run.files)
        self.run = run

    def finish(self) -> Iterable[Output]:
        self.registry.answer_reads()
        if not self.registry.models:
            raise NoModels("no first-party model found")
        failures = self.registry.failures
        if failures:
            reason = f"{len(failures)} classes unread, first {failures[0]}"
            self.run.sink.fail(MODELS_PRODUCER, ProducerFailure(reason, MODEL_KINDS))
        return ()


class MigrationItems(Items):
    """`django.migrations`: the migration graph as one fold; its data is the database aliases
    per app, which the symbols read."""

    def __init__(self, run: DjangoRun) -> None:
        self.run = run
        run.reader.tracer.answer_with(AliasProjection.ROUTING, self.routes)

    def routes(self, key: str) -> object:
        return sorted(AliasProjection(self.run.repo_root, self.run.config.routing).route.items())

    def folds(self) -> list[str]:
        return ["graph"]

    def run_item(self, key: str) -> ItemOutput:
        run = self.run
        assert run.apps is not None
        projection = AliasProjection(run.repo_root, run.config.routing)
        if run.config.routing is not None:
            run.reader.tracer.note(
                AliasProjection.ROUTING, str(run.config.routing.path), sorted(projection.route.items())
            )
        cache = SummaryCache(cache_root() / "packs")
        graph = MigrationReader(run.reader, run.apps, run.config, cache).read(projection)
        aliases = {app: sorted(found) for app, found in graph.aliases.items()}
        return ItemOutput(list(MigrationEdges(graph, run.reader.external).produce()), aliases)

    def take(self, key: str, data: Any) -> None:
        self.run.aliases = {app: frozenset(found) for app, found in data.items()}


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
        self.aliases: dict[str, frozenset[str]] | None = None

    def scan_roots(self, apps: AppRegistry) -> tuple[str, ...]:
        """The configured roots, else the top-level packages of the first-party installed apps."""
        if self.config.scan_roots:
            return self.config.scan_roots
        first_party = self.reader.first_party
        return tuple(sorted({app.name.partition(".")[0] for app in apps.apps if first_party.contains(app.name)}))

    def scan_files(self) -> list[FileScope]:
        assert self.apps is not None
        roots = self.scan_roots(self.apps)
        return [
            scope
            for scope in self.reader.files
            if scope.record.get("status") == "ok"
            and self.reader.first_party.contains(scope.module)
            and any(scope.module == root or scope.module.startswith(root + ".") for root in roots)
        ]

    def read_apps(self) -> list[Edge]:
        """The app registry and the files to scan, outside a plan (tests)."""
        self.apps = AppReader(self.reader).read(self.config)
        self.files = self.scan_files()
        return []

    def start_models(self) -> list[Edge]:
        assert self.apps is not None
        self.registry = ModelRegistry(self.reader, self.apps)
        return []

    def read_models(self) -> list[Edge]:
        """Model discovery outside a plan (tests)."""
        self.start_models()
        ItemRun(ModelStepItems(self), None).outputs()
        return []

    def use_edges(self) -> Sequence[Output]:
        """On the native engine the link judges the uses (D49); `UseProducer` is the meaning."""
        assert self.registry is not None
        producer = UseProducer(self.reader, self.registry)
        if isinstance(self.sink, NativeSink):
            return NativeUses(producer, self.sink).produce(self.files, USES_KINDS)
        return producer.produce(self.files)

    def relation_items(self) -> RelationItems:
        assert self.registry is not None
        builder = RelationBuilder(self.registry, self.config)
        return RelationItems(RelationEdges(self.registry, builder, self.reader.index.path_of))

    def get_model_items(self) -> GetModelItems:
        assert self.registry is not None
        return GetModelItems(GetModelProducer(self.reader, self.registry), self.files)

    def signal_items(self) -> SignalItems:
        assert self.registry is not None
        return SignalItems(SignalProducer(self.reader, self.registry, self.config), self.files)

    def installs_items(self) -> InstallsItems:
        return InstallsItems(InstallsProducer(self.reader, self.config.hooks), self.files)

    def load_edges(self) -> list[Edge]:
        assert self.apps is not None
        return AppLoads(self.reader, self.apps).produce()

    def symbols(self) -> list[Edge]:
        """Model and app symbols. Rules on model edges read them through `dst.kind`, so a failure
        here makes those edge kinds incomplete."""
        assert self.registry is not None and self.apps is not None and self.aliases is not None
        self.sink.add_symbols(ModelFacts(self.registry, self.apps).symbols(self.aliases))
        return []

    def plan(self) -> ProducerPlan:
        def nothing() -> list[Edge]:
            return []

        return ProducerPlan(
            [
                ProducerStep(APPS_PRODUCER, frozenset(), nothing, items=lambda: AppItems(self), stored=False),
                ProducerStep(
                    MODELS_PRODUCER,
                    MODEL_KINDS,
                    self.start_models,
                    (APPS_PRODUCER,),
                    items=lambda: ModelStepItems(self),
                ),
                ProducerStep(
                    MIGRATION_PRODUCER,
                    frozenset({"migration-parent"}),
                    nothing,
                    (APPS_PRODUCER,),
                    items=lambda: MigrationItems(self),
                ),
                ProducerStep(
                    RELATIONS_PRODUCER,
                    frozenset({"relation", "reverse-accessor", "model-label"}),
                    nothing,
                    (MODELS_PRODUCER,),
                    items=self.relation_items,
                    stored=False,
                ),
                ProducerStep(USES_PRODUCER, USES_KINDS, self.use_edges, (MODELS_PRODUCER,)),
                ProducerStep(
                    GET_MODEL_PRODUCER,
                    frozenset({"model-label"}),
                    nothing,
                    (MODELS_PRODUCER,),
                    items=self.get_model_items,
                    stored=False,
                ),
                ProducerStep(
                    SIGNALS_PRODUCER, frozenset({"signal"}), nothing, (MODELS_PRODUCER,), items=self.signal_items
                ),
                ProducerStep(
                    INSTALLS_PRODUCER, frozenset({"installs"}), nothing, (APPS_PRODUCER,), items=self.installs_items
                ),
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
        DjangoRun(reader, config, root, sink).plan().run(sink, reader)


DJANGO_PACK = DjangoPack()
