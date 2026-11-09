"""What packs read of a snapshot, backed by the native link (the CoreBuild interface).

Packs keep their code: records, file scopes, tables and the module index look the same. Every
resolution goes to the link, so packs see the resolver caches the core producers left, as
they do in the Python engine. A fact kind crosses for every file in one call; a record decodes
its part of it the first time a pack reads it.
"""

from __future__ import annotations

from array import array
from collections.abc import Iterator, Mapping
from functools import cached_property
from typing import Any

import orjson

from hoglint.core.config import CoreConfig
from hoglint.core.modules import FirstParty, ModuleIndex, ModuleNamer
from hoglint.core.records import RecordBodies
from hoglint.core.sites import MODULE_SCOPE, FileScope, Lookup
from hoglint.core.symbols import MODULE_LEVEL_FLAGS, Hit, ModuleTable, Tables
from hoglint.model import Context, EntityId, Site
from hoglint.native.graph import CONTEXT_SETS, EdgeMaker

Hits = tuple[str, list[tuple[str, str]]]


ABSENT = object()


ABSENT_RANGE = 0xFFFFFFFF


class KindTexts:
    """The records' JSON text, read in place: one buffer over the link's memory, and per fact
    kind where each record's list sits in it, fetched for every file at once on first use."""

    def __init__(self, link: Any) -> None:
        self.text = memoryview(link.text())
        self.link = link
        self.ranges: dict[str, array[int]] = {}

    def take(self, file: int, key: str) -> memoryview | None:
        ranges = self.ranges.get(key)
        if ranges is None:
            ranges = self.ranges[key] = array("I", self.link.kind_ranges(key))
        start = ranges[2 * file]
        if start == ABSENT_RANGE:
            return None
        return self.text[start : ranges[2 * file + 1]]


class NativeRecord(dict[str, Any]):
    """One file's record. A key decodes from the extractor's JSON text on first read and is
    kept; a key the record lacks is kept as ABSENT, so each one crosses at most once. `path` is
    the snapshot's: a cached record holds an empty one."""

    __slots__ = ("file", "link", "texts")

    def __init__(self, link: Any, texts: KindTexts, file: int, path: str) -> None:
        super().__init__()
        self.link = link
        self.texts = texts
        self.file = file
        dict.__setitem__(self, "path", path)

    def load(self, key: str) -> Any:
        text = self.texts.take(self.file, key)
        value = orjson.loads(text) if text is not None else ABSENT
        dict.__setitem__(self, key, value)
        return value

    def get(self, key: str, default: Any = None) -> Any:  # type: ignore[override]
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
        return iter(self.link.keys(self.file))

    def __len__(self) -> int:
        return len(self.link.keys(self.file))


class NativeStore:
    """`FactStore.records` over the link: path -> record, in fact order."""

    def __init__(self, link: Any, paths: list[str]) -> None:
        texts = KindTexts(link)
        self.records: dict[str, NativeRecord] = {
            path: NativeRecord(link, texts, i, path) for i, path in enumerate(paths)
        }


def module_values(record: Mapping[str, Any]) -> dict[str, object]:
    """name -> value of its last module-level assignment, as Tables.collect records them."""
    values: dict[str, object] = {}
    for row in record.get("assigns", ()):
        if row["s"] != -1 or row.get("f", 0) & ~MODULE_LEVEL_FLAGS or row["op"] not in ("=", ":", "type"):
            continue
        if "v" not in row:
            continue
        for target in row["t"]:
            if isinstance(target, str) and "." not in target:
                values[target] = row["v"]
    return values


class ModuleValues(dict[str, object]):
    """A module table's `values`, decoded from the record's assigns when a pack first reads them:
    most tables packs ask for are read for their names only."""

    def __init__(self, record: Mapping[str, Any] | None) -> None:
        super().__init__()
        self.record = record

    def load(self) -> None:
        if self.record is not None:
            record, self.record = self.record, None
            if record.get("status") == "ok":
                self.update(module_values(record))

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


