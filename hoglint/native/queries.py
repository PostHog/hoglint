"""Candidate queries packs ask of one snapshot: rows by spelling or mention, edges by origin or
spelling, attribute uses by target, and imports by name.

A pack asks here for candidates, then applies its own rule to each one. Every query returns a
superset of what that rule can accept, so pack output never depends on the engine:
- the Python engine filters its decoded records and its graph;
- the native engine asks the link, which answers from its own rows and edge indexes, so no
  record decodes to find candidates (D23).
"""

from __future__ import annotations

import hashlib
from array import array
from collections import defaultdict
from collections.abc import Callable, Collection, Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import orjson

from hoglint.core.build import CoreBuild
from hoglint.core.producers import chain_tokens, leading_attrs
from hoglint.core.records import last_name
from hoglint.core.sites import FileScope
from hoglint.core.symbols import absolute_import
from hoglint.model import LANGUAGE_PYTHON, Edge, EntityId, Graph
from hoglint.plugins import AttributeUse, EdgeBatch

# Where the callee text sits in a row of each call kind.
CALLEE_FIELD = {"calls": 5, "callees": 2}
# Up to this many files, a native rows query decodes each file's rows instead of scanning all.
FEW_FILES = 64


@dataclass(frozen=True, slots=True)
class Words:
    """Texts to look for in a file's facts: strings that end in one of `suffixes`, or hold one of
    `parts`. `ending(names)` finds callees, string values and imported names that end in a name
    (`a.b.name`, `name`); `attributes(names)` finds `.name` inside a chain."""

    suffixes: tuple[str, ...] = ()
    parts: tuple[str, ...] = ()

    @classmethod
    def ending(cls, names: Iterable[str]) -> Words:
        return cls(suffixes=tuple(sorted(set(names))))

    @classmethod
    def attributes(cls, names: Iterable[str]) -> Words:
        return cls(parts=tuple(sorted({f".{name}" for name in names})))


ATTR_COLUMN = "attr:"
EDGE_COLUMNS: dict[str, Callable[[Edge], Any]] = {
    "path": lambda edge: edge.site.path,
    "module": lambda edge: edge.site.module,
    "scope": lambda edge: edge.site.scope,
    "line": lambda edge: edge.site.line,
    "column": lambda edge: edge.site.column,
    "surface": lambda edge: edge.surface,
    "target": lambda edge: edge.target,
    "producer": lambda edge: edge.producer,
}


def column_of(edge: Edge, name: str) -> Any:
    """One `EdgeBatch` column's value for an edge."""
    if name.startswith(ATTR_COLUMN):
        return edge.attr(name[len(ATTR_COLUMN) :])
    return EDGE_COLUMNS[name](edge)


def batch_of(edges: Sequence[Edge], columns: Sequence[str]) -> EdgeBatch:
    return EdgeBatch(
        [edge.handle for edge in edges], {name: [column_of(edge, name) for edge in edges] for name in columns}
    )


def entities_named(qualified: str) -> list[EntityId]:
    """Every (module, symbol) split of a qualified name: one of them is the entity it names."""
    parts = qualified.split(".")
    return [EntityId(LANGUAGE_PYTHON, ".".join(parts[:cut]), ".".join(parts[cut:])) for cut in range(1, len(parts))]


def first_party_roots(scope: FileScope, roots: set[str], first_party: Any) -> set[str]:
    """Those of `roots` a first-party import binds anywhere in the file. A name that only
    third-party imports bind cannot lead into first-party code."""
    out: set[str] = set()
    for _, _, _, _, target, level, name, asname in scope.record.get("imports", ()):
        if name is None:
            bound = asname or target.split(".")[0]
            if bound in roots and first_party.contains(target):
                out.add(bound)
            continue
        bound = asname or name
        if bound not in roots or name == "*":
            continue
        source = absolute_import(scope.module, scope.is_package, target, level)
        if source is not None and first_party.contains(source):
            out.add(bound)
    return out


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


