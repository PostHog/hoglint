"""Extension points: packs make edges, providers set labels, coverage adapters read outside input lists.

Generic code (cli, labels, rules) only looks plugins up here by name. A pack or preset
registers its plugins when its module is imported; `extends` and `[discovery] plugins`
in the config name the modules to import.

The pack API (D22): a pack reads one snapshot through a `PackReader` and writes what it makes
into a `PackSink`. Both engines give packs the same reader and sink types, so one pack runs on
either. A pack never copies an edge core made: it refines it by handle.
"""

from __future__ import annotations

import importlib
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, Protocol

from hoglint.model import Edge, EntityId

if TYPE_CHECKING:
    from hoglint.core.modules import FirstParty, ModuleIndex
    from hoglint.core.records import RecordBodies
    from hoglint.core.sites import FileScope, Lookup
    from hoglint.core.symbols import Tables
    from hoglint.native.queries import Words


@dataclass(frozen=True, slots=True)
class ProducerFailure:
    """A producer that could not run, and the edge kinds it would have made."""

    reason: str
    kinds: frozenset[str]


@dataclass(frozen=True, slots=True)
class Refinement:
    """Fields a producer sets on an edge it did not make, found by the edge's handle. The edge
    stays one edge, and every rule sees it with these fields. A field left None is not set;
    `attrs` are added after the edge's own and may not change one it has."""

    handle: int
    shape: str | None = None
    shape_detail: str | None = None
    attrs: tuple[tuple[str, str], ...] = ()


class EdgeBatch(NamedTuple):
    """Projected candidate edges: their handles, and per requested column one value per edge.
    Columns: `path`, `module`, `scope`, `line`, `column`, `surface`, `target` (EntityId or None),
    `producer`, and `attr:<name>` ("" when the edge has no such attr)."""

    handles: list[int]
    columns: dict[str, list[Any]]


@dataclass(frozen=True, slots=True)
class AttributeUse:
    """A name used through attributes (`a.b.Model.objects`) whose chain reaches one of a
    query's targets: where (`scope` is the def index, -1 at module level), how often, and the
    lookup of the chain."""

    scope: int
    line: int
    column: int
    count: int
    found: Lookup


class PackReader(Protocol):
    """What a pack reads of one snapshot. Bulk reads are candidate queries; `record` is the
    escape hatch for one file's whole record, and the run counts its uses (`records_read`)."""

    files: list[FileScope]  # the analyzed files, in order
    paths: list[str]  # every record's path, analyzed or not, third-party files included
    index: ModuleIndex
    first_party: FirstParty
    tables: Tables
    resolver: Any  # resolve_name, resolve_dotted, resolve_value, resolve_class
    classes: Any  # located, node, bases, mro, is_subclass
    context: Any  # value_lookup, chain_lookup at a file scope's sites
    external: dict[str, str]
    records_read: int

    def record(self, path: str) -> Mapping[str, Any]: ...

    def bodies(self, record: Mapping[str, Any]) -> RecordBodies: ...

    def edges_to(self, kind: str, targets: Collection[EntityId]) -> list[Edge]: ...

    def edges_spelled(self, kind: str, names: Collection[str]) -> list[Edge]: ...

    def batch_to(self, kind: str, targets: Collection[EntityId], columns: Sequence[str]) -> EdgeBatch: ...

    def calls_named(
        self, scopes: Iterable[FileScope], kind: str, names: Collection[str]
    ) -> Iterator[tuple[FileScope, list[list]]]: ...

    def may_mention(self, scope: FileScope, kinds: Iterable[str], words: Words) -> bool: ...

    def has_rows(self, scope: FileScope, kind: str) -> bool: ...

    def fact_digest(self, record: Any, kinds: Iterable[str]) -> str | None: ...

    def uses_to(
        self, scopes: Iterable[FileScope], targets: Collection[EntityId]
    ) -> Iterator[tuple[FileScope, list[AttributeUse]]]: ...

    def importing(self, names: Collection[str]) -> list[tuple[FileScope, Sequence[Any]]]: ...

    def imported_modules(self, scope: FileScope) -> list[str]: ...

    def subclasses(self, qualified: str) -> list[EntityId]: ...


class PackSink(Protocol):
    """Where a pack puts what it makes. Each call carries one producer's batch."""

    def add_edges(self, producer: str, edges: Sequence[Edge]) -> None: ...

    def refine(self, producer: str, kinds: frozenset[str], refinements: Sequence[Refinement]) -> None:
        """Applies the batch, or none of it when it sets a field another producer set apart:
        then `producer` fails for `kinds`."""
        ...

    def add_symbols(self, facts: Mapping[str, Mapping[str, str]]) -> None: ...

    def fail(self, producer: str, failure: ProducerFailure) -> None: ...


def fields_of(refinement: Refinement) -> list[tuple[str, str]]:
    """(field, value) of what a refinement sets; an attr is `attr:<name>`."""
    pairs = (("shape", refinement.shape), ("shape_detail", refinement.shape_detail))
    out = [(name, value) for name, value in pairs if value is not None]
    out.extend((f"attr:{name}", value) for name, value in refinement.attrs)
    return out


