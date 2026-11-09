"""GraphSource for `--engine native`: the same packs and config, the graph from the Rust link."""

from __future__ import annotations

import tempfile
from pathlib import Path

from hoglint.config import Config
from hoglint.core.analysis import AnalysisOptions
from hoglint.model import Graph
from hoglint.native.analysis import NativeAnalysis
from hoglint.plugins import ProducerFailure
from hoglint.source import GraphSource, active_packs

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
        for pack, section in packs:
            try:
                pack.produce(section, analysis, graph, code_root)  # type: ignore[arg-type]
            except Exception as error:  # noqa: BLE001 - a failed pack makes its rules incomplete, never clean
                reason = f"{type(error).__name__}: {error}"
                graph.failed_producers[pack.name] = ProducerFailure(reason, frozenset(pack.edge_kinds))
        return graph


def graph_source(engine: str, work_dir: Path) -> GraphSource:
    return NativeGraphSource(work_dir) if engine == "native" else GraphSource(work_dir)
