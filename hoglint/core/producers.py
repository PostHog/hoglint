"""Core edge producers. Each reads L1 records of analyzed files and appends Edges to a Graph.

Producers know Python, never a framework: a use position says where a name sits (call,
attribute, argument, ...), and packs give it framework meaning.
"""

from __future__ import annotations

import re
import builtins
from dataclasses import dataclass, field, replace

from hoglint.core.config import CoreConfig
from hoglint.core.modules import FirstParty, ModuleIndex
from hoglint.core.records import is_class_var
from hoglint.core.sites import Classifier, FileScope, Lookup, NameLookup, Resolved
from hoglint.core.surfaces import ALL_CLAUSE, HandedOut
from hoglint.core.symbols import CLASS, FUNC, Binding, ClassIndex, Hit, Resolver, entity, import_surface, module_assigns
from hoglint.facts import FactStore
from hoglint.model import Context, Edge, EntityId, Graph, Resolution

PRODUCER_VERSION = 1
UNRESOLVED = Resolved(Resolution.UNRESOLVED, (), ())
# The attr of an import edge that a lazy (PEP 562) name gets.
LAZY_ATTR = ("lazy", "1")
BUILTIN_NAMES = frozenset(dir(builtins))
CHAIN_TOKEN = re.compile(r"\.\w+|\(\)|\[\]")
# Escape steps that pass a value through unchanged, as the extractor's local derivation reads them.
ESCAPE_WRAPPERS = ("await", "ifexp:body", "ifexp:else", "star")
WRAPPER_STEPS = (*ESCAPE_WRAPPERS, "bool:and", "bool:or", "walrus:")

# Fact kinds every producer needs, because the resolver builds module tables from them.
TABLE_KINDS = frozenset({"imports", "defs", "assigns"})

# First step of the escape path above a name -> neutral use position. Checked as prefixes, in
# order, so a longer step comes before any step it starts with.
UP_POSITIONS: tuple[tuple[str, str], ...] = (
    ("arg:", "argument"),
    ("kw:", "argument"),
    ("returns", "annotation"),
    ("return", "return"),
    ("yield", "yield"),
    ("assign:", "store"),
    ("annassign:", "store"),
    ("augassign:", "store"),
    ("for-iter", "iterate"),
    ("comp:iter", "iterate"),
    ("base:", "base"),
    ("classkw:", "class-keyword"),
    ("decorator", "decorator"),
    ("param-ann:", "annotation"),
    ("annotation", "annotation"),
    ("typealias:", "annotation"),
    ("typeparam", "annotation"),
    ("param-default:", "default"),
    ("lambda:default", "default"),
    ("except", "except"),
    ("raise", "raise"),
    ("cmp:", "compare"),
    ("elt:", "element"),
    ("dict:", "element"),
    ("comp:elt", "element"),
    ("comp:key", "element"),
    ("comp:val", "element"),
    ("slice", "index"),
    ("expr", "statement"),
    ("with-target", "target"),
    ("with", "context-manager"),
    ("case-guard", "test"),
    ("case", "match"),
    ("if-test", "test"),
    ("while-test", "test"),
    ("assert", "test"),
    ("ifexp:test", "test"),
    ("unary:not", "test"),
    ("bin:", "operand"),
    ("unary:", "operand"),
    ("fstr", "format"),
    ("tstr", "format"),
    ("del", "delete"),
    ("lambda", "lambda"),
)
# The attr of a slot row whose type alias is explained by the rows of what it expands to.
EXPANDED = ("alias", "expanded")
# The attr of a slot row whose generic type is given arguments: `dict[str, str]`, not `dict`.
SUBSCRIPTED = ("subscripted", "1")
# The attrs of a slot row for an alias nested deeper than MAX_ALIAS_DEPTH: left unread, so unknown.
TOO_DEEP = (("alias", "too-deep"),)
# The attr of a slot row that is a type argument: the generic it is given to (`builtins.dict` for
# the `Any` of `dict[str, Any]`).
ARGUMENT_OF = "argument_of"
# How many type aliases deep one slot's names are followed.
MAX_ALIAS_DEPTH = 32
TARGET_STEPS = ("assign-target", "annassign-target", "augassign-target", "for-target", "comp:target", "walrus-target")


def producer_id(kind: str) -> str:
    return f"core-{kind}@{PRODUCER_VERSION}"


def chain_tokens(chain: str) -> list[str]:
    """`.a.b()[]` -> ['.a', '.b', '()', '[]']."""
    return CHAIN_TOKEN.findall(chain)


def leading_attrs(tokens: list[str]) -> list[str]:
    """Attribute names before the first call or subscript, without their dots."""
    out = []
    for token in tokens:
        if not token.startswith("."):
            break
        out.append(token[1:])
    return out


def up_position(up: str) -> str:
    steps = up.split(" ")
    while steps and steps[0].startswith(WRAPPER_STEPS):
        steps.pop(0)
    if not steps or not steps[0]:
        return "other"
    first = steps[0]
    if first.startswith(TARGET_STEPS):
        return "target"
    for prefix, position in UP_POSITIONS:
        if first.startswith(prefix):
            return position
    return "other"


def use_position(next_token: str | None, up: str) -> str:
    if next_token == "()":
        return "call"
    if next_token == "[]":
        return "subscript"
    if next_token is not None:
        return "attribute"
    return up_position(up)


def chain_text(tokens: list[str]) -> str:
    return "".join(tokens)


def frozen_keyword(call: dict) -> bool:
    """Whether a call value's keywords pass `frozen=True` (a frozen dataclass decorator)."""
    return any(pair == ["frozen", True] for pair in call.get("kw", ()))


def called_base(base: object) -> bool:
    """Whether a class base is a call, also under a subscript: `make()`, `make()[T]`."""
    while isinstance(base, dict) and "sub" in base:
        base = base["sub"]
    return isinstance(base, dict) and "call" in base


@dataclass(frozen=True, slots=True)
class Followed:
    """Where a handed-out name leads: a def in an analyzed file, or (scope None) nowhere the
    engine can follow."""

    scope: FileScope | None
    idx: int


UNFOLLOWED = Followed(None, -1)


