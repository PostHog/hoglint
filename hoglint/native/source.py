"""GraphSource for `--engine native`: the same packs and config, the graph from the Rust link."""

from __future__ import annotations

import gc
import tempfile
from pathlib import Path
from typing import Any

from hoglint.config import Config
from hoglint.core.analysis import AnalysisOptions
from hoglint.core.reader import BuildReader
from hoglint.model import Graph
from hoglint.native.analysis import NativeAnalysis
from hoglint.native.graph import NativeSink
from hoglint.native.queries import NativePackQueries
from hoglint.source import GraphSource, active_packs, run_packs

ENGINES = ("python", "native")


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
        build: Any = analysis.build
        reader = BuildReader(build, NativePackQueries(build, graph, graph.link))
        run_packs(packs, reader, NativeSink(graph), code_root)
        return graph


def batch_mode(engine: str) -> None:
    """A native run is one batch: it builds millions of short-lived objects (decoded facts, edges)
    and frees them at exit. The cyclic collector would rescan them over and over, about 40% of a
    native run, so it stays off; nothing the run keeps forms reference cycles worth collecting."""
    if engine == "native":
        gc.disable()


def graph_source(engine: str, work_dir: Path) -> GraphSource:
    return NativeGraphSource(work_dir) if engine == "native" else GraphSource(work_dir)
