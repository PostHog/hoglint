"""A tiny Django project as hand-written L1 fact records, enough for the pack's producers."""

from __future__ import annotations

from pathlib import Path

from hoglint.model import Graph
from hoglint.packs.django.config import DjangoPackConfig
from hoglint.packs.django.pack import DjangoPack
from hoglint.spike.core import Core, FactStore

SITE = ".venv/lib/python3.14/site-packages/"
FIRST_PARTY = ("shop", "billing", "proj")


def import_from(module: str, name: str, scope: int = -1, flags: int = 0, line: int = 1) -> list:
    return [scope, line, 0, flags, module, 0, name, None]


def class_def(name: str, bases: list, line: int, scope: int = -1, flags: int = 0, **extra: object) -> dict:
    return {"k": "class", "n": name, "s": scope, "l": line, "c": 0, "e": line, "f": flags, "bases": bases, **extra}


def func_def(name: str, line: int, scope: int = -1, flags: int = 0, **extra: object) -> dict:
    return {"k": "def", "n": name, "s": scope, "l": line, "c": 0, "e": line, "f": flags, **extra}


def assign(target: str, value: object, line: int, scope: int = -1, flags: int = 0) -> dict:
    return {"s": scope, "l": line, "c": 0, "op": "=", "t": [target], "v": value, "f": flags}


DJANGO: dict[str, dict] = {
    f"{SITE}django/db/models/base.py": {"defs": [class_def("Model", [], 1)]},
    f"{SITE}django/db/models/fields/related.py": {
        "defs": [class_def("ForeignKey", [], 1), class_def("OneToOneField", ["ForeignKey"], 2)]
    },
    f"{SITE}django/dispatch/dispatcher.py": {"defs": [class_def("Signal", [], 1), func_def("receiver", 2)]},
    f"{SITE}django/db/models/signals.py": {
        "imports": [import_from("django.dispatch.dispatcher", "Signal")],
        "assigns": [assign("post_save", {"call": "Signal"}, 2)],
    },
    f"{SITE}django/apps/config.py": {"defs": [class_def("AppConfig", [], 1)]},
}

APPS: dict[str, dict] = {
    "proj/settings.py": {
        "assigns": [assign("INSTALLED_APPS", {"list": [{"s": "shop"}, {"s": "billing"}, {"s": "ghost"}]}, 1)]
    },
    "shop/__init__.py": {},
    "shop/apps.py": {
        "imports": [import_from("django.apps.config", "AppConfig")],
        "defs": [class_def("ShopConfig", ["AppConfig"], 2)],
        "assigns": [assign("name", {"s": "shop"}, 3, scope=0, flags=8)],
    },
    "shop/models.py": {
        "imports": [import_from("django.db.models.base", "Model")],
        "defs": [class_def("Order", ["Model"], 2)],
    },
    "billing/__init__.py": {},
    "billing/models.py": {
        "imports": [
            import_from("django.db.models.base", "Model"),
            import_from("django.db.models.fields.related", "ForeignKey"),
        ],
        "defs": [class_def("Invoice", ["Model"], 3), class_def("Meta", [], 6, scope=0, flags=8)],
        "assigns": [
            assign("order", {"call": "ForeignKey", "args": [{"s": "shop.Order"}]}, 4, scope=0, flags=8),
            assign(
                "hidden_order",
                {"call": "ForeignKey", "args": [{"s": "shop.Order"}], "kw": [["related_name", {"s": "+"}]]},
                5,
                scope=0,
                flags=8,
            ),
            assign("app_label", {"s": "legacy_billing"}, 7, scope=1, flags=8),
        ],
    },
}


class FactRepo:
    """Records keyed by path, completed with the envelope the store expects."""

    def __init__(self, *groups: dict[str, dict]) -> None:
        self.records: dict[str, dict] = {}
        for group in (DJANGO, APPS, *groups):
            for path, record in group.items():
                self.records[path] = {"path": path, "status": "ok", **record}

    def core(self) -> Core:
        store = FactStore()
        store.records = dict(self.records)
        return Core(store)

    def graph(self, hooks: tuple[str, ...] = ()) -> Graph:
        config = DjangoPackConfig(first_party=FIRST_PARTY, settings_modules=("proj.settings",), hooks=hooks)
        graph = Graph()
        DjangoPack(self.core(), config, Path(".")).produce(graph)
        return graph
