"""Extension points: packs make edges, providers set labels, coverage adapters read outside input lists.

Generic code (cli, labels, rules) only looks plugins up here by name. A pack or preset
registers its plugins when its module is imported; `extends` and `[discovery] plugins`
in the config name the modules to import.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from hoglint.model import Graph


@dataclass(frozen=True, slots=True)
class ProducerFailure:
    """A producer that could not run, and the edge kinds it would have made."""

    reason: str
    kinds: frozenset[str]


class Pack(Protocol):
    """A framework or repo pack: reads its config section and adds edges to the graph."""

    name: str  # the config section it owns, e.g. "django"
    allowed_keys: frozenset[str]
    edge_kinds: frozenset[str]  # every kind it may emit; a failure marks these incomplete

    def produce(self, section: Mapping[str, Any], core: Any, graph: Graph, root: Path) -> None: ...


class Provider(Protocol):
    """Fills label fields from files outside the code (owners files, CODEOWNERS, provenance)."""

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
