"""Views: named graphs over Graph edges. A view selects edges first and groups endpoints second,
so every grouped pair keeps its edges, their contexts and their sites as witnesses.

- FileImportsView: file -> file, import edges (plus dotted paths), the real import chain.
- RefsView: one edge per coupling site, any kinds.
- GroupedView: any edges with both endpoints mapped through a label function (package, zone, ...).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum

from hoglint.model import Context, Edge, Graph, Resolution

GroupOf = Callable[[str], str | None]


class Endpoint(StrEnum):
    """Which end of an edge a view judges: the name the code used, the origin, or a re-export hop."""

    SURFACE = "surface"
    ORIGIN = "origin"
    VIA = "via"


@dataclass(frozen=True, slots=True)
class FileEdge:
    src: str
    dst: str
    edge: Edge


class ModuleLookup:
    """Module of an edge endpoint, against the Graph's module table."""

    def __init__(self, graph: Graph) -> None:
        self.modules = graph.modules

    def longest(self, dotted: str) -> str | None:
        parts = dotted.split(".")
        for end in range(len(parts), 0, -1):
            candidate = ".".join(parts[:end])
            if candidate in self.modules:
                return candidate
        return None

    def surface_module(self, edge: Edge) -> str | None:
        declared = edge.attr("module")
        if declared:
            return declared
        if not edge.surface:
            return None
        if edge.resolution == Resolution.EXTERNAL:
            return edge.surface
        return self.longest(edge.surface)

    def endpoint_modules(self, edge: Edge, endpoint: Endpoint) -> list[str | None]:
        """Destination modules; None stands for an unresolved end and is kept, never dropped."""
        if endpoint == Endpoint.SURFACE:
            return [self.surface_module(edge)]
        if endpoint == Endpoint.VIA:
            return [hop.module for hop in edge.via] or [None]
        return [target.module for target in edge.targets] or [None]


class RefsView:
    """Edges of the given kinds, filtered by contexts. One edge per coupling site."""

    def __init__(
        self,
        graph: Graph,
        kinds: Iterable[str] | None = None,
        contexts_in: Iterable[Context] = (),
        contexts_out: Iterable[Context] = (),
    ) -> None:
        self.graph = graph
        self.kinds = frozenset(kinds) if kinds is not None else None
        self.contexts_in = frozenset(contexts_in)
        self.contexts_out = frozenset(contexts_out)

    def selects(self, edge: Edge) -> bool:
        if self.kinds is not None and edge.kind not in self.kinds:
            return False
        if self.contexts_in and not self.contexts_in <= edge.contexts:
            return False
        return not (self.contexts_out & edge.contexts)

    def edges(self) -> Iterator[Edge]:
        return (edge for edge in self.graph.edges if self.selects(edge))


class FileImportsView:
    """File -> file import chain: the file of the module each import loads.

    Defaults follow the decided semantics: import and dotted-path edges, type-only ones left out.
    Edges whose module has no file (third-party, namespace package, unresolved) have no file edge.
    """

    def __init__(
        self,
        graph: Graph,
        kinds: Iterable[str] = ("import", "dotted-path"),
        contexts_out: Iterable[Context] = (Context.TYPE_ONLY,),
    ) -> None:
        self.graph = graph
        self.refs = RefsView(graph, kinds=kinds, contexts_out=contexts_out)
        self.lookup = ModuleLookup(graph)

    def edges(self) -> Iterator[FileEdge]:
        for edge in self.refs.edges():
            module = self.lookup.surface_module(edge)
            if module is None:
                continue
            path = self.graph.modules.get(module)
            if path is not None:
                yield FileEdge(edge.site.path, path, edge)

    def dependents(self) -> dict[str, list[str]]:
        reverse: dict[str, set[str]] = {}
        for file_edge in self.edges():
            reverse.setdefault(file_edge.dst, set()).add(file_edge.src)
        return {target: sorted(importers) for target, importers in sorted(reverse.items())}


class GroupedView:
    """Edges grouped by (source group, destination group) through a label function.

    The label function maps a module name to its group, or None when the module carries no
    label. Unlabeled and unresolved ends stay in the view as None.
    """

    def __init__(
        self, graph: Graph, edges: Iterable[Edge], group_of: GroupOf, endpoint: Endpoint = Endpoint.ORIGIN
    ) -> None:
        self.edges = edges
        self.group_of = group_of
        self.endpoint = endpoint
        self.lookup = ModuleLookup(graph)

    def dst_groups(self, edge: Edge) -> list[str | None]:
        groups: list[str | None] = []
        for module in self.lookup.endpoint_modules(edge, self.endpoint):
            group = self.group_of(module) if module is not None else None
            if group not in groups:
                groups.append(group)
        return groups

    def groups(self) -> dict[tuple[str | None, str | None], list[Edge]]:
        out: dict[tuple[str | None, str | None], list[Edge]] = {}
        for edge in self.edges:
            src = self.group_of(edge.site.module)
            for dst in self.dst_groups(edge):
                out.setdefault((src, dst), []).append(edge)
        return out

    def crossings(self) -> dict[tuple[str | None, str | None], list[Edge]]:
        """Pairs whose two groups differ, unlabeled ends included."""
        return {pair: edges for pair, edges in self.groups().items() if pair[0] != pair[1] or pair[0] is None}
