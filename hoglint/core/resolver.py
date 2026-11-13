"""The Python engine's name resolution over decoded records: module tables built from the
records, names and dotted chains resolved through re-exports, class ancestry, and a name looked up
at a site of one file. The native link does all of this in Rust (`src/link/resolve.rs`,
`src/link/scope.rs`); only the Python graph build runs this module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hoglint.core.config import CoreConfig
from hoglint.core.modules import ModuleIndex
from hoglint.core.sites import FileScope, Lookup, binding_surface
from hoglint.core.symbols import (
    CONDITIONAL_FLAGS,
    IDENTIFIER,
    MODULE_ATTRIBUTES,
    MODULE_LEVEL_FLAGS,
    Binding,
    Hit,
    LazyEntry,
    ModuleTable,
    Tables,
    absolute_import,
    entity,
    import_binding,
    is_module_assign,
    is_module_path,
    module_assigns,
    module_level,
    unpacked_names,
)
from hoglint.facts import FactStore
from hoglint.model import Context, EntityId, Resolution


class RecordTables(Tables):
    """Module tables built from the decoded records of the Python fact store."""

    def __init__(self, store: FactStore, index: ModuleIndex) -> None:
        super().__init__(store, index)

    def collect(self, table: ModuleTable, record: dict, marker: str) -> None:
        """Later bindings win, except that a conditional def or assignment never replaces an
        import: `except ImportError: Bar = None` and `else: Foo = Any` are fallbacks."""
        module = table.name
        events: list[tuple[int, int, int, int, Binding | None, str, Any]] = []
        for i, d in enumerate(record.get("defs", ())):
            flags = d.get("f", 0)
            if module_level(d["s"], flags, MODULE_LEVEL_FLAGS):
                kind = "class" if d["k"] == "class" else "def"
                events.append((d["l"], d["c"], 0, flags, (kind, i, marker), d["n"], None))
        for row in record.get("imports", ()):
            if not module_level(row[0], row[3], MODULE_LEVEL_FLAGS):
                continue
            if row[6] == "*":
                source = absolute_import(module, table.is_package, row[4], row[5])
                events.append((row[1], row[2], 1, row[3], None, "*", source))
                continue
            bound = import_binding(module, table.is_package, row)
            if bound is not None:
                events.append((row[1], row[2], 1, row[3], bound[1], bound[0], None))
        for row in record.get("assigns", ()):
            if not is_module_assign(row):
                continue
            flags = row.get("f", 0)
            value = row.get("v")
            for target in row["t"]:
                if isinstance(target, str) and "." not in target:
                    assigned: Binding = ("alias", value) if isinstance(value, str) else ("variable",)
                    events.append((row["l"], row["c"], 2, flags, assigned, target, row))
                    continue
                for name in unpacked_names(target):
                    events.append((row["l"], row["c"], 2, flags, ("variable",), name, None))
        if marker == "":
            table.values = {name: row["v"] for name, row in module_assigns(record).items()}
        events.sort(key=lambda e: (e[0], e[1], e[2]))
        for _, _, _, flags, binding, name, source in events:
            if binding is None:
                if source:
                    table.star_imports.append(str(source))
                continue
            if marker == "":
                table.bindings.setdefault(name, []).append(
                    (flags, binding, source if isinstance(source, dict) else None)
                )
            current = table.names.get(name)
            is_import = binding[0] in ("module", "from")
            if not is_import and flags & CONDITIONAL_FLAGS and current is not None and current[0] in ("module", "from"):
                continue
            table.names[name] = binding

    def lazy_map(self, module: str, is_package: bool, record: dict) -> tuple[dict[str, str], list[LazyEntry]]:
        """PEP 562 lazy exports: module-level dicts of name -> module string, in a module that
        defines `__getattr__`. A value is the tail of a module-level string prefix
        (`_BASE + value`), an absolute module, or a module under the package or one of its
        ancestors (nearest first), in that order. A relative value (`.foo`) is anchored at the
        package, as a relative import is.

        Second, every entry: those a module answers for, then the others with the first path the
        map gives them: under a prefix the prefix and the value, else the value as written (a
        relative one anchored). Only an entry whose value reads as a dotted path counts, and a
        value of one name or a relative value only in a dict where another entry resolves:
        another dict of text or names is no lazy map. An entry sits at the dict it is read from:
        the last one whose entry for the name counts."""
        package = module if is_package else module.rpartition(".")[0]
        parts = package.split(".")
        ancestors = [".".join(parts[:end]) for end in range(len(parts), 0, -1)]
        prefixes: list[str] = []
        maps: list[dict] = []
        for row in record.get("assigns", ()):
            if row["s"] != -1 or "v" not in row:
                continue
            value = row["v"]
            if isinstance(value, dict) and set(value) == {"s"} and value["s"].endswith("."):
                prefixes.append(value["s"])
            elif isinstance(value, dict) and "dict" in value:
                maps.append(row)
        out: dict[str, str] = {}
        found_at: dict[str, tuple[int, int]] = {}
        missing: dict[str, str] = {}
        missing_at: dict[str, tuple[int, int]] = {}
        for row in maps:
            unresolved: list[tuple[str, str, bool]] = []
            resolves = False
            for key, value in row["v"]["dict"]:
                if not (isinstance(key, dict) and "s" in key and isinstance(value, dict) and "s" in value):
                    continue
                text = value["s"]
                if text.startswith("."):
                    rest = text.lstrip(".")
                    level = len(text) - len(rest)
                    anchored = absolute_import(module, is_package, rest, level) if is_module_path(rest) else None
                    candidates: tuple[str, ...] = (anchored,) if anchored is not None else ()
                else:
                    anchored = None
                    relative = [f"{ancestor}.{text}" for ancestor in ancestors]
                    candidates = (*(p + text for p in prefixes), text, *relative)
                found = next((candidate for candidate in candidates if self.index.exists(candidate)), None)
                if found is not None:
                    out[key["s"]] = found
                    found_at[key["s"]] = (row["l"], row["c"])
                    resolves = True
                elif not IDENTIFIER.fullmatch(key["s"]):
                    continue
                elif anchored is not None:
                    unresolved.append((key["s"], anchored, False))
                elif is_module_path(text):
                    unresolved.append((key["s"], candidates[0], "." in text))
            for name, path, dotted in unresolved:
                # A bare name or a relative path may be any text; it reads as a module only in a
                # dict that is a lazy map.
                if resolves or dotted:
                    missing[name] = path
                    missing_at[name] = (row["l"], row["c"])
        entries = [LazyEntry(name, found, *found_at[name]) for name, found in out.items()]
        entries += [LazyEntry(name, path, *missing_at[name]) for name, path in missing.items() if name not in out]
        return out, entries

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
                table.lazy, table.lazy_entries = self.lazy_map(module, table.is_package, record)
        stub = self.stub_record(module)
        if stub is not None and stub.get("status") == "ok":
            stub_table = ModuleTable(module, self.index.stub_of[module], table.is_package)
            self.collect(stub_table, stub, "stub")
            for name, binding in stub_table.names.items():
                if name not in table.names:
                    table.names[name] = binding
                    table.stub_names.add(name)
        return table


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
        # A result that met a cycle is never cached, so each top-level answer is a pure function of the tables.
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

    def resolve_class(self, module: str, value: object) -> EntityId | None:
        """The class a value tree names, seen from `module`."""
        hit = self.resolve_value(module, value)
        return hit.origin if hit is not None and hit.kind == "class" else None

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
        self.ancestor_cache: dict[EntityId, frozenset[str]] = {}

    def located(self, ref: EntityId) -> tuple[dict, int] | None:
        """The record that defines a class and the class's def index in it."""
        binding = self.tables.binding(ref.module, ref.symbol)
        if binding is None or binding[0] != "class":
            return None
        path = self.tables.binding_path(ref.module, ref.symbol)
        record = self.tables.store.records.get(path) if path is not None else None
        return (record, binding[1]) if record is not None else None

    def node(self, ref: EntityId) -> dict | None:
        found = self.located(ref)
        return found[0]["defs"][found[1]] if found is not None else None

    def is_subclass(self, ref: EntityId, qualified: str) -> bool:
        ancestors = self.ancestor_cache.get(ref)
        if ancestors is None:
            ancestors = frozenset(str(cls) for cls in self.mro(ref))
            self.ancestor_cache[ref] = ancestors
        return qualified in ancestors

    def bases(self, ref: EntityId) -> list[EntityId]:
        if ref in self.bases_cache:
            return self.bases_cache[ref]
        node = self.node(ref)
        out = self.resolved_bases(ref.module, node) if node else []
        self.bases_cache[ref] = out
        return out

    def resolved_bases(self, module: str, node: dict) -> list[EntityId]:
        """The classes a class def's bases resolve to, seen from its module."""
        out: list[EntityId] = []
        for base in node.get("bases", ()):
            hit = self.resolver.resolve_value(module, base)
            if hit is not None and hit.kind == "class":
                out.append(hit.origin)
        return out

    def ancestors(self, bases: list[EntityId]) -> list[EntityId]:
        """C3 over resolved bases: the MRO of a class with these bases, without the class."""
        if len(bases) <= 1:
            # C3 over one base is that base's MRO; skip the merge, the common case by far.
            return list(self.mro(bases[0])) if bases else []
        return self.merge([list(self.mro(b)) for b in bases] + [list(bases)])

    def mro(self, ref: EntityId) -> list[EntityId]:
        if ref in self.mro_cache:
            return self.mro_cache[ref]
        self.mro_cache[ref] = [ref]
        result = [ref, *self.ancestors(self.bases(ref))]
        self.mro_cache[ref] = result
        return result

    @staticmethod
    def merge(sequences: list[list[EntityId]]) -> list[EntityId]:
        """The C3 merge of the bases' MROs and the bases themselves."""
        result: list[EntityId] = []
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
        return result


