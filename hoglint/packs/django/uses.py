"""The names of the model-use producers, and `apps.get_model` label edges. The uses themselves
are judged in the link (native_uses.py)."""

from __future__ import annotations

from collections.abc import Sequence

from hoglint.core.harness import FileItems, failure_edge
from hoglint.core.memo import NOTHING, ItemOutput
from hoglint.core.records import str_literal
from hoglint.core.sites import FileScope
from hoglint.model import NO_LITERAL, Edge, Resolution
from hoglint.packs.django.models import ModelRegistry
from hoglint.plugins import PackReader

USES_PRODUCER = "django.uses@1"
GET_MODEL_PRODUCER = "django.get-model@1"
CORE_REFERENCE_PRODUCER = "core-reference@1"
GET_MODEL = "get_model"
# Django's app registry, by origin.
APP_REGISTRIES = frozenset({"django.apps.apps", "django.apps.registry.apps"})
# The parameter a migration function takes the historical app registry by.
REGISTRY_PARAMETER = "apps"


USE_COLUMNS = ("producer", "path", "scope", "line", "column", "target", "attr:chain", "attr:up")


class GetModelProducer:
    """`<registry>.get_model("app", "Model")` on Django's app registry: the `apps` object of
    `django.apps`, or the `apps` argument of a migration function, which has no origin. A method of
    any other receiver that is named `get_model` names no model."""

    def __init__(self, reader: PackReader, registry: ModelRegistry) -> None:
        self.reader = reader
        self.registry = registry

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
                "model-label",
                site,
                callee,
                (),
                Resolution.UNRESOLVED,
                contexts,
                GET_MODEL_PRODUCER,
                shape=GET_MODEL,
                attrs=(NO_LITERAL,),
            )
        label = f"{app_label}.{class_name}"
        found = self.registry.resolve_label(label, app_label, None)
        if found is None:
            return Edge(
                "model-label", site, label, (), Resolution.UNRESOLVED, contexts, GET_MODEL_PRODUCER, shape=GET_MODEL
            )
        resolution = self.reader.first_party.resolution(found.module)
        return Edge("model-label", site, label, (found,), resolution, contexts, GET_MODEL_PRODUCER, shape=GET_MODEL)


class GetModelItems(FileItems):
    """`django.get-model` as one item per file: its `get_model` calls, read for the files that
    run in one query."""

    def __init__(self, producer: GetModelProducer, files: list[FileScope]) -> None:
        super().__init__(producer.reader, files, producer.failure)
        self.producer = producer
        self.calls: dict[str, list[list]] = {}

    def prepare(self, keys: Sequence[str]) -> None:
        found = self.reader.calls_named(self.scopes(keys), "calls", (GET_MODEL,))
        self.calls = {scope.path: rows for scope, rows in found}

    def file_item(self, scope: FileScope) -> ItemOutput:
        rows = self.calls.get(scope.path)
        if not rows:
            return NOTHING

        def file_edges(scope: FileScope) -> list[Edge]:
            return [self.producer.edge(scope, row) for row in rows if self.producer.on_registry(scope, row)]

        return self.each_file(scope, file_edges)
