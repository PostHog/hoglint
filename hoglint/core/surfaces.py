"""What a contract surface hands out, by the rule for public surfaces.

A module hands out a name it binds by import when one of these clauses holds:

- `__all__` lists it;
- the import binds it under its own name (`from x import f as f`);
- it is a lazy (PEP 562) name;
- the module has no top-level function, so every import is there to be handed on.

A star import hands out the names it binds (the source's `__all__`, else its public top-level
names) under the same clauses; a source that cannot be read hands out `*`, which nothing follows.

This is not the export primitive's notion of what a module exports (every module-level import).
A surface also imports the code it calls, and that code is not part of its contract.

`__all__` is read in every form whose names a reader can list (`ListedNames`): `__all__ = [...]`,
annotated or not, `+=` with a literal, a sum of literals, and copies of a module-level literal
such as `sorted(_LAZY)`. One built by other code, changed by a method or set under a condition
cannot be read: what the module hands out is then unknown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from hoglint.core.records import str_literal
from hoglint.core.sites import FileScope
from hoglint.core.symbols import ModuleTable, Tables, absolute_import

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


class ListedNames:
    """The names a value lists, read from one module's record: a list, tuple or set of string
    literals, items starred from a readable value, a sum of such values, `sorted`, `list`,
    `tuple` or `set` of one readable value, and a module-level name assigned once,
    unconditionally, to a literal of strings (a dict gives its keys). None for anything else."""

    COPIES = frozenset({"sorted", "list", "tuple", "set"})

    def __init__(self, record: dict) -> None:
        self.record = record

    def literal(self, name: str) -> frozenset[str] | None:
        """The strings a module-level name holds, when one unconditional assignment binds it to a
        literal list, tuple, set or dict of string literals."""
        rows = [row for row in self.record.get("assigns", ()) if row["s"] == -1 and name in row["t"]]
        if len(rows) != 1 or rows[0].get("f", 0) != 0 or rows[0]["op"] not in ("=", ":"):
            return None
        value = rows[0].get("v")
        if not isinstance(value, dict):
            return None
        if "dict" in value:
            items = [key for key, _ in value["dict"]]
        else:
            items = value.get("list", value.get("tuple", value.get("set")))
        if not isinstance(items, list):
            return None
        names = [str_literal(item) for item in items]
        if any(found is None for found in names):
            return None
        return frozenset(found for found in names if found is not None)

    def of(self, value: object) -> frozenset[str] | None:
        if isinstance(value, str):
            return self.literal(value) if "." not in value else None
        if not isinstance(value, dict):
            return None
        if "bin" in value and value["bin"] != "|":
            left, right = self.of(value["l"]), self.of(value["r"])
            return left | right if left is not None and right is not None else None
        if "call" in value:
            args = value.get("args", [])
            callee = value["call"]
            if not isinstance(callee, str) or callee not in self.COPIES or len(args) != 1:
                return None
            return self.of(args[0])
        items = value.get("list", value.get("tuple", value.get("set")))
        if not isinstance(items, list):
            return None
        out: set[str] = set()
        for item in items:
            if isinstance(item, dict) and "star" in item:
                found = self.of(item["star"])
                if found is None:
                    return None
                out |= found
                continue
            text = str_literal(item)
            if text is None:
                return None
            out.add(text)
        return frozenset(out)


def read_dunder_all(record: dict) -> DunderAll:
    """A module body's `__all__`, statement by statement in source order."""
    names: frozenset[str] | None = None
    unread: list[tuple[int, int]] = []
    reader = ListedNames(record)
    for row in record.get("assigns", ()):
        if row["s"] != -1 or "__all__" not in row["t"]:
            continue
        if row["op"] == ":" and "v" not in row:
            continue
        listed = reader.of(row.get("v"))
        if row.get("f", 0) != 0 or listed is None:
            unread.append((row["l"], row["c"]))
        elif row["op"] in ("=", ":"):
            names = listed
        elif row["op"] == "+=" and names is not None:
            names = names | listed
        else:
            unread.append((row["l"], row["c"]))
    for at_scope, header, callee, _count, line, column in record.get("callees", ()):
        if at_scope == -1 and header == -1 and METHOD_CALL.fullmatch(callee):
            unread.append((line, column))
    return DunderAll(names, min(unread) if unread else None)


class HandedOut:
    """The names one module hands out, in import order and then lazy-name order."""

    def __init__(self, scope: FileScope, table: ModuleTable | None, tables: Tables) -> None:
        self.scope = scope
        self.table = table
        self.tables = tables

    def has_functions(self) -> bool:
        """Whether the module body defines a function outside any block."""
        return any(d["s"] == -1 and d.get("f", 0) == 0 and d["k"] != "class" for d in self.scope.defs)

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
        return read_dunder_all(self.scope.record) if self.has_functions() else None

    def star_names(self, source: str) -> list[str] | None:
        """The names `from source import *` binds: the source's `__all__`, else its public
        top-level names; None when the source cannot be read."""
        table = self.tables.get(source)
        record = self.tables.record(source)
        if table is None or record is None or record.get("status") != "ok":
            return None
        listing = read_dunder_all(record)
        if listing.unread is not None:
            return None
        if listing.names is not None:
            return sorted(listing.names)
        return sorted(name for name in table.names if not name.startswith("_"))

    def star(self, source: str, no_functions: bool, listed: frozenset[str] | None) -> list[tuple[str, str, str]]:
        """(bound, target, clause) of each name a star import hands out. A source that cannot be
        read hands out `*`, a name nothing can follow."""
        if not no_functions and listed is None:
            return []
        clause = NO_FUNCTIONS_CLAUSE if no_functions else ALL_CLAUSE
        found = self.star_names(source)
        if found is None:
            return [("*", f"{source}.*", clause)]
        if listed is not None:
            found = [name for name in found if name in listed]
        return [(name, f"{source}.{name}", clause) for name in found]

    def names(self, listing: DunderAll | None) -> list[Handed]:
        scope = self.scope
        no_functions = listing is None
        listed = listing.names if listing is not None else None
        out: list[Handed] = []
        seen: set[tuple[str, str]] = set()
        for _, line, column, flags, target, level, name, asname in scope.record.get("imports", ()):
            if name is None or flags != 0:
                continue
            source = absolute_import(scope.module, scope.is_package, target, level)
            if source is None:
                continue
            if name == "*":
                handed = self.star(source, no_functions, listed)
            else:
                bound = asname or name
                handed = [(bound, f"{source}.{name}", self.clause(bound, name, asname, no_functions, listed))]
            for bound, target_name, clause in handed:
                if not clause or (bound, target_name) in seen:
                    continue
                seen.add((bound, target_name))
                out.append(Handed(bound, target_name, clause, line, column))
        lazy = self.table.lazy if self.table is not None else {}
        for name, module in lazy.items():
            key = (name, f"{module}.{name}")
            if key not in seen:
                seen.add(key)
                out.append(Handed(name, key[1], LAZY_CLAUSE, 0, 0))
        return out
