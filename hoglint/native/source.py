"""GraphSource for `--engine native`: the same packs and config, the graph from the Rust link."""

from __future__ import annotations

import gc
import os
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from hoglint.config import Config
from hoglint.config.schema import canonical
from hoglint.core.analysis import AnalysisOptions
from hoglint.core.memo import OFF, STATS_ENV, TRACE, TRACER, PackMemo, digest
from hoglint.core.reader import BuildReader
from hoglint.model import Graph
from hoglint.native.analysis import NativeAnalysis
from hoglint.native.answers import ReaderAnswers, file_keys
from hoglint.native.build import NativeBuild
from hoglint.native.graph import NativeGraph, NativeSink
from hoglint.native.memo_store import MemoStore, engine_id
from hoglint.native.queries import NativePackQueries
from hoglint.plugins import Pack
from hoglint.source import GraphSource, active_packs, run_packs

ENGINES = ("python", "native")


class StepReleasingSink(NativeSink):
    """A NativeSink that drops the records each producer step decoded once the step ends, so
    the decoded records of every step are not held at once."""

    def __init__(self, graph: NativeGraph, build: NativeBuild) -> None:
        super().__init__(graph)
        self.build = build

    def step_ended(self) -> None:
        self.build.forget_decoded()


class NativeGraphSource(GraphSource):
    def build_fresh(self, code_root: Path, config: Config, staged: bool, tree: str = "") -> Graph:
        packs = active_packs(config)
        work = Path(tempfile.mkdtemp(dir=self.work_dir))
        options = AnalysisOptions(
            repo=code_root,
            config=self.core_config(config),
            work_dir=work,
            staged=staged,
            needs=self.fact_needs(packs),
            tree=tree,
        )
        analysis = NativeAnalysis(options).run()
        graph = analysis.graph
        build = analysis.build
        memo = self.pack_memo(options, build, self.memo_config(config, packs))
        reader = BuildReader(build, NativePackQueries(build, graph, graph.link), memo)
        TRACER.reset()
        try:
            if memo is not None:
                TRACER.files = file_keys(graph.link)
                ReaderAnswers(reader, build).register()
            run_packs(packs, reader, StepReleasingSink(graph, build), self.listing_of(code_root, tree))
            if memo is not None:
                memo.flush()
        finally:
            TRACER.reset()
        build.release()
        return graph

    def memo_config(self, config: Config, packs: list[tuple[Pack, Mapping[str, Any]]]) -> str:
        """The config as the pack memo keys on it. A pack's policy file is named by a path that
        may be a new scratch copy each run, so the key holds the file's bytes' digest instead."""
        sections: dict[str, Any] = {name: config.sections.get(name) for name in sorted(config.sections)}
        for pack, section in packs:
            own = dict(section)
            for key in getattr(pack, "policy_file_keys", ()):
                value = own.get(key)
                if isinstance(value, str) and value and Path(value).is_file():
                    own[key] = digest(Path(value).read_bytes())
            sections[pack.name] = own
        surfaces = [(surface.within, surface.paths) for surface in self.core_config(config).contract_surfaces]
        return canonical({"sections": sections, "contract_surfaces": surfaces})

    @staticmethod
    def pack_memo(options: AnalysisOptions, build: NativeBuild, config_key: str) -> PackMemo | None:
        """The run's pack memo (hoglint/core/memo.py), keyed by every input outside the snapshot
        that a pack item depends on: the engine, the extractor and its options, the config, the
        third-party files and the first-party roots. None when it is switched off."""
        mode = PackMemo.mode()
        if mode == OFF:
            return None
        policy = digest(
            [
                engine_id(),
                build.extracted_by,
                config_key,
                sorted(build.external.items()),
                sorted(build.first_party.roots),
                sys.version,
            ]
        )
        store = None
        if mode != TRACE and options.cache_dir is not None:
            store = MemoStore(options.cache_dir.parent, engine_id())
        return PackMemo(store, policy, os.environ.get(STATS_ENV, ""))


def batch_mode(engine: str) -> None:
    """A native run is one batch: it builds millions of short-lived objects (decoded facts, edges)
    and frees them at exit. The cyclic collector would rescan them over and over, about 40% of a
    native run, so it stays off; nothing the run keeps forms reference cycles worth collecting."""
    if engine == "native":
        gc.disable()


def graph_source(engine: str, work_dir: Path) -> GraphSource:
    return NativeGraphSource(work_dir) if engine == "native" else GraphSource(work_dir)
