"""Policy-independent tables over one Graph: edges by kind and source module, ordinals, symbol
facts and ancestry, export tables. Built once per graph and shared by every engine that reads it,
so compare's runs under two policies over one snapshot index it once."""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable, Mapping
from dataclasses import dataclass, field
from functools import cached_property
from typing import Generic, TypeVar

from hoglint.core.config import DataclassDecorators
from hoglint.model import Edge, Graph, Resolution, set_handle
from hoglint.selectors import split_list

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")

MODULE_SCOPE = "<module>"
DECL_NAMES = {"def": "function", "async": "function"}


class Memo(Generic[K, V]):
    """A function with every answer kept. `None` is a cached answer like any other."""

    def __init__(self, compute: Callable[[K], V]) -> None:
        self.compute = compute
        self.values: dict[K, V] = {}

    def __call__(self, key: K) -> V:
        if key in self.values:
            return self.values[key]
        value = self.values[key] = self.compute(key)
        return value


def has_endpoint_attrs(edge: Edge) -> bool:
    """Whether the edge sets `src.*`/`dst.*` attributes, which override its endpoints' labels."""
    return bool(edge.attrs) and any(name.startswith(("src.", "dst.")) for name, _ in edge.attrs)


@dataclass(frozen=False, slots=True)
class EdgeGroup:
    """The edges of one kind from one module. `irregular` holds the ones a module-wide answer
    cannot speak for: an edge with its own `src.*`/`dst.*` attributes, or a site path that is not
    the module's."""

    edges: list[Edge] = field(default_factory=list)
    irregular: list[Edge] = field(default_factory=list)


def written_facts(symbols: Mapping[str, Mapping[str, str]]) -> Iterable[tuple[str, Mapping[str, str]]]:
    """The symbols whose facts a producer wrote as a dict. A graph that keeps the core's symbols
    as columns (kind and path only) lists the facts packs added in `added`."""
    added = getattr(symbols, "added", None)
    return added.items() if isinstance(added, dict) else symbols.items()


