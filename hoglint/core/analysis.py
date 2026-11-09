"""Snapshot to Graph: discover files, extract facts (cached by content), build core edges."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from hoglint.core.build import CoreBuild
from hoglint.core.config import CoreConfig
from hoglint.core.discovery import Discovery, Inventory, Snapshot, StagedIndex, WorkingTree
from hoglint.core.modules import ModuleNamer
from hoglint.facts import Extractor, ExtractRequest, FactStore, default_cache_dir
from hoglint.model import Graph


@dataclass(frozen=True)
class AnalysisOptions:
    """`staged` reads the git index instead of the working tree. `cache_dir` None disables the cache."""

    repo: Path
    config: CoreConfig
    work_dir: Path
    staged: bool = False
    cache_dir: Path | None = field(default_factory=default_cache_dir)


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

    def extract(self, inventory: Inventory) -> FactStore:
        config = self.options.config
        external = [str(path) for path in Extractor.site_packages_files(config.external_packages)]
        request = ExtractRequest(
            root=inventory.root,
            files=(*inventory.files, *external),
            first_party=self.first_party(inventory),
            out_path=self.options.work_dir / "facts.jsonl",
            cache_dir=self.options.cache_dir,
            watch_callees=config.loader_callee_names(),
        )
        return FactStore.from_jsonl(self.extractor.extract(request))

    def run(self) -> AnalysisResult:
        timings: dict[str, float] = {}
        snapshot = self.snapshot()
        try:
            started = time.perf_counter()
            inventory = Discovery(self.options.config).run(snapshot)
            timings["discover"] = time.perf_counter() - started
            started = time.perf_counter()
            store = self.extract(inventory)
            timings["extract"] = time.perf_counter() - started
        finally:
            snapshot.close()
        started = time.perf_counter()
        build = CoreBuild(self.options.config, store, list(inventory.analyzed))
        graph = build.graph()
        timings["edges"] = time.perf_counter() - started
        return AnalysisResult(inventory, store, build, graph, timings)