@dataclass(frozen=True, slots=True)
class Resolved:
    resolution: Resolution
    targets: tuple[EntityId, ...]
    via: tuple[EntityId, ...]


class Classifier:
    """Turns a surface and a resolver hit into an edge's resolution, targets and via."""

    def __init__(self, index: ModuleIndex) -> None:
        self.index = index

    def classify(self, surface: str, hit: Hit | None) -> Resolved:
        """A hit on a module or name with no sources is external when its head is not repo code."""
        if hit is not None:
            origin = hit.origin.module
            known = hit.kind not in ("missing", "module") or self.index.exists(origin)
            if known:
                resolution = Resolution.EXTERNAL if self.index.is_external(origin) else Resolution.RESOLVED
                return Resolved(resolution, (hit.origin,), hit.via)
            if self.index.is_external(origin):
                return Resolved(Resolution.EXTERNAL, (hit.origin,), hit.via)
            return Resolved(Resolution.UNRESOLVED, (), hit.via)
        if surface and self.index.is_external(surface):
            return Resolved(Resolution.EXTERNAL, (entity(surface),), ())
        return Resolved(Resolution.UNRESOLVED, (), ())

    def classify_lookup(self, found: Lookup) -> Resolved:
        if found.local:
            return Resolved(Resolution.NOT_APPLICABLE, (), ())
        return self.classify(found.surface, found.hit)