@dataclass(frozen=False)
class BuildContext:
    """What every producer reads. Built once per run."""

    config: CoreConfig
    store: FactStore
    index: ModuleIndex
    resolver: Resolver
    files: list[FileScope]
    first_party: FirstParty
    scope_of_path: dict[str, FileScope]
    classes: ClassIndex

    def __post_init__(self) -> None:
        self.classifier = Classifier(self.index)
        self.names = NameLookup(self.resolver)
        self.assigned_by_module: dict[str, dict[str, tuple[FileScope | None, dict]]] = {}

    def binding_scope(self, module: str, name: str) -> FileScope | None:
        """The analyzed file a module table reads a name's binding from (never a stub twin or a
        package of the same name)."""
        path = self.resolver.tables.binding_path(module, name)
        return self.scope_of_path.get(path) if path is not None else None

    def def_of(self, module: str, name: str, kind: str) -> tuple[FileScope, int] | None:
        """The analyzed file and def index of the def or class (`kind`) a module table binds a
        name to."""
        binding = self.resolver.tables.binding(module, name)
        if binding is None or binding[0] != kind:
            return None
        scope = self.binding_scope(module, name)
        return (scope, binding[1]) if scope is not None else None

    def is_builtin(self, name: str) -> bool:
        return name in BUILTIN_NAMES

    def branch_bound(self, hit: Hit) -> EntityId | None:
        """The first step of a hit's chain whose module binds the name more than once (once per
        branch, say): such a name stands for every one of its bindings."""
        for step in hit.chain:
            table = self.resolver.tables.get(step.module) if step.symbol else None
            if table is not None and len(table.bindings.get(step.symbol, ())) > 1:
                return step
        return None

    def hit_classes(self, surface: str, hit: Hit | None, seen: frozenset[tuple[str, str]]) -> list[str] | None:
        """The classes a resolved name stands for, as a base: a class, a third-party name (read as
        a class, as a third-party base is), or a name bound more than once or to no class whose
        bindings all name classes. None for anything else."""
        if hit is None and surface in BUILTIN_NAMES:
            return [f"builtins.{surface}"]
        step = self.branch_bound(hit) if hit is not None else None
        if step is not None:
            return self.bound_classes(step.module, step.symbol, seen)
        resolved = self.classifier.classify(surface, hit)
        if resolved.resolution is Resolution.EXTERNAL:
            return [str(target) for target in resolved.targets]
        if hit is None or resolved.resolution is not Resolution.RESOLVED:
            return None
        if hit.kind == "class":
            return [str(hit.origin)]
        if hit.kind == "variable":
            return self.bound_classes(hit.origin.module, hit.origin.symbol, seen)
        return None

    def followed_base(self, hit: Hit, resolved: Resolved) -> bool:
        """Whether a base's bindings are followed: it resolves to a first-party name that is no
        class, or through a name bound more than once."""
        if resolved.resolution is Resolution.RESOLVED and hit.kind == "variable":
            return True
        return resolved.resolution in (Resolution.RESOLVED, Resolution.EXTERNAL) and self.branch_bound(hit) is not None

    def binding_classes(
        self, module: str, name: str, binding: Binding, value: object, seen: frozenset[tuple[str, str]]
    ) -> list[str] | None:
        """The classes one module-level binding of `name` stands for; None when it is no class."""
        kind = binding[0]
        if kind == "class":
            return [str(entity(module, name))]
        if kind in ("module", "from"):
            surface = binding[1] if kind == "module" else f"{binding[1]}.{binding[2]}"
            return self.hit_classes(surface, self.resolver.resolve_binding(module, binding), seen)
        if kind not in ("alias", "variable"):
            return None
        while isinstance(value, dict) and "sub" in value:
            value = value["sub"]
        if not isinstance(value, str):
            return None
        hit, rest = self.resolver.resolve_chain(module, value)
        return self.hit_classes(value, hit, seen) if not rest else None

    def bound_classes(self, module: str, name: str, seen: frozenset[tuple[str, str]] = frozenset()) -> list[str] | None:
        """The classes a module-level name stands for, as a base: every binding of it names a
        class, or a subscript of one (a name bound once per branch takes the union). Sorted;
        None when one binding is no class, on a cycle, or past the alias depth."""
        key = (module, name)
        if key in seen or len(seen) >= MAX_ALIAS_DEPTH:
            return None
        table = self.resolver.tables.get(module)
        bindings = table.bindings.get(name, []) if table is not None else []
        if not bindings:
            return None
        found: set[str] = set()
        for _, binding, value in bindings:
            classes = self.binding_classes(module, name, binding, value, seen | {key})
            if classes is None:
                return None
            found.update(classes)
        return sorted(found)

    def name_lookup(self, scope: FileScope, name: str, at_scope: int, line: int) -> Lookup:
        """A bare name; unbound names are builtins or missing module globals."""
        found = self.names.lookup(scope, name, at_scope, line)
        if found is not None:
            return found
        if name in BUILTIN_NAMES:
            return Lookup(f"builtins.{name}", None)
        if scope.is_function_scope(at_scope):
            return Lookup(name, None, local=True)
        return Lookup(f"{scope.module}.{name}", None)

    def chain_lookup(self, scope: FileScope, chain: str, at_scope: int, line: int) -> tuple[Lookup, list[str]]:
        head, *attrs = chain.split(".")
        return self.names.descend(self.name_lookup(scope, head, at_scope, line), attrs)

    def dotted_lookup(self, scope: FileScope, dotted: str, at_scope: int, line: int) -> Lookup:
        """A dotted name; attributes past what resolves make a member of the deepest hit."""
        found, rest = self.chain_lookup(scope, dotted, at_scope, line)
        if not rest:
            return found
        surface = ".".join([found.surface, *rest])
        if found.hit is None:
            return Lookup(surface, None, found.local)
        origin = found.hit.origin
        member = entity(origin.module, ".".join([origin.symbol, *rest]) if origin.symbol else ".".join(rest))
        return Lookup(surface, Hit((*found.hit.chain, member), "member"))

    def value_lookup(self, scope: FileScope, value: object, at_scope: int, line: int) -> Lookup | None:
        """A name-like value tree: a chain, a subscript's value, a call's callee, a string annotation."""
        if isinstance(value, str):
            return self.dotted_lookup(scope, value, at_scope, line)
        if not isinstance(value, dict):
            return None
        for key in ("sub", "sa", "call"):
            if key in value:
                return self.value_lookup(scope, value[key], at_scope, line)
        return None

    def first_party_chain(self, hit: Hit) -> bool:
        """Whether every step from the name the code used to the origin is first-party code."""
        return all(self.first_party.contains(str(step)) for step in hit.chain)

    def handed_def(self, target: str) -> Followed | None:
        """Where a handed-out name leads: the top-level function or class in an analyzed
        first-party file, every step of the way inside first-party code, or `UNFOLLOWED`. None
        for a name that needs no row: a third-party name, a constant, a module."""
        if not self.first_party.contains(target):
            return None
        hit, rest = self.resolver.resolve_dotted(target)
        if hit is None or rest:
            return UNFOLLOWED
        if hit.kind in ("variable", "module"):
            return None
        if hit.kind not in ("def", "class") or not self.first_party_chain(hit):
            return UNFOLLOWED
        found = self.def_of(hit.origin.module, hit.origin.symbol, hit.kind)
        return Followed(*found) if found is not None else UNFOLLOWED

    def assigned(self, module: str) -> dict[str, tuple[FileScope | None, dict]]:
        """name -> (file, row) of the last module-level assignment of each name, read once per
        module from the file the module table reads the name from: the `.py`, or the stub for a
        name only the stub binds. The file is None when it is not analyzed."""
        found = self.assigned_by_module.get(module)
        if found is not None:
            return found
        tables = self.resolver.tables
        rows = module_assigns(tables.record(module))
        table = tables.get(module)
        if table is not None and table.stub_names:
            stub = module_assigns(tables.stub_record(module))
            rows.update((name, row) for name, row in stub.items() if name in table.stub_names)
        found = {name: (self.binding_scope(module, name), row) for name, row in rows.items()}
        self.assigned_by_module[module] = found
        return found

    def alias(self, module: str, name: str) -> tuple[FileScope | None, dict] | None:
        """A module-level type alias the bare name binds (`X = dict[str, Any]`): the file and
        row of the assignment, as `assigned` gives them; else None."""
        if "." in name:
            return None
        binding = self.resolver.tables.binding(module, name)
        if binding is None or binding[0] not in ("alias", "variable"):
            return None
        found = self.assigned(module).get(name)
        value = found[1]["v"] if found is not None else None
        if isinstance(value, str) or (isinstance(value, dict) and ("a" in value or "sub" in value or "bin" in value)):
            return found
        return None

    def resolve_dotted(self, dotted: str) -> tuple[Resolved, str] | None:
        """A dotted path in a string. None unless its head names repo code."""
        if self.index.is_external(dotted):
            return None
        hit, rest = self.resolver.resolve_dotted(dotted)
        if hit is None or (rest and hit.kind == "module"):
            return Resolved(Resolution.UNRESOLVED, (), hit.via if hit else ()), ""
        resolved = self.classifier.classify(dotted, hit)
        return resolved, ".".join(rest)


