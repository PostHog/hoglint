"""`loads` edges: modules Django imports at setup, which no call site in the repo shows.

Django imports every installed app, its AppConfig module and its `models` module at setup, and
the admin app imports each app's `admin` module.
"""

from __future__ import annotations

from hoglint.core.build import CoreBuild
from hoglint.model import LANGUAGE_PYTHON, Context, Edge, EntityId, Resolution, Site
from hoglint.packs.django.apps import INSTALLED_APPS, AppRegistry, InstalledApp

LOADS_PRODUCER = "django.loaders@1"
POPULATE = "django.apps.registry.Apps.populate"
AUTODISCOVER = "django.utils.module_loading.autodiscover_modules"
ADMIN_APP = "django.contrib.admin"
PROD = frozenset({Context.PROD})


class AppLoads:
    def __init__(self, build: CoreBuild, apps: AppRegistry) -> None:
        self.build = build
        self.apps = apps

    def settings_site(self) -> Site:
        """Where INSTALLED_APPS is first assigned; the edges start there."""
        for module in self.apps.settings_modules:
            record = self.build.tables.record(module) or {}
            for row in record.get("assigns", ()):
                if row["s"] == -1 and INSTALLED_APPS in row["t"]:
                    return Site(record["path"], module, "<module>", row["l"], row["c"])
        return Site("", "", "<module>", 0)

    def edge(self, site: Site, app: InstalledApp, module: str, role: str, loader: str) -> Edge:
        exists = self.build.index.exists(module)
        resolution = self.build.first_party.resolution(app.name) if exists else Resolution.UNRESOLVED
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
                if wanted and self.build.index.exists(module):
                    out.append(self.edge(site, app, module, role, loader))
        return out
