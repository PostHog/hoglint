"""What a contract surface hands out, by the rule for public surfaces.

A module hands out a name it binds by import when one of these clauses holds:

- `__all__` lists it;
- the import binds it under its own name (`from x import f as f`);
- it is a lazy (PEP 562) name;
- the module has no top-level function, so every import is there to be handed on, unless the
  module's own code refers to the name (a decorator, a base, an annotation, a call, ...).

An import in any block of the module body counts (`try`, `except`, `if`, `else`, `with`), as it
binds the name at run time; one under `if TYPE_CHECKING:` does not.

A star import hands out the names it binds (the source's `__all__`, else its public top-level
names with those of its own star imports, never a `__future__` name) under the same clauses; in an
export module each name it binds is an import edge of its own. A source that cannot be read hands
out `*`, which nothing follows: the export rule reports it as an import that did not resolve.
`import a.b as name` binds the module `a.b` under `name` and is handed out by the same clauses; a
plain `import a.b` binds `a`, no surface of its own, and hands out nothing. A relative import that
climbs above the top package resolves to nothing and still binds its name: handed out, it leaves
what the module hands out unresolved.

Every rule reads this notion of what a module exports: a surface also imports the code it calls,
and that code is not part of its contract. Core records it for every export module
(`Graph.hand_outs`).

`__all__` is read in every form whose names a reader can list (`ListedNames`): `__all__ = [...]`,
annotated or not, `+=` with a literal, a sum of literals, and copies of a module-level literal
such as `sorted(_LAZY)`. One built by other code, changed by a method or set under a condition
cannot be read: what the module hands out is then unknown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from hoglint.core.records import sequence_items, str_literal
from hoglint.core.sites import FileScope
from hoglint.core.symbols import RUN_TIME_MODULE_FLAGS, ModuleTable, Tables, import_surface, module_level
from hoglint.model import HandOut

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
            items = sequence_items(value)
        if items is None:
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
        items = sequence_items(value)
        if items is None:
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


class StarNames:
    """The names `from source import *` binds: the source's `__all__`, else its public top-level
    names with those of its own star imports; never a name a `from __future__ import` binds. None
    when the source, or a source it star-imports, cannot be read. A star import that comes back
    to a module already read adds nothing more."""

    FUTURE = "__future__"

    def __init__(self, tables: Tables) -> None:
        self.tables = tables

    def of(self, source: str) -> list[str] | None:
        found = self.bound(source, set())
        return sorted(found) if found is not None else None

    def from_future(self, source: str, name: str) -> bool:
        binding = self.tables.binding(source, name)
        return binding is not None and binding[0] == "from" and binding[1] == self.FUTURE

    def bound(self, source: str, seen: set[str]) -> set[str] | None:
        if source in seen:
            return set()
        seen.add(source)
        table = self.tables.get(source)
        record = self.tables.record(source)
        if table is None or record is None or record.get("status") != "ok":
            return None
        listing = read_dunder_all(record)
        if listing.unread is not None:
            return None
        if listing.names is not None:
            return {name for name in listing.names if not self.from_future(source, name)}
        names = {name for name in table.names if not name.startswith("_") and not self.from_future(source, name)}
        for star in table.star_imports:
            found = self.bound(star, seen)
            if found is None:
                return None
            names.update(name for name in found if not name.startswith("_"))
        return names


def binds_at_run_time(scope: int, flags: int) -> bool:
    """An import of the module body that runs at run time, in whatever block it sits: one under
    `if TYPE_CHECKING:` binds nothing at run time, and one in a function or class binds no
    module name."""
    return module_level(scope, flags, RUN_TIME_MODULE_FLAGS)


class HandedOut:
    """The names one module hands out, in import order and then lazy-name order.

    `no_functions`: the module body defines no function outside any block, so it hands out every
    import it does not use itself, whatever `__all__` says."""

    def __init__(self, scope: FileScope, table: ModuleTable | None, tables: Tables) -> None:
        self.scope = scope
        self.table = table
        self.tables = tables
        self.no_functions = not any(d["s"] == -1 and d.get("f", 0) == 0 and d["k"] != "class" for d in scope.defs)
        self.dunder_all = read_dunder_all(scope.record)

    def clause(self, bound: str, name: str, asname: str | None, kept: bool) -> str:
        """`kept`: the import is one a module with no function keeps only to hand it on."""
        if kept:
            return NO_FUNCTIONS_CLAUSE
        listed = self.dunder_all.names
        if listed is not None and bound in listed:
            return ALL_CLAUSE
        if asname == name:
            return ALIAS_CLAUSE
        return ""

    def star(self, source: str | None, surface: str) -> list[tuple[str, str, str]]:
        """(bound, target, clause) of each name a star import hands out. A source that cannot be
        read, or a relative one above the top package (`source` None), hands out `*` at the
        import's `surface`: a name nothing can follow."""
        listed = None if self.no_functions else self.dunder_all.names
        if not self.no_functions and listed is None:
            return []
        clause = NO_FUNCTIONS_CLAUSE if self.no_functions else ALL_CLAUSE
        found = StarNames(self.tables).of(source) if source is not None else None
        if found is None:
            return [("*", surface, clause)]
        if listed is not None:
            found = [name for name in found if name in listed]
        return [(name, f"{source}.{name}", clause) for name in found]

    def single(self, target: str, name: str, asname: str | None, used: frozenset[str]) -> tuple[str, str, str]:
        """(bound, target, clause) of an import that binds one name; `used` as in `names`."""
        bound = asname or name
        kept = self.no_functions and bound not in used
        return bound, target, self.clause(bound, name, asname, kept)

    def import_names(self, row: list, used: frozenset[str]) -> list[tuple[str, str, str]]:
        """(bound, target, clause) of each name one import of the module body binds. `import a.b`
        binds `a`, no surface of its own, so it hands out nothing; `import a.b as name` binds the
        module. A relative import that climbs above the top package resolves to nothing and still
        binds its name."""
        name, asname = row[6], row[7]
        if name is None and not asname:
            return []
        _, surface, source = import_surface(self.scope.module, self.scope.is_package, row)
        if name == "*":
            return self.star(source, surface)
        return [self.single(surface, surface if name is None else name, asname, used)]

    def names(self, used: frozenset[str]) -> list[Handed]:
        """`used`: the names the module's own code refers to. In a module with no function such
        a name is no import kept only to be handed on, so another clause has to hand it out."""
        out: list[Handed] = []
        seen: set[tuple[str, str]] = set()
        for row in self.scope.record.get("imports", ()):
            scope_id, line, column, flags = row[:4]
            if not binds_at_run_time(scope_id, flags):
                continue
            for bound, target_name, clause in self.import_names(row, used):
                if not clause or (bound, target_name) in seen:
                    continue
                seen.add((bound, target_name))
                out.append(Handed(bound, target_name, clause, line, column))
        entries = self.table.lazy_entries if self.table is not None else []
        for entry in entries:
            key = (entry.name, f"{entry.module}.{entry.name}")
            if key not in seen:
                seen.add(key)
                out.append(Handed(entry.name, key[1], LAZY_CLAUSE, entry.line, entry.column))
        return out

    def hand_out(self, used: frozenset[str]) -> HandOut:
        """The names the module hands out and the ones it binds by import and keeps to itself.
        `used` as in `names`."""
        handed = frozenset(item.bound for item in self.names(used))
        bound: set[str] = set()
        for row in self.scope.record.get("imports", ()):
            scope_id, _, _, flags, _, _, name, asname = row
            if not binds_at_run_time(scope_id, flags):
                continue
            if name != "*":
                if asname or name:
                    bound.add(asname or name)
                continue
            source = import_surface(self.scope.module, self.scope.is_package, row)[2]
            found = StarNames(self.tables).of(source) if source is not None else None
            bound.update(found or ())
        unread = not self.no_functions and self.dunder_all.unread is not None
        return HandOut(handed, frozenset(bound - handed), unread)
