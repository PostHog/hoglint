"""The graph of the native engine: every edge, core and pack, lives in the Rust link (D22).

`NativeGraph.edges` is the whole graph in graph order: core edges first, then what packs add.
An edge becomes an Edge object only when something reads it, and nothing keeps it: each read
makes a new object. Tables key edges by their handle (`Edge.handle`), the edge's row in the
link, never by object identity. Readers that need only some edges ask the link by kind
(`by_kind`) instead of walking every edge.

`NativeGraph.symbols` keeps the core's symbols as columns, with no dict per symbol.
"""

from __future__ import annotations

from array import array
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from typing import Any, SupportsIndex, overload

from hoglint.index import GraphIndex
from hoglint.model import LANGUAGE_PYTHON, Context, Edge, EntityId, Graph, Resolution, Site
from hoglint.plugins import ProducerFailure, RefinedFields, Refinements

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


class EdgeMaker:
    """Edge objects from the link's edge tuples, sharing equal entity ids."""

    def __init__(self) -> None:
        self.entities: dict[tuple[str, str], EntityId] = {}

    def entity(self, module: str, symbol: str) -> EntityId:
        key = (module, symbol)
        found = self.entities.get(key)
        if found is None:
            found = self.entities[key] = EntityId(LANGUAGE_PYTHON, module, symbol)
        return found

    def edge(self, row: tuple[Any, ...], handle: int) -> Edge:
        (
            kind,
            path,
            module,
            scope,
            line,
            column,
            surface,
            targets,
            res,
            bits,
            via,
            use,
            attrs,
            shape,
            detail,
            producer,
        ) = row
        return Edge(
            kind,
            Site(path, module, scope, line, column),
            surface,
            tuple(self.entity(m, s) for m, s in targets),
            RESOLUTIONS[res],
            CONTEXT_SETS[bits],
            producer,
            tuple(self.entity(m, s) for m, s in via),
            use,
            shape,
            detail,
            attrs,
            handle,
        )


class LinkEdges:
    """Every edge of the link, core and pack, made into Edge objects on each read. An edge's
    handle is its row in the link."""

    def __init__(self, link: Any) -> None:
        self.link = link
        self.maker = EdgeMaker()

    @property
    def count(self) -> int:
        return self.link.edge_count()

    def get(self, ids: Sequence[int]) -> list[Edge]:
        wanted = ids if isinstance(ids, array) else array("I", ids)
        out: list[Edge] = []
        for start in range(0, len(wanted), BATCH):
            chunk = wanted[start : start + BATCH]
            out.extend(self.maker.edge(row, i) for i, row in zip(chunk, self.link.edges(chunk.tobytes())))
        return out


class EdgeList(Sequence[Edge]):
    """The graph's edges in order: core edges, then pack edges, all in the link."""

    def __init__(self, edges: LinkEdges) -> None:
        self.link_edges = edges

    def __len__(self) -> int:
        return self.link_edges.count

    @overload
    def __getitem__(self, index: SupportsIndex) -> Edge: ...

    @overload
    def __getitem__(self, index: slice) -> list[Edge]: ...

    def __getitem__(self, index: SupportsIndex | slice) -> Edge | list[Edge]:
        if isinstance(index, slice):
            return self.link_edges.get(range(*index.indices(len(self))))
        at = index.__index__()
        if at < 0:
            at += len(self)
        if not 0 <= at < len(self):
            raise IndexError(index)
        return self.link_edges.get((at,))[0]

    def __iter__(self) -> Iterator[Edge]:
        count = len(self)
        for start in range(0, count, BATCH):
            yield from self.link_edges.get(range(start, min(start + BATCH, count)))


def edge_columns(edges: Sequence[Edge]) -> tuple[list[Any], ...]:
    """A batch of edges as the field lists `Link.add_edges` takes."""
    return (
        [edge.kind for edge in edges],
        [edge.producer for edge in edges],
        [(edge.site.path, edge.site.module, edge.site.scope, edge.site.line, edge.site.column) for edge in edges],
        [edge.surface for edge in edges],
        [[(target.module, target.symbol) for target in edge.targets] for edge in edges],
        [edge.resolution.value for edge in edges],
        [context_bits(edge.contexts) for edge in edges],
        [[(hop.module, hop.symbol) for hop in edge.via] for edge in edges],
        [edge.use for edge in edges],
        [(edge.shape, edge.shape_detail) for edge in edges],
        [list(edge.attrs) for edge in edges],
    )


