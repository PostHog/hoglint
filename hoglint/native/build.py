"""What packs read of a snapshot, backed by the native link (the CoreBuild interface).

Packs keep their code: records, file scopes, tables and the module index look the same. Every
resolution goes to the link, so packs see the resolver caches the core producers left, as
they do in the Python engine. A record decodes one fact kind the first time a pack reads it,
from that record's text alone.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from functools import cached_property
from typing import Any

import orjson

from hoglint.core.config import CoreConfig
from hoglint.core.memo import NULL_TRACER, FileKeysMap, Traced, TracedMap, TracedQuery, Tracer
from hoglint.core.modules import FirstParty, ModuleIndex, ModuleNamer
from hoglint.core.records import RecordBodies
from hoglint.core.sites import MODULE_SCOPE, FileScope, Lookup
from hoglint.core.symbols import Hit, ModuleTable, Tables, module_assigns
from hoglint.model import Context, EntityId, Site
from hoglint.native.graph import CONTEXT_SETS, EdgeMaker

Hits = tuple[str, list[tuple[str, str]]]


ABSENT = object()
# Bits of a def's flags in `Link.def_scopes`.
IN_FUNCTION = 1
IS_CLASS = 2


class NativeRecord(dict[str, Any]):
    """One file's record. A key decodes from the extractor's JSON text on first read and is
    kept; a key the record lacks is kept as ABSENT, so each one crosses at most once. The link
    reads the text of that one record, never the snapshot's. `path` is the snapshot's: a cached
    record holds an empty one."""

    __slots__ = ("file", "link", "tracer")

    def __init__(self, link: Any, file: int, path: str) -> None:
        super().__init__()
        self.link = link
        self.file = file
        self.tracer: Tracer = NULL_TRACER
        dict.__setitem__(self, "path", path)

    def load(self, key: str) -> Any:
        text = self.link.raw(self.file, key)
        value = orjson.loads(text) if text is not None else ABSENT
        dict.__setitem__(self, key, value)
        return value

    def touch(self) -> None:
        """Notes a read of this record's file in the running item."""
        if self.tracer.frames:
            self.tracer.note_file(self.file)

    def get(self, key: str, default: Any = None) -> Any:  # type: ignore[override]
        if self.tracer.frames:
            self.tracer.note_file(self.file)
        value = dict.get(self, key, ABSENT)
        if value is ABSENT and not dict.__contains__(self, key):
            value = self.load(key)
        return default if value is ABSENT else value

    def __getitem__(self, key: str) -> Any:
        value = self.get(key, ABSENT)
        if value is ABSENT:
            raise KeyError(key)
        return value

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and self.get(key, ABSENT) is not ABSENT

    def __iter__(self) -> Iterator[str]:
        self.touch()
        return iter(self.link.keys(self.file))

    def __len__(self) -> int:
        self.touch()
        return len(self.link.keys(self.file))


class NativeStore:
    """`FactStore.records` over the link: path -> record, in fact order."""

    def __init__(self, link: Any, paths: list[str]) -> None:
        self.records: dict[str, NativeRecord] = {path: NativeRecord(link, i, path) for i, path in enumerate(paths)}
        statuses = link.statuses()
        for record in self.records.values():
            status = statuses[record.file]
            dict.__setitem__(record, "status", ABSENT if status is None else status)

    def forget_decoded(self) -> None:
        """Drops every record's decoded keys but its path and status. A later read decodes the
        key again from the record's text, to an equal value."""
        for record in self.records.values():
            if dict.__len__(record) <= 2:
                continue
            path, status = dict.get(record, "path"), dict.get(record, "status")
            dict.clear(record)
            dict.__setitem__(record, "path", path)
            dict.__setitem__(record, "status", status)


class ModuleValues(dict[str, object]):
    """A module table's `values`, decoded from the record's assigns when a pack first reads them:
    most tables packs ask for are read for their names only."""

    def __init__(self, record: Mapping[str, Any] | None) -> None:
        super().__init__()
        self.record = record

    def load(self) -> None:
        if self.record is not None:
            record, self.record = self.record, None
            self.update((name, row["v"]) for name, row in module_assigns(record).items())

    def get(self, key: str, default: Any = None) -> Any:  # type: ignore[override]
        self.load()
        return super().get(key, default)

    def __getitem__(self, key: str) -> object:
        self.load()
        return super().__getitem__(key)

    def __contains__(self, key: object) -> bool:
        self.load()
        return super().__contains__(key)

    def __iter__(self) -> Iterator[str]:
        self.load()
        return super().__iter__()

    def __len__(self) -> int:
        self.load()
        return super().__len__()

    def items(self) -> Any:
        self.load()
        return super().items()


