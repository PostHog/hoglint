"""A tiny Django project as hand-written L1 fact records, enough for the pack's producers."""

from __future__ import annotations

from pathlib import Path

from core.support import SITE, Repo, assign_row, class_def, func_def, import_row
from hoglint.core.config import CoreConfig
from hoglint.model import Graph
from hoglint.packs.django.config import DatabaseRouting, DjangoPackConfig
from hoglint.packs.django.pack import DjangoPack

FIRST_PARTY = ("shop", "billing", "proj")

DJANGO: dict[str, dict] = {
    f"{SITE}django/db/models/base.py": {"defs": [class_def("Model", [], 1)]},
    f"{SITE}django/db/models/fields/related.py": {
        "defs": [
            class_def("ForeignKey", [], 1),
            class_def("OneToOneField", ["ForeignKey"], 2),
            class_def("ManyToManyField", [], 3),
        ]
    },
    f"{SITE}django/dispatch/dispatcher.py": {"defs": [class_def("Signal", [], 1), func_def("receiver", 2)]},
    f"{SITE}django/db/models/signals.py": {
        "imports": [import_row("django.dispatch.dispatcher", "Signal")],
        "assigns": [assign_row("post_save", {"call": "Signal"}, 2), assign_row("post_delete", {"call": "Signal"}, 3)],
    },
    f"{SITE}django/contrib/auth/models.py": {
        "imports": [import_row("django.db.models.base", "Model")],
        "defs": [class_def("Group", ["Model"], 1)],
    },
    f"{SITE}django/apps/config.py": {"defs": [class_def("AppConfig", [], 1)]},
}

APPS: dict[str, dict] = {
    "proj/settings.py": {
        "assigns": [assign_row("INSTALLED_APPS", {"list": [{"s": "shop"}, {"s": "billing"}, {"s": "ghost"}]}, 1)]
    },
    "shop/__init__.py": {},
    "shop/apps.py": {
        "imports": [import_row("django.apps.config", "AppConfig")],
        "defs": [class_def("ShopConfig", ["AppConfig"], 2)],
        "assigns": [assign_row("name", {"s": "shop"}, 3, scope=0, flags=8)],
    },
    "shop/models.py": {
        "imports": [import_row("django.db.models.base", "Model")],
        "defs": [class_def("Order", ["Model"], 2)],
    },
    "billing/__init__.py": {},
    "billing/models.py": {
        "imports": [
            import_row("django.db.models.base", "Model"),
            import_row("django.db.models.fields.related", "ForeignKey"),
        ],
        "defs": [class_def("Invoice", ["Model"], 3), class_def("Meta", [], 6, scope=0, flags=8)],
        "assigns": [
            assign_row("order", {"call": "ForeignKey", "args": [{"s": "shop.Order"}]}, 4, scope=0, flags=8),
            assign_row(
                "hidden_order",
                {"call": "ForeignKey", "args": [{"s": "shop.Order"}], "kw": [["related_name", {"s": "+"}]]},
                5,
                scope=0,
                flags=8,
            ),
            assign_row("app_label", {"s": "legacy_billing"}, 7, scope=1, flags=8),
        ],
    },
}


class FactRepo(Repo):
    """The Django fixture plus a test's own files: core edges first, then the pack."""

    def __init__(self, *groups: dict[str, dict]) -> None:
        super().__init__(DJANGO, APPS, *groups)

    def graph(
        self, hooks: tuple[str, ...] = (), routing: DatabaseRouting | None = None, scan_roots: tuple[str, ...] = ()
    ) -> Graph:
        build = self.build(CoreConfig(first_party=FIRST_PARTY))
        graph = build.graph()
        config = DjangoPackConfig(
            settings_modules=("proj.settings",), routing=routing, hooks=hooks, scan_roots=scan_roots
        )
        DjangoPack.run(build, config, graph, Path("."))
        return graph
