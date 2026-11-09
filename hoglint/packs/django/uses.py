"""Model uses with their ORM/DRF shape, and `apps.get_model` label edges.

Core makes one `reference` edge per use site, with its use position. This pack keeps those
whose target is a model, gives each a Django shape, and follows locals the use flows into
(`qs = M.objects.filter(); qs.update()`), which only the extractor's derived rows show.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import replace

from hoglint.core.build import CoreBuild
from hoglint.core.harness import each_item, failure_edge
from hoglint.core.producers import chain_tokens, leading_attrs
from hoglint.core.records import str_literal
from hoglint.core.sites import FileScope
from hoglint.model import Edge, Resolution
from hoglint.packs.django.models import ModelRegistry
from hoglint.packs.django.shapes import OrmShapes, Shape, UsePosition

USES_PRODUCER = "django.uses@1"
GET_MODEL_PRODUCER = "django.get-model@1"
CORE_REFERENCE_PRODUCER = "core-reference@1"
GET_MODEL = "get_model"
NOT_A_REGISTRY = frozenset({"self", "cls"})


class LocalSources:
    """Which model reference a local holds at a point in its scope. A reassignment starts a new source."""

    def __init__(self) -> None:
        self.assigned: dict[tuple[str, str], list[tuple[int, int, int]]] = defaultdict(list)

    def assign(self, scope: str, local: str, line: int, column: int, index: int) -> None:
        self.assigned[(scope, local)].append((line, column, index))

    def held(self, scope: str, local: str, line: int, column: int) -> int | None:
        """The last assignment before the use. A use above every assignment (a loop) takes the first one."""
        rows = sorted(self.assigned.get((scope, local), ()))
        if not rows:
            return None
        before = [index for at_line, at_column, index in rows if (at_line, at_column) < (line, column)]
        return before[-1] if before else rows[0][2]


class UseProducer:
    """Shaped `reference` edges onto first-party models, from core's reference edges."""

    def __init__(self, build: CoreBuild, registry: ModelRegistry) -> None:
        self.build = build
        self.registry = registry
        self.shapes = OrmShapes()

    def is_model_edge(self, edge: Edge) -> bool:
        target = edge.target
        return edge.producer == CORE_REFERENCE_PRODUCER and target is not None and target in self.registry.models

    def by_file(self, references: Iterable[Edge]) -> dict[str, list[Edge]]:
        out: dict[str, list[Edge]] = defaultdict(list)
        for edge in references:
            if self.is_model_edge(edge):
                out[edge.site.path].append(edge)
        return out

    def produce(self, files: list[FileScope], references: Iterable[Edge]) -> list[Edge]:
        uses = self.by_file(references)

        def edges_for(scope: FileScope) -> list[Edge]:
            return self.shaped(scope, uses.get(scope.path, [])) + self.aggregate_edges(scope)

        return each_item(files, edges_for, self.failure)

    @staticmethod
    def failure(scope: FileScope, error: Exception) -> Edge:
        return failure_edge("reference", USES_PRODUCER, scope.site(-1, 0, 0), scope.base_contexts, error)

    def shaped(self, scope: FileScope, uses: list[Edge]) -> list[Edge]:
        if not uses:
            return []
        out: list[Edge] = []
        sources = LocalSources()
        positions: list[UsePosition] = []
        for edge in uses:
            position = UsePosition.of(edge.attr("chain"), edge.attr("up"))
            shape = self.shapes.classify(position)
            local = position.local_target()
            if local is not None:
                sources.assign(edge.site.scope, local, edge.site.line, edge.site.column, len(out))
            positions.append(position)
            out.append(replace(edge, producer=USES_PRODUCER, shape=shape.kind, shape_detail=shape.detail))
        later: dict[int, list[UsePosition]] = defaultdict(list)
        derived = sorted(scope.record.get("derived", ()), key=lambda r: (r[2], r[3]))
        for at_scope, _, line, column, _, root, chain, up, origin in derived:
            index = sources.held(scope.scope_name(origin), root, line, column)
            if index is None:
                continue
            position = UsePosition.of(chain, up)
            later[index].append(position)
            local = position.local_target()
            if local is not None:
                sources.assign(scope.scope_name(at_scope), local, line, column, index)
        for index, flows in later.items():
            refined = self.shapes.refine(positions[index], flows)
            if refined is not None and refined != Shape(out[index].shape, out[index].shape_detail):
                out[index] = replace(out[index], attrs=(*out[index].attrs, ("flow_shape", refined.text())))
        return out

    def aggregate_edges(self, scope: FileScope) -> list[Edge]:
        """`import a.b` then `a.b.Model`: L1 only counts these, so the shape stays unknown."""
        out: list[Edge] = []
        for at_scope, _, root, attrs, count, line, column in scope.record.get("uses", ()):
            if not attrs:
                continue
            names = leading_attrs(chain_tokens(attrs))
            found, rest = self.build.context.chain_lookup(scope, ".".join([root, *names]), at_scope, line)
            hit = found.hit
            if hit is None or hit.kind != "class" or len(rest) == len(names) or hit.origin not in self.registry.models:
                continue
            out.append(
                Edge(
                    kind="reference",
                    site=scope.site(at_scope, line, column),
                    surface=found.surface,
                    targets=(hit.origin,),
                    resolution=Resolution.RESOLVED,
                    contexts=scope.contexts(0, at_scope),
                    producer=USES_PRODUCER,
                    via=hit.via,
                    use="aggregate",
                    attrs=(("count", str(count)),),
                )
            )
        return out


class GetModelProducer:
    """`apps.get_model("app", "Model")` and `get_model("app.Model")` with literal arguments."""

    def __init__(self, build: CoreBuild, registry: ModelRegistry) -> None:
        self.build = build
        self.registry = registry

    def produce(self, files: list[FileScope]) -> list[Edge]:
        return each_item(files, self.file_edges, self.failure)

    @staticmethod
    def failure(scope: FileScope, error: Exception) -> Edge:
        return failure_edge("model-label", GET_MODEL_PRODUCER, scope.site(-1, 0, 0), scope.base_contexts, error)

    def file_edges(self, scope: FileScope) -> list[Edge]:
        out = []
        for row in scope.record.get("calls", ()):
            callee = row[5]
            if "." in callee and callee.endswith(f".{GET_MODEL}"):
                out.append(self.edge(scope, row))
        return out

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
        resolution = self.build.first_party.resolution(found.module)
        return Edge("model-label", site, label, (found,), resolution, contexts, GET_MODEL_PRODUCER, shape=GET_MODEL)
