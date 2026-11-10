"""The native engine's labeler: module labels read from the evaluator's columns.

The evaluator labels every graph module once (src/link/rules/labels.rs) and reports every label
set two ways (D44). So the native engine reads a module field's value and a module's registry
from its columns, and the Python label pass runs only to word a conflict and for `explain`.
"""

from __future__ import annotations

import json
from array import array
from typing import Any

from hoglint.config.model import KindSpec, LevelSpec
from hoglint.labels import Labeler
from hoglint.ledger.envelope import EngineError
from hoglint.native.plan import tree
from hoglint.selectors import NONE, UNKNOWN, FieldType, Value

# A module's place in a column where the evaluator has no value for Python to take.
NO_PLACE = 0xFFFFFFFF
# The source a value read from a column names: only `explain` shows sources, and it reads them
# from the Python label pass.
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

    def field_value(self, module: str | None, symbol: str | None, name: str) -> tuple[Value, str]:
        at = self.column_place(module, name)
        if at is None:
            return super().field_value(module, symbol, name)
        values, places = self.value_column(name)
        place = places[at]
        if place == NO_PLACE:
            return super().field_value(module, symbol, name)
        return values[place], COLUMN_SOURCE

    def builtin(self, module: str | None, symbol: str | None, name: str) -> Value:
        at = self.module_ordinal.get(module) if module is not None else None
        if name not in REGISTRY_NAMES or at is None or self.evaluator is None:
            return super().builtin(module, symbol, name)
        if self.registry_places is None:
            self.registry_places = array("I", self.evaluator.registries())
        place = self.registry_places[at]
        if place == NO_PLACE:
            return NONE
        spec = self.registry_specs[place]
        if name == "registry":
            return Value.known(spec.name)
        return Value.known(spec.lifecycle) if spec.lifecycle else NONE

    def kind_members(self, spec: KindSpec) -> frozenset[str]:
        """The evaluator's members of a kind: the Python labeler would classify every symbol."""
        if self.evaluator is None:
            return super().kind_members(spec)
        try:
            return frozenset(self.evaluator.kind_members(spec.name))
        except ValueError as error:
            raise EngineError(f"the native evaluator failed: {error}") from error

    def level_groups(self, spec: LevelSpec) -> list[str] | None:
        """The evaluator's groups of a level, when its group field is not a set: `known_text` of
        a set follows Python's `str()` of a frozenset, which only Python writes."""
        field = self.config.fields.get(spec.group_by)
        if self.evaluator is None or field is None or field.type is FieldType.SET:
            return None
        select = json.dumps(tree(spec.select.node)) if spec.select is not None else None
        try:
            return self.evaluator.level_groups(spec.group_by, select)
        except ValueError as error:
            raise EngineError(f"the native evaluator failed: {error}") from error

    def explain(self, module: str) -> list[tuple[str, str, str]]:
        """Every field with the source that set it: the sources are the Python label pass's."""
        evaluator, self.evaluator = self.evaluator, None
        try:
            return super().explain(module)
        finally:
            self.evaluator = evaluator
