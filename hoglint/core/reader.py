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
from hoglint.core.memo import NULL_TRACER, PackMemo, TracedQuery, digest, edge_form, key_of
from hoglint.core.records import RecordBodies
from hoglint.core.sites import FileScope
from hoglint.model import Edge, EntityId, Graph
from hoglint.native.queries import PackQueries
from hoglint.plugins import AttributeUse, EdgeBatch

if TYPE_CHECKING:
    from hoglint.native.queries import Words


def targets_key(targets: Collection[EntityId]) -> str:
    return digest(sorted(str(target) for target in targets))


def edge_forms(edges: list[Edge]) -> list[tuple[object, ...]]:
    return [edge_form(edge) for edge in edges]


def import_rows_form(found: list[tuple[FileScope, Sequence[Any]]]) -> object:
    return [(scope.path, list(row)) for scope, row in found]


def entity_names(found: list[EntityId]) -> object:
    return [str(ref) for ref in found]


class BuildReader:
    """What a pack reads of one snapshot. `memo` is the run's pack memo, None when it is off;
    `tracer` is its tracer, or one that notes nothing."""

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
        self.tracer = tracer = memo.tracer if memo is not None else NULL_TRACER
        records = build.store.records
        self.record_read = TracedQuery(tracer, "record", records.__contains__, plain=True)
        self.listing = TracedQuery(tracer, "listing", self.listing_answer, plain=True)
        self.importing_read = TracedQuery(tracer, "importing", queries.importing, import_rows_form)
        self.subclasses_read = TracedQuery(tracer, "subclasses", queries.subclasses, entity_names, plain=True)
        for read in (self.record_read, self.listing, self.importing_read, self.subclasses_read):
            read.answer_reads()

    def listing_answer(self, which: str) -> object:
        if which == "files":
            return [(scope.path, scope.module) for scope in self.scopes]
        return self.record_paths

    @property
    def files(self) -> list[FileScope]:
        self.listing.note("files")
        return self.scopes

    @property
    def paths(self) -> list[str]:
        self.listing.note("paths")
        return self.record_paths

    def record(self, path: str) -> Mapping[str, Any]:
        """One file's whole record: the escape hatch, counted."""
        self.records_read += 1
        self.record_read(path)
        return self.build.store.records[path]

    def note_file(self, scope: FileScope) -> None:
        """A read of one file's facts. Only the native engine traces, and its records have a file id."""
        if self.tracer.frames:
            record: Any = scope.record
            self.tracer.note_file(record.file)

    def bodies(self, record: Mapping[str, Any]) -> RecordBodies:
        return self.build.bodies(record)  # type: ignore[arg-type]

    def edges_to(self, kind: str, targets: Collection[EntityId]) -> list[Edge]:
        found = self.queries.edges_to(kind, targets)
        tracer = self.tracer
        if tracer.frames:
            tracer.note(f"edges-to@{tracer.step_name}", key_of(kind, targets_key(targets)), found, edge_forms)
        return found

    def edges_spelled(self, kind: str, names: Collection[str]) -> list[Edge]:
        found = self.queries.edges_spelled(kind, names)
        tracer = self.tracer
        if tracer.frames:
            tracer.note(f"edges-spelled@{tracer.step_name}", key_of(kind, sorted(names)), found, edge_forms)
        return found

    def batch_to(self, kind: str, targets: Collection[EntityId], columns: Sequence[str]) -> EdgeBatch:
        found = self.queries.batch_to(kind, targets, columns)
        tracer = self.tracer
        if tracer.frames:
            key = key_of(kind, targets_key(targets), list(columns))
            tracer.note(f"batch-to@{tracer.step_name}", key, [found.columns[name] for name in columns])
        return found

    def calls_named(
        self, scopes: Iterable[FileScope], kind: str, names: Collection[str]
    ) -> Iterator[tuple[FileScope, list[list]]]:
        scopes = list(scopes)
        if self.tracer.frames:
            for scope in scopes:
                self.note_file(scope)
        return self.queries.calls_named(scopes, kind, names)

    def may_mention(self, scope: FileScope, kinds: Iterable[str], words: Words) -> bool:
        self.note_file(scope)
        return self.queries.may_mention(scope, kinds, words)

    def has_rows(self, scope: FileScope, kind: str) -> bool:
        self.note_file(scope)
        return self.queries.has_rows(scope, kind)

    def fact_digest(self, record: Any, kinds: Iterable[str]) -> str | None:
        if self.tracer.frames:
            self.tracer.note_file(record.file)
        return self.queries.fact_digest(record, kinds)

    def uses_to(
        self, scopes: Iterable[FileScope], targets: Collection[EntityId]
    ) -> Iterator[tuple[FileScope, list[AttributeUse]]]:
        scopes = list(scopes)
        found = dict(self.queries.uses_to(scopes, targets))
        if self.tracer.frames:
            wanted = targets_key(targets)
            for scope in scopes:
                uses = found.get(scope, [])
                self.tracer.note("uses-to", key_of(scope.path, wanted), [repr(use) for use in uses])
        return iter([(scope, found[scope]) for scope in scopes if scope in found])

    def importing(self, names: Collection[str]) -> list[tuple[FileScope, Sequence[Any]]]:
        return self.importing_read(sorted(set(names)))

    def rows(self, scope: FileScope, kind: str) -> list[Any]:
        self.note_file(scope)
        return self.queries.rows(scope, kind)

    def imported_modules(self, scope: FileScope) -> list[str]:
        self.note_file(scope)
        return self.queries.imported_modules(scope)

    def subclasses(self, qualified: str) -> list[EntityId]:
        return self.subclasses_read(qualified)


def python_pack_io(build: CoreBuild, graph: Graph) -> tuple[BuildReader, GraphSink]:
    """The reader and sink packs get on the Python engine."""
    return BuildReader(build, PackQueries(build, graph)), GraphSink(graph)
