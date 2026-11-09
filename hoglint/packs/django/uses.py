"""Model uses with their ORM/DRF shape, and `apps.get_model` label edges.

Core makes one `reference` edge per use site, with its use position. This pack reads those
whose target is a model (a query by target), gives each a Django shape, and follows locals the
use flows into (`qs = M.objects.filter(); qs.update()`), which only the derived rows of those
files show.
"""

from __future__ import annotations

from collections import defaultdict

from hoglint.core.build import CoreBuild
from hoglint.core.harness import each_item, failure_edge
from hoglint.core.producers import chain_tokens, leading_attrs
from hoglint.core.records import str_literal
from hoglint.core.sites import FileScope
from hoglint.core.symbols import absolute_import
from hoglint.model import Edge, Resolution
from hoglint.native.queries import EdgeFields, PackQueries
from hoglint.packs.django.models import ModelRegistry
from hoglint.packs.django.shapes import OrmShapes, Shape, UsePosition

USES_PRODUCER = "django.uses@1"
GET_MODEL_PRODUCER = "django.get-model@1"
CORE_REFERENCE_PRODUCER = "core-reference@1"
GET_MODEL = "get_model"
NOT_A_REGISTRY = frozenset({"self", "cls"})


def attr_of(attrs: tuple[tuple[str, str], ...], name: str) -> str:
    for key, value in attrs:
        if key == name:
            return value
    return ""


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
    """Shaped `reference` edges onto first-party models, made from core's reference edges by
    target. Each use gets one edge, made once: its shape and flow shape are known first."""

    def __init__(self, build: CoreBuild, registry: ModelRegistry, queries: PackQueries) -> None:
        self.build = build
        self.registry = registry
        self.queries = queries
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

    def by_file(self) -> dict[str, list[EdgeFields]]:
        out: dict[str, list[EdgeFields]] = defaultdict(list)
        models = self.registry.models
        for fields in self.queries.fields_to("reference", models):
            if fields.producer == CORE_REFERENCE_PRODUCER and len(fields.targets) == 1 and fields.targets[0] in models:
                out[fields.site.path].append(fields)
        return out

    def produce(self, files: list[FileScope]) -> list[Edge]:
        uses = self.by_file()

        def edges_for(scope: FileScope) -> list[Edge]:
            return self.shaped(scope, uses.get(scope.path, [])) + self.aggregate_edges(scope)

        return each_item(files, edges_for, self.failure)

    @staticmethod
    def failure(scope: FileScope, error: Exception) -> Edge:
        return failure_edge("reference", USES_PRODUCER, scope.site(-1, 0, 0), scope.base_contexts, error)

    def shaped(self, scope: FileScope, uses: list[EdgeFields]) -> list[Edge]:
        if not uses:
            return []
        sources = LocalSources()
        positions: list[UsePosition] = []
        shapes: list[Shape] = []
        locals_: set[str] = set()
        for index, fields in enumerate(uses):
            chain, up = attr_of(fields.attrs, "chain"), attr_of(fields.attrs, "up")
            position, local = self.position(chain, up)
            shape = self.shape(chain, up, position)
            if local is not None:
                site = fields.site
                sources.assign(site.scope, local, site.line, site.column, index)
                locals_.add(local)
            positions.append(position)
            shapes.append(shape)
        later: dict[int, list[UsePosition]] = defaultdict(list)
        derived = sorted(scope.record.get("derived", ()), key=lambda r: (r[2], r[3])) if locals_ else []
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
        out: list[Edge] = []
        for index, fields in enumerate(uses):
            attrs = fields.attrs
            if index in flow_shapes:
                attrs = (*attrs, ("flow_shape", flow_shapes[index]))
            shape = shapes[index]
            out.append(
                Edge(
                    "reference",
                    fields.site,
                    fields.surface,
                    fields.targets,
                    fields.resolution,
                    fields.contexts,
                    USES_PRODUCER,
                    fields.via,
                    fields.use,
                    shape.kind,
                    shape.detail,
                    attrs,
                )
            )
        return out

    def first_party_roots(self, scope: FileScope, roots: set[str]) -> set[str]:
        """Those of `roots` a first-party import binds anywhere in the file. A name that only
        third-party imports bind cannot lead into first-party code, so its uses never reach a model."""
        out: set[str] = set()
        first_party = self.build.first_party
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

    def aggregate_edges(self, scope: FileScope) -> list[Edge]:
        """`import a.b` then `a.b.Model`: L1 only counts these, so the shape stays unknown."""
        out: list[Edge] = []
        rows = [row for row in scope.record.get("uses", ()) if row[3]]
        if not rows:
            return out
        roots = self.first_party_roots(scope, {row[2] for row in rows})
        for at_scope, _, root, attrs, count, line, column in rows:
            if root not in roots:
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
    """`<receiver>.get_model("app", "Model")` with literal arguments, matched by spelling: the
    receiver is often a parameter (`apps` in a migration), which has no origin."""

    def __init__(self, build: CoreBuild, registry: ModelRegistry, queries: PackQueries) -> None:
        self.build = build
        self.registry = registry
        self.queries = queries

    def produce(self, files: list[FileScope]) -> list[Edge]:
        calls = {scope.path: rows for scope, rows in self.queries.calls_named(files, "calls", (GET_MODEL,))}

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
        resolution = self.build.first_party.resolution(found.module)
        return Edge("model-label", site, label, (found,), resolution, contexts, GET_MODEL_PRODUCER, shape=GET_MODEL)
