"""Builds the Graph of one code root under one config: core edges first, then each active pack.

A pack is active when the config has a table under the pack's name. Packs run in the order of
`[vocabulary] sections`, which follows the extends chain. A pack that raises is a failed producer:
every edge kind it may emit goes incomplete in the rules that read it.
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hoglint.config import Config
from hoglint.config.schema import as_tuple, canonical
from hoglint.facts import FACT_KINDS, FactNeeds
from hoglint.model import Graph
from hoglint.plugins import REGISTRY, Pack, ProducerFailure

if TYPE_CHECKING:
    from hoglint.core.config import CoreConfig


def active_packs(config: Config) -> list[tuple[Pack, Mapping[str, Any]]]:
    out = []
    for name in config.pack_sections:
        section = config.sections.get(name)
        pack = REGISTRY.packs.get(name)
        if pack is not None and isinstance(section, dict):
            out.append((pack, section))
    return out


class GraphSource:
    """Builds and caches one Graph per (code root, staged, config sections)."""

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = work_dir
        self.cache: dict[tuple[str, str], Graph] = {}

    def key(self, code_root: Path, config: Config, staged: bool) -> tuple[str, str]:
        sections = {name: config.sections.get(name) for name in sorted(config.sections)}
        return f"{code_root}{':staged' if staged else ''}", canonical(sections)

    def build(self, code_root: Path, config: Config, staged: bool = False) -> Graph:
        key = self.key(code_root, config, staged)
        if key not in self.cache:
            self.cache[key] = self.build_fresh(code_root, config, staged)
        return self.cache[key]

    @staticmethod
    def core_config(config: Config) -> CoreConfig:
        from hoglint.core.config import (
            CoreConfig,  # noqa: PLC0415 - the extractor stack loads only when a graph is built
        )

        discovery = config.discovery
        fields: dict[str, Any] = {
            "source_roots": as_tuple(discovery.get("source_roots", ["."])),
            "first_party": as_tuple(discovery.get("first_party")),
            "excludes": as_tuple(discovery.get("exclude")),
            "external_packages": as_tuple(discovery.get("external")),
        }
        if "tests" in discovery:
            fields["test_paths"] = as_tuple(discovery["tests"])
        if "migrations" in discovery:
            fields["migration_paths"] = as_tuple(discovery["migrations"])
        return CoreConfig(**fields)

    @staticmethod
    def fact_needs(packs: list[tuple[Pack, Mapping[str, Any]]]) -> FactNeeds:
        """The union of what the active packs need extracted. A pack that does not say needs every
        fact kind and every watch list a registered pack declares."""
        from hoglint.core.analysis import registered_needs  # noqa: PLC0415 - see core_config

        needs = FactNeeds()
        for pack, section in packs:
            declare = getattr(pack, "fact_needs", None)
            wanted = declare(section) if declare is not None else FactNeeds(kinds=FACT_KINDS) | registered_needs()
            needs = needs | wanted
        return needs

    def build_fresh(self, code_root: Path, config: Config, staged: bool) -> Graph:
        from hoglint.core.analysis import AnalysisOptions, CoreAnalysis  # noqa: PLC0415 - see core_config

        packs = active_packs(config)
        work = Path(tempfile.mkdtemp(dir=self.work_dir))
        options = AnalysisOptions(
            repo=code_root,
            config=self.core_config(config),
            work_dir=work,
            staged=staged,
            needs=self.fact_needs(packs),
        )
        analysis = CoreAnalysis(options).run()
        graph = analysis.graph
        for pack, section in packs:
            try:
                pack.produce(section, analysis, graph, code_root)
            except Exception as error:  # noqa: BLE001 - a failed pack makes its rules incomplete, never clean
                reason = f"{type(error).__name__}: {error}"
                graph.failed_producers[pack.name] = ProducerFailure(reason, frozenset(pack.edge_kinds))
        return graph
