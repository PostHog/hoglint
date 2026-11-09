"""L2 core: index the fact store, run the core producers, return the Graph."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from hoglint.core.config import CoreConfig
from hoglint.core.harness import GraphSink, ProducerPlan, ProducerStep
from hoglint.core.modules import FirstParty, ModuleIndex, ModuleNamer
from hoglint.core.producers import PRODUCERS, BuildContext, Producer
from hoglint.core.records import RecordBodies
from hoglint.core.sites import FileScope
from hoglint.core.symbols import ClassIndex, Resolver, Tables
from hoglint.facts import FactStore
from hoglint.model import Edge, Graph


class CoreBuild:
    """Everything producers and packs share for one snapshot: index, resolver, classes, files.

    `external` maps each third-party file to its path inside its site-packages root; those files
    resolve as import targets and never produce edges.
    """

    def __init__(
        self,
        config: CoreConfig,
        store: FactStore,
        analyzed: list[str] | None = None,
        external: Mapping[str, str] | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.external = dict(external or {})
        self.namer = ModuleNamer(config.source_roots, self.external)
        self.index = ModuleIndex(list(store.records), self.namer, config.first_party)
        self.first_party = FirstParty(config.first_party or self.index.repo_heads)
        self.tables = Tables(store, self.index)
        self.resolver = Resolver(self.tables)
        self.classes = ClassIndex(self.resolver)
        paths = analyzed if analyzed is not None else sorted(p for p in store.records if p not in self.external)
        self.files = [self.file_scope(path) for path in paths if path in store.records and path not in self.external]
        self.scope_of = {scope.path: scope for scope in self.files}
        self.context = BuildContext(config, store, self.index, self.resolver, self.files)
        self.bodies_cache: dict[str, RecordBodies] = {}

    def file_scope(self, path: str) -> FileScope:
        module, is_package = self.namer.module_of(path)
        return FileScope(path, module, is_package, self.store.records[path], self.config)

    def bodies(self, record: dict) -> RecordBodies:
        """Direct body statements of each def of a record, indexed once per file."""
        path = record["path"]
        found = self.bodies_cache.get(path)
        if found is None:
            found = RecordBodies(record)
            self.bodies_cache[path] = found
        return found

    def step(self, producer: Producer, graph: Graph) -> ProducerStep:
        def run() -> list[Edge]:
            scratch = Graph()
            producer.run(self.context, scratch)
            graph.symbols.update(scratch.symbols)
            graph.modules.update(scratch.modules)
            graph.failed_producers.update(scratch.failed_producers)
            return scratch.edges

        return ProducerStep(producer.name, producer.edge_kinds, run)

    def graph(self, producers: Iterable[type[Producer]] = PRODUCERS) -> Graph:
        """The core edges of the given producers. A consumer that needs only some kinds asks for
        only their producers, so an unrelated failure never makes it incomplete."""
        graph = Graph(modules=self.index.repo_modules())
        ProducerPlan(self.step(producer_class(), graph) for producer_class in producers).run(GraphSink(graph))
        return graph
