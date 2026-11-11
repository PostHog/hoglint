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
from hoglint.core.records import chain_last
from hoglint.core.sites import Classifier, FileScope, Lookup, NameLookup, Resolved
from hoglint.core.surfaces import HandedOut
from hoglint.core.symbols import CLASS, MODULE_LEVEL_FLAGS, Hit, Resolver, Tables, absolute_import, entity
from hoglint.facts import FactStore
from hoglint.model import Context, Edge, Graph, Resolution

PRODUCER_VERSION = 1
UNRESOLVED = Resolved(Resolution.UNRESOLVED, (), ())
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


class AliasValues:
    """The value of each module-level name a module table binds by assignment, read once per
    module from the file the table reads the name from: the `.py`, or the stub for a name only
    the stub binds."""

    def __init__(self, tables: Tables) -> None:
        self.tables = tables
        self.by_module: dict[str, dict[str, dict]] = {}

    @staticmethod
    def assigned(record: dict | None) -> dict[str, dict]:
        """name -> row of its last module-level assignment with a value, in one record."""
        values: dict[str, dict] = {}
        if record is None or record.get("status") != "ok":
            return values
        for row in record.get("assigns", ()):
            if row["s"] != -1 or row.get("f", 0) & ~MODULE_LEVEL_FLAGS or row["op"] not in ("=", ":", "type"):
                continue
            if "v" not in row:
                continue
            for target in row["t"]:
                if isinstance(target, str) and "." not in target:
                    values[target] = row
        return values

    def of(self, module: str) -> dict[str, dict]:
        found = self.by_module.get(module)
        if found is not None:
            return found
        found = self.assigned(self.tables.record(module))
        table = self.tables.get(module)
        if table is not None and table.stub_names:
            stub = self.assigned(self.tables.stub_record(module))
            found.update((name, value) for name, value in stub.items() if name in table.stub_names)
        self.by_module[module] = found
        return found


