"""Core edge producers. Each reads L1 records of analyzed files and appends Edges to a Graph.

Producers know Python, never a framework: a use position says where a name sits (call,
attribute, argument, ...), and packs give it framework meaning.
"""

from __future__ import annotations

import re
import builtins
from dataclasses import dataclass

from hoglint.core.config import CoreConfig
from hoglint.core.modules import ModuleIndex
from hoglint.core.sites import Classifier, FileScope, Lookup, NameLookup, Resolved
from hoglint.core.symbols import TC_ELSE, Hit, Resolver, absolute_import, entity
from hoglint.facts import FactStore
from hoglint.model import Context, Edge, Graph, Resolution

PRODUCER_VERSION = 1
UNRESOLVED = Resolved(Resolution.UNRESOLVED, (), ())
BUILTIN_NAMES = frozenset(dir(builtins))
CHAIN_TOKEN = re.compile(r"\.(\w+)|\(\)|\[\]")
WRAPPER_STEPS = ("await", "ifexp:body", "ifexp:else", "star", "bool:and", "bool:or", "walrus:")

# The `guard` attribute of an import in the else branch of `if TYPE_CHECKING:`. It runs at
# runtime, but some consumers (tach) skip that whole `if`.
TYPE_CHECKING_ELSE = "type-checking-else"

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
TARGET_STEPS = ("assign-target", "annassign-target", "augassign-target", "for-target", "comp:target", "walrus-target")


def producer_id(kind: str) -> str:
    return f"core-{kind}@{PRODUCER_VERSION}"


def chain_tokens(chain: str) -> list[str]:
    """`.a.b()[]` -> ['a', 'b', '()', '[]']; attribute names are bare."""
    return [match.group(1) or match.group(0) for match in CHAIN_TOKEN.finditer(chain)]


def leading_attrs(tokens: list[str]) -> list[str]:
    out = []
    for token in tokens:
        if token in ("()", "[]"):
            break
        out.append(token)
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
    return "".join(token if token in ("()", "[]") else f".{token}" for token in tokens)


@dataclass(frozen=False)
class BuildContext:
    """What every producer reads. Built once per run."""

    config: CoreConfig
    store: FactStore
    index: ModuleIndex
    resolver: Resolver
    files: list[FileScope]

    def __post_init__(self) -> None:
        self.classifier = Classifier(self.index)
        self.names = NameLookup(self.resolver)

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

    @property
    def name(self) -> str:
        return producer_id(self.kind)

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
                if flags & TC_ELSE:
                    attrs.append(("guard", TYPE_CHECKING_ELSE))
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

    def run(self, context: BuildContext, graph: Graph) -> None:
        for scope in context.files:
            for row in scope.record.get("refs", ()):
                if len(row) > 7:
                    continue
                at_scope, header, line, column, name, chain, up = row
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
                    scope.contexts(scope.flags_at(line, column), at_scope),
                    use=use,
                    attrs=tuple(extra),
                )
                graph.edges.append(edge)


class DeclarationProducer(Producer):
    """inherit, decorate, exposes and declares edges from def and class records."""

    kind = "declaration"
    kinds = ("inherit", "decorate", "exposes", "declares")

    @staticmethod
    def fields_by_class(scope: FileScope) -> dict[int, list[dict]]:
        out: dict[int, list[dict]] = {}
        for row in scope.record.get("assigns", ()):
            if row["op"] == ":" and len(row["t"]) == 1 and isinstance(row["t"][0], str) and "." not in row["t"][0]:
                out.setdefault(row["s"], []).append(row)
        return out

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

    def expose(
        self,
        context: BuildContext,
        graph: Graph,
        scope: FileScope,
        idx: int,
        annotation: object,
        slot: str,
        callable_name: str,
        line: int,
    ) -> None:
        node = scope.defs[idx]
        enclosing = node["s"]
        contexts = scope.contexts(node.get("f", 0), enclosing)
        for value, position in self.mentions(annotation, "whole"):
            head = value.split(".")[0]
            found = context.value_lookup(scope, value, enclosing, line)
            if found is None or (found.hit is None and found.surface == f"builtins.{head}"):
                continue
            resolved = context.classifier.classify_lookup(found)
            attrs = (("callable", callable_name), ("slot", slot), ("position", position))
            graph.edges.append(
                self.edge(
                    "exposes",
                    scope,
                    enclosing,
                    line,
                    node["c"],
                    found.surface,
                    resolved,
                    contexts,
                    use="annotation",
                    attrs=attrs,
                )
            )

    def declare(
        self,
        context: BuildContext,
        graph: Graph,
        scope: FileScope,
        idx: int,
        node: dict,
        fields: dict[int, list[dict]],
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
            attrs = (("decorated", qualname), ("called", called))
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
                for row in fields.get(idx, ()):
                    self.expose(
                        context, graph, scope, idx, row["ann"], f"field:{row['t'][0]}", f"{qualname}.__init__", row["l"]
                    )
            return
        if nested:
            return
        for param in node.get("params", ()):
            if len(param) > 2 and param[2] is not None:
                self.expose(context, graph, scope, idx, param[2], f"param:{param[0]}", qualname, line)
        if node.get("ret") is not None:
            self.expose(context, graph, scope, idx, node["ret"], "return", qualname, line)

    def run(self, context: BuildContext, graph: Graph) -> None:
        for scope in context.files:
            fields = self.fields_by_class(scope)
            for idx, node in enumerate(scope.defs):
                self.declare(context, graph, scope, idx, node, fields)


class StringProducer(Producer):
    """dotted-path edges: string literals that name repo modules or symbols.

    A string made of several literal parts gives one edge per dotted part and one for the
    joined value, so consumers that check parts alone (as `tach` does) can select them.
    """

    kind = "dotted-path"

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
                at_scope, header, line, column, value = row[:5]
                up = row[6]
                parts = row[7] if len(row) > 7 else None
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
                        scope.contexts(scope.flags_at(line, column), at_scope),
                        attrs=tuple(attrs),
                    )
                    graph.edges.append(edge)


class DynamicImportProducer(Producer):
    """Calls to declared loaders. A literal module name resolves; anything else is unresolved."""

    kind = "dynamic-import"

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
                at_scope, _header, line, column, callee, args, kws = row
                if callee.rpartition(".")[2] not in names or callee.startswith("?"):
                    continue
                found, rest = context.chain_lookup(scope, callee, at_scope, line)
                loader = ".".join([found.surface, *rest])
                origin = str(found.hit.origin) if found.hit is not None else ""
                if loader not in loaders and origin not in loaders:
                    continue
                argument = self.module_argument(args, kws)
                contexts = scope.contexts(scope.flags_at(line, column), at_scope)
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