def first_set(before: Refinement, after: Refinement) -> Refinement:
    """Two refinements of one edge by one producer as one: the first value set of each field wins."""
    attrs = dict(after.attrs)
    attrs.update(before.attrs)
    return Refinement(
        before.handle,
        before.shape if before.shape is not None else after.shape,
        before.shape_detail if before.shape_detail is not None else after.shape_detail,
        tuple(attrs.items()),
    )


@dataclass(frozen=False, slots=True)
class RefinedFields:
    """What each producer set on each edge it refined, so a sink finds two producers that set
    one field apart. A field's owner is the first producer that set it."""

    made: dict[str, dict[int, Refinement]] = field(default_factory=dict)

    def owner(self, handle: int, name: str) -> tuple[str, str] | None:
        """(producer, value) of the first producer that set field `name` of edge `handle`."""
        for producer, refined in self.made.items():
            found = refined.get(handle)
            if found is None:
                continue
            for field_name, value in fields_of(found):
                if field_name == name:
                    return producer, value
        return None

    def conflicts(self, producer: str, refinements: Sequence[Refinement], own_attrs: Any) -> list[str]:
        """The fields of `refinements` another producer set to another value, or an edge's own
        attr a refinement would change; `own_attrs(handle)` gives an edge's own attrs, asked only
        for a refinement that sets attrs."""
        out = []
        for refinement in refinements:
            mine: dict[str, str] | None = None
            for name, value in fields_of(refinement):
                found = self.owner(refinement.handle, name) if self.made else None
                if found is not None and found[0] != producer and found[1] != value:
                    out.append(f"{name} of edge {refinement.handle} ({found[0]} set {found[1]!r})")
                if not name.startswith("attr:"):
                    continue
                if mine is None:
                    mine = own_attrs(refinement.handle)
                if mine.get(name[5:], value) != value:
                    out.append(f"{name} of edge {refinement.handle} (the edge has {mine[name[5:]]!r})")
        return out

    def record(self, producer: str, refinements: Sequence[Refinement]) -> None:
        refined = self.made.setdefault(producer, {})
        for refinement in refinements:
            before = refined.get(refinement.handle)
            refined[refinement.handle] = refinement if before is None else first_set(before, refinement)


class Pack(Protocol):
    """A framework or repo pack: reads its config section and one snapshot, and writes edges,
    refinements, symbol facts and failures into the sink."""

    name: str  # the config section it owns, e.g. "django"
    allowed_keys: frozenset[str]
    edge_kinds: frozenset[str]  # kinds of the edges only it makes (see ProducerStep); a failure marks these incomplete

    def produce(self, section: Mapping[str, Any], reader: PackReader, sink: PackSink, root: Path) -> None: ...


@dataclass(frozen=True, slots=True)
class Provided:
    """A provider value with the detail `hoglint explain` shows next to it (which file line set it)."""

    value: Any
    detail: str = ""


class Provider(Protocol):
    """Fills label fields from files outside the code (owners files, CODEOWNERS, provenance).

    `values` maps output names to values (plain, or wrapped in `Provided`). A provider may declare
    `outputs: tuple[str, ...]`; one with a single output fills every field its `sets` lists, and
    otherwise each output fills the field of the same name unless `sets` renames it.
    """

    use: str  # the `use = "..."` name in a [[provider]] or [providers.*] entry
    allowed_options: frozenset[str]

    def open(self, options: Mapping[str, Any], root: Path) -> Any: ...

    def values(self, handle: Any, path: str) -> Mapping[str, Any]: ...


class CoverageAdapter(Protocol):
    """Answers whether an outside input list (for example a build tool's task inputs) watches a path."""

    name: str
    allowed_options: frozenset[str]

    def open(self, options: Mapping[str, Any], root: Path) -> Any: ...

    def covered(self, handle: Any, path: str) -> tuple[bool, str]: ...


class Registry:
    """Plugins by name. One process-wide instance; registering a name twice is an error."""

    def __init__(self) -> None:
        self.packs: dict[str, Pack] = {}
        self.providers: dict[str, Provider] = {}
        self.coverage_adapters: dict[str, CoverageAdapter] = {}

    @staticmethod
    def add(table: dict, name: str, plugin: object, kind: str) -> None:
        if name in table and table[name] is not plugin:
            raise ValueError(f"{kind} {name!r} is registered twice")
        table[name] = plugin

    def add_pack(self, pack: Pack) -> None:
        self.add(self.packs, pack.name, pack, "pack")

    def add_provider(self, provider: Provider) -> None:
        self.add(self.providers, provider.use, provider, "provider")

    def add_coverage_adapter(self, adapter: CoverageAdapter) -> None:
        self.add(self.coverage_adapters, adapter.name, adapter, "coverage adapter")


REGISTRY = Registry()


PACK_MODULE_PREFIX = "hoglint.packs."


def import_plugins(modules: Iterable[str]) -> list[str]:
    """Imports each module so it registers its plugins. One problem per module that fails."""
    problems: list[str] = []
    for module in dict.fromkeys(modules):
        try:
            importlib.import_module(module)
        except Exception as error:  # noqa: BLE001 - a broken plugin is a config error, reported with the rest
            problems.append(f"plugin module {module!r} does not import: {type(error).__name__}: {error}")
    return problems
