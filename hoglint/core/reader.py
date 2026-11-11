"""The pack reader (`hoglint.plugins.PackReader`) over an engine's build and candidate queries.

Both engines give packs this one reader; what differs is the build (decoded records, or the
native link) and the queries (`PackQueries` or `NativePackQueries`).

While an item runs (hoglint/core/memo.py), each query notes what it read: a file's facts when its
answer comes from that file alone, else the question and its answer.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from hoglint.core.build import CoreBuild
from hoglint.core.harness import GraphSink
from hoglint.core.memo import TRACER, PackMemo, digest, edge_form, key_of
from hoglint.core.records import RecordBodies
from hoglint.core.sites import FileScope
from hoglint.model import Edge, EntityId, Graph
from hoglint.native.queries import PackQueries
from hoglint.plugins import AttributeUse, EdgeBatch

if TYPE_CHECKING:
    from hoglint.native.queries import Words


def note_file_of(scope: FileScope) -> None:
    """A read of one file's facts; a record with no file id (the Python engine's) notes nothing."""
    file = getattr(scope.record, "file", None)
    if file is not None:
        TRACER.note_file(file)


def targets_key(targets: Collection[EntityId]) -> str:
    return digest(sorted(str(target) for target in targets))


class BuildReader:
    """What a pack reads of one snapshot. `memo` is the run's pack memo, None when it is off."""

    def __init__(self, build: CoreBuild, queries: PackQueries, memo: PackMemo | None = None) -> None:
        self.build = build
        self.queries = queries
        self.scopes = build.files
        self.record_paths = list(build.store.records)
        self.index = build.index
        self.first_party = build.first_party
        self.tables = build.tables
        self.resolver = build.resolver
        self.classes = build.classes
        self.context = build.context
        self.external = build.external
        self.records_read = 0
        self.memo = memo

    @property
    def files(self) -> list[FileScope]:
        if TRACER.frames:
            TRACER.note_lazy("listing", "files", lambda: [(scope.path, scope.module) for scope in self.scopes])
        return self.scopes

    @property
    def paths(self) -> list[str]:
        if TRACER.frames:
            TRACER.note_lazy("listing", "paths", lambda: self.record_paths)
        return self.record_paths

    def record(self, path: str) -> Mapping[str, Any]:
        """One file's whole record: the escape hatch, counted."""
        self.records_read += 1
        records = self.build.store.records
        if TRACER.frames:
            TRACER.note("record", path, path in records)
        return records[path]

    def bodies(self, record: Mapping[str, Any]) -> RecordBodies:
        return self.build.bodies(record)  # type: ignore[arg-type]

    def edges_to(self, kind: str, targets: Collection[EntityId]) -> list[Edge]:
        found = self.queries.edges_to(kind, targets)
        if TRACER.frames:
            TRACER.note(f"edges-to@{TRACER.step}", key_of(kind, targets_key(targets)), [edge_form(e) for e in found])
        return found

    def edges_spelled(self, kind: str, names: Collection[str]) -> list[Edge]:
        found = self.queries.edges_spelled(kind, names)
        if TRACER.frames:
            TRACER.note(f"edges-spelled@{TRACER.step}", key_of(kind, sorted(names)), [edge_form(e) for e in found])
        return found

    def batch_to(self, kind: str, targets: Collection[EntityId], columns: Sequence[str]) -> EdgeBatch:
        found = self.queries.batch_to(kind, targets, columns)
        if TRACER.frames:
            key = key_of(kind, targets_key(targets), list(columns))
            TRACER.note(f"batch-to@{TRACER.step}", key, [found.columns[name] for name in columns])
        return found

    def calls_named(
        self, scopes: Iterable[FileScope], kind: str, names: Collection[str]
    ) -> Iterator[tuple[FileScope, list[list]]]:
        scopes = list(scopes)
        if TRACER.frames:
            for scope in scopes:
                note_file_of(scope)
        return self.queries.calls_named(scopes, kind, names)

    def may_mention(self, scope: FileScope, kinds: Iterable[str], words: Words) -> bool:
        if TRACER.frames:
            note_file_of(scope)
        return self.queries.may_mention(scope, kinds, words)

    def has_rows(self, scope: FileScope, kind: str) -> bool:
        if TRACER.frames:
            note_file_of(scope)
        return self.queries.has_rows(scope, kind)

    def fact_digest(self, record: Any, kinds: Iterable[str]) -> str | None:
        file = getattr(record, "file", None)
        if TRACER.frames and file is not None:
            TRACER.note_file(file)
        return self.queries.fact_digest(record, kinds)

    def uses_to(
        self, scopes: Iterable[FileScope], targets: Collection[EntityId]
    ) -> Iterator[tuple[FileScope, list[AttributeUse]]]:
        scopes = list(scopes)
        found = dict(self.queries.uses_to(scopes, targets))
        if TRACER.frames:
            wanted = targets_key(targets)
            for scope in scopes:
                uses = found.get(scope, [])
                TRACER.note("uses-to", key_of(scope.path, wanted), [repr(use) for use in uses])
        return iter([(scope, found[scope]) for scope in scopes if scope in found])

    def importing(self, names: Collection[str]) -> list[tuple[FileScope, Sequence[Any]]]:
        found = self.queries.importing(names)
        if TRACER.frames:
            TRACER.note("importing", key_of(sorted(set(names))), [(scope.path, list(row)) for scope, row in found])
        return found

    def rows(self, scope: FileScope, kind: str) -> list[Any]:
        if TRACER.frames:
            note_file_of(scope)
        return self.queries.rows(scope, kind)

    def imported_modules(self, scope: FileScope) -> list[str]:
        if TRACER.frames:
            note_file_of(scope)
        return self.queries.imported_modules(scope)

    def subclasses(self, qualified: str) -> list[EntityId]:
        found = self.queries.subclasses(qualified)
        if TRACER.frames:
            TRACER.note("subclasses", qualified, [str(ref) for ref in found])
        return found


def python_pack_io(build: CoreBuild, graph: Graph) -> tuple[BuildReader, GraphSink]:
    """The reader and sink packs get on the Python engine."""
    return BuildReader(build, PackQueries(build, graph)), GraphSink(graph)
