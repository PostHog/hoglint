"""Model uses with their ORM/DRF shape, and `apps.get_model` label edges.

Core makes one `reference` edge per use site, with its use position. This pack reads those
whose target is a model (a projected batch by target), refines each with its Django shape, and
follows locals the use flows into (`qs = M.objects.filter(); qs.update()`), which only the
derived rows of those files show. The core edge stays the one edge of the use.
"""

from __future__ import annotations

from bisect import bisect_left, insort
from collections import defaultdict
from collections.abc import Sequence
from typing import NamedTuple

from hoglint.core.harness import Output, each_item, failure_edge
from hoglint.core.memo import ItemOutput, ItemRun, Items
from hoglint.core.reader import note_file_of
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
# Django's app registry, by origin.
APP_REGISTRIES = frozenset({"django.apps.apps", "django.apps.registry.apps"})
# The parameter a migration function takes the historical app registry by.
REGISTRY_PARAMETER = "apps"


USE_COLUMNS = ("producer", "path", "scope", "line", "column", "target", "attr:chain", "attr:up")


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


class UseProducer:
    """Refines core's `reference` edges onto first-party models with their shape (`shape`,
    `shape_detail`, and the `flow_shape` attr when a later use through a local makes it
    stronger). Each use is refined once: its shape and flow shape are known first."""

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

    def produce(self, files: list[FileScope]) -> list[Output]:
        uses = self.uses()
        aggregates = {scope.path: found for scope, found in self.reader.uses_to(files, self.registry.models)}
        refined = Refinements()

        def output_for(scope: FileScope) -> list[Output]:
            self.shaped(scope, uses, uses.by_file.get(scope.path, []), refined)
            return list(self.aggregate_edges(scope, aggregates.get(scope.path, [])))

        return [*each_item(files, output_for, self.failure), refined]

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


class GetModelProducer:
    """`<registry>.get_model("app", "Model")` on Django's app registry: the `apps` object of
    `django.apps`, or the `apps` argument of a migration function, which has no origin. A method of
    any other receiver that is named `get_model` names no model."""

    def __init__(self, reader: PackReader, registry: ModelRegistry) -> None:
        self.reader = reader
        self.registry = registry

    def produce(self, files: list[FileScope]) -> list[Output]:
        return ItemRun(GetModelItems(self, files), None).outputs()

    @staticmethod
    def failure(scope: FileScope, error: Exception) -> Edge:
        return failure_edge("model-label", GET_MODEL_PRODUCER, scope.site(-1, 0, 0), scope.base_contexts, error)

    @staticmethod
    def is_parameter(scope: FileScope, at_scope: int, name: str) -> bool:
        """Whether a function the site lies in, or one around it, takes a parameter `name`."""
        while at_scope >= 0:
            node = scope.defs[at_scope]
            if any(param[0] == name for param in node.get("params", ())):
                return True
            at_scope = node["s"]
        return False

    def on_registry(self, scope: FileScope, row: list) -> bool:
        """Whether a `get_model` call's receiver is Django's app registry."""
        at_scope, _, line, _, _, callee, _, _ = row
        receiver, _, _ = callee.rpartition(".")
        if not receiver:
            return False
        if receiver == REGISTRY_PARAMETER and self.is_parameter(scope, at_scope, receiver):
            return True
        found, rest = self.reader.context.chain_lookup(scope, receiver, at_scope, line)
        return not rest and found.hit is not None and str(found.hit.origin) in APP_REGISTRIES

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
            return Edge(
                "model-label", site, callee, (), Resolution.UNRESOLVED, contexts, GET_MODEL_PRODUCER, shape=GET_MODEL
            )
        label = f"{app_label}.{class_name}"
        found = self.registry.resolve_label(label, app_label, None)
        if found is None:
            return Edge(
                "model-label", site, label, (), Resolution.UNRESOLVED, contexts, GET_MODEL_PRODUCER, shape=GET_MODEL
            )
        resolution = self.reader.first_party.resolution(found.module)
        return Edge("model-label", site, label, (found,), resolution, contexts, GET_MODEL_PRODUCER, shape=GET_MODEL)


class GetModelItems(Items):
    """`django.get-model` as one item per file: its `get_model` calls, read for the files that
    run in one query."""

    def __init__(self, producer: GetModelProducer, files: list[FileScope]) -> None:
        self.producer = producer
        self.files = {scope.path: scope for scope in files}
        self.calls: dict[str, list[list]] = {}

    def keys(self) -> list[str]:
        return list(self.files)

    def prepare(self, keys: Sequence[str]) -> None:
        scopes = [self.files[key] for key in keys]
        self.calls = {
            scope.path: rows for scope, rows in self.producer.reader.calls_named(scopes, "calls", (GET_MODEL,))
        }

    def run_item(self, key: str) -> ItemOutput:
        scope = self.files[key]
        note_file_of(scope)
        rows = self.calls.get(key)
        if not rows:
            return ItemOutput([])

        def file_edges(scope: FileScope) -> list[Edge]:
            return [self.producer.edge(scope, row) for row in rows if self.on_registry(scope, row)]

        return ItemOutput(list(each_item([scope], file_edges, self.producer.failure)))