class RecordFileScope(FileScope):
    """Scopes, contexts and bindings of one analyzed file, read from its decoded record."""

    def build_qualnames(self) -> list[str]:
        out: list[str] = []
        for d in self.defs:
            parent = d["s"]
            out.append(d["n"] if parent == -1 else f"{out[parent]}.{d['n']}")
        return out

    def build_in_function(self) -> list[bool]:
        """True when the def sits inside a function body (at any depth)."""
        out: list[bool] = []
        for d in self.defs:
            parent = d["s"]
            out.append(parent != -1 and (out[parent] or self.defs[parent]["k"] != "class"))
        return out

    def path_contexts(self, config: CoreConfig) -> frozenset[Context]:
        contexts: set[Context] = set()
        if config.test_globs.matches(self.path):
            contexts.add(Context.TEST)
        elif config.migration_globs.matches(self.path):
            contexts.add(Context.MIGRATION)
        else:
            contexts.add(Context.PROD)
        if self.path.endswith(".pyi"):
            contexts.add(Context.TYPE_ONLY)
        return frozenset(contexts)

    def build_local_bindings(self) -> dict[tuple[int, str], list[tuple[int, Binding]]]:
        """Import bindings that are not in the module table: inside defs, classes and blocks."""
        out: dict[tuple[int, str], list[tuple[int, Binding]]] = {}
        for row in self.record.get("imports", ()):
            bound = import_binding(self.module, self.is_package, row)
            if bound is None:
                continue
            out.setdefault((row[0], bound[0]), []).append((row[1], bound[1]))
        return out

    def build_members(self) -> dict[tuple[int, str], tuple[str, str]]:
        """Names defined inside a def or class body: nested defs, and class-level assignments with
        a value. A bare annotation (`uuid: uuid.UUID`) binds no name, so its own annotation and the
        names after it still see the module's `uuid`."""
        out: dict[tuple[int, str], tuple[str, str]] = {}
        for idx, d in enumerate(self.defs):
            if d["s"] != -1:
                out[(d["s"], d["n"])] = ("class" if d["k"] == "class" else "def", self.qualnames[idx])
        for row in self.record.get("assigns", ()):
            owner = row["s"]
            if owner == -1 or "v" not in row:
                continue
            for target in row["t"]:
                for name in unpacked_names(target):
                    out.setdefault((owner, name), ("variable", f"{self.qualnames[owner]}.{name}"))
        return out

    def __init__(self, path: str, module: str, is_package: bool, record: dict, config: CoreConfig) -> None:
        self.path = path
        self.module = module
        self.is_package = is_package
        self.record = record
        self.defs: list[dict] = record.get("defs", [])
        self.qualnames = self.build_qualnames()
        self.in_function = self.build_in_function()
        self.base_contexts = self.path_contexts(config)
        self.local_bindings = self.build_local_bindings()
        self.members = self.build_members()
        self.scoped_names = {name for _, name in self.members} | {
            name for scope, name in self.local_bindings if scope != -1
        }
        self.module_lookups: dict[str, Lookup | None] = {}
        self.context_sets: dict[tuple[int, bool], frozenset[Context]] = {}

    def binding_at(self, scope: int, name: str, line: int) -> Binding | None:
        """The import binding of `name` in exactly this scope, the latest one before `line`."""
        rows = self.local_bindings.get((scope, name))
        if not rows:
            return None
        before = [binding for at, binding in rows if at <= line]
        return before[-1] if before else rows[0][1]


