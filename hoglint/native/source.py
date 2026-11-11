"""GraphSource for `--engine native`: the same packs and config, the graph from the Rust link."""

from __future__ import annotations

import gc
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from hoglint.config import Config
from hoglint.core.analysis import AnalysisOptions
from hoglint.core.memo import OFF, TRACE, PackMemo, digest
from hoglint.core.reader import BuildReader
from hoglint.ledger.cache import RunStores
from hoglint.ledger.envelope import ConfigInputs
from hoglint.ledger.listing import Listing
from hoglint.model import Graph
from hoglint.native.analysis import NativeAnalysis
from hoglint.native.build import NativeBuild
from hoglint.native.graph import NativeGraph, NativeSink
from hoglint.native.memo_store import MemoStore, engine_id
from hoglint.native.queries import NativePackQueries
from hoglint.plugins import Pack
from hoglint.source import GraphSource, PackRoot, active_packs, run_packs

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
        listing = self.listing_of(code_root, tree)
        memo = self.pack_memo(options, build, self.memo_config(config, packs, listing))
        if memo is not None:
            build.trace_with(memo.tracer)
        reader = BuildReader(build, NativePackQueries(build, graph, graph.link), memo)
        run_packs(packs, reader, StepReleasingSink(graph, build), listing)
        if memo is not None:
            memo.flush()
        build.release()
        return graph

    def policy_digests(self, pack: Pack, section: Mapping[str, Any], listing: Listing) -> dict[str, Any]:
        """A pack's section with each policy file named by the digest of the bytes the pack reads:
        the copy its own root makes from the run's snapshot (`PackRoot`). The path may name a new
        scratch copy each run, and a relative one names a file of the snapshot, not of the
        working directory."""
        own = dict(section)
        keys = [key for key in getattr(pack, "policy_file_keys", ()) if isinstance(own.get(key), str) and own[key]]
        if not keys:
            return own
        with tempfile.TemporaryDirectory(prefix=f"memo-{pack.name}-", dir=self.work_dir) as scratch:
            try:
                copies = PackRoot(pack, section, listing, Path(scratch)).section_of_copies()
            except Exception as error:  # noqa: BLE001 - the pack fails on the same error, so no item runs
                return {**own, "unreadable": type(error).__name__}
            for key in keys:
                own[key] = digest((Path(scratch) / copies[key]).read_bytes())
        return own

    def memo_config(self, config: Config, packs: list[tuple[Pack, Mapping[str, Any]]], listing: Listing) -> str:
        """The config as the pack memo keys on it: the config inputs `GraphSource.key` holds
        (`ConfigInputs`), with each pack's policy files by the bytes the pack reads
        (`policy_digests`)."""
        inputs = ConfigInputs.of(config)
        sections = dict(inputs.sections)
        for pack, section in packs:
            sections[pack.name] = self.policy_digests(pack, section, listing)
        return inputs.key(sections)

    def pack_memo(self, options: AnalysisOptions, build: NativeBuild, config_key: str) -> PackMemo | None:
        """The run's pack memo (hoglint/core/memo.py), keyed by every input outside the snapshot
        that a pack item depends on: the engine, the extractor and its options, the config, the
        third-party files and the first-party roots. None when it is switched off."""
        mode = self.stores.memo_mode()
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
            store = MemoStore(options.cache_dir.parent, engine_id(), self.stores)
        return PackMemo(store, policy, self.stores.memo_stats())


def batch_mode(engine: str) -> None:
    """A native run is one batch: it builds millions of short-lived objects (decoded facts, edges)
    and frees them at exit. The cyclic collector would rescan them over and over, about 40% of a
    native run, so it stays off; nothing the run keeps forms reference cycles worth collecting."""
    if engine == "native":
        gc.disable()


def graph_source(engine: str, work_dir: Path, stores: RunStores | None = None) -> GraphSource:
    return NativeGraphSource(work_dir, stores) if engine == "native" else GraphSource(work_dir, stores)