class NativeTables(Tables):
    """Module tables from the link: the link built them for resolution already. `values` comes
    from the record, because the link keeps no assigned values."""

    def __init__(self, link: Any, store: NativeStore, index: ModuleIndex) -> None:
        super().__init__(store, index)  # type: ignore[arg-type]
        self.link = link

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

    @cached_property
    def defs(self) -> list[dict]:  # type: ignore[override]
        return self.record.get("defs", [])

    @cached_property
    def qualnames(self) -> list[str]:  # type: ignore[override]
        return self.build_qualnames()

    @cached_property
    def in_function(self) -> list[bool]:  # type: ignore[override]
        return self.build_in_function()

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
        self.link = link
        self.entities = entities

    def resolve_name(self, module: str, name: str) -> Hit | None:
        return self.entities.hit(self.link.resolve_name(module, name))

    def resolve_dotted(self, dotted: str) -> tuple[Hit | None, list[str]]:
        found, rest = self.link.resolve_dotted(dotted)
        return self.entities.hit(found), rest

    def resolve_value(self, module: str, value: object) -> Hit | None:
        return self.entities.hit(self.link.resolve_value(module, value))

    def resolve_class(self, module: str, value: object) -> EntityId | None:
        found = self.link.resolve_class(module, value)
        return self.entities.entity(found) if found is not None else None


class NativeClassIndex:
    """The ClassIndex methods packs call: MROs over resolved bases, answered by the link."""

    def __init__(self, link: Any, entities: Entities, store: NativeStore, paths: list[str]) -> None:
        self.link = link
        self.entities = entities
        self.store = store
        self.paths = paths

    def located(self, ref: EntityId) -> tuple[NativeRecord, int] | None:
        found = self.link.located(ref.module, ref.symbol)
        if found is None:
            return None
        file, idx = found
        return self.store.records[self.paths[file]], idx

    def node(self, ref: EntityId) -> dict | None:
        found = self.located(ref)
        return found[0]["defs"][found[1]] if found is not None else None

    def bases(self, ref: EntityId) -> list[EntityId]:
        return [self.entities.entity(pair) for pair in self.link.bases(ref.module, ref.symbol)]

    def mro(self, ref: EntityId) -> list[EntityId]:
        return [self.entities.entity(pair) for pair in self.link.mro(ref.module, ref.symbol)]

    def is_subclass(self, ref: EntityId, qualified: str) -> bool:
        return self.link.is_subclass(ref.module, ref.symbol, qualified)


class NativeContext:
    """The BuildContext lookups packs call, at sites of native file scopes."""

    def __init__(self, link: Any, entities: Entities) -> None:
        self.link = link
        self.entities = entities

    def lookup(self, found: tuple[str, Hits | None, bool]) -> Lookup:
        surface, hit, local = found
        return Lookup(surface, self.entities.hit(hit), local)

    def value_lookup(self, scope: FileScope, value: object, at_scope: int, line: int) -> Lookup | None:
        assert isinstance(scope, NativeFileScope)
        found = self.link.value_lookup(scope.scope, value, at_scope, line)
        return self.lookup(found) if found is not None else None

    def chain_lookup(self, scope: FileScope, chain: str, at_scope: int, line: int) -> tuple[Lookup, list[str]]:
        assert isinstance(scope, NativeFileScope)
        found, rest = self.link.chain_lookup(scope.scope, chain, at_scope, line)
        return self.lookup(found), rest


class NativeBuild:
    """CoreBuild for packs on the native engine."""

    def __init__(self, config: CoreConfig, link: Any, external: Mapping[str, str], maker: EdgeMaker) -> None:
        self.config = config
        self.link = link
        self.external = dict(external)
        paths: list[str] = link.paths()
        self.store = NativeStore(link, paths)
        self.namer = ModuleNamer(config.source_roots, self.external)
        self.index = ModuleIndex(paths, self.namer, config.first_party)
        self.first_party = FirstParty(config.first_party or self.index.repo_heads)
        self.tables = NativeTables(link, self.store, self.index)
        entities = Entities(maker)
        self.resolver = NativeResolver(link, entities)
        self.classes = NativeClassIndex(link, entities, self.store, paths)
        self.context = NativeContext(link, entities)
        self.files: list[FileScope] = [
            NativeFileScope(scope, paths[file], module, is_package, self.store.records[paths[file]], bits)
            for scope, (file, module, is_package, bits) in enumerate(link.scopes())
        ]
        self.scope_of = {scope.path: scope for scope in self.files}
        self.bodies_cache: dict[str, RecordBodies] = {}

    def bodies(self, record: Mapping[str, Any]) -> RecordBodies:
        path = record["path"]
        found = self.bodies_cache.get(path)
        if found is None:
            found = self.bodies_cache[path] = RecordBodies(record)  # type: ignore[arg-type]
        return found
