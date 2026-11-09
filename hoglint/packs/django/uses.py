"""Model uses with their ORM/DRF shape, and `apps.get_model` label edges.

Core makes one `reference` edge per use site, with its use position. This pack reads those
whose target is a model (a projected batch by target), refines each with its Django shape, and
follows locals the use flows into (`qs = M.objects.filter(); qs.update()`), which only the
derived rows of those files show. The core edge stays the one edge of the use.
"""

from __future__ import annotations

from bisect import bisect_left, insort
from collections import defaultdict
from typing import NamedTuple

from hoglint.core.harness import Output, each_item, failure_edge
from hoglint.core.records import str_literal
from hoglint.core.sites import FileScope
from hoglint.model import Edge, Resolution
from hoglint.packs.django.models import ModelRegistry
from hoglint.packs.django.shapes import OrmShapes, Shape, UsePosition
from hoglint.plugins import AttributeUse, PackReader, Refinements

USES_PRODUCER = "django.uses@1"
GET_MODEL_PRODUCER = "django.get-model@1"
CORE_REFERENCE_PRODUCER = "core-reference@1"
GET_MODEL = "get_model"
NOT_A_REGISTRY = frozenset({"self", "cls"})


USE_COLUMNS = ("producer", "path", "scope", "line", "column", "target", "attr:chain", "attr:up")


class Use(NamedTuple):
    """A core `reference` edge onto a model, as much of it as the shape needs."""

    handle: int
    scope: str
    line: int
    column: int
    chain: str
    up: str


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


class UseProducer:
    """Refines core's `reference` edges onto first-party models with their shape (`shape`,
    `shape_detail`, and the `flow_shape` attr when a later use through a local makes it
    stronger). Each use is refined once: its shape and flow shape are known first."""

    def __init__(self, reader: PackReader, registry: ModelRegistry) -> None:
        self.reader = reader
        self.registry = registry
        self.shapes = OrmShapes()
        self.positions: dict[tuple[str, str], tuple[UsePosition, str | None]] = {}
        self.position_shapes: dict[tuple[str, str], Shape] = {}

    def position(self, chain: str, up: str) -> tuple[UsePosition, str | None]:
        """A use position and the local it is assigned to. Most uses repeat a few (chain, up)
        pairs, so each one parses once."""
        key = (chain, up)
        found = self.positions.get(key)
        if found is None:
            position = UsePosition.of(chain, up)
            found = self.positions[key] = (position, position.local_target())
        return found

    def shape(self, chain: str, up: str, position: UsePosition) -> Shape:
        key = (chain, up)
        found = self.position_shapes.get(key)
        if found is None:
            found = self.position_shapes[key] = self.shapes.classify(position)
        return found

    def by_file(self) -> dict[str, list[Use]]:
        out: dict[str, list[Use]] = defaultdict(list)
        models = self.registry.models
        batch = self.reader.batch_to("reference", models, USE_COLUMNS)
        rows = zip(batch.handles, *(batch.columns[name] for name in USE_COLUMNS))
        for handle, producer, path, scope, line, column, target, chain, up in rows:
            if producer == CORE_REFERENCE_PRODUCER and target in models:
                out[path].append(Use(handle, scope, line, column, chain, up))
        return out

    def produce(self, files: list[FileScope]) -> list[Output]:
        uses = self.by_file()
        aggregates = {scope.path: found for scope, found in self.reader.uses_to(files, self.registry.models)}
        refined = Refinements()

        def output_for(scope: FileScope) -> list[Output]:
            self.shaped(scope, uses.get(scope.path, []), refined)
            return list(self.aggregate_edges(scope, aggregates.get(scope.path, [])))

        return [*each_item(files, output_for, self.failure), refined]

    @staticmethod
    def failure(scope: FileScope, error: Exception) -> Edge:
        return failure_edge("reference", USES_PRODUCER, scope.site(-1, 0, 0), scope.base_contexts, error)

    def shaped(self, scope: FileScope, uses: list[Use], out: Refinements) -> None:
        """Adds one row per use to `out`, once every shape of the file is known: a file that
        fails adds nothing."""
        if not uses:
            return
        sources = LocalSources()
        positions: list[UsePosition] = []
        shapes: list[Shape] = []
        locals_: set[str] = set()
        for index, use in enumerate(uses):
            position, local = self.position(use.chain, use.up)
            shape = self.shape(use.chain, use.up, position)
            if local is not None:
                sources.assign(use.scope, local, use.line, use.column, index)
                locals_.add(local)
            positions.append(position)
            shapes.append(shape)
        later: dict[int, list[UsePosition]] = defaultdict(list)
        derived = sorted(self.reader.rows(scope, "derived"), key=lambda r: (r[2], r[3])) if locals_ else []
        for at_scope, _, line, column, _, root, chain, up, origin in derived:
            if root not in locals_:
                continue
            index = sources.held(scope.scope_name(origin), root, line, column)
            if index is None:
                continue
            position, local = self.position(chain, up)
            later[index].append(position)
            if local is not None:
                sources.assign(scope.scope_name(at_scope), local, line, column, index)
                locals_.add(local)
        flow_shapes: dict[int, str] = {}
        for index, flows in later.items():
            refined = self.shapes.refine(positions[index], flows)
            if refined is not None and refined != shapes[index]:
                flow_shapes[index] = refined.text()
        for index, use in enumerate(uses):
            flow = (("flow_shape", flow_shapes[index]),) if index in flow_shapes else ()
            shape = shapes[index]
            out.add(use.handle, shape.kind, shape.detail, flow)

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


