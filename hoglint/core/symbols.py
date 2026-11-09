"""Module symbol tables and name resolution that follows re-exports.

Reads L1 records only. A resolution keeps the whole chain from the name the code used to the
origin, so edges can keep surface, via and origin apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hoglint.core.modules import ModuleIndex
from hoglint.facts import FactStore
from hoglint.model import LANGUAGE_PYTHON, EntityId

TYPE_ONLY = 1
TC_ELSE = 2
FUNC = 4
CLASS = 8
TRY = 16
IF = 32
BLOCK = 64

# Module-level statements: module body plus if, try, with, loop and TYPE_CHECKING bodies.
MODULE_LEVEL_FLAGS = TYPE_ONLY | TC_ELSE | IF | TRY | BLOCK
# Statements that may not run, or run instead of another branch.
CONDITIONAL_FLAGS = TYPE_ONLY | TC_ELSE | IF | TRY

MODULE_ATTRIBUTES = frozenset(
    {"__doc__", "__file__", "__name__", "__path__", "__spec__", "__loader__", "__package__", "__dict__", "__all__"}
)

# Binding forms: ("class", def index, marker) | ("def", def index, marker) | ("variable",) | ("module", dotted)
#   | ("from", source module, attribute) | ("alias", dotted chain)
Binding = tuple


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


@dataclass(frozen=False)
class ModuleTable:
    """Module-level names of one module, later bindings winning."""

    name: str
    path: str | None
    is_package: bool
    names: dict[str, Binding] = field(default_factory=dict)
    star_imports: list[str] = field(default_factory=list)
    lazy: dict[str, str] = field(default_factory=dict)  # name -> module, from a PEP 562 lazy map


class Tables:
    """Lazily built ModuleTables over a ModuleIndex."""

    def __init__(self, store: FactStore, index: ModuleIndex) -> None:
        self.store = store
        self.index = index
        self.cache: dict[str, ModuleTable | None] = {}

    def record(self, module: str) -> dict | None:
        path = self.index.path_of.get(module)
        return self.store.records.get(path) if path is not None else None

    def stub_record(self, module: str) -> dict | None:
        path = self.index.stub_of.get(module)
        return self.store.records.get(path) if path is not None else None

    def collect(self, table: ModuleTable, record: dict, marker: str) -> None:
        """Later bindings win, except that a conditional def or assignment never replaces an
        import: `except ImportError: Bar = None` and `else: Foo = Any` are fallbacks."""
        module = table.name
        events: list[tuple[int, int, int, int, Binding | None, str, object]] = []
        for i, d in enumerate(record.get("defs", ())):
            flags = d.get("f", 0)
            if d["s"] == -1 and not flags & ~MODULE_LEVEL_FLAGS:
                kind = "class" if d["k"] == "class" else "def"
                events.append((d["l"], d["c"], 0, flags, (kind, i, marker), d["n"], None))
        for row in record.get("imports", ()):
            if row[0] != -1 or row[3] & ~MODULE_LEVEL_FLAGS:
                continue
            if row[6] == "*":
                source = absolute_import(module, table.is_package, row[4], row[5])
                events.append((row[1], row[2], 1, row[3], None, "*", source))
                continue
            bound = import_binding(module, table.is_package, row)
            if bound is not None:
                events.append((row[1], row[2], 1, row[3], bound[1], bound[0], None))
        for row in record.get("assigns", ()):
            flags = row.get("f", 0)
            if row["s"] != -1 or flags & ~MODULE_LEVEL_FLAGS or row["op"] not in ("=", ":", "type"):
                continue
            value = row.get("v")
            for target in row["t"]:
                if isinstance(target, str) and "." not in target:
                    assigned: Binding = ("alias", value) if isinstance(value, str) else ("variable",)
                    events.append((row["l"], row["c"], 2, flags, assigned, target, None))
                    continue
                for name in unpacked_names(target):
                    events.append((row["l"], row["c"], 2, flags, ("variable",), name, None))
        events.sort(key=lambda e: (e[0], e[1], e[2]))
        for _, _, _, flags, binding, name, source in events:
            if binding is None:
                if source:
                    table.star_imports.append(str(source))
                continue
            current = table.names.get(name)
            is_import = binding[0] in ("module", "from")
            if not is_import and flags & CONDITIONAL_FLAGS and current is not None and current[0] in ("module", "from"):
                continue
            table.names[name] = binding

    def lazy_map(self, module: str, is_package: bool, record: dict) -> dict[str, str]:
        """PEP 562 lazy exports: module-level dicts of name -> module string, in a module that
        defines `__getattr__`. A value is the tail of a module-level string prefix
        (`_BASE + value`), an absolute module, or a module under the package or one of its
        ancestors (nearest first), in that order."""
        package = module if is_package else module.rpartition(".")[0]
        parts = package.split(".")
        ancestors = [".".join(parts[:end]) for end in range(len(parts), 0, -1)]
        prefixes: list[str] = []
        maps: list[list] = []
        for row in record.get("assigns", ()):
            if row["s"] != -1 or "v" not in row:
                continue
            value = row["v"]
            if isinstance(value, dict) and set(value) == {"s"} and value["s"].endswith("."):
                prefixes.append(value["s"])
            elif isinstance(value, dict) and "dict" in value:
                maps.append(value["dict"])
        out: dict[str, str] = {}
        for items in maps:
            for key, value in items:
                if not (isinstance(key, dict) and "s" in key and isinstance(value, dict) and "s" in value):
                    continue
                relative = [f"{ancestor}.{value['s']}" for ancestor in ancestors]
                for candidate in (*(p + value["s"] for p in prefixes), value["s"], *relative):
                    if self.index.exists(candidate):
                        out[key["s"]] = candidate
                        break
        return out

    def build(self, module: str) -> ModuleTable | None:
        """The module's own names; a `.pyi` beside the `.py` adds the names the source leaves
        dynamic (generated protobuf modules, for example). Stub defs carry a "stub" marker."""
        if not self.index.exists(module):
            return None
        table = ModuleTable(module, self.index.path_of.get(module), self.index.is_package(module))
        record = self.record(module)
        if record is not None and record.get("status") == "ok":
            self.collect(table, record, "")
            if table.names.get("__getattr__", ("",))[0] == "def":
                table.lazy = self.lazy_map(module, table.is_package, record)
        stub = self.stub_record(module)
        if stub is not None and stub.get("status") == "ok":
            stub_table = ModuleTable(module, self.index.stub_of[module], table.is_package)
            self.collect(stub_table, stub, "stub")
            for name, binding in stub_table.names.items():
                table.names.setdefault(name, binding)
        return table

    def get(self, module: str) -> ModuleTable | None:
        if module not in self.cache:
            self.cache[module] = self.build(module)
        return self.cache[module]


class Resolver:
    """Names and dotted chains to symbols or modules, following re-exports."""

    def __init__(self, tables: Tables) -> None:
        self.tables = tables
        self.index = tables.index
        self.name_cache: dict[tuple[str, str], Hit | None] = {}
        self.in_progress: set[tuple[str, str]] = set()
        self.from_cache: dict[tuple[str, str], Hit | None] = {}
        self.met_cycle = False

    def module_hit(self, module: str) -> Hit:
        return Hit((entity(module),), "module")

    def resolve_name(self, module: str, name: str) -> Hit | None:
        """A re-export cycle reads as unresolved; results that met a cycle are not cached."""
        key = (module, name)
        if key in self.name_cache:
            return self.name_cache[key]
        if key in self.in_progress:
            self.met_cycle = True
            return None
        outer = self.met_cycle
        self.met_cycle = False
        self.in_progress.add(key)
        hit = self.lookup(module, name)
        self.in_progress.discard(key)
        if not self.met_cycle:
            self.name_cache[key] = hit
        self.met_cycle = outer or self.met_cycle
        return hit

    def resolve_from(self, source: str, attr: str) -> Hit | None:
        if not self.index.exists(source):
            return Hit((entity(f"{source}.{attr}"),), "missing")
        found = self.resolve_name(source, attr)
        if found is not None:
            return found
        if self.index.exists(f"{source}.{attr}"):
            return self.module_hit(f"{source}.{attr}")
        return None

    def descend(self, hit: Hit | None, attrs: list[str]) -> tuple[Hit | None, list[str]]:
        """Follow attributes through modules. Inside a module with no sources every attribute is
        part of one dotted name, and the hit becomes `missing`."""
        rest = list(attrs)
        while hit is not None and hit.kind == "module" and rest:
            module = hit.origin.module
            if not self.index.exists(module):
                return Hit((*hit.chain, entity(".".join([module, *rest]))), "missing"), []
            nxt = self.resolve_name(module, rest[0])
            if nxt is None:
                break
            hit = nxt
            rest.pop(0)
        return hit, rest

    def resolve_chain(self, module: str, chain: str) -> tuple[Hit | None, list[str]]:
        """Resolve `a.b.c` from `module`; returns the deepest hit and the attributes left over."""
        head, *rest = chain.split(".")
        return self.descend(self.resolve_name(module, head), rest)

    def resolve_binding(self, module: str, binding: Binding) -> Hit | None:
        """Resolve one binding as seen from `module`. Bindings to unknown modules still hit."""
        if binding[0] == "from":
            key = (binding[1], binding[2])
            if key in self.from_cache:
                return self.from_cache[key]
            outer = self.met_cycle
            self.met_cycle = False
            hit = self.resolve_from(binding[1], binding[2])
            if not self.met_cycle:
                self.from_cache[key] = hit
            self.met_cycle = outer or self.met_cycle
            return hit
        kind = binding[0]
        if kind == "module":
            return self.module_hit(binding[1])
        hit, rest = self.resolve_chain(module, binding[1])
        return hit if not rest else None

    def lookup(self, module: str, name: str) -> Hit | None:
        table = self.tables.get(module)
        if table is None:
            return None
        step = entity(module, name)
        binding = table.names.get(name)
        if binding is not None:
            if binding[0] in ("class", "def", "variable"):
                return Hit((step,), binding[0])
            found = self.resolve_binding(module, binding)
            if found is None and binding[0] == "alias":
                return Hit((step,), "variable")
            return found.behind(step) if found is not None else None
        for source in table.star_imports:
            found = self.resolve_name(source, name)
            if found is not None:
                return found.behind(step)
        if self.index.exists(f"{module}.{name}"):
            return self.module_hit(f"{module}.{name}")
        if name in MODULE_ATTRIBUTES:
            return Hit((step,), "variable")
        lazy = table.lazy.get(name)
        if lazy is not None:
            found = self.resolve_binding(module, ("from", lazy, name))
            return found.behind(step) if found is not None else None
        unknown = [source for source in table.star_imports if not self.index.exists(source)]
        if unknown:
            return Hit((step, entity(f"{unknown[0]}.{name}")), "missing")
        return None

    def resolve_value(self, module: str, value: object) -> Hit | None:
        """A value tree from the extractor: a chain, a subscript's value, an attribute, a string annotation."""
        if isinstance(value, str):
            hit, rest = self.resolve_chain(module, value)
            return hit if not rest else None
        if not isinstance(value, dict):
            return None
        if "sub" in value:
            return self.resolve_value(module, value["sub"])
        if "sa" in value:
            return self.resolve_value(module, value["sa"])
        if "a" in value and "v" in value:
            base = self.resolve_value(module, value["v"])
            if base is not None and base.kind == "module":
                return self.resolve_name(base.origin.module, value["a"])
        return None

    def resolve_dotted(self, dotted: str) -> tuple[Hit | None, list[str]]:
        """A dotted path with no local scope: the longest module prefix, then names inside it."""
        module = self.index.longest_module(dotted)
        if module is None:
            return None, dotted.split(".")
        rest = dotted[len(module) + 1 :].split(".") if len(dotted) > len(module) else []
        return self.descend(self.module_hit(module), rest)


