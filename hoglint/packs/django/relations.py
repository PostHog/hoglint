"""`relation`, `reverse-accessor` and FK `model-label` edges, plus model and app symbol facts."""

from __future__ import annotations

from hoglint.core.harness import each_item, failure_edge
from hoglint.model import LANGUAGE_PYTHON, Context, Edge, EntityId, Resolution, Site, flag
from hoglint.packs.django.apps import AppRegistry
from hoglint.packs.django.models import ModelEntry, ModelRegistry, Relation, RelationBuilder

RELATIONS_PRODUCER = "django.relations@1"
PROD = frozenset({Context.PROD})


class ModelFacts:
    """Symbol facts for models and installed apps."""

    def __init__(self, registry: ModelRegistry, apps: AppRegistry) -> None:
        self.registry = registry
        self.apps = apps

    def symbols(self, databases: dict[str, frozenset[str]]) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        for ref, entry in self.registry.models.items():
            out[str(ref)] = {
                "kind": "model",
                "app_label": entry.app_label,
                "label": entry.label,
                "abstract": flag(entry.abstract),
                "proxy": flag(entry.proxy),
                "concrete": str(entry.concrete),
                "path": entry.path,
            }
        for app in self.apps.apps:
            facts = {
                "kind": "django-app",
                "label": app.label,
                "entry": app.entry,
                "known": flag(app.known),
            }
            if app.config is not None:
                facts["config"] = app.config
            if app.label in databases:
                facts["databases"] = ",".join(sorted(databases[app.label]))
            out.setdefault(app.name, facts)
        return out


class RelationEdges:
    def __init__(self, registry: ModelRegistry, builder: RelationBuilder, module_path: dict[str, str]) -> None:
        self.registry = registry
        self.builder = builder
        self.module_path = module_path

    def target(self, relation: Relation) -> tuple[tuple[EntityId, ...], Resolution]:
        if not relation.resolved:
            return (), Resolution.UNRESOLVED
        name = relation.concrete_target.rpartition(".")[2]
        entity = EntityId(LANGUAGE_PYTHON, relation.target_module, name)
        return (entity,), self.registry.reader.first_party.resolution(relation.target_module)

    @staticmethod
    def site(relation: Relation) -> Site:
        module, _, name = relation.model.rpartition(".")
        own_line = relation.declared_in == module and relation.declared_line
        return Site(relation.model_path, module, name, relation.declared_line if own_line else relation.model_line)

    def relation_edge(self, relation: Relation) -> Edge:
        targets, resolution = self.target(relation)
        return Edge(
            kind="relation",
            site=self.site(relation),
            surface=relation.target_surface,
            targets=targets,
            resolution=resolution,
            contexts=PROD,
            producer=RELATIONS_PRODUCER,
            shape=relation.kind,
            attrs=relation.attrs(),
        )

    def accessor_edge(self, relation: Relation) -> Edge | None:
        """The accessor a relation adds to its target. Inherited parent fields add none of their own."""
        if relation.from_concrete_parent or relation.sealed:
            return None
        if relation.accessor_name is None and not relation.hidden:
            return None
        accessor = f"query:{relation.query_name}" if relation.hidden else str(relation.accessor_name)
        targets, resolution = self.target(relation)
        return Edge(
            kind="reverse-accessor",
            site=self.site(relation),
            surface=accessor,
            targets=targets,
            resolution=resolution,
            contexts=PROD,
            producer=RELATIONS_PRODUCER,
            shape=relation.kind,
            shape_detail=accessor,
            attrs=(
                ("model", relation.model),
                ("model_label", relation.model_label),
                ("field", relation.field),
                ("declaring_model", relation.declaring_model),
                ("target_label", relation.target_label or ""),
                ("accessor", accessor),
            ),
        )

    def label_edges(self) -> list[Edge]:
        out = []
        for ref in self.builder.label_refs.values():
            if ref.target is None:
                targets: tuple[EntityId, ...] = ()
                resolution = Resolution.UNRESOLVED
            else:
                targets = (ref.target,)
                resolution = self.registry.reader.first_party.resolution(ref.target.module)
            site = Site(self.module_path.get(ref.module, ""), ref.module, ref.owner, ref.line)
            out.append(
                Edge(
                    kind="model-label",
                    site=site,
                    surface=ref.surface,
                    targets=targets,
                    resolution=resolution,
                    contexts=PROD,
                    producer=RELATIONS_PRODUCER,
                    shape="fk-target",
                    attrs=(("field", ref.field),),
                )
            )
        return out

    def model_edges(self, entry: ModelEntry) -> list[Edge]:
        out: list[Edge] = []
        for relation in self.builder.model_relations(entry):
            out.append(self.relation_edge(relation))
            accessor = self.accessor_edge(relation)
            if accessor is not None:
                out.append(accessor)
        return out

    @staticmethod
    def failure(entry: ModelEntry, error: Exception) -> Edge:
        site = Site(entry.path, entry.ref.module, entry.ref.symbol, entry.line)
        return failure_edge("relation", RELATIONS_PRODUCER, site, PROD, error)

    def produce(self) -> list[Edge]:
        out = each_item(self.builder.concrete_entries(), self.model_edges, self.failure)
        return out + self.label_edges()