class NameLookup:
    """Resolves a bare name used at a site of one file."""

    def __init__(self, resolver: Resolver) -> None:
        self.resolver = resolver

    def module_level(self, scope: RecordFileScope, name: str, line: int) -> Lookup | None:
        table = self.resolver.tables.get(scope.module)
        if table is not None and name in table.names:
            binding = table.names[name]
            if binding[0] in ("module", "from"):
                return Lookup(binding_surface(binding), self.resolver.resolve_binding(scope.module, binding))
            return Lookup(f"{scope.module}.{name}", self.resolver.resolve_name(scope.module, name))
        binding = scope.binding_at(-1, name, line)
        if binding is not None:
            return Lookup(binding_surface(binding), self.resolver.resolve_binding(scope.module, binding))
        return None

    def module_lookup(self, scope: RecordFileScope, name: str, line: int) -> Lookup | None:
        if len(scope.local_bindings.get((-1, name), ())) > 1:
            return self.module_level(scope, name, line)
        if name not in scope.module_lookups:
            scope.module_lookups[name] = self.module_level(scope, name, line)
        return scope.module_lookups[name]

    def lookup(self, scope: RecordFileScope, name: str, at_scope: int, line: int) -> Lookup | None:
        """Python scoping: enclosing function scopes and the starting class body, then the module."""
        if at_scope == -1 or name not in scope.scoped_names:
            return self.module_lookup(scope, name, line)
        for depth, candidate in enumerate(scope.ancestry(at_scope)):
            if candidate == -1:
                break
            if depth > 0 and scope.defs[candidate]["k"] == "class":
                continue
            binding = scope.binding_at(candidate, name, line)
            if binding is not None:
                return Lookup(binding_surface(binding), self.resolver.resolve_binding(scope.module, binding))
            member = scope.members.get((candidate, name))
            if member is not None:
                kind, qualname = member
                return Lookup(f"{scope.module}.{qualname}", Hit((entity(scope.module, qualname),), kind))
        return self.module_lookup(scope, name, line)

    def descend(self, found: Lookup, attrs: list[str]) -> tuple[Lookup, list[str]]:
        """Follow attributes while the name stands for a module; the surface grows with them.

        A missing attribute of a known module joins the surface and leaves the hit empty.
        """
        if found.hit is None:
            return found, list(attrs)
        hit, rest = self.resolver.descend(found.hit, attrs)
        consumed = len(attrs) - len(rest)
        surface = ".".join([found.surface, *attrs[:consumed]])
        if rest and hit is not None and hit.kind == "module":
            surface = f"{surface}.{rest.pop(0)}"
            hit = None
        return Lookup(surface, hit), rest
