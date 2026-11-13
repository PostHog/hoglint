"""Module symbol tables and the records they read: bindings, hits and the helpers that read
import and assign rows. A resolution keeps the whole chain from the name the code used to the
origin, so edges can keep surface, via and origin apart.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from hoglint.core.modules import ModuleIndex
from hoglint.model import LANGUAGE_PYTHON, EntityId

if TYPE_CHECKING:
    from hoglint.native.build import NativeStore

# Statement context flags; the extractor's `flags` module (src/facts.rs, schema.md section 3) is the source.
TYPE_ONLY = 1
TC_ELSE = 2
FUNC = 4
CLASS = 8
TRY = 16
IF = 32
BLOCK = 64

# Module-level statements: module body plus if, try, with, loop and TYPE_CHECKING bodies.
MODULE_LEVEL_FLAGS = TYPE_ONLY | TC_ELSE | IF | TRY | BLOCK
# Module-level statements that run at run time: every block but the `if TYPE_CHECKING:` body.
RUN_TIME_MODULE_FLAGS = TC_ELSE | IF | TRY | BLOCK
# Statements that may not run, or run instead of another branch.
CONDITIONAL_FLAGS = TYPE_ONLY | TC_ELSE | IF | TRY

# A name as a lazy map writes it: an ASCII identifier.
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
MODULE_ATTRIBUTES = frozenset(
    {"__doc__", "__file__", "__name__", "__path__", "__spec__", "__loader__", "__package__", "__dict__", "__all__"}
)


def module_level(scope: int, flags: int, allowed: int) -> bool:
    """Whether a row sits in the module body, in no block but the ones the `allowed` flags name."""
    return scope == -1 and not flags & ~allowed


def is_module_assign(row: dict) -> bool:
    """Whether an assigns row binds module-level names: `=`, `:` or `type` at module level."""
    return module_level(row["s"], row.get("f", 0), MODULE_LEVEL_FLAGS) and row["op"] in ("=", ":", "type")


def module_assigns(record: Mapping[str, Any] | None) -> dict[str, dict]:
    """name -> row of the last module-level assignment with a value of each bare name, in one
    record: the value a module table reads for the name."""
    rows: dict[str, dict] = {}
    if record is None or record.get("status") != "ok":
        return rows
    for row in record.get("assigns", ()):
        if not is_module_assign(row) or "v" not in row:
            continue
        for target in row["t"]:
            if isinstance(target, str) and "." not in target:
                rows[target] = row
    return rows


# Binding forms: ("class", def index, marker) | ("def", def index, marker) | ("variable",) | ("module", dotted)
#   | ("from", source module, attribute) | ("alias", dotted chain)
Binding = tuple


def is_module_path(text: str) -> bool:
    """Dotted ASCII identifiers, as a lazy map writes a module path."""
    return all(IDENTIFIER.fullmatch(part) for part in text.split("."))


def absolute_import(module: str, is_package: bool, target: str | None, level: int) -> str | None:
    """Absolute module named by `from <level dots><target> import ...` inside `module`."""
    if level == 0:
        return target
    package = module if is_package else module.rpartition(".")[0]
    parts = package.split(".") if package else []
    climb = level - 1
    if climb >= len(parts):
        return None
    if climb:
        parts = parts[: len(parts) - climb]
    base = ".".join(parts)
    return f"{base}.{target}" if target else base


def unresolved_surface(target: str | None, level: int, name: str) -> str:
    """How a relative import that climbs above the top package is written: `...pkg.name`."""
    return "." * level + (f"{target}." if target else "") + name


def import_binding(module: str, is_package: bool, row: list) -> tuple[str, Binding] | None:
    """(bound name, binding) for one `imports` row; None for a star import or a bad relative one."""
    _, _, _, _, target, level, name, asname = row
    if name is None:
        if asname:
            return asname, ("module", target)
        head = target.split(".")[0]
        return head, ("module", head)
    if name == "*":
        return None
    source = absolute_import(module, is_package, target, level)
    if source is None:
        return None
    return asname or name, ("from", source, name)


def import_surface(module: str, is_package: bool, row: list) -> tuple[str, str, str | None]:
    """(bound name, surface, source) of one `imports` row. `import a.b` binds `a` (or its alias)
    and has `a.b` as surface and source. A from import's surface is `source.name`, a star's
    `source.*`; above the top package the source is None and the surface is the text as written."""
    _, _, _, _, target, level, name, asname = row
    if name is None:
        return asname or target.split(".")[0], target, target
    source = absolute_import(module, is_package, target, level)
    surface = f"{source}.{name}" if source is not None else unresolved_surface(target, level, name)
    return asname or name, surface, source


def unpacked_names(target: object) -> list[str]:
    """Plain names bound by a tuple or list assignment target, nested ones included."""
    if isinstance(target, str):
        return [target] if "." not in target else []
    if not isinstance(target, dict):
        return []
    items = target.get("tuple", target.get("list", ()))
    if "star" in target:
        items = [target["star"]]
    return [name for item in items for name in unpacked_names(item)]


def entity(module: str, symbol: str = "") -> EntityId:
    return EntityId(LANGUAGE_PYTHON, module, symbol)


@dataclass(frozen=True, slots=True)
class Hit:
    """A resolved name: `chain` runs from the name the code used to the origin."""

    chain: tuple[EntityId, ...]
    kind: str  # class | def | variable | module

    @property
    def origin(self) -> EntityId:
        return self.chain[-1]

    @property
    def via(self) -> tuple[EntityId, ...]:
        return self.chain[1:-1]

    def behind(self, step: EntityId) -> Hit:
        if self.chain and self.chain[0] == step:
            return self
        return Hit((step, *self.chain), self.kind)


@dataclass(frozen=True, slots=True)
class LazyEntry:
    """One name of a PEP 562 lazy map, at the module-level dict assignment it is read from.
    `module` is the first path the map gives it (a relative one anchored) when no module of the
    tree answers for it."""

    name: str
    module: str
    line: int
    column: int


@dataclass(frozen=False)
class ModuleTable:
    """Module-level names of one module, later bindings winning."""

    name: str
    path: str | None
    is_package: bool
    names: dict[str, Binding] = field(default_factory=dict)
    star_imports: list[str] = field(default_factory=list)
    lazy: dict[str, str] = field(default_factory=dict)  # name -> module, from a PEP 562 lazy map
    # The lazy map's entries: those a module answers for in map order, then the others.
    lazy_entries: list[LazyEntry] = field(default_factory=list)
    values: dict[str, object] = field(default_factory=dict)  # name -> V value of its last module-level assign
    stub_names: set[str] = field(default_factory=set)  # names only the `.pyi` beside the module binds
    # name -> every module-level binding of the `.py` in source order: (flags, binding, the
    # assigns row that binds it). Lookups never read it; a base or a type alias bound once per
    # branch is read through it.
    bindings: dict[str, list[tuple[int, Binding, dict | None]]] = field(default_factory=dict)

    def classes(self) -> dict[str, int]:
        """Classes the module's own source defines at module level: name -> def index."""
        return {name: binding[1] for name, binding in self.names.items() if binding[0] == "class" and not binding[2]}


class Tables:
    """Lazily built ModuleTables over a ModuleIndex; a subclass says how one is built."""

    def __init__(self, store: NativeStore, index: ModuleIndex) -> None:
        self.store = store
        self.index = index
        self.cache: dict[str, ModuleTable | None] = {}

    def record(self, module: str) -> dict | None:
        path = self.index.path_of.get(module)
        return self.store.records.get(path) if path is not None else None

    def stub_record(self, module: str) -> dict | None:
        path = self.index.stub_of.get(module)
        return self.store.records.get(path) if path is not None else None

    def build(self, module: str) -> ModuleTable | None:
        raise NotImplementedError

    def get(self, module: str) -> ModuleTable | None:
        if module not in self.cache:
            self.cache[module] = self.build(module)
        return self.cache[module]

    def binding(self, module: str, name: str) -> Binding | None:
        table = self.get(module)
        return table.names.get(name) if table is not None else None

    def binding_path(self, module: str, name: str) -> str | None:
        """The file a module table reads a name's binding from: the `.pyi` for a name only the
        stub binds, else the module's own file."""
        table = self.get(module)
        if table is None:
            return None
        return (self.index.stub_of if name in table.stub_names else self.index.path_of).get(module)
