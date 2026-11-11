"""What a contract surface hands out, by the rule for public surfaces.

A module hands out a name it binds by import when one of these clauses holds:

- `__all__` lists it;
- the import binds it under its own name (`from x import f as f`);
- it is a lazy (PEP 562) name;
- the module has no top-level function, so every import is there to be handed on.

This is not the export primitive's notion of what a module exports (every module-level import).
A surface also imports the code it calls, and that code is not part of its contract.

`__all__` is read in every form whose names a reader can list: `__all__ = [...]`, annotated or
not, `+=` with a literal, and a sum of literals. One built by code, changed by a method or set
under a condition cannot be read: what the module hands out is then unknown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from hoglint.core.records import str_literal
from hoglint.core.sites import FileScope
from hoglint.core.symbols import ModuleTable, absolute_import

ALL_CLAUSE = "all"
ALIAS_CLAUSE = "alias"
LAZY_CLAUSE = "lazy"
NO_FUNCTIONS_CLAUSE = "no-functions"
# A call of a method on `__all__` in the module body: `__all__.extend(...)`.
METHOD_CALL = re.compile(r"__all__\.\w+")


@dataclass(frozen=True, slots=True)
class Handed:
    """A name a module hands out: where it binds it, what it names, and the clause that holds."""

    bound: str
    target: str
    clause: str
    line: int
    column: int


@dataclass(frozen=True, slots=True)
class DunderAll:
    """The names a module's `__all__` lists, or where it first cannot be read (`unread`)."""

    names: frozenset[str] | None
    unread: tuple[int, int] | None


def listed_names(value: object) -> frozenset[str] | None:
    """The strings a list or tuple literal holds, or a sum of such literals; None for any other
    value, and for a literal with an item that is no string."""
    if not isinstance(value, dict):
        return None
    if "bin" in value and value["bin"] != "|":
        left, right = listed_names(value["l"]), listed_names(value["r"])
        return left | right if left is not None and right is not None else None
    items = value.get("list", value.get("tuple"))
    if not isinstance(items, list):
        return None
    names = [str_literal(item) for item in items]
    if any(name is None for name in names):
        return None
    return frozenset(name for name in names if name is not None)


class HandedOut:
    """The names one module hands out, in import order and then lazy-name order."""

    def __init__(self, scope: FileScope, table: ModuleTable | None) -> None:
        self.scope = scope
        self.table = table

    def has_functions(self) -> bool:
        """Whether the module body defines a function outside any block."""
        return any(d["s"] == -1 and d.get("f", 0) == 0 and d["k"] != "class" for d in self.scope.defs)

    def dunder_all(self) -> DunderAll:
        """The module body's `__all__`, statement by statement in source order."""
        names: frozenset[str] | None = None
        unread: list[tuple[int, int]] = []
        for row in self.scope.record.get("assigns", ()):
            if row["s"] != -1 or "__all__" not in row["t"]:
                continue
            if row["op"] == ":" and "v" not in row:
                continue
            listed = listed_names(row.get("v"))
            if row.get("f", 0) != 0 or listed is None:
                unread.append((row["l"], row["c"]))
            elif row["op"] in ("=", ":"):
                names = listed
            elif row["op"] == "+=" and names is not None:
                names = names | listed
            else:
                unread.append((row["l"], row["c"]))
        for at_scope, header, callee, _count, line, column in self.scope.record.get("callees", ()):
            if at_scope == -1 and header == -1 and METHOD_CALL.fullmatch(callee):
                unread.append((line, column))
        return DunderAll(names, min(unread) if unread else None)

    def clause(
        self, bound: str, name: str, asname: str | None, no_functions: bool, listed: frozenset[str] | None
    ) -> str:
        if no_functions:
            return NO_FUNCTIONS_CLAUSE
        if listed is not None and bound in listed:
            return ALL_CLAUSE
        if asname == name:
            return ALIAS_CLAUSE
        return ""

    def listing(self) -> DunderAll | None:
        """The module's `__all__`; None for a module with no top-level function, which hands out
        every import whatever `__all__` says."""
        return self.dunder_all() if self.has_functions() else None

    def names(self, listing: DunderAll | None) -> list[Handed]:
        scope = self.scope
        no_functions = listing is None
        listed = listing.names if listing is not None else None
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