class Producer:
    kind = ""
    reads: frozenset[str] = frozenset()  # fact kinds it reads besides TABLE_KINDS

    @classmethod
    def fact_kinds(cls, config: CoreConfig) -> frozenset[str]:
        """The fact kinds it reads besides TABLE_KINDS under a config."""
        return cls.reads

    @property
    def name(self) -> str:
        return producer_id(self.kind)

    @property
    def edge_kinds(self) -> frozenset[str]:
        return frozenset((self.kind,))

    def run(self, context: BuildContext, graph: Graph) -> None:
        raise NotImplementedError

    @staticmethod
    def edge(
        kind: str,
        scope: FileScope,
        at_scope: int,
        line: int,
        column: int,
        surface: str,
        resolved: Resolved,
        contexts: frozenset[Context],
        use: str = "",
        attrs: tuple[tuple[str, str], ...] = (),
    ) -> Edge:
        return Edge(
            kind=kind,
            site=scope.site(at_scope, line, column),
            surface=surface,
            targets=resolved.targets,
            resolution=resolved.resolution,
            contexts=contexts,
            producer=producer_id(kind),
            via=resolved.via,
            use=use,
            attrs=attrs,
        )


class ImportProducer(Producer):
    """One edge per import binding, in every context. Relative imports become absolute.

    A lazy (PEP 562) name gets an edge too, at the dict entry that names its module: the import
    `__getattr__` runs when the name is first read. It does not run on import, so its context is
    `function_local`, and its `lazy` attr tells it apart from an import written in a function."""

    kind = "import"

    @staticmethod
    def from_target(context: BuildContext, source: str, name: str) -> tuple[str, Hit | None, str]:
        """(surface, hit, loaded module) of `from source import name`."""
        surface = f"{source}.{name}"
        hit = context.resolver.resolve_name(source, name)
        if hit is None and context.index.exists(surface):
            hit = context.resolver.module_hit(surface)
        loaded = surface if context.index.exists(surface) else source
        return surface, hit, loaded

    @staticmethod
    def bound_attrs(context: BuildContext, binds: str, loaded: str) -> list[tuple[str, str]]:
        attrs = [("binds", binds)]
        if context.index.exists(loaded):
            attrs.append(("module", loaded))
        return attrs

    def lazy_imports(self, context: BuildContext, graph: Graph, scope: FileScope) -> None:
        table = context.resolver.tables.get(scope.module)
        if table is None or not table.lazy_entries or table.path != scope.path:
            return
        contexts = scope.contexts(FUNC, -1)
        for entry in table.lazy_entries:
            # Read as `from <module> import <name>`: a third-party module makes an external edge, a
            # first-party one no module answers for an edge that does not resolve.
            surface, hit, loaded = self.from_target(context, entry.module, entry.name)
            attrs = (*self.bound_attrs(context, entry.name, loaded), LAZY_ATTR)
            resolved = context.classifier.classify(surface, hit)
            graph.edges.append(
                self.edge(self.kind, scope, -1, entry.line, entry.column, surface, resolved, contexts, attrs=attrs)
            )

    def run(self, context: BuildContext, graph: Graph) -> None:
        resolver = context.resolver
        for scope in context.files:
            for row in scope.record.get("imports", ()):
                at_scope, line, column, flags, _, _, name, _ = row
                contexts = scope.contexts(flags, at_scope)
                bound, surface, source = import_surface(scope.module, scope.is_package, row)
                if source is None:
                    # The name stays bound, so a reader of what the module hands out sees it unresolved.
                    attrs = (("binds", bound),)
                    graph.edges.append(
                        self.edge(self.kind, scope, at_scope, line, column, surface, UNRESOLVED, contexts, attrs=attrs)
                    )
                    continue
                if name is None or name == "*":
                    hit, loaded = resolver.module_hit(source), source
                else:
                    _, hit, loaded = self.from_target(context, source, name)
                attrs = self.bound_attrs(context, bound, loaded)
                resolved = context.classifier.classify(surface, hit)
                graph.edges.append(
                    self.edge(self.kind, scope, at_scope, line, column, surface, resolved, contexts, attrs=tuple(attrs))
                )
            self.lazy_imports(context, graph, scope)


class ReferenceProducer(Producer):
    """Use sites of first-party names (the extractor's per-site `refs`) with a neutral use position.

    Names a function derives from such a use (flow facts) are left to packs.
    """

    kind = "reference"
    reads = frozenset({"refs"})

    def run(self, context: BuildContext, graph: Graph) -> None:
        for scope in context.files:
            for at_scope, header, line, column, flags, name, chain, up in scope.record.get("refs", ()):
                tokens = chain_tokens(chain)
                attrs = leading_attrs(tokens)
                found, rest = context.names.descend(context.name_lookup(scope, name, at_scope, line), attrs)
                remaining = tokens[len(attrs) - len(rest) :]
                use = use_position(remaining[0] if remaining else None, up)
                extra = [("chain", chain_text(remaining)), ("up", up)]
                if header != -1:
                    extra.append(("header", scope.qualnames[header]))
                resolved = context.classifier.classify_lookup(found)
                edge = self.edge(
                    self.kind,
                    scope,
                    at_scope,
                    line,
                    column,
                    found.surface,
                    resolved,
                    scope.contexts(flags, at_scope),
                    use=use,
                    attrs=tuple(extra),
                )
                graph.edges.append(edge)


@dataclass(frozen=True, slots=True)
class SlotSite:
    """Where an `exposes` edge is written."""

    scope: FileScope
    at_scope: int
    line: int
    column: int
    contexts: frozenset[Context]

    @staticmethod
    def module_level(scope: FileScope, line: int, column: int) -> SlotSite:
        return SlotSite(scope, -1, line, column, scope.base_contexts)

    def edge(self, surface: str, resolved: Resolved, attrs: tuple[tuple[str, str], ...]) -> Edge:
        return Producer.edge(
            "exposes",
            self.scope,
            self.at_scope,
            self.line,
            self.column,
            surface,
            resolved,
            self.contexts,
            use="annotation",
            attrs=attrs,
        )


