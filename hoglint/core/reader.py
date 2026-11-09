"""The pack reader (`hoglint.plugins.PackReader`) over an engine's build and candidate queries.

Both engines give packs this one reader; what differs is the build (decoded records, or the
native link) and the queries (`PackQueries` or `NativePackQueries`).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from hoglint.core.build import CoreBuild
from hoglint.core.harness import GraphSink
from hoglint.core.records import RecordBodies
from hoglint.core.sites import FileScope
from hoglint.model import Edge, EntityId, Graph
from hoglint.native.queries import PackQueries
from hoglint.plugins import EdgeBatch

if TYPE_CHECKING:
    from hoglint.native.queries import Words


class BuildReader:
    """What a pack reads of one snapshot."""

    def __init__(self, build: CoreBuild, queries: PackQueries) -> None:
        self.build = build
        self.queries = queries
        self.files = build.files
        self.paths = list(build.store.records)
        self.index = build.index
        self.first_party = build.first_party
        self.tables = build.tables
        self.resolver = build.resolver
        self.classes = build.classes
        self.context = build.context
        self.external = build.external
        self.records_read = 0

    def record(self, path: str) -> Mapping[str, Any]:
        """One file's whole record: the escape hatch, counted."""
        self.records_read += 1
        return self.build.store.records[path]

    def bodies(self, record: Mapping[str, Any]) -> RecordBodies:
        return self.build.bodies(record)  # type: ignore[arg-type]

    def edges_to(self, kind: str, targets: Collection[EntityId]) -> list[Edge]:
        return self.queries.edges_to(kind, targets)

    def edges_spelled(self, kind: str, names: Collection[str]) -> list[Edge]:
        return self.queries.edges_spelled(kind, names)

    def batch_to(self, kind: str, targets: Collection[EntityId], columns: Sequence[str]) -> EdgeBatch:
        return self.queries.batch_to(kind, targets, columns)

    def calls_named(
        self, scopes: Iterable[FileScope], kind: str, names: Collection[str]
    ) -> Iterator[tuple[FileScope, list[list]]]:
        return self.queries.calls_named(scopes, kind, names)

    def may_mention(self, scope: FileScope, kinds: Iterable[str], words: Words) -> bool:
        return self.queries.may_mention(scope, kinds, words)

    def has_rows(self, scope: FileScope, kind: str) -> bool:
        return self.queries.has_rows(scope, kind)

    def fact_digest(self, record: Any, kinds: Iterable[str]) -> str | None:
        return self.queries.fact_digest(record, kinds)


def python_pack_io(build: CoreBuild, graph: Graph) -> tuple[BuildReader, GraphSink]:
    """The reader and sink packs get on the Python engine."""
    return BuildReader(build, PackQueries(build, graph)), GraphSink(graph)
