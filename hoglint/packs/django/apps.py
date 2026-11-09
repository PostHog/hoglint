"""INSTALLED_APPS, app labels and the module-to-app map, read statically from settings facts.

Never boots Django. An installed app without facts (a dev-only third-party app) is a known gap:
it keeps the label Django would derive from its entry and is listed in `gaps`.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field

from hoglint.packs.django.config import DjangoPackConfig
from hoglint.spike.core import CLASS, NO_CONST, ClassRef, Core, ModuleRef, const_of, is_const

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
        if isinstance(value, dict) and ("list" in value or "tuple" in value):
            out: list[str] = []
            for elt in value.get("list", value.get("tuple")):
                if isinstance(elt, dict) and "star" in elt:
                    out.extend(self.literal(elt["star"]) or [])
                    continue
                found = const_of(elt)
                if isinstance(found, str):
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
        if isinstance(value, dict) and "s" in value and len(value) == 1:
            self.strings[target] = value["s"]
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

    def __init__(self, core: Core) -> None:
        self.core = core

    def modules(self, patterns: tuple[str, ...]) -> list[str]:
        known = sorted(self.core.index.path_of)
        out: list[str] = []
        for pattern in patterns:
            matches = [m for m in known if fnmatch.fnmatchcase(m, pattern)] if "*" in pattern else [pattern]
            out.extend(m for m in matches if m in self.core.index.path_of and m not in out)
        return out

    def read(self, patterns: tuple[str, ...]) -> SettingsValues:
        values = SettingsValues()
        for module in self.modules(patterns):
            self.read_record(self.core.index.record(module) or {}, values)
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
            found = const_of(row[5][0])
            if isinstance(found, str):
                events.append((row[2], row[3], "append", receiver, found))
        for _, _, kind, name, value in sorted(events, key=lambda e: (e[0], e[1])):
            if kind == "assign":
                values.assign(name, value)
            elif kind == "extend":
                values.extend(name, value)
            else:
                values.append(name, str(value))


def class_constants(record: dict, idx: int) -> dict[str, object]:
    """`name = <Constant>` and `name: T = <Constant>` directly in a class body."""
    flags = record["defs"][idx].get("f", 0) | CLASS
    out: dict[str, object] = {}
    for row in record.get("assigns", ()):
        if row["s"] != idx or row.get("f", 0) != flags or row["op"] not in ("=", ":") or "v" not in row:
            continue
        if not is_const(row["v"]):
            continue
        value = const_of(row["v"])
        if value is NO_CONST:
            continue
        for target in row["t"]:
            if isinstance(target, str) and "." not in target:
                out[target] = value
    return out


class AppRegistry:
    def __init__(self, core: Core, config: DjangoPackConfig) -> None:
        self.core = core
        self.settings = SettingsReader(core).read(config.settings_modules)
        self.apps: list[InstalledApp] = [self.app_for_entry(e) for e in self.settings.lists.get(INSTALLED_APPS, [])]
        self.gaps = [app.entry for app in self.apps if not app.known]
        self.by_label = {app.label: app for app in self.apps}
        self.by_name_length = sorted(self.apps, key=lambda a: -len(a.name))

    def setting(self, name: str) -> str | None:
        return self.settings.strings.get(name)

    def config_attrs(self, ref: ClassRef) -> dict[str, object]:
        attrs: dict[str, object] = {}
        for cls in reversed(self.core.classes.mro(ref)):
            record = self.core.index.record(cls.module)
            if record is None:
                continue
            attrs.update(class_constants(record, self.core.classes.def_index(cls)))
        return attrs

    def is_app_config(self, ref: ClassRef) -> bool:
        return ref.id != APP_CONFIG and self.core.classes.is_subclass(ref, APP_CONFIG)

    def default_config(self, entry: str) -> ClassRef | None:
        """Django's choice of AppConfig in `<entry>.apps`: the only one, else the only default=True."""
        table = self.core.tables.get(f"{entry}.apps")
        if table is None:
            return None
        candidates = []
        for name in table.classes:
            ref = ClassRef(table.name, name)
            if self.is_app_config(ref) and self.config_attrs(ref).get("default") is not False:
                candidates.append(ref)
        if len(candidates) == 1:
            return candidates[0]
        defaults = [c for c in candidates if self.config_attrs(c).get("default") is True]
        return defaults[0] if len(defaults) == 1 else None

    def app_for_entry(self, entry: str) -> InstalledApp:
        found = self.core.resolver.resolve_dotted(entry)
        config: ClassRef | None = None
        if isinstance(found, ClassRef) and self.is_app_config(found):
            config = found
        elif isinstance(found, ModuleRef):
            config = self.default_config(entry)
        known = found is not None
        if config is None:
            return InstalledApp(entry, entry, entry.rpartition(".")[2], None, known)
        attrs = self.config_attrs(config)
        name = str(attrs.get("name", entry))
        label = str(attrs.get("label") or name.rpartition(".")[2])
        return InstalledApp(entry, name, label, config.id, True)

    def app_for_module(self, module: str) -> InstalledApp | None:
        for app in self.by_name_length:
            if module == app.name or module.startswith(app.name + "."):
                return app
        return None

    def label_for_module(self, module: str) -> str | None:
        app = self.app_for_module(module)
        return app.label if app is not None else None
