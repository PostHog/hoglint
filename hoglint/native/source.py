"""GraphSource for `--engine native`: the same packs and config, the graph from the Rust link."""

from __future__ import annotations

import gc
import tempfile
from pathlib import Path

from hoglint.config import Config
from hoglint.core.analysis import AnalysisOptions
from hoglint.core.memo import TRACER, PackMemo
from hoglint.core.reader import BuildReader
from hoglint.model import Graph
from hoglint.native.analysis import NativeAnalysis
from hoglint.native.build import NativeBuild
from hoglint.native.graph import NativeGraph, NativeSink
from hoglint.native.queries import NativePackQueries
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
        memo = PackMemo.from_environment()
        reader = BuildReader(build, NativePackQueries(build, graph, graph.link), memo)
        TRACER.reset()
        try:
            run_packs(packs, reader, StepReleasingSink(graph, build), self.listing_of(code_root, tree))
        finally:
            TRACER.reset()
        if memo is not None:
            memo.write_stats()
        build.release()
        return graph


def batch_mode(engine: str) -> None:
    """A native run is one batch: it builds millions of short-lived objects (decoded facts, edges)
    and frees them at exit. The cyclic collector would rescan them over and over, about 40% of a
    native run, so it stays off; nothing the run keeps forms reference cycles worth collecting."""
    if engine == "native":
        gc.disable()


def graph_source(engine: str, work_dir: Path) -> GraphSource:
    return NativeGraphSource(work_dir) if engine == "native" else GraphSource(work_dir)
