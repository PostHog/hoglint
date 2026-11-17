"""Settings of the Django pack, read from a repo's `[django]` table. The pack names no repo."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hoglint.config.schema import as_tuple

SECTION = "django"
ALLOWED_KEYS = frozenset(
    {
        "settings",
        "db_routing",
        "db_alias_template",
        "oracles",
        "settings_proxies",
        "setting_name_helpers",
        "hooks",
        "receiver_decorators",
        "signal_bases",
        "scan_roots",
    }
)
DEFAULT_RECEIVER_DECORATORS = ("django.dispatch.dispatcher.receiver",)
DEFAULT_SIGNAL_BASES = ("django.dispatch.dispatcher.Signal",)


@dataclass(frozen=True)
class SettingsProxy:
    """A third-party settings object whose `X` attribute reads the Django setting `<prefix>X`."""

    name: str
    prefix: str


@dataclass(frozen=True)
class DatabaseRouting:
    """A routing file that maps app labels to databases, and how a database becomes an alias."""

    path: Path
    alias_template: str = "{database}"
    default_alias: str = "default"


# A `hooks` entry as a table: the hook's name, and the arguments that hand it a callable.
HOOK_KEYS = frozenset({"name", "callables"})


def hook_problems(raw: Any) -> list[str]:
    """What is wrong with a `hooks` list: each entry is a qualified name, or a table with `name`
    and `callables`, the keyword names (strings) and positions (integers) of the arguments that
    hand the hook a callable."""
    out: list[str] = []
    for entry in raw if isinstance(raw, list) else as_tuple(raw):
        if isinstance(entry, str):
            continue
        if not isinstance(entry, dict) or set(entry) != HOOK_KEYS or not isinstance(entry["name"], str):
            out.append(f"hooks entry {entry!r}: a qualified name, or a table of exactly `name` and `callables`")
            continue
        callables = entry["callables"]
        if not isinstance(callables, list) or not all(
            isinstance(item, str) or (isinstance(item, int) and not isinstance(item, bool) and item >= 0)
            for item in callables
        ):
            out.append(f"hooks entry {entry['name']!r}: `callables` lists keyword names and positions (0 and up)")
    return out


def read_hooks(raw: Any) -> tuple[tuple[str, ...], dict[str, tuple[str | int, ...]]]:
    """(every hook's name, the declared callable arguments of the hooks a table declares)."""
    names: list[str] = []
    callables: dict[str, tuple[str | int, ...]] = {}
    for entry in raw if isinstance(raw, list) else as_tuple(raw):
        if isinstance(entry, dict):
            names.append(str(entry["name"]))
            callables[str(entry["name"])] = tuple(entry["callables"])
        else:
            names.append(str(entry))
    return tuple(names), callables


@dataclass(frozen=True)
class DjangoPackConfig:
    """Where the Django facts of one repo live.

    `settings_modules` are dotted modules or fnmatch globs (`proj.settings.*`), read in order.
    `settings_proxies` map `<name>.X` to the setting `<prefix>X`; `setting_name_helpers` map a
    call `<name>("A", "B")` to the setting `<prefix>A_B` (both only in migration keys and FK targets).
    `hooks` are qualified names of functions that install a callback; a call or decorator that
    resolves to one becomes an `installs` edge. A hook given as a table names the arguments that
    hand it a callable (`hook_callables`); the others are data. A hook given by name alone may
    take a callable in any argument. `receiver_decorators` are functions that register
    their decorated function as a signal receiver, like Django's own `receiver`. `scan_roots` are
    the module prefixes whose files the pack reads for models, uses, signals and installs; empty
    means the top-level packages of the first-party INSTALLED_APPS, so first-party tooling that
    is no Django code stays out.
    """

    settings_modules: tuple[str, ...] = ()
    routing: DatabaseRouting | None = None
    settings_proxies: tuple[SettingsProxy, ...] = ()
    setting_name_helpers: tuple[SettingsProxy, ...] = ()
    hooks: tuple[str, ...] = ()
    hook_callables: Mapping[str, tuple[str | int, ...]] = field(default_factory=dict)
    receiver_decorators: tuple[str, ...] = DEFAULT_RECEIVER_DECORATORS
    signal_bases: tuple[str, ...] = DEFAULT_SIGNAL_BASES
    scan_roots: tuple[str, ...] = ()

    @classmethod
    def from_section(cls, section: Mapping[str, Any]) -> DjangoPackConfig:
        """The `[django]` table; `settings` may also be a table with `module` and `variants`."""
        settings = section.get("settings", ())
        if isinstance(settings, Mapping):
            settings = [settings.get("module", ""), *settings.get("variants", {}).values()]
        routing = None
        if section.get("db_routing"):
            routing = DatabaseRouting(Path(section["db_routing"]), section.get("db_alias_template", "{database}"))
        hooks, hook_callables = read_hooks(section.get("hooks"))
        return cls(
            settings_modules=as_tuple(settings),
            routing=routing,
            settings_proxies=tuple(SettingsProxy(k, v) for k, v in section.get("settings_proxies", {}).items()),
            setting_name_helpers=tuple(SettingsProxy(k, v) for k, v in section.get("setting_name_helpers", {}).items()),
            hooks=hooks,
            hook_callables=hook_callables,
            receiver_decorators=as_tuple(section.get("receiver_decorators")) or DEFAULT_RECEIVER_DECORATORS,
            signal_bases=as_tuple(section.get("signal_bases")) or DEFAULT_SIGNAL_BASES,
            scan_roots=as_tuple(section.get("scan_roots")),
        )
