"""Extension points: packs make edges, providers set labels, coverage adapters read outside input lists.

Generic code (cli, labels, rules) only looks plugins up here by name. A pack or preset
registers its plugins when its module is imported; `extends` and `[discovery] plugins`
in the config name the modules to import.

The pack API (D22): a pack reads one snapshot through a `PackReader` and writes what it makes
into a `PackSink`. A pack never copies an edge core made: it refines it by handle.
"""

from __future__ import annotations

import importlib
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, Protocol

from hoglint.ledger.listing import WorktreeListing
from hoglint.model import Edge, EntityId

if TYPE_CHECKING:
    from hoglint.core.memo import PackMemo, Tracer
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
    escape hatch for one file's whole record, and the run counts its uses (`records_read`).

    `memo` is the run's pack memo, None when it is off, and `tracer` notes what the running item
    reads (hoglint/core/memo.py): the memo's tracer, or one that notes nothing."""

    @property
    def files(self) -> list[FileScope]:
        """The analyzed files, in order."""
        ...

    @property
    def paths(self) -> list[str]:
        """Every record's path, analyzed or not, third-party files included."""
        ...

    index: ModuleIndex
    first_party: FirstParty
    tables: Tables
    resolver: Any  # resolve_name, resolve_dotted, resolve_value, resolve_class
    classes: Any  # located, node, bases, mro, is_subclass
    context: Any  # value_lookup, chain_lookup at a file scope's sites
    external: dict[str, str]
    records_read: int
    memo: PackMemo | None
    tracer: Tracer

    def record(self, path: str) -> Mapping[str, Any]: ...

    def note_file(self, scope: FileScope) -> None:
        """Notes a read of one file's facts in the running item."""
        ...

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

    def rows(self, scope: FileScope, kind: str) -> list[Any]: ...

    def imported_modules(self, scope: FileScope) -> list[str]: ...

    def subclasses(self, qualified: str) -> list[EntityId]: ...


class PackSink(Protocol):
    """Where a pack puts what it makes. Each call carries one producer's batch."""

    def add_edges(self, producer: str, edges: Sequence[Edge]) -> None: ...

    def refine(self, producer: str, kinds: frozenset[str], refinements: Refinements) -> None:
        """Applies the batch, or none of it when it sets a field another producer set apart:
        then `producer` fails for `kinds`."""
        ...

    def add_symbols(self, facts: Mapping[str, Mapping[str, str]]) -> None: ...

    def fail(self, producer: str, failure: ProducerFailure) -> None: ...


def conflict_text(name: str, handle: int, producer: str | None, value: str) -> str:
    """One field a refinement would set apart, as a producer failure names it: `producer` set it
    to `value`, or, with no producer, the edge has `value` there."""
    if producer is None:
        return f"{name} of edge {handle} (the edge has {value!r})"
    return f"{name} of edge {handle} ({producer} set {value!r})"


@dataclass(frozen=False, slots=True)
class Refinements:
    """Refinements as columns, one row per refined edge, in the order they apply: what a sink
    takes, so a producer that refines many edges makes no object per edge."""

    handles: list[int] = field(default_factory=list)
    shapes: list[str | None] = field(default_factory=list)
    details: list[str | None] = field(default_factory=list)
    attrs: list[tuple[tuple[str, str], ...]] = field(default_factory=list)

    def add(
        self, handle: int, shape: str | None = None, detail: str | None = None, attrs: tuple[tuple[str, str], ...] = ()
    ) -> None:
        self.handles.append(handle)
        self.shapes.append(shape)
        self.details.append(detail)
        self.attrs.append(attrs)

    def extend(self, other: Refinements) -> None:
        self.handles.extend(other.handles)
        self.shapes.extend(other.shapes)
        self.details.extend(other.details)
        self.attrs.extend(other.attrs)

    def __len__(self) -> int:
        return len(self.handles)

    def row(self, at: int) -> Refinement:
        return Refinement(self.handles[at], self.shapes[at], self.details[at], self.attrs[at])

    @staticmethod
    def of(items: Iterable[Any]) -> Refinements:
        """The `Refinement`s and `Refinements` among `items`, in order, as one batch."""
        out = Refinements()
        for item in items:
            if isinstance(item, Refinement):
                out.add(item.handle, item.shape, item.shape_detail, item.attrs)
            elif isinstance(item, Refinements):
                out.extend(item)
        return out