@dataclass(frozen=True, slots=True)
class SlotLookup:
    """Where the names of a slot's annotation are looked up: the def's file, scope and line."""

    scope: FileScope
    at_scope: int
    line: int


@dataclass(frozen=True, slots=True)
class Field:
    """A field of a dataclass's generated `__init__`: its row, where its names are looked up, and
    whether the class itself declares it (else a base does)."""

    row: dict
    lookup: SlotLookup
    own: bool


@dataclass(frozen=True, slots=True)
class Mention:
    """A dotted name in a type expression: whether it is the whole annotation, and where it is
    looked up (an alias's names are looked up where the alias is assigned)."""

    value: str
    position: str
    lookup: SlotLookup


@dataclass(frozen=True, slots=True)
class Expanded:
    """A name a type alias expands to: whether it sits at the top of the expansion (the whole
    annotation, when the alias is), where it is looked up, whether the expansion stopped at it
    because the alias it names is too deep to follow, whether it is given arguments, and the
    generic it is an argument of, as written ("" for none)."""

    value: str
    top: bool
    lookup: SlotLookup
    too_deep: bool = False
    subscripted: bool = False
    argument_of: str = ""


@dataclass(frozen=False, slots=True)
class Expansion:
    """One alias's expansion while it is worked out: the aliases above the current one
    (`stack`), the (alias, top) pairs followed, and the names written, each once, in order."""

    stack: list[tuple[str, str]]
    followed: set[tuple[tuple[str, str], bool]] = field(default_factory=set)
    seen: set[Expanded] = field(default_factory=set)
    out: list[Expanded] = field(default_factory=list)

    def write(self, item: Expanded) -> None:
        if item not in self.seen:
            self.seen.add(item)
            self.out.append(item)


@dataclass(frozen=True, slots=True)
class SlotRows:
    """What every `exposes` row of one callable shares. `handed` is the clause a handed-out
    callable comes by, empty for the module's own."""

    site: SlotSite
    callable_name: str
    handed: str = ""

    def edge(self, surface: str, resolved: Resolved, slot: str, position: str, *extra: tuple[str, str]) -> Edge:
        attrs = (("callable", self.callable_name), ("slot", slot), ("position", position), *extra)
        if self.handed:
            attrs = (*attrs, ("handed", self.handed))
        return self.site.edge(surface, resolved, attrs)

    def unresolved(self, surface: str, slot: str, *extra: tuple[str, str]) -> Edge:
        """A row for a whole slot whose types are unknown."""
        return self.edge(surface, UNRESOLVED, slot, "whole", *extra)