class NativePackQueries(PackQueries):
    """The native engine's answers, from the link's rows and edge indexes."""

    def __init__(self, build: CoreBuild, graph: Graph, link: Any) -> None:
        super().__init__(build, graph)
        self.link = link
        self.paths: list[str] = link.paths()
        self.keys: list[str | None] | None = None
        self.patterns: dict[Words, int] = {}

    def scope_at(self, file: int) -> FileScope:
        scope_of: Any = self.build.scope_of
        return scope_of[self.paths[file]]

    def may_mention(self, scope: FileScope, kinds: Iterable[str], words: Words) -> bool:
        pattern = self.patterns.get(words)
        if pattern is None:
            pattern = self.patterns[words] = self.link.words(list(words.suffixes), list(words.parts))
        record: Any = scope.record
        return self.link.mentions(record.file, list(kinds), pattern)

    def has_rows(self, scope: FileScope, kind: str) -> bool:
        record: Any = scope.record
        return self.link.has_rows(record.file, kind)

    def calls_named(
        self, scopes: Iterable[FileScope], kind: str, names: Collection[str]
    ) -> Iterator[tuple[FileScope, list[list]]]:
        scopes = list(scopes)
        if len(scopes) <= FEW_FILES:
            return super().calls_named(scopes, kind, names)
        return self.calls_named_in_all(scopes, kind, names)

    def calls_named_in_all(
        self, scopes: list[FileScope], kind: str, names: Collection[str]
    ) -> Iterator[tuple[FileScope, list[list]]]:
        by_file: dict[int, list[list]] = defaultdict(list)
        for file, row in self.link.rows_named(kind, sorted(names)):
            by_file[file].append(orjson.loads(row))
        for scope in scopes:
            record: Any = scope.record
            rows = by_file.get(record.file)
            if rows:
                yield scope, rows

    def uses_to(
        self, scopes: Iterable[FileScope], targets: Collection[EntityId]
    ) -> Iterator[tuple[FileScope, list[AttributeUse]]]:
        context: Any = self.build.context
        files = self.build.files
        by_scope: dict[str, list[AttributeUse]] = defaultdict(list)
        for at, at_scope, line, column, count, found in self.link.uses_to([(t.module, t.symbol) for t in targets]):
            by_scope[files[at].path].append(AttributeUse(at_scope, line, column, count, context.lookup(found)))
        for scope in scopes:
            uses = by_scope.get(scope.path)
            if uses:
                yield scope, uses

    def importing(self, names: Collection[str]) -> list[tuple[FileScope, Sequence[Any]]]:
        return [(self.scope_at(file), row) for file, row in self.link.import_rows_named(sorted(set(names)))]

    def imported_modules(self, scope: FileScope) -> list[str]:
        record: Any = scope.record
        return self.link.imported_modules(record.file)

    def rows(self, scope: FileScope, kind: str) -> list[Any]:
        record: Any = scope.record
        if dict.__contains__(record, kind):
            return record.get(kind, [])
        text = self.link.raw(record.file, kind)
        return orjson.loads(text) if text is not None else []

    def subclasses(self, qualified: str) -> list[EntityId]:
        graph: Any = self.graph
        maker = graph.core.maker
        return [maker.entity(module, symbol) for module, symbol in self.link.subclasses(qualified)]

    def fact_digest(self, record: Any, kinds: Iterable[str]) -> str | None:
        """A record's facts are what the extractor made of the file's bytes, so for a file with
        a cache key the digest stands on the extractor, its options and the key, with no fact
        text read. A file without one digests its facts' text."""
        if self.keys is None:
            self.keys = self.link.blob_keys()
        digest = hashlib.blake2b(digest_size=16)
        for kind in kinds:
            digest.update(kind.encode())
            digest.update(b"\0")
        key = self.keys[record.file]
        extracted_by: str = getattr(self.build, "extracted_by", "")
        if key is not None and extracted_by:
            digest.update(extracted_by.encode())
            digest.update(b"\0")
            digest.update(key.encode())
            return digest.hexdigest()
        for kind in kinds:
            text = self.link.raw(record.file, kind)
            digest.update(b"\0" if text is None else text)
            digest.update(b"\0")
        return digest.hexdigest()

    def edges(self, ids: bytes) -> list[Edge]:
        graph: Any = self.graph
        return graph.core.get(array("I", ids))

    def edges_spelled(self, kind: str, names: Collection[str]) -> list[Edge]:
        return self.edges(self.link.ids_spelled(kind, sorted(names)))

    def batch_to(self, kind: str, targets: Collection[EntityId], columns: Sequence[str]) -> EdgeBatch:
        """The link's edges by target, core and pack, as columns with no Edge in between."""
        graph: Any = self.graph
        maker = graph.core.maker
        ids = array("I", self.link.ids_to(kind, [(target.module, target.symbol) for target in targets]))
        names = ["target_module" if name == "target" else name for name in columns]
        found = self.link.columns(ids.tobytes(), names) if ids else [[] for _ in columns]
        batch = EdgeBatch(list(ids), dict(zip(columns, found)))
        if "target" in batch.columns:
            pairs = zip(batch.columns["target"], self.link.columns(ids.tobytes(), ["target_symbol"])[0] if ids else [])
            batch.columns["target"] = [
                maker.entity(module, symbol) if module is not None else None for module, symbol in pairs
            ]
        return batch
