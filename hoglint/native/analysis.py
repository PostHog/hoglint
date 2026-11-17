"""Snapshot to native Graph: list the snapshot's blobs, read or extract their facts, link in Rust.

The facts of a blob are cached under (facts id, blob oid) in the fact database, so a warm run
reads one database and no source at all. Packs read them through `NativeBuild`.
"""

from __future__ import annotations

import os
import json
import time
from dataclasses import dataclass, field

from hoglint.core.analysis import AnalysisOptions, registered_needs
from hoglint.core.catalog import core_edge_kinds, core_fact_kinds
from hoglint.core.discovery import Discovery, Inventory
from hoglint.core.modules import ModuleNamer
from hoglint.facts import FactNeeds, SitePackagesFiles, site_packages
from hoglint.native import bridge
from hoglint.native.build import NativeBuild, NativeStore
from hoglint.native.config import link_config
from hoglint.native.factdb import FactDatabase
from hoglint.native.graph import NativeGraph
from hoglint.native.snapshot import BlobSnapshot
from hoglint.plugins import REGISTRY, ProducerFailure

SNAPSHOT_PRODUCER = "snapshot"
# The threads a staged run (pre-commit) takes: it runs beside other hooks (D6).
STAGED_THREADS = 1
# What the extractor's `--lite-kinds` defaults to: what module tables and class ancestry read.
LITE_KINDS = ("imports", "defs", "assigns")


@dataclass(frozen=False)
class NativeAnalysisResult:
    """What a run of one snapshot gives: its inventory, the link's store and build, the graph,
    and the seconds of each phase."""

    inventory: Inventory
    store: NativeStore
    build: NativeBuild
    graph: NativeGraph
    timings: dict[str, float] = field(default_factory=dict)


class NativeAnalysis:
    def __init__(self, options: AnalysisOptions) -> None:
        self.options = options

    def first_party(self, inventory: Inventory) -> tuple[str, ...]:
        config = self.options.config
        if config.first_party:
            return config.first_party
        namer = ModuleNamer(config.source_roots)
        heads = {namer.module_of(path)[0].split(".")[0] for path in inventory.files}
        return tuple(sorted(head for head in heads if head.isidentifier()))

    def needs(self) -> FactNeeds:
        config = self.options.config
        core = FactNeeds(kinds=core_fact_kinds(config), watch_callees=frozenset(config.watched_callee_names()))
        return core | (self.options.needs if self.options.needs is not None else registered_needs())

    def threads(self) -> int:
        """Worker threads for extraction and the link: HOGLINT_THREADS, else one for a staged run
        (pre-commit) and the baseline it starts, else every core (0). Output is the same at any
        thread count."""
        override = os.environ.get("HOGLINT_THREADS")
        if override:
            return int(override)
        return STAGED_THREADS if self.options.staged else 0

    def fact_database(self, facts_id: str, layout: int) -> FactDatabase | None:
        """The fact database of this build, pruned on the first run of the day; None without a
        cache, or when the cache is not usable (logged once)."""
        if self.options.cache_dir is None:
            return None
        database = FactDatabase(self.options.cache_dir, facts_id, layout)
        return database if database.open_for_run() else None

    def blob_snapshot(self) -> BlobSnapshot:
        if self.options.tree:
            return BlobSnapshot.tree(self.options.repo, self.options.tree)
        if self.options.staged:
            return BlobSnapshot.index(self.options.repo)
        return BlobSnapshot.worktree(self.options.repo)

    def extract_options(self, inventory: Inventory) -> str:
        needs = self.needs()
        return json.dumps(
            {
                "first_party": list(self.first_party(inventory)),
                "kinds": sorted(needs.kinds),
                "watch_callees": sorted(needs.watch_callees),
                "watch_prefixes": sorted(needs.watch_prefixes),
                "watch_keys": sorted(needs.watch_keys),
                "watch_hooks": sorted(needs.watch_hooks),
                "lite_kinds": list(LITE_KINDS),
            }
        )

    @staticmethod
    def conflict_failure(conflicts: list[str]) -> ProducerFailure:
        """Conflict entries in the index: the code under check is not decided, so every rule that
        reads edges is incomplete."""
        kinds = set(core_edge_kinds())
        kinds |= {kind for pack in REGISTRY.packs.values() for kind in pack.edge_kinds}
        more = f" and {len(conflicts) - 1} more" if len(conflicts) > 1 else ""
        return ProducerFailure(f"the index holds merge conflicts: {conflicts[0]}{more}", frozenset(kinds))

    def run(self) -> NativeAnalysisResult:
        timings: dict[str, float] = {}
        config = self.options.config
        started = time.perf_counter()
        snapshot = self.blob_snapshot()
        external = SitePackagesFiles.of(config.external_packages)
        inventory = Discovery(config).run(snapshot)
        timings["snapshot"] = time.perf_counter() - started
        started = time.perf_counter()
        native = bridge.load()
        blobs = [(path, *snapshot.blobs[path]) for path in inventory.files]
        database = self.fact_database(native.FACTS_ID, native.FACT_LAYOUT)
        options = self.extract_options(inventory)
        link, (hits, extracted) = native.link_blobs(
            str(self.options.repo),
            blobs,
            [(record, str(site_packages() / inside)) for record, inside in external.items()],
            options,
            json.dumps(link_config(config, inventory.analyzed, external)),
            str(database.path) if database is not None else None,
            self.threads(),
        )
        timings["link"] = time.perf_counter() - started
        timings.update({f"link.{name}": seconds for name, seconds in link.timings()})
        timings["facts.cached"] = hits
        timings["facts.extracted"] = extracted
        if self.options.needs is not None:
            link.release_kinds(sorted(self.options.needs.kinds))
        started = time.perf_counter()
        graph = NativeGraph(link, {})
        build = NativeBuild(config, link, external, graph.core.maker, f"{native.FACTS_ID}\0{options}")
        graph.modules = build.index.repo_modules()
        if snapshot.conflicts:
            graph.failed_producers[SNAPSHOT_PRODUCER] = self.conflict_failure(snapshot.conflicts)
        timings["graph"] = time.perf_counter() - started
        return NativeAnalysisResult(inventory, build.store, build, graph, timings)
