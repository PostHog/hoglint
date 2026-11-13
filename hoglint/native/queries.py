"""Candidate queries packs ask of one snapshot: rows by spelling or mention, edges by origin or
spelling, attribute uses by target, and imports by name.

A pack asks here for candidates, then applies its own rule to each one. Every query returns a
superset of what that rule can accept, so pack output never depends on the engine:
the link answers from its own rows and edge indexes, so no record decodes to find candidates
(D23).
"""

from __future__ import annotations

import hashlib
from array import array
from collections import defaultdict
from collections.abc import Collection, Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import orjson

from hoglint.core.records import last_name
from hoglint.core.sites import FileScope
from hoglint.model import LANGUAGE_PYTHON, Edge, EntityId, Graph
from hoglint.native.build import NativeBuild
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


def entities_named(qualified: str) -> list[EntityId]:
    """Every (module, symbol) split of a qualified name: one of them is the entity it names."""
    parts = qualified.split(".")
    return [EntityId(LANGUAGE_PYTHON, ".".join(parts[:cut]), ".".join(parts[cut:])) for cut in range(1, len(parts))]


class NativePackQueries:
    """The native engine's answers, from the link's rows and edge indexes."""

    def __init__(self, build: NativeBuild, graph: Graph, link: Any) -> None:
        self.build = build
        self.graph = graph
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
        """Rows of a call kind (`calls`, `callees`) whose callee's last dotted name is one of
        `names`, resolved or not, by file in order. Files with no such row are left out."""
        scopes = list(scopes)
        if len(scopes) <= FEW_FILES:
            return self.calls_named_in_few(scopes, kind, names)
        return self.calls_named_in_all(scopes, kind, names)

    @staticmethod
    def calls_named_in_few(
        scopes: list[FileScope], kind: str, names: Collection[str]
    ) -> Iterator[tuple[FileScope, list[list]]]:
        """`calls_named` by decoding each file's rows."""
        position = CALLEE_FIELD[kind]
        for scope in scopes:
            rows = [row for row in scope.record.get(kind, ()) if last_name(row[position]) in names]
            if rows:
                yield scope, rows

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

    def edges_to(self, kind: str, targets: Collection[EntityId]) -> list[Edge]:
        """Edges of `kind` whose target is one of `targets` (by resolved origin), in graph order."""
        return self.graph.edges_to(kind, targets)

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