def table_form(table: ModuleTable | None) -> object:
    """A table as the answer of its read: its names, not the values its record holds."""
    if table is None:
        return None
    return (table.path, table.is_package, table.names, table.star_imports, table.lazy)


class TracedModuleIndex(ModuleIndex):
    """The module index, each answer a read: of the listing, by module or path."""

    def __init__(self, paths: list[str], namer: ModuleNamer, first_party: tuple[str, ...]) -> None:
        super().__init__(paths, namer, first_party)
        tracer = NULL_TRACER
        path_of = TracedMap(tracer, "module-path", self.path_of)
        module_of = TracedMap(tracer, "path-module", self.module_of)
        self.path_of, self.module_of = path_of, module_of
        self.exists_read = TracedQuery(tracer, "module-exists", self.has_module, plain=True)
        self.package_read = TracedQuery(tracer, "module-package", self.has_package, plain=True)
        self.external_read = TracedQuery(tracer, "module-external", super().is_external, plain=True)
        self.reads: list[Traced] = [path_of, module_of, self.exists_read, self.package_read, self.external_read]

    def has_module(self, module: str) -> bool:
        return dict.__contains__(self.path_of, module) or module in self.dirs

    def has_package(self, module: str) -> bool:
        return self.package.get(module, module in self.dirs)

    def exists(self, module: str) -> bool:
        return self.exists_read(module)

    def is_file_module(self, module: str) -> bool:
        return module in self.path_of

    def is_package(self, module: str) -> bool:
        return self.package_read(module)

    def is_external(self, module: str) -> bool:
        return self.external_read(module)


class NativeTables(Tables):
    """Module tables from the link: the link built them for resolution already. `values` comes
    from the record, because the link keeps no assigned values."""

    def __init__(self, link: Any, store: NativeStore, index: ModuleIndex) -> None:
        super().__init__(store, index)  # type: ignore[arg-type]
        self.link = link
        self.table_read = TracedQuery(NULL_TRACER, "table", super().get, table_form, plain=True)

    def build(self, module: str) -> ModuleTable | None:
        found = self.link.table(module)
        if found is None:
            return None
        is_package, names, stars, lazy = found
        table = ModuleTable(module, self.index.path_of.get(module), is_package)
        table.names = dict(names)
        table.star_imports = stars
        table.lazy = dict(lazy)
        table.values = ModuleValues(self.record(module))
        return table

    def get(self, module: str) -> ModuleTable | None:
        """A module's table, a read of its names; its `values` come from the module's record,
        whose reads note the file."""
        table = self.table_read(module)
        if table is not None and self.table_read.tracer.frames:
            record: Any = self.record(module)
            if record is not None:
                record.touch()
        return table


class NativeFileScope(FileScope):
    """A FileScope whose bindings live in the link. Name lookups at its sites go to the link
    (`NativeContext`); the record-derived parts load when a pack reads them."""

    def __init__(self, scope: int, path: str, module: str, is_package: bool, record: NativeRecord, bits: int) -> None:
        self.scope = scope
        self.path = path
        self.module = module
        self.is_package = is_package
        self.record = record  # type: ignore[assignment]
        self.base_contexts = CONTEXT_SETS[bits]
        self.context_sets: dict[tuple[int, bool], frozenset[Context]] = {}

    def touch(self) -> None:
        """Notes a read of the file: what these properties keep comes from its facts."""
        record: Any = self.record
        record.touch()

    @property
    def defs(self) -> list[dict]:  # type: ignore[override]
        self.touch()
        return self.kept_defs

    @cached_property
    def kept_defs(self) -> list[dict]:
        return self.record.get("defs", [])

    @property
    def def_scopes(self) -> tuple[list[str], bytes]:
        """The link's qualified names and flags of the defs (`Link.def_scopes`), so no def
        decodes for a site's scope name or contexts."""
        self.touch()
        return self.kept_def_scopes

    @cached_property
    def kept_def_scopes(self) -> tuple[list[str], bytes]:
        record: Any = self.record
        return record.link.def_scopes(self.scope)

    @property
    def qualnames(self) -> list[str]:  # type: ignore[override]
        return self.def_scopes[0]

    @property
    def in_function(self) -> list[bool]:  # type: ignore[override]
        self.touch()
        return self.kept_in_function

    @cached_property
    def kept_in_function(self) -> list[bool]:
        return [bool(flags & IN_FUNCTION) for flags in self.def_scopes[1]]

    def is_function_scope(self, scope: int) -> bool:
        if scope == -1:
            return False
        flags = self.def_scopes[1][scope]
        return not flags & IS_CLASS or bool(flags & IN_FUNCTION)

    def site(self, scope: int, line: int, column: int) -> Site:
        return Site(self.path, self.module, self.scope_name(scope), line, column)

    def scope_name(self, scope: int) -> str:
        return MODULE_SCOPE if scope == -1 else self.qualnames[scope]


