"""INSTALLED_APPS, app labels and the module-to-app map, read statically from settings facts.

Never boots Django. An installed app without facts (a dev-only third-party app) keeps the label
Django would derive from its entry and reads as not `known`.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field

from hoglint.core.build import CoreBuild
from hoglint.core.records import sequence_items, str_literal
from hoglint.core.symbols import entity
from hoglint.model import EntityId
from hoglint.packs.django.config import DjangoPackConfig

APP_CONFIG = "django.apps.config.AppConfig"
INSTALLED_APPS = "INSTALLED_APPS"


@dataclass(frozen=True)
class InstalledApp:
    entry: str
    name: str
    label: str
    config: str | None
    known: bool


@dataclass(frozen=False)
class SettingsValues:
    """Module-level str constants and str lists of the settings modules, as one namespace."""

    strings: dict[str, str] = field(default_factory=dict)
    lists: dict[str, list[str]] = field(default_factory=dict)

    def literal(self, value: object) -> list[str] | None:
        """The str entries of a list/tuple literal, a `+` of them, or a known list name."""
        items = sequence_items(value) if isinstance(value, dict) and "set" not in value else None
        if items is not None:
            out: list[str] = []
            for elt in items:
                if isinstance(elt, dict) and "star" in elt:
                    out.extend(self.literal(elt["star"]) or [])
                    continue
                found = str_literal(elt)
                if found is not None:
                    out.append(found)
            return out
        if isinstance(value, dict) and value.get("bin") == "+":
            left, right = self.literal(value["l"]), self.literal(value["r"])
            if left is None or right is None:
                return None
            return left + right
        if isinstance(value, str) and "." not in value and value in self.lists:
            return list(self.lists[value])
        return None

    def assign(self, target: str, value: object) -> None:
        text = str_literal(value)
        if text is not None:
            self.strings[target] = text
            return
        found = self.literal(value)
        if found is not None:
            self.lists[target] = found

    def extend(self, target: str, value: object) -> None:
        found = self.literal(value)
        if found is not None:
            self.lists.setdefault(target, []).extend(found)

    def append(self, target: str, value: str) -> None:
        self.lists.setdefault(target, []).append(value)


class SettingsReader:
    """Replays assigns, `+=` and `X.append(<const>)` of each settings module in source order."""

    def __init__(self, build: CoreBuild) -> None:
        self.build = build

    def modules(self, patterns: tuple[str, ...]) -> list[str]:
        known = sorted(self.build.index.path_of)
        out: list[str] = []
        for pattern in patterns:
            matches = [m for m in known if fnmatch.fnmatchcase(m, pattern)] if "*" in pattern else [pattern]
            out.extend(m for m in matches if self.build.index.is_file_module(m) and m not in out)
        return out

    def read(self, modules: list[str]) -> SettingsValues:
        values = SettingsValues()
        for module in modules:
            self.read_record(self.build.tables.record(module) or {}, values)
        return values

    @staticmethod
    def read_record(record: dict, values: SettingsValues) -> None:
        events: list[tuple[int, int, str, str, object]] = []
        for row in record.get("assigns", ()):
            if row["s"] != -1 or "v" not in row:
                continue
            names = [t for t in row["t"] if isinstance(t, str) and "." not in t]
            if row["op"] in ("=", ":"):
                events.extend((row["l"], row["c"], "assign", name, row["v"]) for name in names)
            elif row["op"] == "+=":
                events.extend((row["l"], row["c"], "extend", name, row["v"]) for name in names)
        for row in record.get("calls", ()):
            receiver, _, attr = row[4].rpartition(".")
            if attr != "append" or not receiver or "." in receiver or receiver == "?" or len(row[5]) != 1:
                continue
            found = str_literal(row[5][0])
            if found is not None:
                events.append((row[2], row[3], "append", receiver, found))
        for _, _, kind, name, value in sorted(events, key=lambda e: (e[0], e[1])):
            if kind == "assign":
                values.assign(name, value)
            elif kind == "extend":
                values.extend(name, value)
            else:
                values.append(name, str(value))


class AppRegistry:
    def __init__(self, build: CoreBuild, config: DjangoPackConfig) -> None:
        self.build = build
        self.settings_modules = SettingsReader(build).modules(config.settings_modules)
        self.settings = SettingsReader(build).read(self.settings_modules)
        self.apps: list[InstalledApp] = [self.app_for_entry(e) for e in self.settings.lists.get(INSTALLED_APPS, [])]
        self.by_label = {app.label: app for app in self.apps}
        self.by_name_length = sorted(self.apps, key=lambda a: -len(a.name))
        self.module_labels: dict[str, str | None] = {}

    def setting(self, name: str) -> str | None:
        return self.settings.strings.get(name)

    def config_attrs(self, ref: EntityId) -> dict[str, object]:
        attrs: dict[str, object] = {}
        for cls in reversed(self.build.classes.mro(ref)):
            found = self.build.classes.located(cls)
            if found is not None:
                attrs.update(self.build.bodies(found[0]).constants(found[1]))
        return attrs

    def is_app_config(self, ref: EntityId) -> bool:
        return str(ref) != APP_CONFIG and self.build.classes.is_subclass(ref, APP_CONFIG)

    def default_config(self, entry: str) -> EntityId | None:
        """Django's choice of AppConfig in `<entry>.apps`: the only one, else the only default=True."""
        table = self.build.tables.get(f"{entry}.apps")
        if table is None:
            return None
        candidates = []
        for name in table.classes():
            ref = entity(table.name, name)
            if self.is_app_config(ref) and self.config_attrs(ref).get("default") is not False:
                candidates.append(ref)
        if len(candidates) == 1:
            return candidates[0]
        defaults = [c for c in candidates if self.config_attrs(c).get("default") is True]
        return defaults[0] if len(defaults) == 1 else None

    def app_for_entry(self, entry: str) -> InstalledApp:
        hit, rest = self.build.resolver.resolve_dotted(entry)
        found = hit if hit is not None and not rest else None
        config: EntityId | None = None
        if found is not None and found.kind == "class" and self.is_app_config(found.origin):
            config = found.origin
        elif found is not None and found.kind == "module":
            config = self.default_config(entry)
        if config is None:
            return InstalledApp(entry, entry, entry.rpartition(".")[2], None, found is not None)
        attrs = self.config_attrs(config)
        name = str(attrs.get("name", entry))
        label = str(attrs.get("label") or name.rpartition(".")[2])
        return InstalledApp(entry, name, label, str(config), True)

    def app_for_module(self, module: str) -> InstalledApp | None:
        for app in self.by_name_length:
            if module == app.name or module.startswith(app.name + "."):
                return app
        return None

    def label_for_module(self, module: str) -> str | None:
        if module not in self.module_labels:
            app = self.app_for_module(module)
            self.module_labels[module] = app.label if app is not None else None
        return self.module_labels[module]