class GetModelProducer:
    """`<receiver>.get_model("app", "Model")` with literal arguments, matched by spelling: the
    receiver is often a parameter (`apps` in a migration), which has no origin."""

    def __init__(self, reader: PackReader, registry: ModelRegistry) -> None:
        self.reader = reader
        self.registry = registry

    def produce(self, files: list[FileScope]) -> list[Edge]:
        calls = {scope.path: rows for scope, rows in self.reader.calls_named(files, "calls", (GET_MODEL,))}

        def file_edges(scope: FileScope) -> list[Edge]:
            return [self.edge(scope, row) for row in calls[scope.path] if "." in row[5]]

        return each_item([scope for scope in files if scope.path in calls], file_edges, self.failure)

    @staticmethod
    def failure(scope: FileScope, error: Exception) -> Edge:
        return failure_edge("model-label", GET_MODEL_PRODUCER, scope.site(-1, 0, 0), scope.base_contexts, error)

    @staticmethod
    def label_of(args: list, kws: list) -> tuple[str | None, str | None]:
        literal = [text for text in (str_literal(a) for a in args) if text is not None]
        kw = {k: str_literal(v) for k, v in kws if k in ("app_label", "model_name") and str_literal(v) is not None}
        app_label = kw.get("app_label", literal[0] if literal else None)
        class_name = kw.get("model_name", literal[1] if len(literal) > 1 else None)
        if app_label is not None and class_name is None and "." in app_label:
            app_label, class_name = app_label.split(".", 1)
        return app_label, class_name

    def edge(self, scope: FileScope, row: list) -> Edge:
        at_scope, _, line, column, flags, callee, args, kws = row
        app_label, class_name = self.label_of(args, kws)
        site = scope.site(at_scope, line, column)
        contexts = scope.contexts(flags, at_scope)
        if app_label is None or class_name is None:
            receiver = callee.split(".", 1)[0]
            resolution = Resolution.NOT_APPLICABLE if receiver in NOT_A_REGISTRY else Resolution.UNRESOLVED
            return Edge("model-label", site, callee, (), resolution, contexts, GET_MODEL_PRODUCER, shape=GET_MODEL)
        label = f"{app_label}.{class_name}"
        found = self.registry.resolve_label(label, app_label, None)
        if found is None:
            return Edge(
                "model-label", site, label, (), Resolution.UNRESOLVED, contexts, GET_MODEL_PRODUCER, shape=GET_MODEL
            )
        resolution = self.reader.first_party.resolution(found.module)
        return Edge("model-label", site, label, (found,), resolution, contexts, GET_MODEL_PRODUCER, shape=GET_MODEL)
