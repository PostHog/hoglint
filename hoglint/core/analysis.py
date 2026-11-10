"""Snapshot to Graph: discover files, extract facts (cached by content), build core edges."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from hoglint.core.build import CoreBuild
from hoglint.core.config import CoreConfig
from hoglint.core.discovery import Discovery, GitTree, Inventory, Snapshot, StagedIndex, WorkingTree
from hoglint.core.modules import ModuleNamer
from hoglint.core.producers import PRODUCERS, TABLE_KINDS, Producer
from hoglint.facts import (
    Extractor,
    ExtractRequest,
    FactNeeds,
    FactStore,
    default_cache_dir,
    site_packages,
    site_packages_files,
)
from hoglint.model import Graph
from hoglint.plugins import REGISTRY


def registered_needs() -> FactNeeds:
    """What every registered pack asks of the extractor with its defaults."""
    out = FactNeeds()
    for pack in REGISTRY.packs.values():
        fact_needs = getattr(pack, "fact_needs", None)
        if fact_needs is not None:
            out = out | fact_needs({})
    return out


@dataclass(frozen=True)
class AnalysisOptions:
    """`staged` reads the git index instead of the working tree. `cache_dir` None disables the cache.

    `producers` are the core producers to run. `needs` is what the packs that run afterwards ask
    of the extractor; None means every registered pack with its defaults.
    """

    repo: Path
    config: CoreConfig
    work_dir: Path
    staged: bool = False
    cache_dir: Path | None = field(default_factory=default_cache_dir)
    producers: tuple[type[Producer], ...] = PRODUCERS
    needs: FactNeeds | None = None
    tree: str = ""  # a git tree id: read that tree's blobs instead of the index or the working tree


@dataclass(frozen=False)
class AnalysisResult:
    inventory: Inventory
    store: FactStore
    build: CoreBuild
    graph: Graph
    timings: dict[str, float] = field(default_factory=dict)


class CoreAnalysis:
    def __init__(self, options: AnalysisOptions, extractor: Extractor | None = None) -> None:
        self.options = options
        self.extractor = extractor or Extractor()

    def snapshot(self) -> Snapshot:
        if self.options.tree:
            return GitTree(self.options.repo, self.options.tree)
        if self.options.staged:
            return StagedIndex(self.options.repo)
        return WorkingTree(self.options.repo)

    def first_party(self, inventory: Inventory) -> tuple[str, ...]:
        config = self.options.config
        if config.first_party:
            return config.first_party
        namer = ModuleNamer(config.source_roots)
        heads = {namer.module_of(path)[0].split(".")[0] for path in inventory.files}
        return tuple(sorted(head for head in heads if head.isidentifier()))

    def needs(self) -> FactNeeds:
        options = self.options
        reads = frozenset(TABLE_KINDS).union(*(producer.reads for producer in options.producers))
        core = FactNeeds(kinds=reads, watch_callees=frozenset(options.config.loader_callee_names()))
        return core | (options.needs if options.needs is not None else registered_needs())

    def extract(self, inventory: Inventory, external: dict[str, str]) -> FactStore:
        site = site_packages()
        records = {str(site / inside): record for record, inside in external.items()}
        request = ExtractRequest(
            root=inventory.root,
            files=(*inventory.files, *records),
            first_party=self.first_party(inventory),
            out_path=self.options.work_dir / "facts.jsonl",
            cache_dir=self.options.cache_dir,
            needs=self.needs(),
            lite=tuple(records),
        )
        return FactStore.from_jsonl(self.extractor.extract(request), records)

    def run(self) -> AnalysisResult:
        timings: dict[str, float] = {}
        snapshot = self.snapshot()
        external = site_packages_files(self.options.config.external_packages)
        try:
            started = time.perf_counter()
            inventory = Discovery(self.options.config).run(snapshot)
            timings["discover"] = time.perf_counter() - started
            started = time.perf_counter()
            store = self.extract(inventory, external)
            timings["extract"] = time.perf_counter() - started
        finally:
            snapshot.close()
        started = time.perf_counter()
        build = CoreBuild(self.options.config, store, list(inventory.analyzed), external)
        graph = build.graph(self.options.producers)
        timings["edges"] = time.perf_counter() - started
        return AnalysisResult(inventory, store, build, graph, timings)
