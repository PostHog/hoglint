"""Django's own dynamic loaders, declared so a computed import inside them is not an unknown.

`DJANGO_LOADERS` lists the loader functions and what their arguments name; core's dynamic-import
fact reads it. App loading has no call site in the repo, so this module also emits its `loads`
edges: Django imports every installed app, its AppConfig module and its `models` module at
setup, and the admin app imports each app's `admin` module.
"""

from __future__ import annotations

from dataclasses import dataclass

from hoglint.model import LANGUAGE_PYTHON, Context, Edge, EntityId, Resolution, Site
from hoglint.packs.django.apps import INSTALLED_APPS, AppRegistry, InstalledApp
from hoglint.spike.core import Core

LOADS_PRODUCER = "django.loaders@1"
POPULATE = "django.apps.registry.Apps.populate"
AUTODISCOVER = "django.utils.module_loading.autodiscover_modules"
ADMIN_APP = "django.contrib.admin"
PROD = frozenset({Context.PROD})


@dataclass(frozen=True)
class DeclaredLoader:
    function: str
    argument: str  # what the literal argument names: model-label | dotted-path | module-per-app
    edge_kind: str


DJANGO_LOADERS = (
    DeclaredLoader("django.apps.registry.Apps.get_model", "model-label", "model-label"),
    DeclaredLoader("django.utils.module_loading.import_string", "dotted-path", "dotted-path"),
    DeclaredLoader(AUTODISCOVER, "module-per-app", "loads"),
    DeclaredLoader("django.db.migrations.loader.MigrationLoader.load_disk", "module-per-app", "loads"),
)


class AppLoads:
    def __init__(
        self, core: Core, apps: AppRegistry, settings_modules: list[str], first_party: tuple[str, ...]
    ) -> None:
        self.core = core
        self.apps = apps
        self.settings_modules = settings_modules
        self.first_party = first_party

    def settings_site(self) -> Site:
        """Where INSTALLED_APPS is first assigned; the edges start there."""
        for module in self.settings_modules:
            record = self.core.index.record(module) or {}
            for row in record.get("assigns", ()):
                if row["s"] == -1 and INSTALLED_APPS in row["t"]:
                    return Site(record["path"], module, "<module>", row["l"], row["c"])
        return Site("", "", "<module>", 0)

    def edge(self, site: Site, app: InstalledApp, module: str, role: str, loader: str) -> Edge:
        exists = self.core.index.exists(module)
        first_party = app.name.split(".", 1)[0] in self.first_party
        if not exists:
            resolution = Resolution.UNRESOLVED
        else:
            resolution = Resolution.RESOLVED if first_party else Resolution.EXTERNAL
        targets = (EntityId(LANGUAGE_PYTHON, module),) if exists else ()
        return Edge(
            kind="loads",
            site=site,
            surface=app.entry if role == "app" else module,
            targets=targets,
            resolution=resolution,
            contexts=PROD,
            producer=LOADS_PRODUCER,
            shape=role,
            attrs=(("loader", loader), ("app", app.label)),
        )

    def produce(self) -> list[Edge]:
        site = self.settings_site()
        admin = any(app.name == ADMIN_APP for app in self.apps.apps)
        out: list[Edge] = []
        for app in self.apps.apps:
            out.append(self.edge(site, app, app.name, "app", POPULATE))
            if app.config is not None:
                out.append(self.edge(site, app, app.config.rpartition(".")[0], "config", POPULATE))
            for role, loader, wanted in (("models", POPULATE, True), ("admin", AUTODISCOVER, admin)):
                module = f"{app.name}.{role}"
                if wanted and self.core.index.exists(module):
                    out.append(self.edge(site, app, module, role, loader))
        return out
