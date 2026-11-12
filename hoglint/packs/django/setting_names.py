"""The setting each string of a settings module sits in, as the `setting` attr of the dotted-path
edges core makes there: a rule selects a setting's entries by its name (`MIDDLEWARE`,
`REST_FRAMEWORK.DEFAULT_AUTHENTICATION_CLASSES`), not by the syntax around them.

A string in a list that another setting takes in (`[*EXTRA]`, `A + B`, `B = A`, `{**A}`) belongs to
that setting; one under a subscript target or a computed key belongs to the name and literal keys
above it; one passed to a method of a setting belongs to that setting. A string in a condition is
no value of a setting and gets no attr. A string whose setting cannot be told gets
`unknown = "setting"`: one passed to a call that is no method of a plain name (`X["k"].append(...)`:
the facts hold no text of the receiver), and one in a list that flows into two settings.
"""

from __future__ import annotations

import bisect
from collections.abc import Iterable, Mapping
from typing import Any

from hoglint.core.records import sequence_items
from hoglint.core.sites import MODULE_SCOPE
from hoglint.model import UNKNOWN_ATTRS
from hoglint.plugins import PackReader, Refinement

SETTINGS_PRODUCER = "django.settings@1"
SETTING = "setting"
DOTTED_PATH = "dotted-path"
# Steps of a string's syntax chain that keep it a value of the same setting.
CONTAINER_STEPS = ("elt:", "star", "ifexp:body", "ifexp:else")
# The statement kinds that name the setting a value goes into.
ASSIGN_STEPS = ("assign:", "annassign:", "augassign:")
# The name an assignment step gives a subscript target.
SUBSCRIPT = "[]"
# Statements whose strings are no value of a setting: a condition.
CONDITION_STEPS = frozenset({"if-test", "assert"})
# The place of a string that is no value of a setting.
NO_SETTING = ""


class SettingPlaces:
    """Reads a string's setting from its syntax chain (`up`, innermost step first): the name the
    statement assigns or mutates, and the literal keys on the way down to the string, up to the
    first key that is computed. A string in a condition is no value of a setting, and a string
    passed to a method of a setting (`x = MIDDLEWARE.index("...")`) belongs to that setting, not
    to the name the result is assigned to."""

    def __init__(self, settings: set[str]) -> None:
        self.settings = settings

    @staticmethod
    def receiver_of(step: str) -> str:
        """The name a call step's callee is a method of (`arg:0:MIDDLEWARE.index`)."""
        return step.split(":", 2)[2].rpartition(".")[0]

    def method_of_setting(self, inner: list[str]) -> tuple[str, list[str], list[str]] | None:
        """The setting whose method takes the string as an argument, innermost call first."""
        for index, step in enumerate(inner):
            if not step.startswith(("arg:", "kw:")):
                continue
            receiver = self.receiver_of(step)
            if receiver in self.settings:
                return receiver, [], inner[:index]
        return None

    def target(
        self, steps: list[str], statements: Statements, line: int, column: int
    ) -> tuple[str, list[str], list[str]] | None:
        """The statement's name, the literal keys of a subscript target, and the steps inside the
        statement; None when no name can be read (a call that is no method of a plain name). The
        subscript target is looked up only for a statement that assigns to one."""
        last = steps[-1]
        if last.startswith(ASSIGN_STEPS):
            lookup = self.method_of_setting(steps[:-1])
            if lookup is not None:
                return lookup
            name = last.rsplit(":", 1)[1]
            subscript = statements.subscript_at(line, column) if name == SUBSCRIPT else None
            if subscript is not None:
                return subscript.name, subscript.keys, steps[:-1]
            return name, [], steps[:-1]
        if last == "expr" and len(steps) >= 2 and steps[-2].startswith("arg:"):
            return self.receiver_of(steps[-2]), [], steps[:-2]
        return None

    @staticmethod
    def in_condition(up: str) -> bool:
        return up.rpartition(" ")[2] in CONDITION_STEPS

    def place(self, up: str, statements: Statements, line: int, column: int) -> str | None:
        """`NAME` or `NAME.KEY.KEY`; NO_SETTING for a string in a condition; None when the name
        cannot be read."""
        if self.in_condition(up):
            return NO_SETTING
        steps = up.split(" ")
        found = self.target(steps, statements, line, column)
        if found is None:
            return None
        name, keys, inner = found
        if not name.isidentifier():
            return None
        keys = list(keys)
        for step in reversed(inner):
            if step.startswith("dict:val:"):
                keys.append(step.removeprefix("dict:val:"))
                continue
            if not step.startswith(CONTAINER_STEPS):
                break
        return ".".join([name, *keys])


