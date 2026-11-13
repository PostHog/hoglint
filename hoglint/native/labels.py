"""The native engine's labeler: module labels read from the evaluator's columns.

The evaluator labels every graph module once (src/link/rules/labels.rs) and reports every label
set two ways (D44). So the native engine reads a module field's value and a module's registry
from its columns, and the Python label pass runs only to word a conflict and for `explain`.
"""

from __future__ import annotations

from array import array
from typing import Any

from hoglint.config.model import KindSpec, LevelSpec
from hoglint.labels import HANDED_ZONES, Labeler, render
from hoglint.native.bridge import engine_errors, import_module
from hoglint.selectors import NONE, UNKNOWN, FieldType, Value

# The source a value read from a column names: only `explain` shows sources, and it asks the
# Python label pass for them (`sourced`).
COLUMN_SOURCE = "evaluator"
REGISTRY_NAMES = frozenset({"registry", "lifecycle"})


class NativeLabeler(Labeler):
    """A Labeler that, once the rule context hands it the evaluator (`read_from`), reads module
    fields and registries of graph modules from the evaluator's columns. Symbol fields, fields a
    provider sets, modules outside the graph and a value that meets a provider conflict stay the
    Python labeler's."""

    evaluator: Any = None

    def read_from(self, evaluator: Any) -> None:
        self.evaluator = evaluator
        # A module's place in a column where the evaluator has no value for Python to take.
        self.no_place: int = import_module().NO_PLACE
        self.value_columns: dict[str, tuple[list[Value], array[int]]] = {}
        self.registry_places: array[int] | None = None
        self.registry_specs = list(self.config.registries.values())

    def value_of(self, kind: str, items: list[str]) -> Value:
        """A value of the evaluator's column as `Labeler.to_value` makes it."""
        if kind == "unknown":
            return UNKNOWN
        if kind == "none":
            return NONE
        if kind == "zone":
            return Value.known(items[0], self.config.zones.chain(items[0]))
        if kind == "set":
            return Value.known(frozenset(items))
        if kind == "bool":
            return Value.known(items[0] == "True")
        return Value.known(items[0])

    def value_column(self, name: str) -> tuple[list[Value], array[int]]:
        found = self.value_columns.get(name)
        if found is None:
            typed, places = self.evaluator.values(name)
            found = self.value_columns[name] = (
                [self.value_of(kind, items) for kind, items in typed],
                array("I", places),
            )
        return found

    def column_place(self, module: str | None, name: str) -> int | None:
        """The module's ordinal when the evaluator's column of field `name` answers for it."""
        if self.evaluator is None or module is None or name in self.provider_fields:
            return None
        if self.config.fields[name].on != "module":
            return None
        return self.module_ordinal.get(module)

    def field_value(
        self, module: str | None, symbol: str | None, name: str, sourced: bool = False
    ) -> tuple[Value, str]:
        at = None if sourced else self.column_place(module, name)
        if at is None:
            return super().field_value(module, symbol, name)
        values, places = self.value_column(name)
        place = places[at]
        if place == self.no_place:
            return super().field_value(module, symbol, name)
        return values[place], COLUMN_SOURCE

    def builtin(self, module: str | None, symbol: str | None, name: str, sourced: bool = False) -> Value:
        at = self.module_ordinal.get(module) if module is not None else None
        if name not in REGISTRY_NAMES or at is None or self.evaluator is None or sourced:
            return super().builtin(module, symbol, name)
        if self.registry_places is None:
            self.registry_places = array("I", self.evaluator.registries())
        place = self.registry_places[at]
        if place == self.no_place:
            return NONE
        spec = self.registry_specs[place]
        if name == "registry":
            return Value.known(spec.name)
        return Value.known(spec.lifecycle) if spec.lifecycle else NONE

    def explain(self, module: str) -> list[tuple[str, str, str]]:
        """`Labeler.explain` with each value the evaluator holds taken from it; the Python label
        pass gives the source of each and the values it alone holds (provider fields)."""
        rows = super().explain(module)
        if self.evaluator is None:
            return rows
        out = []
        for name, value, source in rows:
            if name == HANDED_ZONES:
                with engine_errors():
                    kind, items = self.evaluator.handed_zones(module)
                value = render(self.value_of(kind, items))
            elif name in REGISTRY_NAMES:
                found = self.builtin(module, None, name)
                value = str(found.data) if name == "registry" else render(found)
            else:
                value = render(self.field_value(module, None, name)[0])
            out.append((name, value, source))
        return out

    def kind_members(self, spec: KindSpec) -> frozenset[str]:
        """The evaluator's members of a kind: the Python labeler would classify every symbol."""
        if self.evaluator is None:
            return super().kind_members(spec)
        with engine_errors():
            return frozenset(self.evaluator.kind_members(spec.name))

    def level_groups(self, spec: LevelSpec) -> list[str] | None:
        """The evaluator's groups of a level, when its group field is not a set: `known_text` of
        a set follows Python's `str()` of a frozenset, which only Python writes."""
        field = self.config.fields.get(spec.group_by)
        if self.evaluator is None or field is None or field.type is FieldType.SET:
            return None
        with engine_errors():
            return self.evaluator.level_groups(list(self.config.levels).index(spec.name))