class Entities:
    """Entity ids and hits from the link's (module, symbol) pairs."""

    def __init__(self, maker: EdgeMaker) -> None:
        self.maker = maker

    def entity(self, pair: tuple[str, str]) -> EntityId:
        return self.maker.entity(pair[0], pair[1])

    def hit(self, found: Hits | None) -> Hit | None:
        if found is None:
            return None
        kind, chain = found
        return Hit(tuple(self.entity(pair) for pair in chain), kind)


class NativeResolver:
    """The Resolver methods packs call, answered by the link."""

    def __init__(self, link: Any, entities: Entities) -> None:
        self.entities = entities
        tracer = NULL_TRACER
        self.names = TracedQuery(tracer, "resolve-name", link.resolve_name)
        self.dotted = TracedQuery(tracer, "resolve-dotted", link.resolve_dotted, tuple, plain=True)
        self.values = TracedQuery(tracer, "resolve-value", link.resolve_value)
        self.classes = TracedQuery(tracer, "resolve-class", link.resolve_class)

    def traced(self) -> list[Traced]:
        return [self.names, self.dotted, self.values, self.classes]

    def resolve_name(self, module: str, name: str) -> Hit | None:
        return self.entities.hit(self.names(module, name))

    def resolve_dotted(self, dotted: str) -> tuple[Hit | None, list[str]]:
        found, rest = self.dotted(dotted)
        return self.entities.hit(found), rest

    def resolve_value(self, module: str, value: object) -> Hit | None:
        return self.entities.hit(self.values(module, value))

    def resolve_class(self, module: str, value: object) -> EntityId | None:
        found = self.classes(module, value)
        return self.entities.entity(found) if found is not None else None


class NativeClassIndex:
    """The ClassIndex methods packs call: MROs over resolved bases, answered by the link."""

    def __init__(self, link: Any, entities: Entities, store: NativeStore, paths: list[str]) -> None:
        self.entities = entities
        self.store = store
        self.paths = paths
        tracer = NULL_TRACER
        self.locations = TracedQuery(tracer, "located", link.located, self.location_form)
        self.bases_read = TracedQuery(tracer, "bases", link.bases)
        self.mro_read = TracedQuery(tracer, "mro", link.mro)
        self.subclass_read = TracedQuery(tracer, "is-subclass", link.is_subclass)

    def traced(self) -> list[Traced]:
        return [self.locations, self.bases_read, self.mro_read, self.subclass_read]

    def location_form(self, found: tuple[int, int] | None) -> object:
        return None if found is None else (self.paths[found[0]], found[1])

    def located(self, ref: EntityId) -> tuple[NativeRecord, int] | None:
        found = self.locations(ref.module, ref.symbol)
        if found is None:
            return None
        file, idx = found
        return self.store.records[self.paths[file]], idx

    def node(self, ref: EntityId) -> dict | None:
        found = self.located(ref)
        return found[0]["defs"][found[1]] if found is not None else None

    def bases(self, ref: EntityId) -> list[EntityId]:
        return [self.entities.entity(pair) for pair in self.bases_read(ref.module, ref.symbol)]

    def mro(self, ref: EntityId) -> list[EntityId]:
        return [self.entities.entity(pair) for pair in self.mro_read(ref.module, ref.symbol)]

    def is_subclass(self, ref: EntityId, qualified: str) -> bool:
        return self.subclass_read(ref.module, ref.symbol, qualified)


