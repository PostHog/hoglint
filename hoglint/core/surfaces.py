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
from hoglint.core.symbols import (
    RUN_TIME_MODULE_FLAGS,
    Hit,
    Resolver,
    Tables,
    entity,
    import_surface,
    is_module_assign,
    module_assigns,
    module_level,
)
from hoglint.model import EntityId, HandOut

ALL_CLAUSE = "all"
ALIAS_CLAUSE = "alias"
LAZY_CLAUSE = "lazy"
ASSIGNED_CLAUSE = "assigned"
NO_FUNCTIONS_CLAUSE = "no-functions"
# The markers of a number the value tree does not spell out, and of a long or odd string.
LITERAL_MARKERS = frozenset({"int", "float", "complex", "longstr", "badstr"})
# The kinds of binding that are a def or an import: an edge or a symbol says what they bind.
BOUND_KINDS = frozenset({"class", "def", "module", "from"})
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


def is_literal(value: object) -> bool:
    """A number, a string, None, a bool, or a tuple, list, set or dict of those."""
    if value is None or isinstance(value, (bool, int)):
        return True
    if not isinstance(value, dict):
        return False
    if set(value) == {"s"}:
        return True
    if set(value) == {"o"}:
        return value["o"] in LITERAL_MARKERS
    if "dict" in value:
        return all(key is not None and is_literal(key) and is_literal(item) for key, item in value["dict"])
    items = sequence_items(value)
    return items is not None and all(is_literal(item) for item in items)


def binds_literal(record: dict, name: str) -> bool:
    """Whether the last module-level assignment of a bare name binds a literal constant. The
    record leaves out a value that is None."""
    rows = [row for row in record.get("assigns", ()) if is_module_assign(row) and name in row["t"]]
    if not rows:
        return False
    if "v" not in rows[-1]:
        return rows[-1]["op"] == "="
    return is_literal(rows[-1]["v"])


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
    """The names one module hands out, in import order, then lazy-name order, then the order of
    the assignments that bind a name again.

    `no_functions`: the module body defines no function outside any block, so it hands out every
    import it does not use itself, whatever `__all__` says."""

    def __init__(self, scope: FileScope, resolver: Resolver) -> None:
        self.scope = scope
        self.resolver = resolver
        self.tables = resolver.tables
        self.table = resolver.tables.get(scope.module)
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
        out.extend(item for item, _ in self.assigned())
        return out

    def nested_class(self, outer: EntityId, attrs: list[str]) -> EntityId | None:
        """The class `attrs` name inside the body of a top-level class, one level per name, the
        last def of a name winning; None when a name is no class declared directly in the class
        above it."""
        binding = self.tables.binding(outer.module, outer.symbol)
        path = self.tables.binding_path(outer.module, outer.symbol)
        if binding is None or binding[0] != "class" or path is None:
            return None
        record = self.tables.store.records.get(path)
        if record is None:
            return None
        defs = record.get("defs", ())
        at: int = binding[1]
        symbol = outer.symbol
        for attr in attrs:
            found = [i for i, node in enumerate(defs) if node["s"] == at and node["n"] == attr]
            if not found or defs[found[-1]]["k"] != "class":
                return None
            at = found[-1]
            symbol = f"{symbol}.{attr}"
        return entity(outer.module, symbol)

    def assigned_hit(self, name: str, chain: str) -> Hit | None:
        """What `name = chain` binds, when the chain resolves, whole, to a class or a function: a
        class nested in a class counts, a member past a class does not."""
        hit, rest = self.resolver.resolve_chain(self.scope.module, chain)
        if hit is None or hit.kind not in ("class", "def") or (rest and hit.kind != "class"):
            return None
        if rest:
            nested = self.nested_class(hit.origin, rest)
            if nested is None:
                return None
            hit = Hit((*hit.chain[:-1], nested), "class")
        return hit.behind(entity(self.scope.module, name))

    def assigned(self) -> list[tuple[Handed, Hit]]:
        """The names the module binds again by a top-level assignment of one bare name to a name
        or an attribute chain that resolves, whole, to a class or a function, each with what it
        binds: handed out by the clause of a definition, when the name is public or `__all__`
        lists it. Only the last assignment of a name counts, as the module table reads it."""
        if self.table is None:
            return []
        listed = self.dunder_all.names or frozenset()
        out: list[tuple[Handed, Hit]] = []
        for name, row in module_assigns(self.scope.record).items():
            value = row["v"]
            if len(row["t"]) != 1 or not isinstance(value, str):
                continue
            if self.table.names.get(name) != ("alias", value):
                continue
            if name.startswith("_") and name not in listed:
                continue
            hit = self.assigned_hit(name, value)
            if hit is not None:
                out.append((Handed(name, str(hit.origin), ASSIGNED_CLAUSE, row["l"], row["c"]), hit))
        return sorted(out, key=lambda pair: (pair[0].line, pair[0].column, pair[0].bound))

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
        return HandOut(
            handed,
            frozenset(bound - handed),
            unread,
            self.unaccounted(handed),
            self.listed_private(),
            self.unknown_values(used),
        )

    def unknown_values(self, used: frozenset[str]) -> frozenset[str]:
        """The first-party module-level assignments the handed-out names resolve to, as symbols,
        when the value is no literal constant: a `NewType`, a `TypeVar`, a type alias, a value a
        call makes. What they hand out is unknown."""
        out: set[str] = set()
        for item in self.names(used):
            hit, rest = self.resolver.resolve_dotted(item.target)
            if hit is None or rest or hit.kind != "variable":
                continue
            origin = hit.origin
            if not origin.symbol or self.tables.index.is_external(origin.module):
                continue
            record = self.tables.record(origin.module)
            if record is not None and binds_literal(record, origin.symbol):
                continue
            out.add(str(origin))
        return frozenset(out)

    def listed(self) -> frozenset[str]:
        """The names a readable `__all__` lists; none when it cannot be read or is not there."""
        names = self.dunder_all.names
        return names if names is not None and self.dunder_all.unread is None else frozenset()

    def listed_private(self) -> frozenset[str]:
        """The names with a leading `_` a readable `__all__` lists that the module defines by a
        top-level def or class: handed out as a public def is."""
        if self.table is None:
            return frozenset()
        return frozenset(
            name
            for name in self.listed()
            if name.startswith("_") and self.table.names.get(name, ("",))[0] in ("class", "def")
        )

    def unaccounted(self, handed: frozenset[str]) -> frozenset[str]:
        """The names a readable `__all__` lists that the module binds by no def, no import, no
        other edge of what it hands out and no literal constant: what they hand out is unknown."""
        out: set[str] = set()
        for name in self.listed() - handed:
            binding = self.table.names.get(name) if self.table is not None else None
            if binding is not None and binding[0] in BOUND_KINDS:
                continue
            if binding is not None and binds_literal(self.scope.record, name):
                continue
            out.add(name)
        return frozenset(out)
