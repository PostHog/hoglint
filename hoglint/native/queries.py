"""Candidate queries packs ask of one snapshot: rows by spelling or mention, core edges by origin
or spelling.

A pack asks here for candidates, then applies its own rule to each one. Every query returns a
superset of what that rule can accept, so pack output never depends on the engine:
- the Python engine filters its decoded records and its graph;
- the native engine asks the link's edge indexes, and runs a bytes prefilter over each file's
  fact text before any of its rows decode.

The link has no row index yet. So row queries decode the files the prefilter passes and filter
their rows here; a native row query by spelling replaces that when it exists.
"""

from __future__ import annotations

import re
from array import array
from collections.abc import Collection, Iterable, Iterator
from typing import Any, NamedTuple

from hoglint.core.build import CoreBuild
from hoglint.core.records import last_name
from hoglint.core.sites import FileScope
from hoglint.model import LANGUAGE_PYTHON, Context, Edge, EntityId, Graph, Resolution, Site
from hoglint.native.graph import CONTEXT_SETS, RESOLUTIONS, producer_of

# Where the callee text sits in a row of each call kind.
CALLEE_FIELD = {"calls": 5, "callees": 2}
EMPTY_LIST = 2  # the length of "[]": a fact kind with no rows


def common_substring(texts: list[str]) -> str:
    """The longest text that every one of `texts` contains."""
    shortest = min(texts, key=len)
    for size in range(len(shortest), 0, -1):
        for start in range(len(shortest) - size + 1):
            part = shortest[start : start + size]
            if all(part in text for text in texts):
                return part
    return ""


class Words:
    """Texts to look for in fact JSON, spelled as they appear there.

    `ending(names)` finds strings that end in a name (`"a.b.name"`, `"name"`): callees, string
    values, imported names. `attributes(names)` finds `.name` inside a chain. The test for the
    text all of them share runs first, because it is a single fast search."""

    def __init__(self, texts: Iterable[str]) -> None:
        spelled = sorted(set(texts))
        self.pattern = re.compile("|".join(map(re.escape, spelled)).encode()) if spelled else None
        common = common_substring(spelled) if spelled else ""
        self.common = re.compile(re.escape(common).encode()) if common else None

    @classmethod
    def ending(cls, names: Iterable[str]) -> Words:
        return cls(f'{name}"' for name in names)

    @classmethod
    def attributes(cls, names: Iterable[str]) -> Words:
        return cls(f".{name}" for name in names)

    def found_in(self, text: Any) -> bool:
        if self.pattern is None:
            return False
        if self.common is not None and self.common.search(text) is None:
            return False
        return self.pattern.search(text) is not None


class EdgeFields(NamedTuple):
    """An edge's fields without its kind: what a pack copies from a core edge it shapes."""

    site: Site
    surface: str
    targets: tuple[EntityId, ...]
    resolution: Resolution
    contexts: frozenset[Context]
    producer: str
    via: tuple[EntityId, ...]
    use: str
    attrs: tuple[tuple[str, str], ...]

    @staticmethod
    def of(edge: Edge) -> EdgeFields:
        return EdgeFields(
            edge.site,
            edge.surface,
            edge.targets,
            edge.resolution,
            edge.contexts,
            edge.producer,
            edge.via,
            edge.use,
            edge.attrs,
        )


def entities_named(qualified: str) -> list[EntityId]:
    """Every (module, symbol) split of a qualified name: one of them is the entity it names."""
    parts = qualified.split(".")
    return [EntityId(LANGUAGE_PYTHON, ".".join(parts[:cut]), ".".join(parts[cut:])) for cut in range(1, len(parts))]


class PackQueries:
    """The Python engine's answers, from its decoded records and its graph."""

    def __init__(self, build: CoreBuild, graph: Graph) -> None:
        self.build = build
        self.graph = graph

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
        words = Words.ending(names)
        position = CALLEE_FIELD[kind]
        for scope in scopes:
            if not self.may_mention(scope, (kind,), words):
                continue
            rows = [row for row in scope.record.get(kind, ()) if last_name(row[position]) in names]
            if rows:
                yield scope, rows

    def edges_to(self, kind: str, targets: Collection[EntityId]) -> list[Edge]:
        """Edges of `kind` whose target is one of `targets` (by resolved origin), in graph order."""
        return self.graph.edges_to(kind, targets)

    def edges_spelled(self, kind: str, names: Collection[str]) -> list[Edge]:
        """Edges of `kind` whose surface's last dotted name is one of `names`, resolved or not."""
        return [edge for edge in self.graph.by_kind(kind) if edge.surface.rpartition(".")[2] in names]

    def fields_to(self, kind: str, targets: Collection[EntityId]) -> list[EdgeFields]:
        """`edges_to` as fields, for a pack that makes its own edges from them."""
        return [EdgeFields.of(edge) for edge in self.edges_to(kind, targets)]


class NativePackQueries(PackQueries):
    """The native engine's answers: edge queries go to the link, and row queries read the fact
    text of a file before they decode any of it."""

    def __init__(self, build: CoreBuild, graph: Graph, link: Any) -> None:
        super().__init__(build, graph)
        self.link = link

    @staticmethod
    def text(scope: FileScope, kind: str) -> Any:
        record: Any = scope.record
        return record.texts.take(record.file, kind)

    def may_mention(self, scope: FileScope, kinds: Iterable[str], words: Words) -> bool:
        for kind in kinds:
            text = self.text(scope, kind)
            if text is not None and words.found_in(text):
                return True
        return False

    def has_rows(self, scope: FileScope, kind: str) -> bool:
        text = self.text(scope, kind)
        return text is not None and len(text) > EMPTY_LIST

    def core_edges(self, ids: bytes) -> list[Edge]:
        graph: Any = self.graph
        return graph.core.get(array("I", ids))

    def pack_edges(self, kind: str) -> list[Edge]:
        graph: Any = self.graph
        graph.catch_up()
        return list(graph.extra_index.get(kind, ()))

    def edges_spelled(self, kind: str, names: Collection[str]) -> list[Edge]:
        core = self.core_edges(self.link.ids_spelled(kind, sorted(names)))
        extra = [edge for edge in self.pack_edges(kind) if edge.surface.rpartition(".")[2] in names]
        return core + extra

    def fields_to(self, kind: str, targets: Collection[EntityId]) -> list[EdgeFields]:
        """The link's rows of the core edges by target, made into fields with no Edge in between;
        pack edges of the kind come after them, as in graph order."""
        graph: Any = self.graph
        maker = graph.core.maker
        ids = array("I", self.link.ids_to(kind, [(target.module, target.symbol) for target in targets]))
        rows = self.link.edges(ids.tobytes()) if ids else []
        out = [self.fields(maker, row) for row in rows]
        wanted = set(targets)
        out.extend(
            EdgeFields.of(edge) for edge in self.pack_edges(kind) if any(target in wanted for target in edge.targets)
        )
        return out

    @staticmethod
    def fields(maker: Any, row: tuple[Any, ...]) -> EdgeFields:
        kind, path, module, scope, line, column, surface, target_module, target_symbol, res, bits, via, use, attrs = row
        targets = (maker.entity(target_module, target_symbol),) if target_module is not None else ()
        return EdgeFields(
            Site(path, module, scope, line, column),
            surface,
            targets,
            RESOLUTIONS[res],
            CONTEXT_SETS[bits],
            producer_of(kind),
            tuple(maker.entity(m, s) for m, s in via),
            use,
            attrs,
        )