@dataclass(frozen=False)
class BuildContext:
    """What every producer reads. Built once per run."""

    config: CoreConfig
    store: FactStore
    index: ModuleIndex
    resolver: Resolver
    files: list[FileScope]
    first_party: FirstParty
    scope_of_path: dict[str, FileScope] = field(init=False)

    def __post_init__(self) -> None:
        self.classifier = Classifier(self.index)
        self.names = NameLookup(self.resolver)
        self.scope_of_path = {scope.path: scope for scope in self.files}
        self.aliases = AliasValues(self.resolver.tables)

    def table_scope(self, module: str, stub: bool = False) -> FileScope | None:
        """The analyzed file a module table reads a binding from: the `.pyi` for a name only the
        stub binds, else the one file the index names for the module (never a stub twin or a
        package of the same name)."""
        path = (self.index.stub_of if stub else self.index.path_of).get(module)
        return self.scope_of_path.get(path) if path is not None else None

    def is_builtin(self, name: str) -> bool:
        return name in BUILTIN_NAMES

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

    def value_lookup(self, scope: FileScope, value: object, at_scope: int, line: int) -> Lookup | None:
        """A name-like value tree: a chain, a subscript's value, a call's callee, a string annotation."""
        if isinstance(value, str):
            found, rest = self.chain_lookup(scope, value, at_scope, line)
            if not rest:
                return found
            surface = ".".join([found.surface, *rest])
            if found.hit is None:
                return Lookup(surface, None, found.local)
            origin = found.hit.origin
            member = entity(origin.module, ".".join([origin.symbol, *rest]) if origin.symbol else ".".join(rest))
            return Lookup(surface, Hit((*found.hit.chain, member), "member"))
        if not isinstance(value, dict):
            return None
        for key in ("sub", "sa", "call"):
            if key in value:
                return self.value_lookup(scope, value[key], at_scope, line)
        return None

    def handed_def(self, target: str) -> tuple[FileScope, int] | None:
        """The top-level function or class a handed-out name leads to, in an analyzed
        first-party file: every step of the way stays inside first-party code."""
        if not self.first_party.contains(target):
            return None
        hit, rest = self.resolver.resolve_dotted(target)
        if hit is None or rest or hit.kind not in ("def", "class"):
            return None
        if not all(self.first_party.contains(str(step)) for step in hit.chain):
            return None
        origin = hit.origin
        table = self.resolver.tables.get(origin.module)
        binding = table.names.get(origin.symbol) if table is not None else None
        if binding is None or binding[0] != hit.kind or binding[2]:
            return None
        scope = self.table_scope(origin.module)
        if scope is None:
            return None
        return scope, binding[1]

    def alias_value(self, module: str, name: str) -> object:
        """The value of a module-level type alias the bare name binds (`X = dict[str, Any]`),
        else None."""
        if "." in name:
            return None
        table = self.resolver.tables.get(module)
        binding = table.names.get(name) if table is not None else None
        if binding is None or binding[0] not in ("alias", "variable"):
            return None
        row = self.aliases.of(module).get(name)
        value = row["v"] if row is not None else None
        if isinstance(value, str) or (isinstance(value, dict) and ("a" in value or "sub" in value or "bin" in value)):
            return value
        return None

    def alias_site(self, module: str, name: str) -> tuple[FileScope, int] | None:
        """The file and line of the assignment `alias_value` reads."""
        row = self.aliases.of(module).get(name)
        table = self.resolver.tables.get(module)
        if row is None or table is None:
            return None
        scope = self.table_scope(module, name in table.stub_names)
        return (scope, row["l"]) if scope is not None else None

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
    """One edge per import binding, in every context. Relative imports become absolute."""

    kind = "import"

    def run(self, context: BuildContext, graph: Graph) -> None:
        resolver, index, classifier = context.resolver, context.index, context.classifier
        for scope in context.files:
            for row in scope.record.get("imports", ()):
                at_scope, line, column, flags, target, level, name, asname = row
                contexts = scope.contexts(flags, at_scope)
                if name is None:
                    surface, hit = target, resolver.module_hit(target)
                    loaded = target
                    binds = asname or target.split(".")[0]
                else:
                    source = absolute_import(scope.module, scope.is_package, target, level)
                    if source is None:
                        surface = "." * level + (f"{target}." if target else "") + name
                        unresolved = UNRESOLVED
                        graph.edges.append(
                            self.edge(self.kind, scope, at_scope, line, column, surface, unresolved, contexts)
                        )
                        continue
                    if name == "*":
                        surface, hit, loaded, binds = f"{source}.*", resolver.module_hit(source), source, "*"
                    else:
                        surface = f"{source}.{name}"
                        hit = resolver.resolve_name(source, name)
                        if hit is None and index.exists(surface):
                            hit = resolver.module_hit(surface)
                        loaded = surface if index.exists(surface) else source
                        binds = asname or name
                attrs = [("binds", binds)]
                if index.exists(loaded):
                    attrs.append(("module", loaded))
                resolved = classifier.classify(surface, hit)
                graph.edges.append(
                    self.edge(self.kind, scope, at_scope, line, column, surface, resolved, contexts, attrs=tuple(attrs))
                )


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
class Mention:
    """A dotted name in a type expression: whether it is the whole annotation, and where it is
    looked up (an alias's names are looked up where the alias is assigned)."""

    value: str
    position: str
    lookup: SlotLookup


@dataclass(frozen=True, slots=True)
class SlotRows:
    """What every `exposes` row of one slot shares."""

    site: SlotSite
    callable_name: str
    slot: str
    contract: bool
    tail: tuple[tuple[str, str], ...]

    def attrs(self, position: str, *extra: tuple[str, str]) -> tuple[tuple[str, str], ...]:
        return (("callable", self.callable_name), ("slot", self.slot), ("position", position), *extra, *self.tail)


