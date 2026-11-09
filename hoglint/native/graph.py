"""The graph of the native engine: core edges stay in the Rust link, pack edges are Python.

`NativeGraph.edges` is the whole graph in graph order: core edges first, then what packs add.
A core edge becomes an Edge object only when something reads it, and stays the same object
after that, so identity-keyed tables (ordinals, witnesses) hold. Readers that need only some
edges ask the link by kind (`by_kind`) instead of walking every edge.
"""

from __future__ import annotations

from array import array
from collections.abc import Collection, Iterable, Iterator, Sequence
from typing import Any, SupportsIndex, cast, overload

from hoglint.index import GraphIndex
from hoglint.model import LANGUAGE_PYTHON, Context, Edge, EntityId, Graph, Resolution, Site

# Bit order of the link's context bits (src/link/scope.rs `ctx`).
CONTEXT_BITS = (
    Context.PROD,
    Context.TEST,
    Context.MIGRATION,
    Context.TYPE_ONLY,
    Context.FUNCTION_LOCAL,
    Context.CONDITIONAL,
    Context.TYPE_CHECKING_ELSE,
)
CONTEXT_SETS = tuple(
    frozenset(context for bit, context in enumerate(CONTEXT_BITS) if mask & (1 << bit))
    for mask in range(1 << len(CONTEXT_BITS))
)
RESOLUTIONS = {resolution.value: resolution for resolution in Resolution}
BATCH = 65536


def producer_of(kind: str) -> str:
    return f"core-{kind}@1"


class EdgeMaker:
    """Edge objects from the link's edge tuples, sharing equal entity ids."""

    def __init__(self) -> None:
        self.entities: dict[tuple[str, str], EntityId] = {}
        self.producers: dict[str, str] = {}

    def entity(self, module: str, symbol: str) -> EntityId:
        key = (module, symbol)
        found = self.entities.get(key)
        if found is None:
            found = self.entities[key] = EntityId(LANGUAGE_PYTHON, module, symbol)
        return found

    def edge(self, row: tuple[Any, ...]) -> Edge:
        kind, path, module, scope, line, column, surface, target_module, target_symbol, res, bits, via, use, attrs = row
        producer = self.producers.get(kind)
        if producer is None:
            producer = self.producers[kind] = producer_of(kind)
        targets = (self.entity(target_module, target_symbol),) if target_module is not None else ()
        return Edge(
            kind,
            Site(path, module, scope, line, column),
            surface,
            targets,
            RESOLUTIONS[res],
            CONTEXT_SETS[bits],
            producer,
            tuple(self.entity(m, s) for m, s in via),
            use,
            "",
            "",
            attrs,
        )


class CoreEdges:
    """The link's core edges, made into Edge objects on first read and kept."""

    def __init__(self, link: Any) -> None:
        self.link = link
        self.count: int = link.edge_count()
        self.made: list[Edge | None] = [None] * self.count
        self.maker = EdgeMaker()
        self.kind_ids: dict[str, array[int]] = {}

    def ids_of_kind(self, kind: str) -> array[int]:
        found = self.kind_ids.get(kind)
        if found is None:
            found = self.kind_ids[kind] = array("I", self.link.ids_of_kind(kind))
        return found

    def get(self, ids: Sequence[int]) -> list[Edge]:
        missing = array("I", (i for i in ids if self.made[i] is None))
        for start in range(0, len(missing), BATCH):
            chunk = missing[start : start + BATCH]
            for i, row in zip(chunk, self.link.edges(chunk.tobytes())):
                self.made[i] = self.maker.edge(row)
        return cast(list[Edge], [self.made[i] for i in ids])

    def all(self) -> list[Edge]:
        return self.get(range(self.count))


class EdgeList(Sequence[Edge]):
    """Core edges, then pack edges: the graph's edges in order."""

    def __init__(self, core: CoreEdges) -> None:
        self.core = core
        self.extra: list[Edge] = []

    def __len__(self) -> int:
        return self.core.count + len(self.extra)

    @overload
    def __getitem__(self, index: SupportsIndex) -> Edge: ...

    @overload
    def __getitem__(self, index: slice) -> list[Edge]: ...

    def __getitem__(self, index: SupportsIndex | slice) -> Edge | list[Edge]:
        if isinstance(index, slice):
            positions = range(*index.indices(len(self)))
            core = [i for i in positions if i < self.core.count]
            return self.core.get(core) + [self.extra[i - self.core.count] for i in positions if i >= self.core.count]
        at = index.__index__()
        if at < 0:
            at += len(self)
        if at < self.core.count:
            return self.core.get((at,))[0]
        return self.extra[at - self.core.count]

    def __iter__(self) -> Iterator[Edge]:
        for start in range(0, self.core.count, BATCH):
            yield from self.core.get(range(start, min(start + BATCH, self.core.count)))
        yield from self.extra

    def append(self, edge: Edge) -> None:
        self.extra.append(edge)

    def extend(self, edges: Iterable[Edge]) -> None:
        self.extra.extend(edges)


class NativeGraph(Graph):
    """A Graph whose core edges live in the link. `by_kind` asks the link for core kinds."""

    def __init__(self, link: Any, modules: dict[str, str], symbols: dict[str, dict[str, str]]) -> None:
        self.core = CoreEdges(link)
        super().__init__(edges=EdgeList(self.core), modules=modules, symbols=symbols)  # type: ignore[arg-type]
        self.extra_index: dict[str, list[Edge]] = {}
        self.extra_indexed = 0

    def make_index(self) -> GraphIndex:
        from hoglint.native.rules import NativeGraphIndex  # noqa: PLC0415 - rules imports this module

        return NativeGraphIndex(self)

    @property
    def link(self) -> Any:
        return self.core.link

    def extend(self, edges: Iterable[Edge]) -> None:
        self.edges.extend(edges)

    def catch_up(self) -> None:
        extra = self.edge_list.extra
        for edge in extra[self.extra_indexed :]:
            self.extra_index.setdefault(edge.kind, []).append(edge)
        self.extra_indexed = len(extra)

    @property
    def edge_list(self) -> EdgeList:
        assert isinstance(self.edges, EdgeList)
        return self.edges

    def edges_to(self, kind: str, targets: Collection[EntityId]) -> list[Edge]:
        self.catch_up()
        core = self.core.get(array("I", self.link.ids_to(kind, [(t.module, t.symbol) for t in targets])))
        wanted = targets if isinstance(targets, (set, frozenset, dict)) else set(targets)
        extra = [edge for edge in self.extra_index.get(kind, ()) if any(t in wanted for t in edge.targets)]
        return core + extra

    def by_kind(self, *kinds: str) -> list[Edge]:
        self.catch_up()
        if len(kinds) == 1:
            return self.core.get(self.core.ids_of_kind(kinds[0])) + list(self.extra_index.get(kinds[0], ()))
        wanted = set(kinds)
        ids = sorted(i for kind in wanted for i in self.core.ids_of_kind(kind))
        return self.core.get(ids) + [edge for edge in self.edge_list.extra if edge.kind in wanted]