class DeclarationProducer(Producer):
    """inherit, decorate, exposes and declares edges from def and class records.

    In a contract surface (`CoreConfig.contract_surfaces`) every public callable exposes its whole
    signature: a slot with no annotation, builtin types, and the slots of the functions and
    dataclass fields the module hands out from elsewhere (`surfaces.HandedOut`).
    """

    kind = "declaration"
    kinds = ("inherit", "decorate", "exposes", "declares")
    # `callees`: a call on `__all__` makes a contract's hand-out unknown. `refs` and `uses`: the
    # names a contract module refers to itself.
    contract_reads = frozenset({"callees", "refs", "uses"})

    @classmethod
    def fact_kinds(cls, config: CoreConfig) -> frozenset[str]:
        """Only a contract or export surface reads facts besides the tables."""
        return cls.contract_reads if config.contract_surfaces or config.export_surfaces else frozenset()

    def __init__(self) -> None:
        self.expansions: dict[tuple[tuple[str, str], SlotLookup], list[Expanded]] = {}
        self.fields: dict[str, dict[int, list[dict]]] = {}
        self.dataclass_bases: dict[tuple[str, int], bool] = {}
        self.roots: dict[str, frozenset[tuple[int, str]]] = {}

    @property
    def edge_kinds(self) -> frozenset[str]:
        return frozenset(self.kinds)

    def fields_by_class(self, scope: FileScope) -> dict[int, list[dict]]:
        """Annotated single-name assignments by the def they sit in, read once per file: the
        candidate dataclass fields."""
        found = self.fields.get(scope.path)
        if found is not None:
            return found
        out: dict[int, list[dict]] = {}
        for row in scope.record.get("assigns", ()):
            if row["op"] == ":" and len(row["t"]) == 1 and isinstance(row["t"][0], str) and "." not in row["t"][0]:
                out.setdefault(row["s"], []).append(row)
        self.fields[scope.path] = out
        return out

    def contract_roots(self, context: BuildContext, scope: FileScope) -> frozenset[tuple[int, str]]:
        """(export, root) of every contract surface that covers a file, read once per file; none
        for a test file, which hands nothing out. A file is a contract surface when one covers
        it, and two files that share one belong to the same export of the same captured root."""
        if not context.config.contract_surfaces:
            return frozenset()
        found = self.roots.get(scope.path)
        if found is None:
            test = Context.TEST in scope.base_contexts
            found = self.roots[scope.path] = frozenset() if test else context.config.surface_roots(scope.path)
        return found

    @staticmethod
    def in_contract(scope: FileScope, idx: int) -> bool:
        """Whether a def is a public callable of the module's contract: a top-level function or
        class, or a method directly in a top-level class, with no leading `_` (`__init__` kept)."""
        node = scope.defs[idx]
        parent = node["s"]
        if parent == -1:
            return node.get("f", 0) == 0 and not node["n"].startswith("_")
        owner = scope.defs[parent]
        if node["k"] == "class" or owner["k"] != "class" or not DeclarationProducer.in_contract(scope, parent):
            return False
        if owner["s"] != -1 or node.get("f", 0) != CLASS:
            return False
        return not node["n"].startswith("_") or node["n"] == "__init__"

    @staticmethod
    def lookup_at(context: BuildContext, place: SlotLookup, name: str) -> Lookup:
        """A dotted name, looked up where a slot's names are."""
        return context.dotted_lookup(place.scope, name, place.at_scope, place.line)

    def mentions(self, value: object, position: str, argument_of: str = "") -> list[tuple[str, str, bool, str]]:
        """Every dotted name in a type expression, with whether it is the whole annotation,
        whether it is a generic given arguments (`dict[str, str]`, not `dict`), and the generic
        it is an argument of, as written. `Annotated` passes its type through as it stands."""
        if isinstance(value, str):
            return [(value, position, False, argument_of)]
        if not isinstance(value, dict):
            return []
        if "sa" in value:
            return self.mentions(value["sa"], position, argument_of)
        if "sub" in value:
            outer = [(name, at, True, of) for name, at, _, of in self.mentions(value["sub"], position, argument_of)]
            container = value["sub"] if isinstance(value["sub"], str) else ""
            last = container.rpartition(".")[2]
            if last == "Literal":
                return outer
            index = value.get("i")
            if last == "Annotated" and isinstance(index, dict) and "tuple" in index and index["tuple"]:
                return outer + self.mentions(index["tuple"][0], "nested", argument_of)
            return outer + self.mentions(index, "nested", container)
        if "bin" in value and value["bin"] == "|":
            return self.mentions(value["l"], position, argument_of) + self.mentions(value["r"], position, argument_of)
        for key in ("tuple", "list"):
            if key in value:
                return [m for item in value[key] for m in self.mentions(item, "nested", argument_of)]
        return []

    def argument_marker(
        self, context: BuildContext, lookup: SlotLookup, argument_of: str
    ) -> tuple[tuple[str, str], ...]:
        """The `argument_of` attr of a type argument: the generic it is given to, as it resolves."""
        if not argument_of:
            return ()
        return ((ARGUMENT_OF, self.lookup_at(context, lookup, argument_of).surface),)

    def markers(
        self, context: BuildContext, expanded: bool, subscripted: bool, lookup: SlotLookup, argument_of: str
    ) -> tuple[tuple[str, str], ...]:
        """The attrs that tell a slot's rows of one name apart: an expanded alias, a generic
        given arguments, and the generic it is an argument of."""
        return (
            *((EXPANDED,) if expanded else ()),
            *((SUBSCRIPTED,) if subscripted else ()),
            *self.argument_marker(context, lookup, argument_of),
        )

    def alias_of(
        self, context: BuildContext, mention: Mention, found: Lookup
    ) -> tuple[tuple[str, str], object, SlotLookup] | None:
        """The type alias a mention names, as (module, name), its value, and where its names are
        looked up: assigned in the module that uses it, or followed through imports to the
        first-party module that assigns it. A module holds `Handler = Callable[[Order], None]` to
        shorten a signature, not to hide its types."""
        module = mention.lookup.scope.module
        local = context.alias(module, mention.value)
        if local is not None:
            return (module, mention.value), local[1]["v"], mention.lookup
        hit = found.hit
        if hit is None or hit.kind != "variable":
            return None
        if not context.first_party_chain(hit):
            return None
        origin = hit.origin
        imported = context.alias(origin.module, origin.symbol)
        if imported is None:
            return None
        scope, row = imported
        if scope is None:
            return None
        return (origin.module, origin.symbol), row["v"], SlotLookup(scope, -1, row["l"])

    def expand_into(
        self,
        context: BuildContext,
        value: object,
        where: SlotLookup,
        top: bool,
        expansion: Expansion,
    ) -> None:
        """Depth first, the names in an alias's value, each alias in it followed once per place
        (top or nested). A cycle stops at the alias that closes it; an alias deeper than
        MAX_ALIAS_DEPTH stays a marker, because a type left unread is unknown."""
        for name, position, subscripted, argument_of in self.mentions(value, "whole"):
            item = Expanded(name, top and position == "whole", where, subscripted=subscripted, argument_of=argument_of)
            expansion.write(item)
            found = self.lookup_at(context, where, name)
            alias = self.alias_of(context, Mention(name, position, where), found)
            if alias is None or alias[0] in expansion.stack or (alias[0], item.top) in expansion.followed:
                continue
            expansion.followed.add((alias[0], item.top))
            if len(expansion.stack) >= MAX_ALIAS_DEPTH:
                expansion.write(replace(item, too_deep=True))
                continue
            expansion.stack.append(alias[0])
            self.expand_into(context, alias[1], alias[2], item.top, expansion)
            expansion.stack.pop()

    def expanded_in(self, context: BuildContext, expansion: list[Expanded], item: Expanded, found: Lookup) -> bool:
        """Whether a name of an expansion is a type alias whose own expansion was written there: it
        was followed, not left as a too-deep marker, and it names at least one type."""
        if replace(item, too_deep=True) in expansion:
            return False
        alias = self.alias_of(context, Mention(item.value, "whole" if item.top else "nested", item.lookup), found)
        return alias is not None and bool(self.expansion(context, *alias))

    def expansion(
        self, context: BuildContext, key: tuple[str, str], value: object, where: SlotLookup
    ) -> list[Expanded]:
        """The names a type alias expands to, worked out once per alias and place its names are
        looked up: the alias decides them, never the slot that names it."""
        found = self.expansions.get((key, where))
        if found is None:
            expansion = Expansion([key])
            self.expand_into(context, value, where, True, expansion)
            found = self.expansions[(key, where)] = expansion.out
        return found

    @staticmethod
    def plain_builtin(found: Lookup, name: str) -> bool:
        """Whether a name resolves to a plain builtin: outside a contract it gets no row."""
        return found.hit is None and found.surface == f"builtins.{name.split('.')[0]}"

    def expose_mentions(
        self,
        context: BuildContext,
        graph: Graph,
        rows: SlotRows,
        slot: str,
        contract: bool,
        lookup: SlotLookup,
        names: list[tuple[str, str, bool, str]],
    ) -> None:
        """One row per mention, each followed by a row per name of the type alias it is, when it
        is one: what an alias stands for is a fact in every file. Outside a contract a name that
        resolves to a plain builtin gets no row, in the alias's expansion too. A slot names each
        row an alias expands to once, however often its aliases repeat it; rows that differ in an
        attr (such as the generic a name is an argument of) are different rows."""
        written: set[tuple[str, str, tuple[tuple[str, str], ...]]] = set()
        for value, mentioned, subscripted, argument_of in names:
            found = self.lookup_at(context, lookup, value)
            alias = self.alias_of(context, Mention(value, mentioned, lookup), found)
            expansion = self.expansion(context, *alias) if alias is not None else None
            if contract or not self.plain_builtin(found, value):
                resolved = context.classifier.classify_lookup(found)
                marker = self.markers(context, bool(expansion), subscripted, lookup, argument_of)
                graph.edges.append(rows.edge(found.surface, resolved, slot, mentioned, *marker))
            if expansion is None:
                continue
            for item in expansion:
                position = "whole" if mentioned == "whole" and item.top else "nested"
                inner = self.lookup_at(context, item.lookup, item.value)
                if item.too_deep:
                    if (item.value, position, TOO_DEEP) not in written:
                        written.add((item.value, position, TOO_DEEP))
                        graph.edges.append(rows.edge(item.value, UNRESOLVED, slot, position, *TOO_DEEP))
                    continue
                if not contract and self.plain_builtin(inner, item.value):
                    continue
                expanded = self.expanded_in(context, expansion, item, inner)
                marker = self.markers(context, expanded, item.subscripted, item.lookup, item.argument_of)
                if (inner.surface, position, marker) in written:
                    continue
                written.add((inner.surface, position, marker))
                resolved = context.classifier.classify_lookup(inner)
                graph.edges.append(rows.edge(inner.surface, resolved, slot, position, *marker))

    def expose(
        self,
        context: BuildContext,
        graph: Graph,
        rows: SlotRows,
        lookup: SlotLookup,
        annotation: object,
        slot: str,
        contract: bool,
    ) -> None:
        """One `exposes` edge per type name in a slot. Builtin types count only in a contract,
        and there an unannotated slot (annotation None) is an edge of its own."""
        if annotation is None:
            graph.edges.append(rows.unresolved("", slot, ("annotation", "missing")))
            return
        self.expose_mentions(context, graph, rows, slot, contract, lookup, self.mentions(annotation, "whole"))

    @staticmethod
    def has_receiver(context: BuildContext, lookup: SlotLookup, node: dict) -> bool:
        """Whether a def's first parameter is bound by the call, whatever its name: a method's
        first positional parameter, unless the method is a `staticmethod`."""
        parent = node["s"]
        params = node.get("params", ())
        if parent == -1 or lookup.scope.defs[parent]["k"] != "class":
            return False
        if not params or params[0][1] not in ("po", "a"):
            return False
        for dec in node.get("dec", ()):
            found = context.value_lookup(lookup.scope, dec, parent, node["l"])
            if found is not None and found.surface == "builtins.staticmethod":
                return False
        return True

    def expose_signature(
        self,
        context: BuildContext,
        graph: Graph,
        rows: SlotRows,
        lookup: SlotLookup,
        node: dict,
        contract: bool,
    ) -> None:
        """The parameter and return slots of a function. In a contract a method's receiver and an
        unannotated return are no slots, and an unannotated parameter is one."""
        receiver = contract and self.has_receiver(context, lookup, node)
        for position, param in enumerate(node.get("params", ())):
            annotation = param[2] if len(param) > 2 else None
            in_slot = contract and not (receiver and position == 0)
            if not in_slot and annotation is None:
                continue
            self.expose(context, graph, rows, lookup, annotation, f"param:{param[0]}", in_slot)
        if node.get("ret") is not None:
            self.expose(context, graph, rows, lookup, node["ret"], "return", contract)

    def expose_fields(
        self,
        context: BuildContext,
        graph: Graph,
        rows: SlotRows,
        fields: list[Field],
        contract: bool,
        kind: str = "field",
    ) -> None:
        """The annotated class-body rows of a class as slots of `kind`: the fields of a
        dataclass-like class as `field:` slots of its `__init__`, or the attributes of a class that
        is no dataclass as `attr:` slots of the class. In a contract only a row directly in a class
        body is a whole slot, and a `ClassVar` is no field (an attribute it is: callers read it). A
        row the class itself declares sits at its own line, unless the class is handed out."""
        for member in fields:
            row = member.row
            if row.get("ann") is None:
                continue
            in_slot = contract and row.get("f", 0) == CLASS and (kind == "attr" or not is_class_var(row["ann"]))
            if rows.handed and not in_slot:
                continue
            line_rows = rows
            if member.own and not rows.handed:
                site = rows.site
                line_site = SlotSite(site.scope, site.at_scope, row["l"], site.column, site.contexts)
                line_rows = SlotRows(line_site, rows.callable_name)
            place = member.lookup
            line_lookup = SlotLookup(place.scope, place.at_scope, row["l"])
            self.expose(context, graph, line_rows, line_lookup, row["ann"], f"{kind}:{row['t'][0]}", in_slot)

    def lineage(self, context: BuildContext, scope: FileScope, idx: int) -> list[tuple[FileScope, int]]:
        """A class and its ancestors in MRO order, through the link's own class resolution, each
        as its file and def index. An ancestor whose file is not analyzed is left out."""
        classes = context.classes
        own = entity(scope.module, scope.qualnames[idx])
        out = [(scope, idx)]
        for ref in classes.ancestors(classes.resolved_bases(scope.module, scope.defs[idx])):
            found = context.def_of(ref.module, ref.symbol, "class") if ref != own else None
            if found is not None:
                out.append(found)
        return out

    @staticmethod
    def named_in(wanted: tuple[str, ...], surface: str, resolved: Resolved) -> bool:
        """Whether a name is one `wanted` holds, by its spelling or by its target."""
        return surface in wanted or any(str(target) in wanted for target in resolved.targets)

    @classmethod
    def is_dataclass_decorator(cls, context: BuildContext, surface: str, resolved: Resolved) -> bool:
        """Whether a decorator is one `dataclass_decorators` names, by spelling or target."""
        return cls.named_in(context.config.dataclass_decorators, surface, resolved)

    def has_dataclass_base(self, context: BuildContext, scope: FileScope, idx: int) -> bool:
        """Whether a class or one of its ancestors names a base of `dataclass_bases`, by spelling
        or target: a model library's base makes annotated class attributes `__init__` fields.
        Worked out once per class."""
        if not context.config.dataclass_bases:
            return False
        key = (scope.path, idx)
        found = self.dataclass_bases.get(key)
        if found is None:
            found = self.dataclass_bases[key] = self.find_dataclass_base(context, scope, idx)
        return found

    def find_dataclass_base(self, context: BuildContext, scope: FileScope, idx: int) -> bool:
        wanted = context.config.dataclass_bases
        for owner, at in self.lineage(context, scope, idx):
            node = owner.defs[at]
            for base in node.get("bases", ()):
                found = context.value_lookup(owner, base, node["s"], node["l"])
                if found is None:
                    continue
                if self.named_in(wanted, found.surface, context.classifier.classify_lookup(found)):
                    return True
        return False

    def is_dataclass_like(self, context: BuildContext, scope: FileScope, idx: int) -> bool:
        """Whether a class takes a decorator that makes it a dataclass, or has a dataclass base."""
        node = scope.defs[idx]
        for dec in node.get("dec", ()):
            found = context.value_lookup(scope, dec, node["s"], node["l"])
            if found is None:
                continue
            if self.is_dataclass_decorator(context, found.surface, context.classifier.classify_lookup(found)):
                return True
        return self.has_dataclass_base(context, scope, idx)

    def mro_fields(
        self, context: BuildContext, lineage: list[tuple[FileScope, int]], lookup: SlotLookup
    ) -> list[Field]:
        """The fields of the generated `__init__`, as the dataclass machinery collects them: from
        every dataclass-like class along the MRO, base first, a redefinition replacing the
        earlier field in its place."""
        fields: dict[str, Field] = {}
        for position in range(len(lineage) - 1, -1, -1):
            scope, idx = lineage[position]
            node = scope.defs[idx]
            if position and not self.is_dataclass_like(context, scope, idx):
                continue
            where = lookup if not position else SlotLookup(scope, node["s"], node["l"])
            for row in self.fields_by_class(scope).get(idx, []):
                fields[row["t"][0]] = Field(row, where, not position)
        return list(fields.values())

    def expose_unresolved_bases(
        self,
        context: BuildContext,
        graph: Graph,
        rows: SlotRows,
        lineage: list[tuple[FileScope, int]],
    ) -> None:
        """One unresolved row per base along the MRO that does not resolve or is a call: the
        fields it adds to the generated `__init__` are unknown."""
        for scope, idx in lineage:
            node = scope.defs[idx]
            for base in node.get("bases", ()):
                found = context.value_lookup(scope, base, node["s"], node["l"])
                resolved = (
                    found is not None and context.classifier.classify_lookup(found).resolution != Resolution.UNRESOLVED
                )
                if resolved and not called_base(base):
                    continue
                graph.edges.append(rows.unresolved(found.surface if found else "", "fields"))

    def expose_dataclass(
        self,
        context: BuildContext,
        graph: Graph,
        rows: SlotRows,
        lookup: SlotLookup,
        idx: int,
    ) -> None:
        """The `__init__` slots of a contract dataclass (its def index in the file `lookup` names):
        a row per unresolved base along its MRO, then its fields as the dataclass machinery
        collects them."""
        lineage = self.lineage(context, lookup.scope, idx)
        self.expose_unresolved_bases(context, graph, rows, lineage)
        self.expose_fields(context, graph, rows, self.mro_fields(context, lineage, lookup), True)

    def declare(
        self,
        context: BuildContext,
        graph: Graph,
        scope: FileScope,
        idx: int,
        node: dict,
        contract: bool,
    ) -> None:
        qualname = scope.qualnames[idx]
        enclosing = node["s"]
        line, column = node["l"], node["c"]
        contexts = scope.contexts(node.get("f", 0), enclosing)
        nested = scope.in_function[idx]
        graph.symbols[f"{scope.module}.{qualname}"] = {"kind": node["k"], "path": scope.path}
        if not nested:
            resolved = Resolved(Resolution.RESOLVED, (entity(scope.module, qualname),), ())
            attrs = (("decl", node["k"]), ("body", "0" if node.get("stub") else "1"))
            graph.edges.append(
                self.edge(
                    "declares",
                    scope,
                    enclosing,
                    line,
                    column,
                    f"{scope.module}.{qualname}",
                    resolved,
                    contexts,
                    attrs=attrs,
                )
            )
        dataclass = False
        for dec in node.get("dec", ()):
            found = context.value_lookup(scope, dec, enclosing, line)
            surface = found.surface if found else ""
            resolved = context.classifier.classify_lookup(found) if found else UNRESOLVED
            called = "1" if isinstance(dec, dict) and "call" in dec else "0"
            attrs: tuple[tuple[str, str], ...] = (("decorated", qualname), ("called", called))
            if called == "1" and frozen_keyword(dec):
                attrs = (*attrs, ("frozen", "1"))
            graph.edges.append(
                self.edge(
                    "decorate",
                    scope,
                    enclosing,
                    line,
                    column,
                    surface,
                    resolved,
                    contexts,
                    use="decorator",
                    attrs=attrs,
                )
            )
            dataclass |= self.is_dataclass_decorator(context, surface, resolved)
        is_class = node["k"] == "class"
        if is_class:
            for position, base in enumerate(node.get("bases", ())):
                found = context.value_lookup(scope, base, enclosing, line)
                surface = found.surface if found else ""
                resolved = context.classifier.classify_lookup(found) if found else UNRESOLVED
                attrs = (("class", qualname), ("index", str(position)))
                if called_base(base):
                    attrs = (*attrs, ("called", "1"))
                elif found is not None and found.hit is not None and context.followed_base(found.hit, resolved):
                    classes = context.hit_classes(surface, found.hit, frozenset())
                    attrs = (*attrs, ("classes", ",".join(classes) if classes is not None else "?"))
                graph.edges.append(
                    self.edge(
                        "inherit", scope, enclosing, line, column, surface, resolved, contexts, use="base", attrs=attrs
                    )
                )
        in_contract = contract and self.in_contract(scope, idx)
        # Only a class whose fields can be slots asks for its dataclass base: one that declares a
        # field, or one in a contract, which also exposes the fields it inherits.
        if (
            is_class
            and not dataclass
            and not nested
            and context.config.dataclass_bases
            and (bool(self.fields_by_class(scope).get(idx)) or in_contract)
        ):
            dataclass = self.has_dataclass_base(context, scope, idx)
        if nested:
            return
        site = SlotSite(scope, enclosing, line, column, contexts)
        lookup = SlotLookup(scope, enclosing, line)
        if not is_class:
            self.expose_signature(context, graph, SlotRows(site, qualname), lookup, node, in_contract)
            return
        own = [Field(row, lookup, True) for row in self.fields_by_class(scope).get(idx, [])]
        if not dataclass:
            self.expose_fields(context, graph, SlotRows(site, qualname), own, in_contract, "attr")
            return
        rows = SlotRows(site, f"{qualname}.__init__")
        if not in_contract:
            self.expose_fields(context, graph, rows, own, False)
            return
        self.expose_dataclass(context, graph, rows, lookup, idx)

    def used_names(self, scope: FileScope) -> frozenset[str]:
        """The import-bound names a module's own code refers to: every `refs` and `uses` row, in
        any scope, and the type names of its annotations, which also reads string annotations."""
        names = {row[5] for row in scope.record.get("refs", ())}
        names.update(row[2] for row in scope.record.get("uses", ()))
        annotations: list[object] = [row.get("ann") for row in scope.record.get("assigns", ())]
        for node in scope.defs:
            annotations.extend(param[2] for param in node.get("params", ()) if len(param) > 2)
            annotations.append(node.get("ret"))
        for annotation in annotations:
            if annotation is not None:
                names.update(value.split(".")[0] for value, _, _, _ in self.mentions(annotation, "whole"))
        return frozenset(names)

    def hand_out(self, context: BuildContext, graph: Graph, scope: FileScope) -> None:
        """The slots of the functions and dataclass fields a contract surface hands out from other
        modules, at the import that hands each one out. An `__all__` it cannot read is one
        unresolved row: what the module hands out is unknown. So is a handed-out name the engine
        cannot follow to a def. A def in a file of the same export and root (the same product's
        facade) gets no rows here: that file reports it."""
        roots = self.contract_roots(context, scope)
        handed = HandedOut(scope, context.resolver.tables.get(scope.module), context.resolver.tables)
        unread = handed.dunder_all.unread
        if not handed.no_functions and unread is not None:
            line, column = unread
            rows = SlotRows(SlotSite.module_level(scope, line, column), "__all__", ALL_CLAUSE)
            graph.edges.append(rows.unresolved("__all__", "names"))
        used = self.used_names(scope) if handed.no_functions else frozenset()
        for item in handed.names(used):
            found = context.handed_def(item.target)
            if found is None:
                continue
            rows = SlotRows(SlotSite.module_level(scope, item.line, item.column), item.bound, item.clause)
            if found.scope is None:
                graph.edges.append(rows.unresolved(item.target, "signature"))
                continue
            origin, idx = found.scope, found.idx
            if roots & self.contract_roots(context, origin):
                continue
            node = origin.defs[idx]
            lookup = SlotLookup(origin, node["s"], node["l"])
            if node["k"] != "class":
                self.expose_signature(context, graph, rows, lookup, node, True)
                continue
            if not self.is_dataclass_like(context, origin, idx):
                continue
            init = SlotRows(rows.site, f"{item.bound}.__init__", item.clause)
            self.expose_dataclass(context, graph, init, lookup, idx)

    def record_hand_out(self, context: BuildContext, graph: Graph, scope: FileScope) -> None:
        """What an export module hands out by import, for every rule that asks what it exports.
        A test file hands nothing out."""
        if Context.TEST in scope.base_contexts or not context.config.in_export(scope.path):
            return
        handed = HandedOut(scope, context.resolver.tables.get(scope.module), context.resolver.tables)
        used = self.used_names(scope) if handed.no_functions else frozenset()
        graph.hand_outs[scope.module] = handed.hand_out(used)

    def run(self, context: BuildContext, graph: Graph) -> None:
        for scope in context.files:
            contract = bool(self.contract_roots(context, scope))
            for idx, node in enumerate(scope.defs):
                self.declare(context, graph, scope, idx, node, contract)
            if contract:
                self.hand_out(context, graph, scope)
            self.record_hand_out(context, graph, scope)


