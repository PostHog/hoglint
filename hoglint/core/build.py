"""L2 core: index the fact store, run the core producers, return the Graph."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from typing import Any

from hoglint.core.config import CoreConfig
from hoglint.core.harness import GraphSink, ProducerPlan, ProducerStep
from hoglint.core.modules import FirstParty, ModuleIndex, ModuleNamer
from hoglint.core.producers import PRODUCERS, BuildContext, Producer
from hoglint.core.records import RecordBodies, chain_tokens, last_name, leading_attrs
from hoglint.core.sites import FileScope
from hoglint.core.symbols import ClassIndex, Resolver, Tables, absolute_import
from hoglint.facts import FactStore
from hoglint.model import LANGUAGE_PYTHON, Edge, EntityId, Graph
from hoglint.native.queries import CALLEE_FIELD, NativePackQueries, Words, batch_of, first_party_roots
from hoglint.plugins import AttributeUse, EdgeBatch


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
        self.context = BuildContext(
            config, store, self.index, self.resolver, self.files, self.first_party, self.scope_of, self.classes
        )
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
            graph.hand_outs.update(scratch.hand_outs)
            return scratch.edges

        return ProducerStep(producer.name, producer.edge_kinds, run)

    def graph(self, producers: Iterable[type[Producer]] = PRODUCERS) -> Graph:
        """The core edges of the given producers. A consumer that needs only some kinds asks for
        only their producers, so an unrelated failure never makes it incomplete."""
        graph = Graph(modules=self.index.repo_modules())
        ProducerPlan(self.step(producer_class(), graph) for producer_class in producers).run(GraphSink(graph))
        return graph


class PackQueries:
    """The Python engine's answers, from its decoded records and its graph."""

    def __init__(self, build: CoreBuild, graph: Graph) -> None:
        self.build = build
        self.graph = graph
        # imported name -> (file, row index, file scope, row) of the rows that import it
        self.imports_by_name: dict[str, list[tuple[int, int, FileScope, list]]] | None = None

    @staticmethod
    def of(build: CoreBuild, graph: Graph) -> PackQueries:
        link = getattr(build, "link", None)
        return NativePackQueries(build, graph, link) if link is not None else PackQueries(build, graph)

    def may_mention(self, scope: FileScope, kinds: Iterable[str], words: Words) -> bool:
        """False only when no row of `kinds` in the file can hold one of `words`."""
        return any(scope.record.get(kind) for kind in kinds)

    def has_rows(self, scope: FileScope, kind: str) -> bool:
        return bool(scope.record.get(kind))

    def calls_named(
        self, scopes: Iterable[FileScope], kind: str, names: Collection[str]
    ) -> Iterator[tuple[FileScope, list[list]]]:
        """Rows of a call kind (`calls`, `callees`) whose callee's last dotted name is one of
        `names`, resolved or not, by file in order. Files with no such row are left out."""
        position = CALLEE_FIELD[kind]
        for scope in scopes:
            rows = [row for row in scope.record.get(kind, ()) if last_name(row[position]) in names]
            if rows:
                yield scope, rows

    def uses_to(
        self, scopes: Iterable[FileScope], targets: Collection[EntityId]
    ) -> Iterator[tuple[FileScope, list[AttributeUse]]]:
        """`import a.b` then `a.b.Model.objects`: the `uses` rows with attributes whose root a
        first-party import binds, and whose chain resolves to a class among `targets` with at
        least one attribute resolved past the root. By file in order; files with none are left out."""
        context = self.build.context
        for scope in scopes:
            rows = [row for row in scope.record.get("uses", ()) if row[3]]
            if not rows:
                continue
            roots = first_party_roots(scope, {row[2] for row in rows}, self.build.first_party)
            found: list[AttributeUse] = []
            for at_scope, _, root, attrs, count, line, column in rows:
                if root not in roots:
                    continue
                names = leading_attrs(chain_tokens(attrs))
                lookup, rest = context.chain_lookup(scope, ".".join([root, *names]), at_scope, line)
                hit = lookup.hit
                if hit is None or hit.kind != "class" or len(rest) == len(names) or hit.origin not in targets:
                    continue
                found.append(AttributeUse(at_scope, line, column, count, lookup))
            if found:
                yield scope, found

    def importing(self, names: Collection[str]) -> list[tuple[FileScope, Sequence[Any]]]:
        """`from x import name` rows of the analyzed files that import one of `names`, by file in
        order, then row order: [scope, line, column, flags, module, level, name, asname]."""
        if self.imports_by_name is None:
            self.imports_by_name = defaultdict(list)
            for file, scope in enumerate(self.build.files):
                for at, row in enumerate(scope.record.get("imports", ())):
                    if row[6] is not None:
                        self.imports_by_name[row[6]].append((file, at, scope, row))
        found = sorted(entry for name in set(names) for entry in self.imports_by_name.get(name, ()))
        return [(scope, row) for _, _, scope, row in found]

    def rows(self, scope: FileScope, kind: str) -> list[Any]:
        """One file's rows of `kind`, for a pack that reads the kind of a file once: the native
        engine decodes them for this read and keeps nothing."""
        return scope.record.get(kind, [])

    def imported_modules(self, scope: FileScope) -> list[str]:
        """The module each import of a file names, in row order: `import a.b` names a.b, `from a
        import b` names a.b. A relative import that climbs out of the repo names nothing."""
        out = []
        for _, _, _, _, target, level, name, _ in scope.record.get("imports", ()):
            if name is None:
                out.append(target)
                continue
            module = absolute_import(scope.module, scope.is_package, target, level)
            if module is not None:
                out.append(f"{module}.{name}")
        return out

    def subclasses(self, qualified: str) -> list[EntityId]:
        """Module-level classes the analyzed modules' own source defines whose MRO holds
        `qualified`, the class itself included: by module in file order, then table order."""
        out: list[EntityId] = []
        seen: set[str] = set()
        for scope in self.build.files:
            if scope.module in seen:
                continue
            seen.add(scope.module)
            table = self.build.tables.get(scope.module)
            if table is None:
                continue
            for name in table.classes():
                ref = EntityId(LANGUAGE_PYTHON, scope.module, name)
                if self.build.classes.is_subclass(ref, qualified):
                    out.append(ref)
        return out

    def edges_to(self, kind: str, targets: Collection[EntityId]) -> list[Edge]:
        """Edges of `kind` whose target is one of `targets` (by resolved origin), in graph order."""
        return self.graph.edges_to(kind, targets)

    def edges_spelled(self, kind: str, names: Collection[str]) -> list[Edge]:
        """Edges of `kind` whose surface's last dotted name is one of `names`, resolved or not."""
        return [edge for edge in self.graph.by_kind(kind) if edge.surface.rpartition(".")[2] in names]

    def batch_to(self, kind: str, targets: Collection[EntityId], columns: Sequence[str]) -> EdgeBatch:
        """`edges_to` as a projected batch: handles, and only the columns asked for."""
        return batch_of(self.edges_to(kind, targets), columns)

    def fact_digest(self, record: Any, kinds: Iterable[str]) -> str | None:
        """A digest of a file's facts of `kinds`, for a pack that caches what it reads from one
        file. None when the engine has no fact text to digest: the pack reads the file instead."""
        return None
