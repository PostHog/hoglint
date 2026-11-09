"""Views: named selections over Graph edges. A view selects edges and keeps each one, with its
contexts and site, as a witness.

- RefsView: one edge per coupling site, any kinds, filtered by contexts.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from hoglint.model import Context, Edge, Graph


def reverse_dependents(pairs: Iterable[tuple[str, str]]) -> dict[str, list[str]]:
    """(importer, target) pairs as `tach map --direction dependents`: target -> sorted importers."""
    reverse: dict[str, set[str]] = {}
    for importer, target in pairs:
        reverse.setdefault(target, set()).add(importer)
    return {target: sorted(importers) for target, importers in sorted(reverse.items())}


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
        self.kinds = tuple(dict.fromkeys(kinds)) if kinds is not None else None
        self.contexts_in = frozenset(contexts_in)
        self.contexts_out = frozenset(contexts_out)

    def selects(self, edge: Edge) -> bool:
        if self.contexts_in and not self.contexts_in <= edge.contexts:
            return False
        return not (self.contexts_out & edge.contexts)

    def edges(self) -> Iterator[Edge]:
        if self.kinds is None:
            candidates: Iterable[Edge] = self.graph.edges
        else:
            candidates = (edge for kind in self.kinds for edge in self.graph.by_kind(kind))
        return (edge for edge in candidates if self.selects(edge))