class StringProducer(Producer):
    """dotted-path edges: string literals that name repo modules or symbols. A string that names
    nothing is an edge only where the code holds a dotted path (`in_path_position`).

    A string made of several literal parts gives one edge per dotted part and one for the
    joined value, so consumers that check parts alone (as `tach` does) can select them.
    """

    kind = "dotted-path"
    reads = frozenset({"strs"})

    @staticmethod
    def first_step(up: str) -> tuple[str, str, str]:
        """The first step of `up` (`arg:0:callee`, `kw:target:callee`): its kind, its position or
        keyword, and the last name of its callee."""
        kind, _, rest = up.split(" ")[0].partition(":")
        slot, _, callee = rest.partition(":")
        return kind, slot, callee.rpartition(".")[2]

    @staticmethod
    def in_path_position(up: str, role: str, path_callees: tuple[str, ...]) -> bool:
        """Whether a string sits where the code holds a dotted path: a patch target, or the first
        argument of a callee `path_callees` names. Only there does a string that resolves to
        nothing stay an edge; anywhere else it is a label, a name or a host, not a path."""
        if role == "patch-target":
            return True
        kind, slot, callee = StringProducer.first_step(up)
        return kind == "arg" and slot == "0" and callee in path_callees

    @staticmethod
    def role(up: str, patch_callees: tuple[str, ...]) -> str:
        kind, slot, callee = StringProducer.first_step(up)
        if kind == "arg" and slot == "0" and callee in patch_callees:
            return "patch-target"
        if kind == "kw" and slot == "target" and callee in patch_callees:
            return "patch-target"
        return "literal"

    def run(self, context: BuildContext, graph: Graph) -> None:
        patch_callees = context.config.patch_callees
        path_callees = context.config.path_callees
        for scope in context.files:
            for row in scope.record.get("strs", ()):
                at_scope, _header, line, column, flags, value, _chain, up = row[:8]
                parts = row[8] if len(row) > 8 else None
                candidates: list[tuple[str, str]] = []
                if parts is None:
                    if value is not None:
                        candidates.append((value, "whole"))
                else:
                    candidates.extend((part, "part") for part in parts if part is not None and "." in part)
                    if value is not None:
                        candidates.append((value, "joined"))
                role = self.role(up, patch_callees)
                path_position = self.in_path_position(up, role, path_callees)
                for text, literal in candidates:
                    found = context.resolve_dotted(text)
                    if found is None:
                        continue
                    resolved, member = found
                    if resolved.resolution == Resolution.UNRESOLVED and not path_position:
                        continue
                    attrs = [("literal", literal), ("role", role), ("up", up)]
                    if member:
                        attrs.append(("member", member))
                    if resolved.targets and resolved.resolution != Resolution.UNRESOLVED:
                        module = context.index.longest_module(text)
                        if module is not None:
                            attrs.append(("module", module))
                    edge = self.edge(
                        self.kind,
                        scope,
                        at_scope,
                        line,
                        column,
                        text,
                        resolved,
                        scope.contexts(flags, at_scope),
                        attrs=tuple(attrs),
                    )
                    graph.edges.append(edge)