class SymbolFacts:
    """Symbol facts from the graph: declarations, bases and decorators.

    Bases come from `inherit` edges (attr `class`), decorators from `decorate` edges (attr
    `decorated`). An unresolved base is kept as `?<surface>`, so ancestry knows it is incomplete.
    Producers may add string facts to Graph.symbols: `abstract`, `frozen`, `metaclass`, `members`.
    `frozen_by` holds the names of each class's decorators that a call passes `frozen=True`.
    """

    def __init__(
        self, graph: Graph, inherits: list[Edge], decorates: list[Edge], declares: list[Edge] | None = None
    ) -> None:
        self.symbols = graph.symbols
        self.base_map: dict[str, list[str]] = {}
        self.decorator_map: dict[str, list[str]] = {}
        self.frozen_by: dict[str, list[str]] = {}
        self.classes = {
            str(target) for edge in declares or () if edge.attr("decl") == "class" for target in edge.targets
        }
        self.add_edges(inherits, decorates)
        self.ancestors = Memo(self.walk_ancestors)
        self.any_decl = Memo(self.find_decl)

    def add_base(self, owner: str, names: list[str]) -> None:
        self.base_map.setdefault(owner, []).extend(names)

    def add_decorator(self, owner: str, surface: str, names: list[str], frozen: bool) -> None:
        self.decorator_map.setdefault(owner, []).extend([surface, *names])
        if frozen:
            self.frozen_by.setdefault(owner, []).extend([surface, *names])

    def add_edges(self, inherits: list[Edge], decorates: list[Edge]) -> None:
        for edge in inherits:
            if edge.attr("class"):
                called, resolved = edge.attr("called") == "1", edge.resolution is Resolution.RESOLVED
                names = self.base_names(edge.surface, self.names(edge), called, resolved, edge.attr("classes"))
                self.add_base(f"{edge.site.module}.{edge.attr('class')}", names)
        for edge in decorates:
            if not edge.attr("decorated"):
                continue
            owner = f"{edge.site.module}.{edge.attr('decorated')}"
            self.add_decorator(owner, edge.surface, self.names(edge), edge.attr("frozen") == "1")

    @staticmethod
    def names(edge: Edge) -> list[str]:
        if edge.resolution is Resolution.UNRESOLVED:
            return [f"?{edge.surface}"]
        if edge.targets:
            return [str(target) for target in edge.targets]
        return [edge.surface] if edge.surface else []

    def base_names(self, surface: str, names: list[str], called: bool, resolved: bool, classes: str) -> list[str]:
        """`names` of an inherit edge as ancestry reads them: a call's result is an unknown base
        (`?`); a first-party name the producer followed is the classes it wrote (`classes`), or
        unknown; any other first-party name that is no declared class is unknown."""
        if called:
            return [f"?{surface}()"]
        if classes == "?":
            return [f"?{name}" for name in names]
        if classes:
            return classes.split(",")
        if not resolved:
            return names
        return [name if self.declared_class(name) else f"?{name}" for name in names]

    def declared_class(self, symbol: str) -> bool:
        """Whether the core declared the symbol a class: a pack may give a class another kind. A
        class in a function body has no `declares` edge; its own `kind` fact says it."""
        return symbol in self.classes or self.get(symbol, "kind") == "class"

    def has(self, symbol: str) -> bool:
        return symbol in self.symbols

    def find_decl(self, decl: str) -> bool:
        """Whether any symbol has this declaration, so a producer that records it ran."""
        return any(self.decl(symbol) == decl for symbol in self.symbols)

    def with_decl(self, decl: str) -> list[str]:
        """The symbols with this declaration, in `graph.symbols` order."""
        return [symbol for symbol in self.symbols if self.decl(symbol) == decl]

    def get(self, symbol: str, key: str) -> str:
        return self.symbols.get(symbol, {}).get(key, "")

    def decl(self, symbol: str) -> str:
        facts = self.symbols.get(symbol, {})
        raw = facts.get("decl") or facts.get("kind", "")
        return DECL_NAMES.get(raw, raw)

    def bases(self, symbol: str) -> list[str]:
        return self.base_map.get(symbol, []) + split_list(self.get(symbol, "bases"))

    def decorators(self, symbol: str) -> list[str]:
        return self.decorator_map.get(symbol, []) + split_list(self.get(symbol, "decorators"))

    def is_dataclass(self, symbol: str, kinds: DataclassDecorators) -> bool:
        return bool(kinds.make & set(self.decorators(symbol))) or self.get(symbol, "dataclass") == "true"

    def is_frozen(self, symbol: str, kinds: DataclassDecorators) -> bool:
        """A dataclass decorator call passes `frozen=True`, or a decorator that always freezes
        makes the class."""
        if self.get(symbol, "frozen") == "true":
            return True
        if kinds.make & set(self.frozen_by.get(symbol, ())):
            return True
        return bool(kinds.freeze & set(self.decorators(symbol)))

    @cached_property
    def children(self) -> dict[str, list[str]]:
        """base -> the classes that name it as a base: `bases` read backwards. Read after every
        base is added."""
        out: dict[str, list[str]] = {}
        for owner, bases in self.base_map.items():
            for base in bases:
                out.setdefault(base, []).append(owner)
        for symbol, facts in written_facts(self.symbols):
            for base in split_list(facts.get("bases", "")):
                out.setdefault(base, []).append(symbol)
        return out

    def descendants(self, targets: tuple[str, ...]) -> set[str]:
        """Every name whose ancestors (`walk_ancestors`) include one of `targets`."""
        children = self.children
        seen: set[str] = set()
        queue = list(targets)
        while queue:
            for child in children.get(queue.pop(), ()):
                if child not in seen:
                    seen.add(child)
                    queue.append(child)
        return seen

    def walk_ancestors(self, symbol: str) -> tuple[frozenset[str], bool]:
        """Every base class reachable from `symbol`, and whether the walk saw every base."""
        seen: set[str] = set()
        complete = True
        queue = list(self.bases(symbol))
        while queue:
            base = queue.pop()
            if base.startswith("?"):
                complete = False
                continue
            if base in seen:
                continue
            seen.add(base)
            queue.extend(self.bases(base))
        return frozenset(seen), complete


