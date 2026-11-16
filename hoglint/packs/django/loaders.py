"""`loads` edges: modules Django imports at setup, which no call site in the repo shows.

Django imports every installed app, its AppConfig module and its `models` module at setup, and
the admin app imports each app's `admin` module. What an `admin` module imports at module level
runs with it: those import edges get the `autodiscovered` attr (`autodiscovered = "admin"`).
"""

from __future__ import annotations

from hoglint.core.sites import MODULE_SCOPE
from hoglint.model import LANGUAGE_PYTHON, Context, Edge, EntityId, Resolution, Site
from hoglint.packs.django.apps import INSTALLED_APPS, AppRegistry, InstalledApp
from hoglint.plugins import PackReader, Refinement

LOADS_PRODUCER = "django.loaders@1"
AUTODISCOVERED_PRODUCER = "django.autodiscovered@1"
AUTODISCOVERED = "autodiscovered"
ADMIN = "admin"
IMPORT = "import"
POPULATE = "django.apps.registry.Apps.populate"
AUTODISCOVER = "django.utils.module_loading.autodiscover_modules"
ADMIN_APP = "django.contrib.admin"
PROD = frozenset({Context.PROD})


class AppLoads:
    def __init__(self, reader: PackReader, apps: AppRegistry) -> None:
        self.reader = reader
        self.apps = apps

    def settings_site(self) -> Site:
        """Where INSTALLED_APPS is first assigned; the edges start there."""
        for module in self.apps.settings_modules:
            record = self.reader.tables.record(module) or {}
            for row in record.get("assigns", ()):
                if row["s"] == -1 and INSTALLED_APPS in row["t"]:
                    return Site(record["path"], module, "<module>", row["l"], row["c"])
        return Site("", "", "<module>", 0)

    def edge(self, site: Site, app: InstalledApp, module: str, role: str, loader: str) -> Edge:
        exists = self.reader.index.exists(module)
        resolution = self.reader.first_party.resolution(app.name) if exists else Resolution.UNRESOLVED
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
            for role, loader, wanted in (("models", POPULATE, True), (ADMIN, AUTODISCOVER, admin)):
                module = f"{app.name}.{role}"
                if wanted and self.reader.index.exists(module):
                    out.append(self.edge(site, app, module, role, loader))
        return out


class AdminImports:
    """Refines the module-level import edges of each installed app's `admin` module, which the
    admin app imports by name. An import inside a function of it runs later, if ever."""

    def __init__(self, reader: PackReader, apps: AppRegistry) -> None:
        self.reader = reader
        self.apps = apps

    def admin_modules(self) -> set[str]:
        if not any(app.name == ADMIN_APP for app in self.apps.apps):
            return set()
        return {f"{app.name}.{ADMIN}" for app in self.apps.apps}

    def produce(self) -> list[Refinement]:
        modules = self.admin_modules()
        paths: set[str] = set()
        names: set[str] = set()
        for scope in self.reader.files:
            if scope.module in modules:
                paths.add(scope.path)
                names.update(module.rpartition(".")[2] for module in self.reader.imported_modules(scope))
        out: list[Refinement] = []
        for edge in self.reader.edges_spelled(IMPORT, names) if names else ():
            if edge.site.path in paths and edge.site.scope == MODULE_SCOPE:
                out.append(Refinement(edge.handle, attrs=((AUTODISCOVERED, ADMIN),)))
        return out
