"""L2 core: index the fact store, run the core producers, return the Graph."""

from __future__ import annotations

from hoglint.core.config import CoreConfig
from hoglint.core.modules import ModuleIndex, ModuleNamer
from hoglint.core.producers import PRODUCERS, BuildContext, DeclarationProducer, producer_id
from hoglint.core.sites import FileScope
from hoglint.core.symbols import ClassIndex, Resolver, Tables
from hoglint.facts import SITE_MARKER, FactStore
from hoglint.model import Graph


class CoreBuild:
    """Everything producers and packs share for one snapshot: index, resolver, classes, files."""

    def __init__(self, config: CoreConfig, store: FactStore, analyzed: list[str] | None = None) -> None:
        self.config = config
        self.store = store
        self.namer = ModuleNamer(config.source_roots)
        self.index = ModuleIndex(list(store.records), self.namer, config.first_party)
        self.tables = Tables(store, self.index)
        self.resolver = Resolver(self.tables)
        self.classes = ClassIndex(self.resolver)
        paths = analyzed if analyzed is not None else store.repo_paths()
        self.files = [self.file_scope(path) for path in paths if path in store.records and SITE_MARKER not in path]
        self.context = BuildContext(config, store, self.index, self.resolver, self.files)

    def file_scope(self, path: str) -> FileScope:
        module, is_package = self.namer.module_of(path)
        return FileScope(path, module, is_package, self.store.records[path], self.config)

    def graph(self) -> Graph:
        graph = Graph(modules=self.index.repo_modules())
        for producer_class in PRODUCERS:
            producer = producer_class()
            try:
                producer.run(self.context, graph)
            except Exception as error:  # noqa: BLE001 - a failed producer makes its rules incomplete, never clean
                kinds = DeclarationProducer.kinds if producer_class is DeclarationProducer else (producer.kind,)
                for kind in kinds:
                    graph.failed_producers[producer_id(kind)] = f"{type(error).__name__}: {error}"
        return graph
