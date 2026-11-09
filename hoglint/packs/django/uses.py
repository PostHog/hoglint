"""Reference edges onto model classes with their ORM/DRF shape, and `apps.get_model` label edges.

Until core emits reference edges, this producer walks the spike's code path: import-bound
names in `refs` rows, resolved through re-exports to a model class. The shape logic sits in
`OrmShapes`, which takes only a use position, so it can read core's edges later unchanged.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace

from hoglint.model import LANGUAGE_PYTHON, Edge, EntityId, Resolution
from hoglint.packs.django.models import ModelRegistry
from hoglint.packs.django.shapes import OrmShapes, Shape, UsePosition, split_chain
from hoglint.packs.django.sites import PathKinds, failure_edge, file_site, make_site
from hoglint.packs.django.symbols import FileBindings, Symbol, SymbolKind, SymbolResolver
from hoglint.spike.core import ClassRef, Core, module_of_path

REFERENCE_PRODUCER = "django.uses@1"
MODEL_LABEL_PRODUCER = "django.model-labels@1"
NOT_A_REGISTRY = frozenset({"self", "cls"})


@dataclass(frozen=True)
class Hit:
    symbol: Symbol
    surface: str
    cut: int
    flags: int


class ModelUses:
    """Resolves roots and alias chains of one file to model classes."""

    def __init__(self, core: Core, symbols: SymbolResolver, registry: ModelRegistry) -> None:
        self.core = core
        self.symbols = symbols
        self.registry = registry
        self.member_cache: dict[tuple[str, str], Symbol | None] = {}

    def is_model(self, symbol: Symbol) -> bool:
        return symbol.kind == SymbolKind.CLASS and ClassRef(symbol.module, symbol.name) in self.registry.models

    def member(self, module: str, name: str) -> Symbol | None:
        key = (module, name)
        if key not in self.member_cache:
            hop = EntityId(LANGUAGE_PYTHON, module, name)
            self.member_cache[key] = self.symbols.resolve_name(module, name, (hop,))
        return self.member_cache[key]

    def hits(self, bindings: FileBindings, root: str, trailers: tuple[str, ...]) -> list[Hit]:
        binding = bindings.names.get(root)
        if binding is None:
            return []
        found = self.symbols.resolve_binding(binding)
        if found is None:
            return []
        if self.is_model(found):
            return [Hit(found, self.symbols.surface_and_via(found)[0], 0, binding.flags)]
        if found.kind != SymbolKind.MODULE:
            return []
        module = found.module
        for i, trailer in enumerate(trailers):
            if not trailer.startswith("."):
                return []
            member = self.member(module, trailer[1:])
            if member is None:
                return []
            if self.is_model(member):
                return [Hit(member, f"{module}.{trailer[1:]}", i + 1, binding.flags)]
            if member.kind != SymbolKind.MODULE:
                return []
            module = member.module
        return []


class LocalSources:
    """Which model reference a local holds at a point in its scope. A reassignment starts a new source."""

    def __init__(self) -> None:
        self.assigned: dict[tuple[int, str], list[tuple[int, int, int]]] = defaultdict(list)

    def assign(self, scope: int, local: str, line: int, column: int, index: int) -> None:
        self.assigned[(scope, local)].append((line, column, index))

    def held(self, scope: int, local: str, line: int, column: int) -> int | None:
        """The last assignment before the use. A use above every assignment (a loop) takes the first one."""
        rows = sorted(self.assigned.get((scope, local), ()))
        if not rows:
            return None
        before = [index for at_line, at_column, index in rows if (at_line, at_column) < (line, column)]
        return before[-1] if before else rows[0][2]


class UseProducer:
    """`reference` edges onto first-party models, each with a Django shape."""

    def __init__(self, core: Core, symbols: SymbolResolver, registry: ModelRegistry) -> None:
        self.core = core
        self.registry = registry
        self.symbols = symbols
        self.uses = ModelUses(core, symbols, registry)
        self.shapes = OrmShapes()

    def produce(self, files: list[tuple[str, dict]]) -> list[Edge]:
        edges: list[Edge] = []
        for path, record in files:
            module, is_package = module_of_path(path)
            if "refs" not in record and "uses" not in record:
                continue
            try:
                bindings = FileBindings(record, module, is_package)
                edges.extend(self.file_edges(path, record, module, bindings))
            except Exception as error:  # one unreadable file is unknown, the other files still count
                edges.append(failure_edge("reference", REFERENCE_PRODUCER, file_site(path), error))
        return edges

    def file_edges(self, path: str, record: dict, module: str, bindings: FileBindings) -> list[Edge]:
        out: list[Edge] = []
        sources = LocalSources()
        positions: dict[int, UsePosition] = {}
        later: dict[int, list[UsePosition]] = defaultdict(list)
        derived: list[list] = []
        for row in record.get("refs", ()):
            if len(row) > 7:
                derived.append(row)
                continue
            scope, _, line, column, root, chain, up = row
            trailers = split_chain(chain)
            for hit in self.uses.hits(bindings, root, trailers):
                position = UsePosition(trailers[hit.cut :], tuple(up.split()))
                shape = self.shapes.classify(position)
                local = position.local_target()
                if local is not None:
                    sources.assign(scope, local, line, column, len(out))
                positions[len(out)] = position
                out.append(self.edge(path, record, module, scope, line, column, hit, shape))
        for row in sorted(derived, key=lambda r: (r[2], r[3])):
            scope, _, line, column, root, chain, up, origin = row
            index = sources.held(origin, root, line, column)
            if index is None:
                continue
            position = UsePosition(split_chain(chain), tuple(up.split()))
            later[index].append(position)
            local = position.local_target()
            if local is not None:
                sources.assign(scope, local, line, column, index)
        for index, uses in later.items():
            refined = self.shapes.refine(positions[index], uses)
            current = Shape(out[index].shape, out[index].shape_detail)
            if refined is not None and refined != current:
                out[index] = self.with_flow_shape(out[index], refined)
        out.extend(self.aggregate_edges(path, record, module, bindings))
        return out

    def edge(
        self, path: str, record: dict, module: str, scope: int, line: int, column: int, hit: Hit, shape: Shape
    ) -> Edge:
        _, via = self.symbols.surface_and_via(hit.symbol)
        return Edge(
            kind="reference",
            site=make_site(record, module, scope, line, column),
            surface=hit.surface,
            targets=(hit.symbol.entity,),
            resolution=Resolution.RESOLVED,
            contexts=PathKinds.contexts(path, hit.flags),
            producer=REFERENCE_PRODUCER,
            via=via,
            use="name",
            shape=shape.kind,
            shape_detail=shape.detail,
        )

    @staticmethod
    def with_flow_shape(edge: Edge, refined: Shape) -> Edge:
        return replace(edge, attrs=(*edge.attrs, ("flow_shape", refined.text())))

    def aggregate_edges(self, path: str, record: dict, module: str, bindings: FileBindings) -> list[Edge]:
        """`import a.b` then `a.b.Model`: L1 only counts these, so the shape stays unknown."""
        out = []
        for scope, _, root, attrs, count, line, column in record.get("uses", ()):
            for hit in self.uses.hits(bindings, root, split_chain(attrs)):
                if hit.cut == 0:
                    continue
                edge = self.edge(path, record, module, scope, line, column, hit, Shape(""))
                out.append(replace(edge, use="aggregate", shape="", attrs=(("count", str(count)),)))
        return out


class GetModelProducer:
    """`apps.get_model("app", "Model")` and `get_model("app.Model")` with literal arguments."""

    def __init__(self, core: Core, registry: ModelRegistry) -> None:
        self.core = core
        self.registry = registry

    def produce(self, files: list[tuple[str, dict]]) -> list[Edge]:
        out = []
        for path, record in files:
            try:
                out.extend(self.file_edges(path, record))
            except Exception as error:  # one unreadable file is unknown, the other files still count
                out.append(failure_edge("model-label", MODEL_LABEL_PRODUCER, file_site(path), error))
        return out

    def file_edges(self, path: str, record: dict) -> list[Edge]:
        module, _ = module_of_path(path)
        out = []
        for row in record.get("calls", ()):
            callee = row[4]
            if "." not in callee or callee.rsplit(".", 1)[-1] != "get_model":
                continue
            out.append(self.edge(path, record, module, row))
        return out

    @staticmethod
    def label_of(args: list, kws: list) -> tuple[str | None, str | None]:
        literal = [a["s"] for a in args if isinstance(a, dict) and "s" in a and len(a) == 1]
        kw = {k: v["s"] for k, v in kws if k in ("app_label", "model_name") and isinstance(v, dict) and "s" in v}
        app_label = kw.get("app_label", literal[0] if literal else None)
        class_name = kw.get("model_name", literal[1] if len(literal) > 1 else None)
        if app_label is not None and class_name is None and "." in app_label:
            app_label, class_name = app_label.split(".", 1)
        return app_label, class_name

    def edge(self, path: str, record: dict, module: str, row: list) -> Edge:
        scope, _, line, column, callee, args, kws = row
        app_label, class_name = self.label_of(args, kws)
        site = make_site(record, module, scope, line, column)
        contexts = PathKinds.contexts(path)
        if app_label is None or class_name is None:
            receiver = callee.split(".", 1)[0]
            resolution = Resolution.NOT_APPLICABLE if receiver in NOT_A_REGISTRY else Resolution.UNRESOLVED
            return Edge("model-label", site, callee, (), resolution, contexts, MODEL_LABEL_PRODUCER, shape="get_model")
        label = f"{app_label}.{class_name}"
        found = self.registry.resolve_label(label, app_label, None)
        if found is None:
            return Edge(
                "model-label", site, label, (), Resolution.UNRESOLVED, contexts, MODEL_LABEL_PRODUCER, shape="get_model"
            )
        resolution = Resolution.RESOLVED if self.registry.is_first_party(found.module) else Resolution.EXTERNAL
        target = EntityId(LANGUAGE_PYTHON, found.module, found.name)
        return Edge(
            "model-label", site, label, (target,), resolution, contexts, MODEL_LABEL_PRODUCER, shape="get_model"
        )
