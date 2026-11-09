"""Readers for L1 fact values and rows that every producer shares. No framework names."""

from __future__ import annotations

import json

from hoglint.core.symbols import CLASS, FUNC

NO_CONST = object()  # const_of() of a value that is not a plain constant
CONST_MARKERS = frozenset({"float", "complex", "bytes", "...", "int", "longstr", "badstr"})


def const_of(value: object) -> object:
    """The Python constant of a V constant (str, int, bool, None), else NO_CONST."""
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, dict) and "s" in value and len(value) == 1:
        return value["s"]
    return NO_CONST


def is_const(value: object) -> bool:
    """True for every constant form in V, the ones const_of cannot spell (floats, bytes, ...) too."""
    if value is None or isinstance(value, (bool, int)):
        return True
    if isinstance(value, dict):
        return "s" in value or value.get("o") in CONST_MARKERS
    return False


def str_literal(value: object) -> str | None:
    """The text of a plain string constant, else None."""
    found = const_of(value)
    return found if isinstance(found, str) else None


def sequence_items(value: object) -> list[object] | None:
    """Elements of a list, tuple or set literal, else None."""
    if not isinstance(value, dict):
        return None
    for key in ("list", "tuple", "set"):
        items = value.get(key)
        if isinstance(items, list):
            return items
    return None


def value_text(value: object) -> str:
    """Display text for a V value: a chain as written, a string quoted, an opaque node as <Kind>."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and len(value) == 1 and "s" in value:
        return repr(value["s"])
    if isinstance(value, dict) and len(value) == 1 and "o" in value:
        return f"<{value['o']}>"
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def last_name(callee: str) -> str | None:
    """The last dotted part of a callee text; None for a callee that is a bare expression (`?`)."""
    if callee == "?":
        return None
    return callee.rpartition(".")[2]


def chain_last(value: object) -> str | None:
    """The last name of a chain or attribute value, the callee of a call value."""
    if isinstance(value, dict) and "call" in value:
        value = value["call"]
    if isinstance(value, str):
        return value.rpartition(".")[2]
    if isinstance(value, dict) and "a" in value:
        return value["a"]
    return None


class RecordBodies:
    """Statements directly in each def body of one record (not under an if, try or loop there).

    Index -1 is the module body. Built once per record, on first use.
    """

    def __init__(self, record: dict) -> None:
        self.record = record
        self.defs: list[dict] = record.get("defs", [])
        self.assigns_by: dict[int, list[dict]] | None = None
        self.children_by: dict[int, list[int]] | None = None

    def body_flags(self, idx: int) -> int:
        if idx == -1:
            return 0
        d = self.defs[idx]
        return d.get("f", 0) | (CLASS if d["k"] == "class" else FUNC)

    def build(self) -> None:
        self.assigns_by = {}
        self.children_by = {}
        for row in self.record.get("assigns", ()):
            scope = row["s"]
            if row.get("f", 0) == self.body_flags(scope):
                self.assigns_by.setdefault(scope, []).append(row)
        for i, d in enumerate(self.defs):
            if d.get("f", 0) == self.body_flags(d["s"]):
                self.children_by.setdefault(d["s"], []).append(i)

    def assigns(self, idx: int) -> list[dict]:
        if self.assigns_by is None:
            self.build()
        assert self.assigns_by is not None
        return self.assigns_by.get(idx, [])

    def children(self, idx: int) -> list[int]:
        if self.children_by is None:
            self.build()
        assert self.children_by is not None
        return self.children_by.get(idx, [])

    def constants(self, idx: int) -> dict[str, object]:
        """`name = <constant>` and `name: T = <constant>` directly in one body."""
        out: dict[str, object] = {}
        for row in self.assigns(idx):
            if row["op"] not in ("=", ":") or "v" not in row or not is_const(row["v"]):
                continue
            value = const_of(row["v"])
            if value is NO_CONST:
                continue
            for target in row["t"]:
                if isinstance(target, str) and "." not in target:
                    out[target] = value
        return out