class DeclarationProducer(Producer):
    """inherit, decorate, exposes and declares edges from def and class records.

    In a contract surface (`CoreConfig.contract_surfaces`) every public callable exposes its whole
    signature: a slot with no annotation, builtin types, and the slots of the functions and
    dataclass fields the module hands out from elsewhere (`surfaces.HandedOut`).
    """

    kind = "declaration"
    kinds = ("inherit", "decorate", "exposes", "declares")

    @property
    def edge_kinds(self) -> frozenset[str]:
        return frozenset(self.kinds)

    @staticmethod
    def fields_by_class(scope: FileScope) -> dict[int, list[dict]]:
        out: dict[int, list[dict]] = {}
        for row in scope.record.get("assigns", ()):
            if row["op"] == ":" and len(row["t"]) == 1 and isinstance(row["t"][0], str) and "." not in row["t"][0]:
                out.setdefault(row["s"], []).append(row)
        return out

    @staticmethod
    def is_contract(context: BuildContext, scope: FileScope) -> bool:
        """A test file hands nothing out, so it is no contract surface."""
        return Context.TEST not in scope.base_contexts and context.config.is_contract_surface(scope.path)

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
    def is_class_var(annotation: object) -> bool:
        return isinstance(annotation, dict) and "sub" in annotation and chain_last(annotation["sub"]) == "ClassVar"

    def mentions(self, value: object, position: str) -> list[tuple[str, str]]:
        """Every dotted name in a type expression, with whether it is the whole annotation."""
        if isinstance(value, str):
            return [(value, position)]
        if not isinstance(value, dict):
            return []
        if "sa" in value:
            return self.mentions(value["sa"], position)
        if "sub" in value:
            outer = self.mentions(value["sub"], position)
            container = value["sub"] if isinstance(value["sub"], str) else ""
            last = container.rpartition(".")[2]
            if last == "Literal":
                return outer
            index = value.get("i")
            if last == "Annotated" and isinstance(index, dict) and "tuple" in index and index["tuple"]:
                return outer + self.mentions(index["tuple"][0], "nested")
            return outer + self.mentions(index, "nested")
        if "bin" in value and value["bin"] == "|":
            return self.mentions(value["l"], position) + self.mentions(value["r"], position)
        for key in ("tuple", "list"):
            if key in value:
                return [m for item in value[key] for m in self.mentions(item, "nested")]
        return []

    def alias_of(
        self, context: BuildContext, mention: Mention, found: Lookup
    ) -> tuple[tuple[str, str], object, SlotLookup] | None:
        """The type alias a mention names, as (module, name), its value, and where its names are
        looked up: assigned in the module that uses it, or followed through imports to the
        first-party module that assigns it. A module holds `Handler = Callable[[Order], None]` to
        shorten a signature, not to hide its types."""
        module = mention.lookup.scope.module
        local = context.alias_value(module, mention.value)
        if local is not None:
            return (module, mention.value), local, mention.lookup
        hit = found.hit
        if hit is None or hit.kind != "variable":
            return None
        if not all(context.first_party.contains(str(step)) for step in hit.chain):
            return None
        origin = hit.origin
        value = context.alias_value(origin.module, origin.symbol)
        site = context.alias_site(origin.module, origin.symbol)
        if value is None or site is None:
            return None
        return (origin.module, origin.symbol), value, SlotLookup(site[0], -1, site[1])

    def expose_mentions(
        self,
        context: BuildContext,
        graph: Graph,
        rows: SlotRows,
        mentions: list[Mention],
        expanding: frozenset[tuple[str, str]],
    ) -> None:
        """One row per mention, each followed by the rows of the type alias it is, when it is one
        (in a contract only). A cycle stops at the alias it closes; an expansion deeper than
        MAX_ALIAS_DEPTH stops with an unresolved row, because a type left unread is unknown."""
        for mention in mentions:
            lookup = mention.lookup
            head = mention.value.split(".")[0]
            found = context.value_lookup(lookup.scope, mention.value, lookup.at_scope, lookup.line)
            if found is None:
                continue
            if rows.contract or found.hit is not None or found.surface != f"builtins.{head}":
                resolved = context.classifier.classify_lookup(found)
                graph.edges.append(rows.site.edge(found.surface, resolved, rows.attrs(mention.position)))
            if not rows.contract:
                continue
            alias = self.alias_of(context, mention, found)
            if alias is None or alias[0] in expanding:
                continue
            key, value, where = alias
            if len(expanding) >= MAX_ALIAS_DEPTH:
                attrs = rows.attrs(mention.position, ("alias", "too-deep"))
                graph.edges.append(rows.site.edge(mention.value, UNRESOLVED, attrs))
                continue
            inner = [Mention(name, position, where) for name, position in self.mentions(value, mention.position)]
            self.expose_mentions(context, graph, rows, inner, expanding | {key})

    def expose(
        self,
        context: BuildContext,
        graph: Graph,
        site: SlotSite,
        lookup: SlotLookup,
        annotation: object,
        slot: str,
        callable_name: str,
        contract: bool,
        handed: str = "",
    ) -> None:
        """One `exposes` edge per type name in a slot. Builtin types count only in a contract,
        and there an unannotated slot (annotation None) is an edge of its own."""
        rows = SlotRows(site, callable_name, slot, contract, (("handed", handed),) if handed else ())
        if annotation is None:
            graph.edges.append(site.edge("", UNRESOLVED, rows.attrs("whole", ("annotation", "missing"))))
            return
        mentions = [Mention(value, position, lookup) for value, position in self.mentions(annotation, "whole")]
        self.expose_mentions(context, graph, rows, mentions, frozenset())

    def expose_signature(
        self,
        context: BuildContext,
        graph: Graph,
        site: SlotSite,
        lookup: SlotLookup,
        node: dict,
        callable_name: str,
        contract: bool,
        handed: str = "",
    ) -> None:
        """The parameter and return slots of a function. In a contract `self`, `cls` and an
        unannotated return are no slots, and an unannotated parameter is one."""
        for param in node.get("params", ()):
            annotation = param[2] if len(param) > 2 else None
            in_slot = contract and param[0] not in ("self", "cls")
            if not in_slot and (annotation is None or handed):
                continue
            slot = f"param:{param[0]}"
            self.expose(context, graph, site, lookup, annotation, slot, callable_name, in_slot, handed)
        if node.get("ret") is not None:
            self.expose(context, graph, site, lookup, node["ret"], "return", callable_name, contract, handed)

    def expose_fields(
        self,
        context: BuildContext,
        graph: Graph,
        site: SlotSite,
        lookup: SlotLookup,
        rows: list[dict],
        callable_name: str,
        contract: bool,
        handed: str = "",
    ) -> None:
        """The fields of a dataclass-like class, as `field:` slots of its `__init__`. In a contract
        only fields directly in the class body count, and a `ClassVar` is no field."""
        for row in rows:
            if row.get("ann") is None:
                continue
            in_slot = contract and row.get("f", 0) == CLASS and not self.is_class_var(row["ann"])
            if handed and not in_slot:
                continue
            line_site = site if handed else replace(site, line=row["l"])
            line_lookup = replace(lookup, line=row["l"])
            slot = f"field:{row['t'][0]}"
            self.expose(context, graph, line_site, line_lookup, row["ann"], slot, callable_name, in_slot, handed)

    def is_dataclass_like(self, context: BuildContext, scope: FileScope, node: dict) -> bool:
        """Whether a class takes a decorator `dataclass_decorators` names, by spelling or target."""
        wanted = context.config.dataclass_decorators
        for dec in node.get("dec", ()):
            found = context.value_lookup(scope, dec, node["s"], node["l"])
            if found is None:
                continue
            if found.surface in wanted or any(
                str(t) in wanted for t in context.classifier.classify_lookup(found).targets
            ):
                return True
        return False

    def declare(
        self,
        context: BuildContext,
        graph: Graph,
        scope: FileScope,
        idx: int,
        node: dict,
        fields: dict[int, list[dict]],
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
        decorators: list[str] = []
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
            decorators.append(surface)
            decorators.extend(str(target) for target in resolved.targets)
        site = SlotSite(scope, enclosing, line, column, contexts)
        lookup = SlotLookup(scope, enclosing, line)
        in_contract = contract and self.in_contract(scope, idx)
        if node["k"] == "class":
            for position, base in enumerate(node.get("bases", ())):
                found = context.value_lookup(scope, base, enclosing, line)
                surface = found.surface if found else ""
                resolved = context.classifier.classify_lookup(found) if found else UNRESOLVED
                attrs = (("class", qualname), ("index", str(position)))
                graph.edges.append(
                    self.edge(
                        "inherit", scope, enclosing, line, column, surface, resolved, contexts, use="base", attrs=attrs
                    )
                )
            if not nested and any(name in context.config.dataclass_decorators for name in decorators):
                rows = fields.get(idx, [])
                self.expose_fields(context, graph, site, lookup, rows, f"{qualname}.__init__", in_contract)
            return
        if nested:
            return
        self.expose_signature(context, graph, site, lookup, node, qualname, in_contract)

    def hand_out(self, context: BuildContext, graph: Graph, scope: FileScope) -> None:
        """The slots of the functions and dataclass fields a contract surface hands out from other
        modules, at the import that hands each one out."""
        for item in HandedOut(scope, context.resolver.tables.get(scope.module)).names():
            found = context.handed_def(item.target)
            if found is None:
                continue
            origin, idx = found
            node = origin.defs[idx]
            site = SlotSite(scope, -1, item.line, item.column, scope.base_contexts)
            lookup = SlotLookup(origin, node["s"], node["l"])
            if node["k"] != "class":
                self.expose_signature(context, graph, site, lookup, node, item.bound, True, item.clause)
                continue
            if not self.is_dataclass_like(context, origin, node):
                continue
            rows = self.fields_by_class(origin).get(idx, [])
            self.expose_fields(context, graph, site, lookup, rows, f"{item.bound}.__init__", True, item.clause)

    def run(self, context: BuildContext, graph: Graph) -> None:
        for scope in context.files:
            fields = self.fields_by_class(scope)
            contract = self.is_contract(context, scope)
            for idx, node in enumerate(scope.defs):
                self.declare(context, graph, scope, idx, node, fields, contract)
            if contract:
                self.hand_out(context, graph, scope)


class StringProducer(Producer):
    """dotted-path edges: string literals that name repo modules or symbols.

    A string made of several literal parts gives one edge per dotted part and one for the
    joined value, so consumers that check parts alone (as `tach` does) can select them.
    """

    kind = "dotted-path"
    reads = frozenset({"strs"})

    @staticmethod
    def path_like(text: str, role: str) -> bool:
        """An unresolved string still counts when it reads as a path: a patch target, or two dots
        or more. One-dot strings that resolve to nothing are labels or names, not paths."""
        return role == "patch-target" or text.count(".") >= 2

    @staticmethod
    def role(up: str, patch_callees: tuple[str, ...]) -> str:
        first = up.split(" ")[0]
        kind, _, rest = first.partition(":")
        if kind == "arg":
            position, _, callee = rest.partition(":")
            if position == "0" and callee.rpartition(".")[2] in patch_callees:
                return "patch-target"
        if kind == "kw":
            keyword, _, callee = rest.partition(":")
            if keyword == "target" and callee.rpartition(".")[2] in patch_callees:
                return "patch-target"
        return "literal"

    def run(self, context: BuildContext, graph: Graph) -> None:
        patch_callees = context.config.patch_callees
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
                for text, literal in candidates:
                    found = context.resolve_dotted(text)
                    if found is None:
                        continue
                    resolved, member = found
                    if resolved.resolution == Resolution.UNRESOLVED and not self.path_like(text, role):
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
