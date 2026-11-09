"""What packs read of a snapshot, backed by the native link (the CoreBuild interface).

Packs keep their code: records, file scopes, tables and the module index look the same. Every
resolution goes to the link, so packs see the resolver caches the core producers left, as
they do in the Python engine. A record decodes a fact list from the extractor's JSON text the
first time a pack reads it.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from functools import cached_property
from typing import Any

import orjson

from hoglint.core.config import CoreConfig
from hoglint.core.modules import FirstParty, ModuleIndex, ModuleNamer
from hoglint.core.records import RecordBodies
from hoglint.core.sites import MODULE_SCOPE, FileScope, Lookup
from hoglint.core.symbols import Hit, Tables
from hoglint.model import Context, EntityId, Site
from hoglint.native.graph import CONTEXT_SETS, EdgeMaker

Hits = tuple[str, list[tuple[str, str]]]


class NativeRecord(Mapping[str, Any]):
    """One file's record; each key decodes from the link's JSON text on first read."""

    __slots__ = ("decoded", "file", "link")

    def __init__(self, link: Any, file: int) -> None:
        self.link = link
        self.file = file
        self.decoded: dict[str, Any] = {}

    def __getitem__(self, key: str) -> Any:
        if key in self.decoded:
            return self.decoded[key]
        raw = self.link.raw(self.file, key)
        if raw is None:
            raise KeyError(key)
        value = self.decoded[key] = orjson.loads(raw)
        return value

    def get(self, key: str, default: Any = None) -> Any:
        found = self.decoded.get(key, self)
        if found is not self:
            return found
        try:
            return self[key]
        except KeyError:
            return default

    def __iter__(self) -> Iterator[str]:
        return iter(self.link.keys(self.file))

    def __len__(self) -> int:
        return len(self.link.keys(self.file))


class NativeStore:
    """`FactStore.records` over the link: path -> record, in fact order."""

    def __init__(self, link: Any, paths: list[str]) -> None:
        self.records: dict[str, NativeRecord] = {path: NativeRecord(link, i) for i, path in enumerate(paths)}


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
        self.tables = Tables(self.store, self.index)  # type: ignore[arg-type]
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
