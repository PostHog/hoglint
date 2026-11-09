"""Settings of the Django pack, read from a repo's `[django]` table. The pack names no repo."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
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


@dataclass(frozen=True)
class DjangoPackConfig:
    """Where the Django facts of one repo live.

    `settings_modules` are dotted modules or fnmatch globs (`proj.settings.*`), read in order.
    `settings_proxies` map `<name>.X` to the setting `<prefix>X`; `setting_name_helpers` map a
    call `<name>("A", "B")` to the setting `<prefix>A_B` (both only in migration keys and FK targets).
    `hooks` are qualified names of functions that install a callback; a call or decorator that
    resolves to one becomes an `installs` edge. `receiver_decorators` are functions that register
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
        return cls(
            settings_modules=as_tuple(settings),
            routing=routing,
            settings_proxies=tuple(SettingsProxy(k, v) for k, v in section.get("settings_proxies", {}).items()),
            setting_name_helpers=tuple(SettingsProxy(k, v) for k, v in section.get("setting_name_helpers", {}).items()),
            hooks=as_tuple(section.get("hooks")),
            receiver_decorators=as_tuple(section.get("receiver_decorators")) or DEFAULT_RECEIVER_DECORATORS,
            signal_bases=as_tuple(section.get("signal_bases")) or DEFAULT_SIGNAL_BASES,
            scan_roots=as_tuple(section.get("scan_roots")),
        )