def index_of(graph: Graph) -> GraphIndex:
    """The index of a graph. A graph that keeps its edges outside Python makes its own."""
    make = getattr(graph, "make_index", None)
    return make() if make is not None else GraphIndex(graph)


class GraphIndex:
    def __init__(self, graph: Graph) -> None:
        self.graph = graph
        by_kind: dict[str, list[Edge]] = {}
        groups: dict[str, dict[str, EdgeGroup]] = {}
        for at, edge in enumerate(graph.edges):
            if edge.handle != at:
                set_handle(edge, at)  # an edge appended to `Graph.edges` directly
            site = edge.site
            by_kind.setdefault(edge.kind, []).append(edge)
            group = groups.setdefault(edge.kind, {}).get(site.module)
            if group is None:
                group = groups[edge.kind][site.module] = EdgeGroup()
            group.edges.append(edge)
            if has_endpoint_attrs(edge) or (site.path and site.path != graph.modules.get(site.module)):
                group.irregular.append(edge)
        self.by_kind: Mapping[str, list[Edge]] = by_kind
        self.groups: Mapping[str, Mapping[str, EdgeGroup]] = groups
        self.facts = SymbolFacts(
            graph, by_kind.get("inherit", []), by_kind.get("decorate", []), by_kind.get("declares", [])
        )
        self.path_to_module = {path: module for module, path in graph.modules.items()}
        self.module_of_symbol = Memo(self.find_module)

    def find_module(self, symbol: str) -> str | None:
        """The longest module name that prefixes the symbol (the symbol itself when it is a module)."""
        head = symbol
        while head:
            if head in self.graph.modules:
                return head
            head = head.rpartition(".")[0]
        return None

    def count_ordinals(self) -> dict[int, int]:
        """`ordinals`, counted over every edge in graph order."""
        out: dict[int, int] = {}
        counters: dict[tuple[str, str, str, str], int] = {}
        for edge in self.graph.edges:
            key = (edge.site.module, edge.site.scope, edge.kind, edge.surface)
            out[edge.handle] = counters.get(key, 0)
            counters[key] = counters.get(key, 0) + 1
        return out

    @cached_property
    def ordinals(self) -> dict[int, int]:
        """Each edge's place among edges with the same module, scope, kind and surface, by its
        handle. An edge object a producer repeats has one handle, so it keeps its last place."""
        return self.count_ordinals()

    @cached_property
    def sorted_modules(self) -> list[tuple[str, str]]:
        return sorted(self.graph.modules.items())

    @cached_property
    def sorted_symbols(self) -> list[str]:
        return sorted(self.graph.symbols)

    @cached_property
    def module_imports(self) -> dict[str, list[Edge]]:
        """Module-scope import edges per module."""
        out: dict[str, list[Edge]] = {}
        for edge in self.by_kind.get("import", []):
            if edge.site.scope in (MODULE_SCOPE, ""):
                out.setdefault(edge.site.module, []).append(edge)
        return out

    @cached_property
    def module_rebinds(self) -> dict[str, list[Edge]]:
        """`rebind` edges per module: the names an export module hands out by assigning them again."""
        out: dict[str, list[Edge]] = {}
        for edge in self.by_kind.get("rebind", []):
            out.setdefault(edge.site.module, []).append(edge)
        return out

    def edges_at(self, handles: list[int]) -> list[Edge]:
        """The edges at `handles`."""
        return [self.graph.edges[handle] for handle in handles]
