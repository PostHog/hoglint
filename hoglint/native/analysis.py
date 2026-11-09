"""Snapshot to native Graph: discover files, extract facts (cached by content), link in Rust.

The same discovery, extractor run and fact cache as CoreAnalysis; the facts go to the link
instead of into Python dicts, and packs read them through `NativeBuild`.
"""

from __future__ import annotations

import os
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from hoglint.core.analysis import CoreAnalysis
from hoglint.core.discovery import Discovery, Inventory
from hoglint.facts import ExtractRequest, site_packages_files
from hoglint.native import bridge
from hoglint.native.build import NativeBuild, NativeStore
from hoglint.native.config import link_config
from hoglint.native.graph import NativeGraph


@dataclass(frozen=False)
class NativeAnalysisResult:
    """AnalysisResult with the link in place of the Python fact store and core build."""

    inventory: Inventory
    store: NativeStore
    build: NativeBuild
    graph: NativeGraph
    timings: dict[str, float] = field(default_factory=dict)


class NativeAnalysis(CoreAnalysis):
    def threads(self) -> int:
        """Worker threads for the link: HOGLINT_THREADS, else one for a staged run (pre-commit,
        where ten worktrees may run at once), else every core (0)."""
        override = os.environ.get("HOGLINT_THREADS")
        if override:
            return int(override)
        return 1 if self.options.staged else 0

    def extract_jsonl(self, inventory: Inventory, external: dict[str, str]) -> Path:
        request = ExtractRequest(
            root=inventory.root,
            files=(*inventory.files, *external),
            first_party=self.first_party(inventory),
            out_path=self.options.work_dir / "facts.jsonl",
            cache_dir=self.options.cache_dir,
            needs=self.needs(),
            lite=tuple(external),
        )
        return self.extractor.extract(request)

    def run(self) -> NativeAnalysisResult:  # type: ignore[override]
        timings: dict[str, float] = {}
        snapshot = self.snapshot()
        config = self.options.config
        external = site_packages_files(config.external_packages)
        try:
            started = time.perf_counter()
            inventory = Discovery(config).run(snapshot)
            timings["discover"] = time.perf_counter() - started
            started = time.perf_counter()
            facts = self.extract_jsonl(inventory, external)
            timings["extract"] = time.perf_counter() - started
        finally:
            snapshot.close()
        started = time.perf_counter()
        native = bridge.load()
        config_json = json.dumps(link_config(config, inventory.analyzed, external))
        link = native.link_jsonl(str(facts), config_json, self.threads())
        timings["link"] = time.perf_counter() - started
        timings.update({f"link.{name}": seconds for name, seconds in link.timings()})
        started = time.perf_counter()
        symbols = {name: {"kind": kind, "path": path} for name, kind, path in link.symbols()}
        graph = NativeGraph(link, {}, symbols)
        build = NativeBuild(config, link, external, graph.core.maker)
        graph.modules = build.index.repo_modules()
        timings["graph"] = time.perf_counter() - started
        return NativeAnalysisResult(inventory, build.store, build, graph, timings)
