"""The `app_setup` attr of the import edges in the body of an installed app's `ready()`.

Django calls `ready()` of each installed app's AppConfig once at setup, in every process, so an
import in its body runs as surely as one at module level. The attr says so (`app_setup =
"ready"`), and a rule reads it next to the `function_local` context. A function that `ready()`
defines or hands on runs later, if ever: its imports sit in another scope and get no attr.

The `ready()` that runs is the first one along the AppConfig's MRO. One in a class outside the
repo's sources has no edges to mark.
"""

from __future__ import annotations

from hoglint.model import EntityId
from hoglint.packs.django.apps import AppRegistry
from hoglint.plugins import PackReader, Refinement

APP_SETUP_PRODUCER = "django.app-setup@1"
APP_SETUP = "app_setup"
READY = "ready"
IMPORT = "import"


class AppSetupImports:
    """Refines the import edges of each installed AppConfig's `ready()` body."""

    def __init__(self, reader: PackReader, apps: AppRegistry) -> None:
        self.reader = reader
        self.apps = apps

    def config_class(self, config: str) -> EntityId | None:
        hit, rest = self.reader.resolver.resolve_dotted(config)
        if hit is None or rest or hit.kind != "class":
            return None
        return hit.origin

    def ready_scope(self, config: EntityId) -> tuple[str, str] | None:
        """(path, scope name) of the first `ready()` along the MRO of `config`; None when none
        of the repo's classes defines it."""
        for cls in self.reader.classes.mro(config):
            found = self.reader.classes.located(cls)
            if found is None:
                continue
            record, class_idx = found
            for d in record["defs"]:
                if d["s"] == class_idx and d["n"] == READY and d["k"] != "class":
                    return record["path"], f"{cls.symbol}.{READY}"
        return None

    def ready_scopes(self) -> dict[str, set[str]]:
        """The scope names of the `ready()` bodies that run, by path."""
        out: dict[str, set[str]] = {}
        for app in self.apps.apps:
            config = self.config_class(app.config) if app.config is not None else None
            found = self.ready_scope(config) if config is not None else None
            if found is not None:
                out.setdefault(found[0], set()).add(found[1])
        return out

    def produce(self) -> list[Refinement]:
        scopes = self.ready_scopes()
        names: set[str] = set()
        for scope in self.reader.files:
            if scope.path in scopes:
                names.update(module.rpartition(".")[2] for module in self.reader.imported_modules(scope))
        out: list[Refinement] = []
        for edge in self.reader.edges_spelled(IMPORT, names) if names else ():
            if edge.site.scope in scopes.get(edge.site.path, ()):
                out.append(Refinement(edge.handle, attrs=((APP_SETUP, READY),)))
        return out
