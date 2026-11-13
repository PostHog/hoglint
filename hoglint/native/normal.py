"""The normalized forms two dumps are compared in: one JSON-ready list per edge, every field and
attr kept, in graph order."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from hoglint.model import Edge, Graph

Row = list[Any]


def edge_row(edge: Edge) -> Row:
    site = edge.site
    return [
        edge.kind,
        site.path,
        site.module,
        site.scope,
        site.line,
        site.column,
        edge.surface,
        [str(target) for target in edge.targets],
        edge.resolution.value,
        sorted(context.value for context in edge.contexts),
        edge.producer,
        [str(hop) for hop in edge.via],
        edge.use,
        edge.shape,
        edge.shape_detail,
        [[key, value] for key, value in edge.attrs],
    ]


def edge_rows(edges: Iterable[Edge]) -> list[Row]:
    return [edge_row(edge) for edge in edges]


def symbol_rows(graph: Graph) -> list[Row]:
    """`graph.symbols` in insertion order, with every fact sorted by key."""
    return [[name, sorted(facts.items())] for name, facts in graph.symbols.items()]


FIELDS = (
    "kind",
    "path",
    "module",
    "scope",
    "line",
    "column",
    "surface",
    "targets",
    "resolution",
    "contexts",
    "producer",
    "via",
    "use",
    "shape",
    "shape_detail",
    "attrs",
)