class NativeContext:
    """The BuildContext lookups packs call, at sites of native file scopes. A lookup's read is
    keyed by the scope's path, so it is asked again through the scope of that path."""

    def __init__(self, link: Any, entities: Entities, scope_of: dict[str, FileScope]) -> None:
        self.link = link
        self.entities = entities
        self.scope_of = scope_of
        tracer = NULL_TRACER
        self.values = TracedQuery(tracer, "value-lookup", self.ask_value)
        self.chains = TracedQuery(tracer, "chain-lookup", self.ask_chain, tuple)

    def traced(self) -> list[Traced]:
        return [self.values, self.chains]

    def ask_value(self, path: str, value: object, at_scope: int, line: int) -> Any:
        scope: Any = self.scope_of[path]
        return self.link.value_lookup(scope.scope, value, at_scope, line)

    def ask_chain(self, path: str, chain: str, at_scope: int, line: int) -> Any:
        scope: Any = self.scope_of[path]
        return self.link.chain_lookup(scope.scope, chain, at_scope, line)

    def lookup(self, found: tuple[str, Hits | None, bool]) -> Lookup:
        surface, hit, local = found
        return Lookup(surface, self.entities.hit(hit), local)

    def value_lookup(self, scope: FileScope, value: object, at_scope: int, line: int) -> Lookup | None:
        assert isinstance(scope, NativeFileScope)
        found = self.values(scope.path, value, at_scope, line)
        return self.lookup(found) if found is not None else None

    def chain_lookup(self, scope: FileScope, chain: str, at_scope: int, line: int) -> tuple[Lookup, list[str]]:
        assert isinstance(scope, NativeFileScope)
        found, rest = self.chains(scope.path, chain, at_scope, line)
        return self.lookup(found), rest


class NativeBuild:
    """CoreBuild for packs on the native engine. `extracted_by` names what made the facts (the
    extractor and its options); a pack's per-file cache keys on it (`fact_digest`)."""

    def __init__(
        self, config: CoreConfig, link: Any, external: Mapping[str, str], maker: EdgeMaker, extracted_by: str = ""
    ) -> None:
        self.config = config
        self.link = link
        self.extracted_by = extracted_by
        self.external = dict(external)
        paths: list[str] = link.paths()
        self.paths = paths
        self.store = NativeStore(link, paths)
        self.namer = ModuleNamer(config.source_roots, self.external)
        self.index = TracedModuleIndex(paths, self.namer, config.first_party)
        self.first_party = FirstParty(config.first_party or self.index.repo_heads)
        self.tables = NativeTables(link, self.store, self.index)
        entities = Entities(maker)
        self.resolver = NativeResolver(link, entities)
        self.classes = NativeClassIndex(link, entities, self.store, paths)
        self.files: list[FileScope] = [
            NativeFileScope(scope, paths[file], module, is_package, self.store.records[paths[file]], bits)
            for scope, (file, module, is_package, bits) in enumerate(link.scopes())
        ]
        self.scope_of = {scope.path: scope for scope in self.files}
        self.context = NativeContext(link, entities, self.scope_of)
        self.bodies_cache: dict[str, RecordBodies] = {}

    def trace_with(self, tracer: Tracer) -> None:
        """Notes what packs read of this build on `tracer`, the run's pack memo's: the files by
        id and content key, and each query the build answers."""
        tracer.files = FileKeysMap(self.paths, dict(zip(self.paths, self.link.blob_keys(), strict=True)))
        for record in self.store.records.values():
            record.tracer = tracer
        reads = [*self.index.reads, self.tables.table_read]
        reads += [*self.resolver.traced(), *self.classes.traced(), *self.context.traced()]
        for read in reads:
            read.tracer = tracer
            read.answer_reads()

    def bodies(self, record: Mapping[str, Any]) -> RecordBodies:
        if isinstance(record, NativeRecord):
            record.touch()
        path = record["path"]
        found = self.bodies_cache.get(path)
        if found is None:
            found = self.bodies_cache[path] = RecordBodies(record)  # type: ignore[arg-type]
        return found

    def forget_decoded(self) -> None:
        """Drops the decoded records and the bodies over them (`NativeStore.forget_decoded`)."""
        self.store.forget_decoded()
        self.bodies_cache.clear()

    def release(self) -> None:
        """Drops what packs read, once the pack phase ends (D23): the decoded records, the
        tables and file scopes over them, and the link's fact rows. Rules read edges, symbols
        and scopes only. The objects may sit in reference cycles, which no collector frees in a
        native run (`batch_mode`), so their contents are cleared here."""
        for record in self.store.records.values():
            dict.clear(record)
        self.store.records.clear()
        self.tables.cache.clear()
        self.files.clear()
        self.scope_of.clear()
        self.bodies_cache.clear()
        self.link.release_facts()
