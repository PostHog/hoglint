"""L2 core: load L1 fact records, index modules, resolve bindings and class ancestry.

Reads only L1 records (schema v1). Never reads or parses Python source.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

try:
    import orjson
except ImportError:  # pragma: no cover
    orjson = None

SITE_MARKER = "site-packages/"
FIRST_PARTY_ROOTS = ("posthog", "ee", "products", "common")

# Context flags (schema section 3).
TYPE_ONLY = 1
TC_ELSE = 2
FUNC = 4
CLASS = 8
TRY = 16
IF = 32
BLOCK = 64

NO_CONST = object()  # marker: a V value that is not a plain constant


class FactStore:
    """Records by path. `kinds` keeps only those fact lists (the envelope is always kept)."""

    def __init__(self) -> None:
        self.records: dict[str, dict] = {}

    def load(self, path: Path, loader: str = "orjson", kinds: frozenset[str] | None = None) -> None:
        loads = orjson.loads if loader == "orjson" and orjson is not None else json.loads
        with open(path, "rb") as handle:
            for line in handle:
                record = loads(line)
                if kinds is not None:
                    record = {k: v for k, v in record.items() if k in kinds or k in ENVELOPE}
                self.records[record["path"]] = record

    def load_split(self, directory: Path, kinds: tuple[str, ...]) -> None:
        """Per-kind store from split_store.py: the envelope, then only the named fact kinds."""
        loads = orjson.loads if orjson is not None else json.loads
        for path, envelope in loads((directory / "envelope.json").read_bytes()).items():
            self.records.setdefault(path, {}).update(envelope)
        for kind in kinds:
            for path, rows in loads((directory / f"{kind}.json").read_bytes()).items():
                self.records[path][kind] = rows

    def get(self, path: str) -> dict | None:
        return self.records.get(path)


ENVELOPE = frozenset({"v", "cfg", "path", "sha256", "bytes", "status"})


def module_of_path(path: str) -> tuple[str, bool]:
    """(dotted module, is_package) for a repo path or a site-packages path."""
    rel = path.split(SITE_MARKER, 1)[1] if SITE_MARKER in path else path
    stem = rel[: -len(".pyi")] if rel.endswith(".pyi") else rel[: -len(".py")]
    parts = stem.split("/")
    is_package = parts[-1] == "__init__"
    if is_package:
        parts = parts[:-1]
    return ".".join(parts), is_package


def absolute_import(module: str, is_package: bool, target: str | None, level: int) -> str | None:
    """Absolute module named by `from <level dots><target> import ...` inside `module`."""
    if level == 0:
        return target
    package = module if is_package else module.rpartition(".")[0]
    parts = package.split(".") if package else []
    climb = level - 1
    if climb > len(parts):
        return None
    if climb:
        parts = parts[: len(parts) - climb]
    if not parts:
        return target if target else None
    base = ".".join(parts)
    return f"{base}.{target}" if target else base


def const_of(value: object) -> object:
    """Python constant for a V constant (str, int, bool, None), else NO_CONST."""
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, dict) and "s" in value and len(value) == 1:
        return value["s"]
    return NO_CONST


def is_const(value: object) -> bool:
    """True for every Constant node form in V (incl. floats, bytes, ..., longstr)."""
    if value is None or isinstance(value, (bool, int)):
        return True
    if isinstance(value, dict):
        if "s" in value:
            return True
        return value.get("o") in ("float", "complex", "bytes", "...", "int", "longstr", "badstr")
    return False


def is_chain(value: object) -> bool:
    return isinstance(value, str)


def scope_ancestry(defs: list[dict], idx: int) -> list[int]:
    out = [idx]
    while idx != -1:
        idx = defs[idx]["s"]
        out.append(idx)
    return out


def qualname(defs: list[dict], idx: int) -> str:
    names = []
    while idx != -1:
        names.append(defs[idx]["n"])
        idx = defs[idx]["s"]
    return ".".join(reversed(names))


class ModuleIndex:
    """Module name <-> path over one or more record sets. Repo paths win over site-packages."""

    def __init__(self, store: FactStore) -> None:
        self.store = store
        self.path_of: dict[str, str] = {}
        self.module_of: dict[str, str] = {}
        self.package: dict[str, bool] = {}
        self.dirs: set[str] = set()
        for path in sorted(store.records, key=lambda p: (SITE_MARKER in p, p)):
            module, is_package = module_of_path(path)
            self.module_of[path] = module
            if module in self.path_of:
                continue
            self.path_of[module] = path
            self.package[module] = is_package
            parts = module.split(".")
            for i in range(1, len(parts)):
                self.dirs.add(".".join(parts[:i]))

    def exists(self, module: str) -> bool:
        """A module file, a package, or a namespace directory that holds known files."""
        return module in self.path_of or module in self.dirs

    def is_file_module(self, module: str) -> bool:
        return module in self.path_of

    def record(self, module: str) -> dict | None:
        path = self.path_of.get(module)
        return self.store.records.get(path) if path is not None else None

    def is_package(self, module: str) -> bool:
        return self.package.get(module, module in self.dirs)


@dataclass(frozen=True)
class ClassRef:
    module: str
    name: str

    @property
    def id(self) -> str:
        return f"{self.module}.{self.name}"


@dataclass(frozen=True)
class ModuleRef:
    name: str


@dataclass(frozen=False)
class ModuleTable:
    """Module-level names of one module (spike A semantics: module body and if/try bodies)."""

    name: str
    path: str | None
    is_package: bool
    classes: dict[str, int] = field(default_factory=dict)  # name -> def index
    class_lines: dict[str, int] = field(default_factory=dict)
    bindings: dict[str, tuple] = field(default_factory=dict)  # ("from", mod, attr) | ("import", mod) | ("alias", chain)
    star_imports: list[str] = field(default_factory=list)
    constants: dict[str, object] = field(default_factory=dict)


# Statement nesting the spike A collector descends into: module body, if bodies (any), try parts.
A_ALLOWED_FLAGS = TYPE_ONLY | TC_ELSE | IF | TRY


class Tables:
    """Lazily built ModuleTables over a ModuleIndex."""

    def __init__(self, index: ModuleIndex) -> None:
        self.index = index
        self.cache: dict[str, ModuleTable | None] = {}

    def get(self, module: str) -> ModuleTable | None:
        if module in self.cache:
            return self.cache[module]
        table = self.build(module)
        self.cache[module] = table
        return table

    def build(self, module: str) -> ModuleTable | None:
        if not self.index.exists(module):
            return None
        path = self.index.path_of.get(module)
        table = ModuleTable(module, path, self.index.is_package(module))
        record = self.index.store.records.get(path) if path else None
        if record is None or record.get("status") != "ok":
            return table
        events: list[tuple[int, int, int, str, object]] = []
        for i, d in enumerate(record.get("defs", ())):
            if d["s"] == -1 and d["k"] == "class" and not d.get("f", 0) & ~A_ALLOWED_FLAGS:
                events.append((d["l"], d["c"], 0, "class", i))
        for row in record.get("imports", ()):
            if row[0] == -1 and not row[3] & ~A_ALLOWED_FLAGS:
                events.append((row[1], row[2], 1, "import", row))
        for row in record.get("assigns", ()):
            if row["s"] == -1 and row["op"] in ("=", ":") and not row.get("f", 0) & ~A_ALLOWED_FLAGS:
                events.append((row["l"], row["c"], 2, "assign", row))
        events.sort(key=lambda e: (e[0], e[1], e[2]))
        defs = record.get("defs", [])
        for _, _, _, kind, payload in events:
            if kind == "class":
                d = defs[payload]
                table.classes[d["n"]] = cast(int, payload)
                table.class_lines[d["n"]] = d["l"]
                table.bindings.pop(d["n"], None)
            elif kind == "import":
                _, _, _, _, mod, level, name, asname = payload
                if name is None:
                    if asname:
                        table.bindings[asname] = ("import", mod)
                    else:
                        head = mod.split(".")[0]
                        table.bindings[head] = ("import", head)
                    continue
                source = absolute_import(module, table.is_package, mod, level) or ""
                if name == "*":
                    table.star_imports.append(source)
                    continue
                table.bindings[asname or name] = ("from", source, name)
            else:
                if "v" not in payload:
                    continue
                value = payload["v"]
                for target in payload["t"]:
                    if not isinstance(target, str) or "." in target:
                        continue
                    if is_const(value):
                        table.constants[target] = const_of(value)
                    elif isinstance(value, str):
                        table.bindings[target] = ("alias", value)
        return table


class Resolver:
    """Names and dotted chains to classes or modules, following re-exports (spike A)."""

    def __init__(self, tables: Tables) -> None:
        self.tables = tables
        self.index = tables.index

    def resolve_name(self, module: str, name: str, seen: frozenset = frozenset()) -> ClassRef | ModuleRef | None:
        key = (module, name)
        if key in seen:
            return None
        seen = seen | {key}
        table = self.tables.get(module)
        if table is None:
            return None
        if name in table.classes:
            return ClassRef(module, name)
        binding = table.bindings.get(name)
        if binding is not None:
            if binding[0] == "import":
                return ModuleRef(binding[1])
            if binding[0] == "from":
                _, source, attr = binding
                found = self.resolve_name(source, attr, seen)
                if found is not None:
                    return found
                if self.index.exists(f"{source}.{attr}"):
                    return ModuleRef(f"{source}.{attr}")
                return None
            if binding[0] == "alias":
                return self.resolve_chain(module, binding[1], seen)
        for source in table.star_imports:
            found = self.resolve_name(source, name, seen)
            if found is not None:
                return found
        if table.is_package and self.index.exists(f"{module}.{name}"):
            return ModuleRef(f"{module}.{name}")
        return None

    def resolve_chain(self, module: str, chain: str, seen: frozenset = frozenset()) -> ClassRef | ModuleRef | None:
        head, *rest = chain.split(".")
        found = self.resolve_name(module, head, seen)
        for attr in rest:
            if not isinstance(found, ModuleRef):
                return None
            found = self.resolve_name(found.name, attr, seen)
        return found

    def resolve_value(self, module: str, value: object, seen: frozenset = frozenset()) -> ClassRef | ModuleRef | None:
        """A V value: chain, subscript (its value), attribute on a non-chain (None)."""
        if isinstance(value, str):
            return self.resolve_chain(module, value, seen)
        if isinstance(value, dict) and "sub" in value:
            return self.resolve_value(module, value["sub"], seen)
        if isinstance(value, dict) and "a" in value:
            base = self.resolve_value(module, value["v"], seen)
            if isinstance(base, ModuleRef):
                return self.resolve_name(base.name, value["a"], seen)
        return None

    def resolve_dotted(self, dotted: str) -> ClassRef | ModuleRef | None:
        if self.index.exists(dotted):
            return ModuleRef(dotted)
        module, _, name = dotted.rpartition(".")
        return self.resolve_name(module, name)


class ClassIndex:
    """Class facts and C3 MRO over resolved bases."""

    def __init__(self, resolver: Resolver) -> None:
        self.resolver = resolver
        self.tables = resolver.tables
        self.mro_cache: dict[ClassRef, list[ClassRef]] = {}
        self.bases_cache: dict[ClassRef, list[ClassRef]] = {}

    def record(self, ref: ClassRef) -> dict:
        return self.tables.index.record(ref.module) or {}

    def def_index(self, ref: ClassRef) -> int:
        return self.tables.get(ref.module).classes[ref.name]

    def node(self, ref: ClassRef) -> dict:
        return self.record(ref)["defs"][self.def_index(ref)]

    def bases(self, ref: ClassRef) -> list[ClassRef]:
        if ref in self.bases_cache:
            return self.bases_cache[ref]
        out = []
        for base in self.node(ref).get("bases", ()):
            found = self.resolver.resolve_value(ref.module, base)
            if isinstance(found, ClassRef):
                out.append(found)
        self.bases_cache[ref] = out
        return out

    def mro(self, ref: ClassRef) -> list[ClassRef]:
        if ref in self.mro_cache:
            return self.mro_cache[ref]
        self.mro_cache[ref] = [ref]  # cycle guard
        bases = self.bases(ref)
        sequences = [list(self.mro(b)) for b in bases] + [list(bases)]
        result = [ref]
        while True:
            sequences = [s for s in sequences if s]
            if not sequences:
                break
            for seq in sequences:
                head = seq[0]
                if not any(head in s[1:] for s in sequences):
                    break
            else:
                head = sequences[0][0]
            result.append(head)
            for s in sequences:
                if s and s[0] == head:
                    s.pop(0)
        self.mro_cache[ref] = result
        return result

    def is_subclass(self, ref: ClassRef, target_id: str) -> bool:
        return any(c.id == target_id for c in self.mro(ref))


def children_of(defs: list[dict], idx: int) -> list[int]:
    return [i for i, d in enumerate(defs) if d["s"] == idx]


class Core:
    """Everything a channel needs, built once from a FactStore."""

    def __init__(self, store: FactStore) -> None:
        self.store = store
        self.index = ModuleIndex(store)
        self.tables = Tables(self.index)
        self.resolver = Resolver(self.tables)
        self.classes = ClassIndex(self.resolver)

    def repo_paths(self) -> list[str]:
        return sorted(p for p in self.store.records if SITE_MARKER not in p)


def load_core(paths: list[Path], loader: str = "orjson", kinds: frozenset[str] | None = None) -> Core:
    store = FactStore()
    for path in paths:
        store.load(path, loader, kinds)
    return Core(store)


def load_split_core(directory: Path, kinds: tuple[str, ...]) -> Core:
    store = FactStore()
    store.load_split(directory, kinds)
    return Core(store)
