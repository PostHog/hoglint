"""`django.uses`: the link judges every use and refines its edge (D49), with no Python object per
use. The aggregate edges and the failure edges stay here: there are few.

Core makes one `reference` edge per use site, with its use position. The link reads those whose
target is a model, refines each with its Django shape, and follows locals the use flows into
(`qs = M.objects.filter(); qs.update()`), which only the derived rows of those files show. The
core edge stays the one edge of the use. A file the link cannot read fails whole; `UseReading`
reads it again here to word that failure.
"""

from __future__ import annotations

from bisect import bisect_left, insort
from collections import defaultdict
from typing import Any, NamedTuple

from hoglint.core.harness import failure_edge
from hoglint.core.sites import FileScope
from hoglint.model import Edge, Resolution
from hoglint.native.graph import NativeSink
from hoglint.packs.django.models import ModelRegistry
from hoglint.packs.django.shapes import VOCABULARY, OrmShapes, Shape, UsePosition
from hoglint.packs.django.uses import CORE_REFERENCE_PRODUCER, USE_COLUMNS, USES_PRODUCER
from hoglint.plugins import AttributeUse, PackReader, Refinements


class Uses(NamedTuple):
    """The core `reference` edges onto models, as columns, as much of them as the shape needs,
    and each file's rows."""

    handles: list[int]
    scopes: list[str]
    lines: list[int]
    columns: list[int]
    chains: list[str]
    ups: list[str]
    by_file: dict[str, list[int]]


class Position(NamedTuple):
    """A use position and the local it is assigned to."""

    position: UsePosition
    local: str | None


class Shaped(NamedTuple):
    """A use position, the local it is assigned to, and its shape."""

    position: UsePosition
    local: str | None
    shape: Shape


class LocalSources:
    """Which model reference a local holds at a point in its scope. A reassignment starts a new
    source. Each local keeps its assignments in source order, so a use finds its source with one
    predecessor search."""

    def __init__(self) -> None:
        self.assigned: dict[tuple[str, str], list[tuple[int, int, int]]] = defaultdict(list)

    def assign(self, scope: str, local: str, line: int, column: int, index: int) -> None:
        insort(self.assigned[(scope, local)], (line, column, index))

    def held(self, scope: str, local: str, line: int, column: int) -> int | None:
        """The last assignment before the use. A use above every assignment (a loop) takes the first one."""
        rows = self.assigned.get((scope, local))
        if not rows:
            return None
        before = bisect_left(rows, (line, column))
        return rows[before - 1][2] if before else rows[0][2]