class Pack(Protocol):
    """A framework or repo pack: reads its config section and one snapshot, and writes edges,
    refinements, symbol facts and failures into the sink.

    A pack, a provider or an adapter that imports an installed distribution declares
    `distributions: tuple[str, ...]` (import names). The run summary key covers their code like
    `[discovery] external` packages (see `Framework` in ledger/envelope.py).

    A pack reads no file but the policy files its `policy_file_keys` name (the section keys that
    hold a path). `produce` gets a root of its own that holds exactly those files, and the
    section it gets names each one relative to that root, so a read of any other file fails.

    A pack whose row in one module can move when another module changes, with no trace of that
    read, declares `whole_modules(section) -> tuple[str, ...]`: the module names and fnmatch globs
    it reads as one unit. The predictor (hoglint/native/predict.py) predicts all of them when one
    changes.

    A pack whose framework reads every string of some modules as a dotted path (settings
    modules) declares `path_modules(section) -> tuple[str, ...]`: their names and `*` globs. Core
    reads a string there that names a module as a `dotted-path` edge wherever it stands.

    A pack that writes a symbol fact on every run from the whole tree, where the fact can sit in
    another file than what it was read from, declares `whole_symbol_kinds(section) ->
    tuple[str, ...]`: the `kind` facts of those symbols. The predictor compares every symbol of
    such a kind in both trees."""

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
    otherwise each output fills the field of the same name unless `sets` renames it. It declares
    `distributions` as a pack does, and `open` fails when one of them is not installed.

    `inputs` declares the files `open` reads, as path patterns relative to the repo root (`**`
    spans directories, `*` and `?` stay in one segment). The run summary key covers those files.
    In a run of a git tree, `root` holds exactly those files, so a read of any other file fails.
    """

    use: str  # the `use = "..."` name in a [[provider]] or [providers.*] entry
    allowed_options: frozenset[str]

    def inputs(self, options: Mapping[str, Any]) -> tuple[str, ...]: ...

    def open(self, options: Mapping[str, Any], root: Path) -> Any: ...

    def values(self, handle: Any, path: str) -> Mapping[str, Any]: ...


class CoverageAdapter(Protocol):
    """Answers whether an outside input list (for example a build tool's task inputs) watches a
    path. It declares `inputs` as a provider does; to ask whether the snapshot holds a file it
    does not read, it calls `PLUGIN_ROOTS.holds`. In a run it may ask that only about a path its
    `inputs`, a source pattern or another declared input covers, so the answer is in the run's
    key; any other question fails the run. An adapter may also have `open_run(options, run_root)`,
    which the rule calls in place of `open` with the run's root, so the adapter can answer from an
    answer stored under `PLUGIN_ROOTS.key` before `PLUGIN_ROOTS.of` writes the files it reads.

    `narrowed` answers a level's `claimed_by`: the file that narrows the inputs which decide for
    the files in `directory` ("" for the repo root), or None where they watch every file or no
    input list applies."""

    name: str
    allowed_options: frozenset[str]

    def inputs(self, options: Mapping[str, Any]) -> tuple[str, ...]: ...

    def open(self, options: Mapping[str, Any], root: Path) -> Any: ...

    def covered(self, handle: Any, path: str) -> tuple[bool, str]: ...

    def narrowed(self, handle: Any, directory: str) -> str | None: ...


def open_coverage(adapter: CoverageAdapter, options: Mapping[str, Any], repo: Path) -> Any:
    """The adapter's handle for a run whose root is `repo`, through `open_run` when it has one."""
    open_run = getattr(adapter, "open_run", None)
    if open_run is not None:
        return open_run(options, repo)
    return adapter.open(options, PLUGIN_ROOTS.of(repo, adapter, options))


def declared_inputs(plugin: object, options: Mapping[str, Any]) -> tuple[str, ...]:
    """The path patterns a provider or coverage adapter reads; none when it declares none."""
    inputs = getattr(plugin, "inputs", None)
    return tuple(inputs(options)) if inputs is not None else ()


class TreeRoot(Protocol):
    """The files of one git tree, for a run of that tree (see `PluginRoots`)."""

    def plugin_root(self, patterns: tuple[str, ...]) -> Path: ...

    def inputs_key(self, patterns: tuple[str, ...]) -> str: ...

    def holds(self, path: str) -> bool: ...


class PluginRoots:
    """Which roots stand for a snapshot: a git tree, or the working tree as git sees it (D40).

    A run's root is an empty directory filed here; each plugin it opens gets a directory of its
    own that holds exactly the files the plugin declares, written from the snapshot. A question
    about a file that no plugin reads, such as whether it exists, goes to `holds`, which answers
    from the snapshot's list of files. A root not filed here is a directory a plugin reads as it
    is; whether it holds a path is still answered from git's list of its working tree."""

    def __init__(self) -> None:
        self.trees: dict[Path, TreeRoot] = {}

    def add(self, root: Path, tree: TreeRoot) -> None:
        self.trees[root.resolve()] = tree

    def tree(self, root: Path) -> TreeRoot | None:
        return self.trees.get(root.resolve())

    def of(self, root: Path, plugin: object, options: Mapping[str, Any]) -> Path:
        """The root `plugin` reads under `options`."""
        tree = self.tree(root)
        return root if tree is None else tree.plugin_root(declared_inputs(plugin, options))

    def key(self, root: Path, plugin: object, options: Mapping[str, Any]) -> str:
        """A digest of the blob ids of what `plugin` reads under `options`, so an answer that
        depends only on those files can be stored under it; empty for a root that stands for no
        snapshot, or when the bytes of one of the files are not in a blob."""
        tree = self.tree(root)
        return "" if tree is None else tree.inputs_key(declared_inputs(plugin, options))

    def holds(self, root: Path, path: str) -> bool:
        """Whether the snapshot behind `root` holds `path`, a file or a directory."""
        tree = self.tree(root)
        return WorktreeListing.of(root).holds(path) if tree is None else tree.holds(path)


PLUGIN_ROOTS = PluginRoots()


class Registry:
    """Plugins by name. One process-wide instance; registering a name twice is an error."""

    def __init__(self) -> None:
        self.packs: dict[str, Pack] = {}
        self.providers: dict[str, Provider] = {}
        self.coverage_adapters: dict[str, CoverageAdapter] = {}
        # A finding's headline by the kind of its edge, for the kinds a pack makes.
        self.headlines: dict[str, str] = {}

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

    def add_headlines(self, headlines: Mapping[str, str]) -> None:
        """Templates that word a finding on an edge of a kind the pack makes: `{src}`, `{dst}`,
        `{label}` and the edge's attrs by name."""
        for kind, template in headlines.items():
            self.add(self.headlines, kind, template, "headline of edge kind")


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