def context_bits(contexts: Iterable[Context]) -> int:
    return sum(1 << CONTEXT_BITS.index(context) for context in contexts)


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

    def fact(self, name: str, key: str) -> str:
        """One fact of a symbol, "" when it has none, with no dict made."""
        found = self.added.get(name)
        if found is not None:
            return found.get(key, "")
        at = self.at.get(name)
        if at is None:
            return ""
        if key == "kind":
            return SYMBOL_KINDS[self.kinds[at]]
        if key == "path":
            return self.paths[self.files[at]]
        return ""

    def core_with_kinds(self, kinds: Collection[str]) -> list[str]:
        """The core's symbols whose kind is one of `kinds` and whose facts no pack replaced, in
        insertion order, found in the kind column."""
        codes = {code for code, kind in enumerate(SYMBOL_KINDS) if kind in kinds}
        return [name for name, code in zip(self.names, self.kinds) if code in codes and name not in self.added]

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
    """The native engine's sink (`hoglint.plugins.PackSink`): pack edges and refinements go into
    the link, so every reader of edges reads one table with the refined edges in it."""

    def __init__(self, graph: NativeGraph) -> None:
        self.graph = graph
        self.refined = RefinedFields()

    def add_edges(self, producer: str, edges: Sequence[Edge]) -> None:
        self.graph.extend(edges)

    def own_attrs(self, handle: int) -> dict[str, str]:
        return dict(self.graph.edges[handle].attrs)

    def refine(self, producer: str, kinds: frozenset[str], refinements: Refinements) -> None:
        conflicts = self.refined.conflicts(producer, refinements, self.own_attrs)
        if conflicts:
            more = f" and {len(conflicts) - 1} more" if len(conflicts) > 1 else ""
            self.fail(producer, ProducerFailure(f"sets {conflicts[0]}{more} apart", kinds))
            return
        self.refined.record(producer, refinements)
        self.graph.link.refine(
            array("I", refinements.handles).tobytes(),
            refinements.shapes,
            refinements.details,
            refinements.attrs,
        )

    def add_symbols(self, facts: Mapping[str, Mapping[str, str]]) -> None:
        symbols: Any = self.graph.symbols
        symbols.update(facts)

    def fail(self, producer: str, failure: ProducerFailure) -> None:
        self.graph.failed_producers[producer] = failure


class NativeGraph(Graph):
    """A Graph whose edges, core and pack, live in the link. `by_kind` and `edges_to` ask it.

    A producer may hand over one Edge object several times; the Python engine gives such an
    object one handle, its last row, so its ordinal is its last place. `repeats` maps each row of
    such an object to its last row, so ordinals agree."""

    def __init__(self, link: Any, modules: dict[str, str]) -> None:
        self.core = LinkEdges(link)
        names, kinds, files = link.symbol_columns()
        symbols = NativeSymbols(names, kinds, array("I", files), link.paths())
        super().__init__(edges=EdgeList(self.core), modules=modules, symbols=symbols)  # type: ignore[arg-type]
        self.repeats: dict[int, int] = {}

    def make_index(self) -> GraphIndex:
        from hoglint.native.rules import NativeGraphIndex  # noqa: PLC0415 - rules imports this module

        return NativeGraphIndex(self)

    @property
    def link(self) -> Any:
        return self.core.link

    def extend(self, edges: Iterable[Edge]) -> None:
        batch = list(edges)
        if not batch:
            return
        first = self.link.add_edges(*edge_columns(batch))
        objects = [id(edge) for edge in batch]
        last = {object_id: first + at for at, object_id in enumerate(objects)}
        if len(last) == len(objects):
            return
        for at, object_id in enumerate(objects):
            if last[object_id] != first + at:
                self.repeats[first + at] = last[object_id]

    def catch_up(self) -> None:
        return None

    def edges_to(self, kind: str, targets: Collection[EntityId]) -> list[Edge]:
        return self.core.get(array("I", self.link.ids_to(kind, [(t.module, t.symbol) for t in targets])))

    def by_kind(self, *kinds: str) -> list[Edge]:
        ids = sorted(i for kind in set(kinds) for i in array("I", self.link.ids_of_kind(kind)))
        return self.core.get(ids)