class Subscript:
    """A subscript assignment target (`CACHES["default"]["OPTIONS"] = ...`): the name it starts
    from and its literal keys, up to the first computed one."""

    def __init__(self, name: str, keys: list[str]) -> None:
        self.name = name
        self.keys = keys

    @staticmethod
    def of(target: object) -> Subscript | None:
        indices: list[object] = []
        while isinstance(target, dict) and "sub" in target:
            indices.append(target.get("i"))
            target = target["sub"]
        if not isinstance(target, str) or not indices:
            return None
        keys: list[str] = []
        for index in reversed(indices):
            if not (isinstance(index, dict) and isinstance(index.get("s"), str)):
                break
            keys.append(index["s"])
        return Subscript(target, keys)


class Statements:
    """The module-level assignments of one settings module, and their targets in source order to
    find the subscript target of the statement a string sits in."""

    def __init__(self, record: Mapping[str, Any]) -> None:
        self.rows = [row for row in record.get("assigns", ()) if row["s"] == -1]
        ordered = sorted(((row["l"], row["c"]), row["t"]) for row in self.rows)
        self.starts = [start for start, _ in ordered]
        self.targets = [targets for _, targets in ordered]

    def names(self) -> set[str]:
        """The plain names the module assigns at module level: its settings."""
        return {target for row in self.rows for target in row["t"] if isinstance(target, str) and target.isidentifier()}

    def subscript_at(self, line: int, column: int) -> Subscript | None:
        """The subscript target of the last statement that starts before the string."""
        index = bisect.bisect_right(self.starts, (line, column)) - 1
        if index < 0 or not self.targets[index]:
            return None
        return Subscript.of(self.targets[index][0])


class SettingFlows:
    """Which module-level name flows into which setting: a splat (`[*A]`, `{**A}`), an operand of
    `+` or `+=`, or a plain alias (`B = A`), with the dict keys on the way."""

    def __init__(self) -> None:
        self.into: dict[str, set[str]] = {}

    def read(self, statements: Statements) -> None:
        for row in statements.rows:
            if "v" not in row:
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
        statements = {module: Statements(record) for module, record in records}
        flows = SettingFlows()
        settings: set[str] = set()
        for module, _ in records:
            flows.read(statements[module])
            settings.update(statements[module].names())
        places = SettingPlaces(settings)
        names: set[str] = set()
        for _, record in records:
            for row in record.get("strs", ()):
                at_scope, _header, _line, _column, _flags, value, _chain, up = row[:8]
                # A string inside a function or in a condition is no value of a setting.
                if at_scope != -1 or places.in_condition(up):
                    continue
                parts = row[8] if len(row) > 8 else None
                texts = [value] if parts is None else [*parts, value]
                names.update(text.rpartition(".")[2] for text in texts if text)
        out: list[Refinement] = []
        if not names:
            return out
        for edge in self.reader.edges_spelled(DOTTED_PATH, names):
            site = edge.site
            found = statements.get(site.module)
            if found is None or site.scope != MODULE_SCOPE:
                continue
            place = places.place(edge.attr("up"), found, site.line, site.column)
            if place == NO_SETTING:
                continue
            setting = flows.setting(place) if place is not None else None
            attrs = ((SETTING, setting),) if setting is not None else ((UNKNOWN_ATTRS, SETTING),)
            out.append(Refinement(edge.handle, attrs=attrs))
        return out
