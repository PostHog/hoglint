"""Per-file view of L1 records: scopes, contexts and name lookup at a site."""

from __future__ import annotations

from dataclasses import dataclass

from hoglint.core.config import CoreConfig
from hoglint.core.modules import ModuleIndex
from hoglint.core.symbols import (
    FUNC,
    IF,
    TC_ELSE,
    TRY,
    TYPE_ONLY,
    Binding,
    Hit,
    Resolver,
    entity,
    import_binding,
    unpacked_names,
)
from hoglint.model import Context, EntityId, Resolution, Site

MODULE_SCOPE = "<module>"


def binding_surface(binding: Binding) -> str:
    if binding[0] == "module":
        return binding[1]
    return f"{binding[1]}.{binding[2]}"


@dataclass(frozen=True, slots=True)
class Lookup:
    """A name at a site: the dotted surface it stands for and what it resolves to.

    `local` marks a name no scope binds, used inside a function: a parameter or local variable.
    """

    surface: str
    hit: Hit | None
    local: bool = False


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


class FileScope:
    """Scopes, contexts and bindings of one analyzed file."""

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

    def scope_name(self, scope: int) -> str:
        return MODULE_SCOPE if scope == -1 else self.qualnames[scope]

    def is_function_scope(self, scope: int) -> bool:
        if scope == -1:
            return False
        return self.defs[scope]["k"] != "class" or self.in_function[scope]

    def site(self, scope: int, line: int, column: int) -> Site:
        return Site(self.path, self.module, self.scope_name(scope), line, column)

    def contexts(self, flags: int = 0, scope: int = -1) -> frozenset[Context]:
        """Path contexts plus syntax contexts. The else branch of `if TYPE_CHECKING:` runs at
        runtime, so only the body is type-only; the else branch gets its own context."""
        key = (flags, self.is_function_scope(scope))
        found = self.context_sets.get(key)
        if found is not None:
            return found
        extra: set[Context] = set()
        if flags & TYPE_ONLY:
            extra.add(Context.TYPE_ONLY)
        if flags & TC_ELSE:
            extra.add(Context.TYPE_CHECKING_ELSE)
        if flags & FUNC or key[1]:
            extra.add(Context.FUNCTION_LOCAL)
        if flags & (TRY | IF):
            extra.add(Context.CONDITIONAL)
        found = self.base_contexts | extra if extra else self.base_contexts
        self.context_sets[key] = found
        return found

    def ancestry(self, scope: int) -> list[int]:
        out = [scope]
        while scope != -1:
            scope = self.defs[scope]["s"]
            out.append(scope)
        return out

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

    def module_level(self, scope: FileScope, name: str, line: int) -> Lookup | None:
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

    def module_lookup(self, scope: FileScope, name: str, line: int) -> Lookup | None:
        if len(scope.local_bindings.get((-1, name), ())) > 1:
            return self.module_level(scope, name, line)
        if name not in scope.module_lookups:
            scope.module_lookups[name] = self.module_level(scope, name, line)
        return scope.module_lookups[name]

    def lookup(self, scope: FileScope, name: str, at_scope: int, line: int) -> Lookup | None:
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
