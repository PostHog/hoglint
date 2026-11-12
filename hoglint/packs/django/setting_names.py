"""The setting each string of a settings module sits in, as the `setting` attr of the dotted-path
edges core makes there: a rule selects a setting's entries by its name (`MIDDLEWARE`,
`REST_FRAMEWORK.DEFAULT_AUTHENTICATION_CLASSES`), not by the syntax around them.

A string in a list that another setting takes in (`[*EXTRA]`, `A + B`, `B = A`, `{**A}`) belongs to
that setting. A string whose setting cannot be told gets `unknown = "setting"`, never no attr.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from hoglint.core.records import sequence_items
from hoglint.model import UNKNOWN_ATTRS
from hoglint.plugins import PackReader, Refinement

SETTINGS_PRODUCER = "django.settings@1"
SETTING = "setting"
DOTTED_PATH = "dotted-path"
# Steps of a string's syntax chain that keep it a value of the same setting.
CONTAINER_STEPS = ("elt:", "star", "ifexp:body", "ifexp:else")
# The statement kinds that name the setting a value goes into.
ASSIGN_STEPS = ("assign:", "annassign:")


class SettingPlaces:
    """Reads a string's setting from its syntax chain (`up`, innermost step first): the name the
    statement assigns or mutates, and the literal dict keys on the way down to the string."""

    @staticmethod
    def target(steps: list[str]) -> tuple[str, list[str]] | None:
        """The statement's name, and the steps inside it; None when the statement names no plain
        setting (a subscript target, a condition, a call that is no method of a name)."""
        last = steps[-1]
        if last.startswith(ASSIGN_STEPS):
            return last.partition(":")[2], steps[:-1]
        if last.startswith("augassign:"):
            return last.split(":", 2)[2], steps[:-1]
        if last == "expr" and len(steps) >= 2 and steps[-2].startswith("arg:"):
            receiver = steps[-2].split(":", 2)[2].rpartition(".")[0]
            return receiver, steps[:-2]
        return None

    def place(self, up: str) -> str | None:
        """`NAME` or `NAME.KEY.KEY`; None when it cannot be told."""
        found = self.target(up.split(" "))
        if found is None:
            return None
        name, inner = found
        if not name.isidentifier():
            return None
        keys: list[str] = []
        for step in reversed(inner):
            if step.startswith("dict:val:"):
                keys.append(step.removeprefix("dict:val:"))
                continue
            if step == "dict:val":
                return None
            if not step.startswith(CONTAINER_STEPS):
                break
        return ".".join([name, *keys])


class SettingFlows:
    """Which module-level name flows into which setting: a splat (`[*A]`, `{**A}`), an operand of
    `+` or `+=`, or a plain alias (`B = A`), with the dict keys on the way."""

    def __init__(self) -> None:
        self.into: dict[str, set[str]] = {}

    def read(self, record: Mapping[str, Any]) -> None:
        for row in record.get("assigns", ()):
            if row["s"] != -1 or "v" not in row:
                continue
            for target in row["t"]:
                if isinstance(target, str) and target.isidentifier():
                    self.walk(row["v"], target)

    def walk(self, value: object, place: str) -> None:
        if isinstance(value, str):
            if value.isidentifier() and value != place.partition(".")[0]:
                self.into.setdefault(value, set()).add(place)
            return
        if not isinstance(value, dict):
            return
        items = sequence_items(value)
        if items is not None:
            for item in items:
                if isinstance(item, dict) and "star" in item:
                    self.walk(item["star"], place)
            return
        if value.get("bin") == "+":
            self.walk(value["l"], place)
            self.walk(value["r"], place)
            return
        for key, inner in value.get("dict", ()):
            if key is None:
                self.walk(inner, place)
            elif isinstance(key, dict) and isinstance(key.get("s"), str):
                self.walk(inner, f"{place}.{key['s']}")

    def setting(self, place: str) -> str | None:
        """The setting a place ends in, through every name it flows into; None when a name
        flows into two places."""
        seen: set[str] = set()
        while True:
            head, _, rest = place.partition(".")
            targets = self.into.get(head)
            if not targets:
                return place
            if len(targets) > 1 or place in seen:
                return None
            seen.add(place)
            target = next(iter(targets))
            place = f"{target}.{rest}" if rest else target


class SettingNames:
    """Refines the dotted-path edges of the settings modules with their setting's name."""

    def __init__(self, reader: PackReader, settings_modules: Iterable[str]) -> None:
        self.reader = reader
        self.modules = list(settings_modules)

    def produce(self) -> list[Refinement]:
        records = [(module, self.reader.tables.record(module) or {}) for module in self.modules]
        flows = SettingFlows()
        for _, record in records:
            flows.read(record)
        places = SettingPlaces()
        wanted: dict[tuple[str, int, int], str | None] = {}
        names: set[str] = set()
        for module, record in records:
            for row in record.get("strs", ()):
                at_scope, _header, line, column, _flags, value, _chain, up = row[:8]
                # A string inside a function is no value of a setting.
                if at_scope != -1:
                    continue
                parts = row[8] if len(row) > 8 else None
                texts = [value] if parts is None else [*parts, value]
                names.update(text.rpartition(".")[2] for text in texts if text)
                place = places.place(up)
                wanted[(module, line, column)] = flows.setting(place) if place is not None else None
        out: list[Refinement] = []
        if not wanted:
            return out
        for edge in self.reader.edges_spelled(DOTTED_PATH, names):
            site = edge.site
            key = (site.module, site.line, site.column)
            if key not in wanted:
                continue
            setting = wanted[key]
            attrs = ((SETTING, setting),) if setting is not None else ((UNKNOWN_ATTRS, SETTING),)
            out.append(Refinement(edge.handle, attrs=attrs))
        return out
