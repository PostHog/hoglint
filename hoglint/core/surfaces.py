"""What a contract surface hands out, by the rule for public surfaces.

A module hands out a name it binds by import when one of these clauses holds:

- `__all__` lists it;
- the import binds it under its own name (`from x import f as f`);
- it is a lazy (PEP 562) name;
- the module has no top-level function, so every import is there to be handed on.

This is not the export primitive's notion of what a module exports (every module-level import).
A surface also imports the code it calls, and that code is not part of its contract.
"""

from __future__ import annotations

from dataclasses import dataclass

from hoglint.core.records import sequence_items, str_literal
from hoglint.core.sites import FileScope
from hoglint.core.symbols import ModuleTable, absolute_import

ALL_CLAUSE = "all"
ALIAS_CLAUSE = "alias"
LAZY_CLAUSE = "lazy"
NO_FUNCTIONS_CLAUSE = "no-functions"


@dataclass(frozen=True, slots=True)
class Handed:
    """A name a module hands out: where it binds it, what it names, and the clause that holds."""

    bound: str
    target: str
    clause: str
    line: int
    column: int


class HandedOut:
    """The names one module hands out, in import order and then lazy-name order."""

    def __init__(self, scope: FileScope, table: ModuleTable | None) -> None:
        self.scope = scope
        self.table = table

    def has_functions(self) -> bool:
        """Whether the module body defines a function outside any block."""
        return any(d["s"] == -1 and d.get("f", 0) == 0 and d["k"] != "class" for d in self.scope.defs)

    def dunder_all(self) -> set[str] | None:
        """The names of the last plain `__all__ = [...]` in the module body."""
        found = None
        for row in self.scope.record.get("assigns", ()):
            if row["s"] != -1 or row.get("f", 0) != 0 or row["op"] != "=" or "__all__" not in row["t"]:
                continue
            value = row.get("v")
            items = sequence_items(value)
            if items is not None and isinstance(value, dict) and "set" not in value:
                found = {text for text in (str_literal(item) for item in items) if text is not None}
        return found

    def clause(self, bound: str, name: str, asname: str | None, no_functions: bool, listed: set[str] | None) -> str:
        if no_functions:
            return NO_FUNCTIONS_CLAUSE
        if listed is not None and bound in listed:
            return ALL_CLAUSE
        if asname == name:
            return ALIAS_CLAUSE
        return ""

    def names(self) -> list[Handed]:
        scope = self.scope
        no_functions = not self.has_functions()
        listed = None if no_functions else self.dunder_all()
        out: list[Handed] = []
        seen: set[tuple[str, str]] = set()
        for _, line, column, flags, target, level, name, asname in scope.record.get("imports", ()):
            if name is None or name == "*" or flags != 0:
                continue
            source = absolute_import(scope.module, scope.is_package, target, level)
            if source is None:
                continue
            bound = asname or name
            clause = self.clause(bound, name, asname, no_functions, listed)
            key = (bound, f"{source}.{name}")
            if not clause or key in seen:
                continue
            seen.add(key)
            out.append(Handed(bound, key[1], clause, line, column))
        lazy = self.table.lazy if self.table is not None else {}
        for name, module in lazy.items():
            key = (name, f"{module}.{name}")
            if key not in seen:
                seen.add(key)
                out.append(Handed(name, key[1], LAZY_CLAUSE, 0, 0))
        return out
