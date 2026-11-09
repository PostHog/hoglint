"""Names to definitions on top of the core resolver, keeping the re-export hops.

The core resolver knows classes and modules. The Django pack also needs functions (hooks,
receivers) and module-level values (signals), and the via chain for edges. Import the resolver
from hoglint.spike.core until hoglint.core lands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from hoglint.model import LANGUAGE_PYTHON, EntityId
from hoglint.spike.core import A_ALLOWED_FLAGS, Core, ModuleTable, absolute_import, qualname

MODULE_SCOPE = "<module>"


class SymbolKind(StrEnum):
    CLASS = "class"
    FUNCTION = "function"
    VALUE = "value"
    MODULE = "module"
    MEMBER = "member"


@dataclass(frozen=True)
class Symbol:
    kind: SymbolKind
    module: str
    name: str = ""
    via: tuple[EntityId, ...] = ()

    @property
    def qualified(self) -> str:
        return f"{self.module}.{self.name}" if self.name else self.module

    @property
    def entity(self) -> EntityId:
        return EntityId(LANGUAGE_PYTHON, self.module, self.name)


@dataclass(frozen=False)
class ModuleDefinitions:
    """Module-level functions and non-alias values of one module (module body and if/try bodies)."""

    functions: dict[str, int] = field(default_factory=dict)
    values: dict[str, object] = field(default_factory=dict)


def scope_name(defs: list[dict], idx: int) -> str:
    return MODULE_SCOPE if idx == -1 else qualname(defs, idx)


@dataclass(frozen=True)
class Binding:
    """One import-bound name: `from source import name` (name set) or `import source` (name None)."""

    source: str
    name: str | None
    flags: int


class FileBindings:
    """Import bindings of one file in any scope, function-local ones too; a later binding wins."""

    def __init__(self, record: dict, module: str, is_package: bool) -> None:
        self.names: dict[str, Binding] = {}
        for _, _, _, flags, mod, level, name, asname in record.get("imports", ()):
            if name is None:
                if asname:
                    self.names[asname] = Binding(mod, None, flags)
                else:
                    head = mod.split(".")[0]
                    self.names[head] = Binding(head, None, flags)
                continue
            if name == "*":
                continue
            source = absolute_import(module, is_package, mod, level)
            if source is None:
                continue
            self.names[asname or name] = Binding(source, name, flags)


class SymbolResolver:
    def __init__(self, core: Core) -> None:
        self.core = core
        self.tables = core.tables
        self.index = core.index
        self.definitions_cache: dict[str, ModuleDefinitions] = {}
        self.binding_cache: dict[tuple[str, str], Symbol | None] = {}

    def definitions(self, module: str) -> ModuleDefinitions:
        cached = self.definitions_cache.get(module)
        if cached is not None:
            return cached
        found = ModuleDefinitions()
        record = self.index.record(module) or {}
        for i, d in enumerate(record.get("defs", ())):
            if d["s"] == -1 and d["k"] != "class" and not d.get("f", 0) & ~A_ALLOWED_FLAGS:
                found.functions[d["n"]] = i
        for row in record.get("assigns", ()):
            if row["s"] != -1 or row["op"] not in ("=", ":") or "v" not in row or row.get("f", 0) & ~A_ALLOWED_FLAGS:
                continue
            for target in row["t"]:
                if isinstance(target, str) and "." not in target:
                    found.values[target] = row["v"]
        self.definitions_cache[module] = found
        return found

    def resolve_name(
        self, module: str, name: str, hops: tuple[EntityId, ...] = (), seen: frozenset[tuple[str, str]] = frozenset()
    ) -> Symbol | None:
        if (module, name) in seen:
            return None
        seen = seen | {(module, name)}
        table = self.tables.get(module)
        if table is None:
            return None
        if name in table.classes:
            return Symbol(SymbolKind.CLASS, module, name, hops)
        found = self.from_binding(table, name, hops, seen)
        if found is not None:
            return found
        definitions = self.definitions(module)
        if name in definitions.functions:
            return Symbol(SymbolKind.FUNCTION, module, name, hops)
        if name in definitions.values or name in table.constants:
            return Symbol(SymbolKind.VALUE, module, name, hops)
        for source in table.star_imports:
            found = self.resolve_name(source, name, hops, seen)
            if found is not None:
                return found
        if table.is_package and self.index.exists(f"{module}.{name}"):
            return Symbol(SymbolKind.MODULE, f"{module}.{name}", "", hops)
        return None

    def from_binding(
        self, table: ModuleTable, name: str, hops: tuple[EntityId, ...], seen: frozenset[tuple[str, str]]
    ) -> Symbol | None:
        binding = table.bindings.get(name)
        if binding is None:
            return None
        if binding[0] == "import":
            return Symbol(SymbolKind.MODULE, binding[1], "", hops)
        if binding[0] == "alias":
            return self.resolve_chain(table.name, binding[1], hops, seen)
        _, source, attr = binding
        hop = EntityId(LANGUAGE_PYTHON, source, attr)
        found = self.resolve_name(source, attr, (*hops, hop), seen)
        if found is not None:
            return found
        if self.index.exists(f"{source}.{attr}"):
            return Symbol(SymbolKind.MODULE, f"{source}.{attr}", "", (*hops, hop))
        return None

    def resolve_chain(
        self, module: str, chain: str, hops: tuple[EntityId, ...] = (), seen: frozenset[tuple[str, str]] = frozenset()
    ) -> Symbol | None:
        head, *rest = chain.split(".")
        found = self.resolve_name(module, head, hops, seen)
        for attr in rest:
            if found is None:
                return None
            if found.kind == SymbolKind.MODULE:
                found = self.resolve_name(found.module, attr, found.via, seen)
            elif found.kind == SymbolKind.CLASS:
                found = Symbol(SymbolKind.MEMBER, found.module, f"{found.name}.{attr}", found.via)
            else:
                return None
        return found

    def resolve_binding(self, binding: Binding) -> Symbol | None:
        key = (binding.source, binding.name or "")
        if key in self.binding_cache:
            return self.binding_cache[key]
        found: Symbol | None
        if binding.name is None:
            found = Symbol(SymbolKind.MODULE, binding.source) if self.index.exists(binding.source) else None
        else:
            hop = EntityId(LANGUAGE_PYTHON, binding.source, binding.name)
            found = self.resolve_name(binding.source, binding.name, (hop,))
            if found is None and self.index.exists(f"{binding.source}.{binding.name}"):
                found = Symbol(SymbolKind.MODULE, f"{binding.source}.{binding.name}", "", (hop,))
        self.binding_cache[key] = found
        return found

    def resolve_in_file(self, bindings: FileBindings, module: str, chain: str) -> Symbol | None:
        """A chain in one file: module-level names first, then imports in any scope of the file."""
        found = self.resolve_chain(module, chain)
        if found is not None:
            return found
        head, *rest = chain.split(".")
        binding = bindings.names.get(head)
        if binding is None:
            return None
        found = self.resolve_binding(binding)
        for attr in rest:
            if found is None or found.kind != SymbolKind.MODULE:
                return None
            found = self.resolve_name(found.module, attr, found.via)
        return found

    def resolve_qualified(self, qualified: str) -> Symbol | None:
        if self.index.exists(qualified):
            return Symbol(SymbolKind.MODULE, qualified)
        module, _, name = qualified.rpartition(".")
        if not module:
            return None
        found = self.resolve_qualified(module)
        if found is None or found.kind != SymbolKind.MODULE:
            return None
        return self.resolve_name(found.module, name)

    def value_of(self, symbol: Symbol) -> object:
        """The V value assigned to a module-level value symbol, or None."""
        if symbol.kind != SymbolKind.VALUE:
            return None
        return self.definitions(symbol.module).values.get(symbol.name)

    @staticmethod
    def surface_and_via(symbol: Symbol) -> tuple[str, tuple[EntityId, ...]]:
        """The first hop is the name the caller imported; the hops after it, up to the origin, are via."""
        if not symbol.via:
            return symbol.qualified, ()
        surface = str(symbol.via[0])
        middle = tuple(hop for hop in symbol.via[1:] if hop != symbol.entity)
        return surface, middle


class FileNames:
    """Name lookup inside one file: its module, its import bindings and the resolver."""

    def __init__(self, symbols: SymbolResolver, record: dict, module: str, is_package: bool) -> None:
        self.symbols = symbols
        self.record = record
        self.module = module
        self.bindings = FileBindings(record, module, is_package)

    def resolve(self, chain: str) -> Symbol | None:
        return self.symbols.resolve_in_file(self.bindings, self.module, chain)
