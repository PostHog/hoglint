"""The graph of the native engine: core edges stay in the Rust link, pack edges are Python.

`NativeGraph.edges` is the whole graph in graph order: core edges first, then what packs add.
A core edge becomes an Edge object only when something reads it, and nothing keeps it: each
read makes a new object. Tables key edges by their handle (`Edge.handle`), the edge's row in
the graph, never by object identity. Readers that need only some edges ask the link by kind
(`by_kind`) instead of walking every edge.

`NativeGraph.symbols` keeps the core's symbols as columns, with no dict per symbol.
"""

from __future__ import annotations

from array import array
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from typing import Any, SupportsIndex, overload

from hoglint.index import GraphIndex
from hoglint.model import LANGUAGE_PYTHON, Context, Edge, EntityId, Graph, Resolution, Site, refined_edge, set_handle
from hoglint.plugins import ProducerFailure, RefinedFields, Refinement

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
# The link's symbol kinds by code (`Link.symbol_columns`).
SYMBOL_KINDS = ("def", "async", "class")


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

    def edge(self, row: tuple[Any, ...], handle: int) -> Edge:
        kind, path, module, scope, line, column, surface, target_module, target_symbol, res, bits, via, use, attrs = (
            row[:14]
        )
        shape, shape_detail = row[14:]
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
            shape,
            shape_detail,
            attrs,
            handle,
        )


class CoreEdges:
    """The link's core edges, made into Edge objects on each read. The handle of a core edge is
    its row in the link."""

    def __init__(self, link: Any) -> None:
        self.link = link
        self.count: int = link.edge_count()
        self.maker = EdgeMaker()
        self.kind_ids: dict[str, array[int]] = {}

    def ids_of_kind(self, kind: str) -> array[int]:
        found = self.kind_ids.get(kind)
        if found is None:
            found = self.kind_ids[kind] = array("I", self.link.ids_of_kind(kind))
        return found

    def get(self, ids: Sequence[int]) -> list[Edge]:
        wanted = ids if isinstance(ids, array) else array("I", ids)
        out: list[Edge] = []
        for start in range(0, len(wanted), BATCH):
            chunk = wanted[start : start + BATCH]
            out.extend(self.maker.edge(row, i) for i, row in zip(chunk, self.link.edges(chunk.tobytes())))
        return out

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
        self.extend((edge,))

    def extend(self, edges: Iterable[Edge]) -> None:
        start = len(self.extra)
        self.extra.extend(edges)
        for at in range(start, len(self.extra)):
            set_handle(self.extra[at], self.core.count + at)


class NativeSymbols(Mapping[str, Mapping[str, str]]):
    """`graph.symbols` of the native engine: the core's symbols as columns, with no dict per
    symbol, and over them the facts packs add, which replace a symbol's facts as `dict.update`
    does. Iteration order is a dict's: the core's symbols, then the names packs add."""

    def __init__(self, names: list[str], kinds: bytes, files: array[int], paths: list[str]) -> None:
        self.names = names
        self.at = {name: at for at, name in enumerate(names)}
        self.kinds = kinds
        self.files = files
        self.paths = paths
        self.added: dict[str, Mapping[str, str]] = {}
        self.new_names = 0

    def __getitem__(self, name: str) -> Mapping[str, str]:
        found = self.added.get(name)
        if found is not None:
            return found
        at = self.at[name]
        return {"kind": SYMBOL_KINDS[self.kinds[at]], "path": self.paths[self.files[at]]}

    def __contains__(self, name: object) -> bool:
        return name in self.added or name in self.at

    def __iter__(self) -> Iterator[str]:
        yield from self.names
        yield from (name for name in self.added if name not in self.at)

    def __len__(self) -> int:
        return len(self.names) + self.new_names

    def update(self, facts: Mapping[str, Mapping[str, str]]) -> None:
        for name, values in facts.items():
            if name not in self.at and name not in self.added:
                self.new_names += 1
            self.added[name] = values


class NativeSink:
    """The native engine's sink (`hoglint.plugins.PackSink`): a refinement of a core edge goes into
    the link, so every reader of the link sees the one refined edge; pack edges join the graph."""

    def __init__(self, graph: NativeGraph) -> None:
        self.graph = graph
        self.refined = RefinedFields()

    def add_edges(self, producer: str, edges: Sequence[Edge]) -> None:
        self.graph.extend(edges)

    def own_attrs(self, handle: int) -> dict[str, str]:
        return dict(self.graph.edges[handle].attrs)

    def refine(self, producer: str, kinds: frozenset[str], refinements: Sequence[Refinement]) -> None:
        conflicts = self.refined.conflicts(producer, refinements, self.own_attrs)
        if conflicts:
            more = f" and {len(conflicts) - 1} more" if len(conflicts) > 1 else ""
            self.fail(producer, ProducerFailure(f"sets {conflicts[0]}{more} apart", kinds))
            return
        self.refined.record(producer, refinements)
        count = self.graph.core.count
        core = [refinement for refinement in refinements if refinement.handle < count]
        self.graph.link.refine(
            array("I", (refinement.handle for refinement in core)).tobytes(),
            [refinement.shape for refinement in core],
            [refinement.shape_detail for refinement in core],
            [list(refinement.attrs) for refinement in core],
        )
        extra = self.graph.edge_list.extra
        for refinement in refinements:
            if refinement.handle >= count:
                edge = extra[refinement.handle - count]
                extra[refinement.handle - count] = refined_edge(edge, refinement)
        self.graph.extra_index = {}
        self.graph.extra_indexed = 0

    def add_symbols(self, facts: Mapping[str, Mapping[str, str]]) -> None:
        symbols: Any = self.graph.symbols
        symbols.update(facts)

    def fail(self, producer: str, failure: ProducerFailure) -> None:
        self.graph.failed_producers[producer] = failure


class NativeGraph(Graph):
    """A Graph whose core edges live in the link. `by_kind` asks the link for core kinds."""

    def __init__(self, link: Any, modules: dict[str, str]) -> None:
        self.core = CoreEdges(link)
        names, kinds, files = link.symbol_columns()
        symbols = NativeSymbols(names, kinds, array("I", files), link.paths())
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