class DynamicImportProducer(Producer):
    """Calls to declared loaders. A literal module name resolves; anything else is unresolved."""

    kind = "dynamic-import"
    reads = frozenset({"calls"})

    @staticmethod
    def module_argument(args: list, kws: list) -> object:
        """The module name: the first positional argument, else the `name` keyword."""
        if args:
            return args[0]
        for keyword, value in kws:
            if keyword == "name":
                return value
        return None

    @staticmethod
    def resolve_literal(context: BuildContext, dotted: str) -> Resolved:
        if not dotted or dotted.startswith("."):
            return UNRESOLVED
        found = context.resolve_dotted(dotted)
        if found is None:
            return Resolved(Resolution.EXTERNAL, (entity(dotted),), ())
        return found[0]

    @staticmethod
    def describe(argument: object) -> str:
        if argument is None:
            return "missing"
        if isinstance(argument, str):
            return f"name:{argument}"
        if isinstance(argument, dict) and "o" in argument:
            return f"expr:{argument['o']}"
        return "expr"

    def run(self, context: BuildContext, graph: Graph) -> None:
        loaders = frozenset(context.config.dynamic_loaders)
        names = frozenset(context.config.loader_callee_names())
        for scope in context.files:
            for row in scope.record.get("calls", ()):
                at_scope, _header, line, column, flags, callee, args, kws = row
                if callee.rpartition(".")[2] not in names or callee.startswith("?"):
                    continue
                found, rest = context.chain_lookup(scope, callee, at_scope, line)
                loader = ".".join([found.surface, *rest])
                origin = str(found.hit.origin) if found.hit is not None else ""
                if loader not in loaders and origin not in loaders:
                    continue
                argument = self.module_argument(args, kws)
                contexts = scope.contexts(flags, at_scope)
                attrs = [("loader", loader)]
                if isinstance(argument, dict) and set(argument) == {"s"}:
                    surface = argument["s"]
                    resolved = self.resolve_literal(context, surface)
                    attrs.append(("argument", "literal"))
                else:
                    surface = ""
                    resolved = UNRESOLVED
                    attrs.append(("argument", self.describe(argument)))
                graph.edges.append(
                    self.edge(
                        self.kind,
                        scope,
                        at_scope,
                        line,
                        column,
                        surface,
                        resolved,
                        contexts,
                        use="call",
                        attrs=tuple(attrs),
                    )
                )


class ParseFailureProducer(Producer):
    """A file that did not parse is an unresolved fact, never an absence."""

    kind = "unparsed"

    def run(self, context: BuildContext, graph: Graph) -> None:
        for scope in context.files:
            status = scope.record.get("status")
            if status == "ok":
                continue
            resolved = UNRESOLVED
            attrs = (("status", str(status)),)
            graph.edges.append(
                self.edge(self.kind, scope, -1, 0, 0, scope.path, resolved, scope.base_contexts, attrs=attrs)
            )


PRODUCERS: tuple[type[Producer], ...] = (
    ImportProducer,
    ReferenceProducer,
    DeclarationProducer,
    StringProducer,
    DynamicImportProducer,
    ParseFailureProducer,
)