class ClassIndex:
    """Class facts and C3 MRO over resolved bases."""

    def __init__(self, resolver: Resolver) -> None:
        self.resolver = resolver
        self.tables = resolver.tables
        self.mro_cache: dict[EntityId, list[EntityId]] = {}
        self.bases_cache: dict[EntityId, list[EntityId]] = {}

    def node(self, ref: EntityId) -> dict | None:
        table = self.tables.get(ref.module)
        if table is None:
            return None
        binding = table.names.get(ref.symbol)
        if binding is None or binding[0] != "class":
            return None
        record = self.tables.stub_record(ref.module) if binding[2] == "stub" else self.tables.record(ref.module)
        return record["defs"][binding[1]] if record is not None else None

    def bases(self, ref: EntityId) -> list[EntityId]:
        if ref in self.bases_cache:
            return self.bases_cache[ref]
        out: list[EntityId] = []
        node = self.node(ref)
        for base in node.get("bases", ()) if node else ():
            hit = self.resolver.resolve_value(ref.module, base)
            if hit is not None and hit.kind == "class":
                out.append(hit.origin)
        self.bases_cache[ref] = out
        return out

    def mro(self, ref: EntityId) -> list[EntityId]:
        if ref in self.mro_cache:
            return self.mro_cache[ref]
        self.mro_cache[ref] = [ref]
        bases = self.bases(ref)
        sequences = [list(self.mro(b)) for b in bases] + [list(bases)]
        result = [ref]
        while True:
            sequences = [s for s in sequences if s]
            if not sequences:
                break
            head = sequences[0][0]
            for seq in sequences:
                if not any(seq[0] in s[1:] for s in sequences):
                    head = seq[0]
                    break
            result.append(head)
            for s in sequences:
                if s and s[0] == head:
                    s.pop(0)
        self.mro_cache[ref] = result
        return result