class UseReading:
    """The uses of a file the link could not read, read again with the shape code to word the
    failure: the exception the reading raises is the failure edge's text. It also makes the
    aggregate edges and the failure edges of the producer."""

    def __init__(self, reader: PackReader, registry: ModelRegistry) -> None:
        self.reader = reader
        self.registry = registry
        self.shapes = OrmShapes()
        self.positions: dict[tuple[str, str], Position] = {}
        self.shaped_positions: dict[tuple[str, str], Shaped] = {}

    def position(self, chain: str, up: str) -> Position:
        """A use position and the local it is assigned to. Most uses repeat a few (chain, up)
        pairs, so each one parses once."""
        key = (chain, up)
        found = self.positions.get(key)
        if found is None:
            position = UsePosition.of(chain, up)
            found = self.positions[key] = Position(position, position.local_target())
        return found

    def shaped_position(self, chain: str, up: str) -> Shaped:
        """`position` with the use's shape, classified once per (chain, up)."""
        key = (chain, up)
        found = self.shaped_positions.get(key)
        if found is None:
            position, local = self.position(chain, up)
            found = self.shaped_positions[key] = Shaped(position, local, self.shapes.classify(position))
        return found

    def uses(self) -> Uses:
        models = self.registry.models
        batch = self.reader.batch_to("reference", models, USE_COLUMNS)
        columns = batch.columns
        by_file: dict[str, list[int]] = defaultdict(list)
        rows = zip(columns["producer"], columns["path"], columns["target"])
        for at, (producer, path, target) in enumerate(rows):
            if producer == CORE_REFERENCE_PRODUCER and target in models:
                by_file[path].append(at)
        return Uses(
            batch.handles,
            columns["scope"],
            columns["line"],
            columns["column"],
            columns["attr:chain"],
            columns["attr:up"],
            by_file,
        )

    @staticmethod
    def failure(scope: FileScope, error: Exception) -> Edge:
        return failure_edge("reference", USES_PRODUCER, scope.site(-1, 0, 0), scope.base_contexts, error)

    def shaped(self, scope: FileScope, uses: Uses, rows: list[int], out: Refinements) -> None:
        """Adds one row per use (`rows` of `uses`) to `out`, once every shape of the file is
        known: a file that fails adds nothing."""
        if not rows:
            return
        sources = LocalSources()
        positions = [self.shaped_position(uses.chains[row], uses.ups[row]) for row in rows]
        locals_: set[str] = set()
        for index, (row, found) in enumerate(zip(rows, positions)):
            if found.local is not None:
                sources.assign(uses.scopes[row], found.local, uses.lines[row], uses.columns[row], index)
                locals_.add(found.local)
        later: dict[int, list[UsePosition]] = defaultdict(list)
        derived = sorted(self.reader.rows(scope, "derived"), key=lambda r: (r[2], r[3])) if locals_ else []
        for at_scope, _, line, column, _, root, chain, up, origin in derived:
            if root not in locals_:
                continue
            index = sources.held(scope.scope_name(origin), root, line, column)
            if index is None:
                continue
            found = self.position(chain, up)
            later[index].append(found.position)
            if found.local is not None:
                sources.assign(scope.scope_name(at_scope), found.local, line, column, index)
                locals_.add(found.local)
        flow_shapes: dict[int, str] = {}
        for index, flows in later.items():
            refined = self.shapes.refine(positions[index].position, flows)
            if refined is not None and refined != positions[index].shape:
                flow_shapes[index] = refined.text()
        for index, (row, found) in enumerate(zip(rows, positions)):
            flow = (("flow_shape", flow_shapes[index]),) if index in flow_shapes else ()
            out.add(uses.handles[row], found.shape.kind, found.shape.detail, flow)

    @staticmethod
    def aggregate_edges(scope: FileScope, found: list[AttributeUse]) -> list[Edge]:
        """`import a.b` then `a.b.Model`: L1 only counts these, so the shape stays unknown."""
        out: list[Edge] = []
        for use in found:
            hit = use.found.hit
            if hit is None:
                continue
            out.append(
                Edge(
                    kind="reference",
                    site=scope.site(use.scope, use.line, use.column),
                    surface=use.found.surface,
                    targets=(hit.origin,),
                    resolution=Resolution.RESOLVED,
                    contexts=scope.contexts(0, use.scope),
                    producer=USES_PRODUCER,
                    via=hit.via,
                    use="aggregate",
                    attrs=(("count", str(use.count)),),
                )
            )
        return out


def file_id(scope: FileScope) -> int:
    record: Any = scope.record
    return record.file


class NativeUses:
    """Runs `django.uses` in the link, into a native sink."""

    def __init__(self, reader: PackReader, registry: ModelRegistry, sink: NativeSink) -> None:
        self.producer = UseReading(reader, registry)
        self.sink = sink
        self.uses: Uses | None = None

    def produce(self, files: list[FileScope], kinds: frozenset[str]) -> list[Edge]:
        """The producer's edges by file: a failure edge for a file the link could not read, else
        its aggregate edges. Its refinements go into the sink last, once nothing else can fail."""
        models = self.producer.registry.models
        aggregates = {scope.path: found for scope, found in self.producer.reader.uses_to(files, models)}
        link = self.sink.graph.link
        staged = link.django_uses(
            [file_id(scope) for scope in files], [(m.module, m.symbol) for m in models], VOCABULARY
        )
        failed = set(staged.failed())
        out: list[Edge] = []
        for scope in files:
            if file_id(scope) in failed:
                out.append(self.producer.failure(scope, self.error_of(scope)))
                continue
            out.extend(self.producer.aggregate_edges(scope, aggregates.get(scope.path, [])))
        if len(staged):
            self.sink.refine_staged(USES_PRODUCER, kinds, staged)
        return out

    def error_of(self, scope: FileScope) -> Exception:
        """What reading the uses of a file the link could not read raises."""
        if self.uses is None:
            self.uses = self.producer.uses()
        try:
            self.producer.shaped(scope, self.uses, self.uses.by_file.get(scope.path, []), Refinements())
        except Exception as error:  # noqa: BLE001 - the reading's own error is the failure's text
            return error
        raise RuntimeError(f"the link could not read the model uses of {scope.path}, the shape code could")
