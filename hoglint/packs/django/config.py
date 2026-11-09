"""Settings of the Django pack. A repo preset fills them; the pack names no repo."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


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
    `hooks` are qualified function names or fnmatch globs (`**.register_*`); a call or decorator
    that resolves to one becomes an `installs` edge. `receiver_decorators` are functions that
    register their decorated function as a signal receiver, like Django's own `receiver`.
    """

    first_party: tuple[str, ...]
    settings_modules: tuple[str, ...]
    routing: DatabaseRouting | None = None
    settings_proxies: tuple[SettingsProxy, ...] = ()
    setting_name_helpers: tuple[SettingsProxy, ...] = ()
    hooks: tuple[str, ...] = ()
    receiver_decorators: tuple[str, ...] = ("django.dispatch.dispatcher.receiver",)
    signal_bases: tuple[str, ...] = ("django.dispatch.dispatcher.Signal",)
