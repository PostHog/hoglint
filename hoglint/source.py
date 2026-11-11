"""Builds the Graph of one code root under one config: core edges first, then each active pack.

A pack is active when the config has a table under the pack's name. Packs run in the order of
`[vocabulary] sections`, which follows the extends chain. A pack that raises is a failed producer:
every edge kind it may emit goes incomplete in the rules that read it.
"""

from __future__ import annotations

import shutil
import tempfile
import posixpath
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hoglint.config import Config
from hoglint.config.schema import as_tuple, canonical
from hoglint.facts import FACT_KINDS, FactNeeds
from hoglint.globs import ExportPaths
from hoglint.ledger.listing import Listing, TreeListing, WorktreeListing
from hoglint.model import Graph
from hoglint.plugins import REGISTRY, Pack, PackReader, PackSink, ProducerFailure

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


class PackRoot:
    """A directory of one pack's own: it holds exactly the policy files the pack's
    `policy_file_keys` name, and the pack's section names those copies. A path under the code
    root is written from the run's snapshot (`listing`), as every other input of the run is; an
    absolute path, which `PolicyFiles.anchored` makes for a policy root of its own, is copied as
    it is. A pack that reads any other file finds nothing there, so it fails instead of reading
    the working tree."""

    def __init__(self, pack: Pack, section: Mapping[str, Any], listing: Listing, directory: Path) -> None:
        self.pack = pack
        self.section = section
        self.listing = listing
        self.directory = directory

    def copy(self, key: str, value: str, known: Mapping[str, bytes]) -> Path:
        """Writes the policy file `value` names under the directory; returns the copy's path
        relative to it. `known` holds the bytes of the pack's policy files read from blobs."""
        if Path(value).is_absolute():
            copy = Path(key) / Path(value).name
            (self.directory / key).mkdir(parents=True, exist_ok=True)
            shutil.copyfile(value, self.directory / copy)
            return copy
        path = posixpath.normpath(value)
        if path not in self.listing.modes or self.listing.write(self.directory / key, [path], known):
            raise FileNotFoundError(f"{value}: the snapshot holds no file there that hoglint can read")
        return Path(key) / path

    def section_of_copies(self) -> dict[str, Any]:
        """The section with each policy file key naming the file's copy, relative to the directory."""
        out = dict(self.section)
        named: dict[str, str] = {}
        for key in getattr(self.pack, "policy_file_keys", ()):
            value = self.section.get(key)
            if isinstance(value, str) and value:
                named[key] = value
        relative = [posixpath.normpath(value) for value in named.values() if not Path(value).is_absolute()]
        known = self.listing.read(relative)
        for key, value in named.items():
            out[key] = self.copy(key, value, known).as_posix()
        return out


def run_packs(
    packs: list[tuple[Pack, Mapping[str, Any]]], reader: PackReader, sink: PackSink, listing: Listing
) -> None:
    """Each active pack in order, into one sink, each reading through a root of its own (see
    `PackRoot`); `listing` is the run's snapshot, which the policy's relative paths name. A pack
    that raises, or whose policy file is missing, is a failed producer."""
    for pack, section in packs:
        with tempfile.TemporaryDirectory(prefix=f"pack-{pack.name}-") as scratch:
            directory = Path(scratch)
            try:
                own = PackRoot(pack, section, listing, directory).section_of_copies()
                pack.produce(own, reader, sink, directory)
            except Exception as error:  # noqa: BLE001 - a failed pack makes its rules incomplete, never clean
                reason = f"{type(error).__name__}: {error}"
                sink.fail(pack.name, ProducerFailure(reason, frozenset(pack.edge_kinds)))


class GraphSource:
    """Builds and caches one Graph per (code root, staged or git tree, config sections).

    `tree` names a git tree whose blobs are the code, read by id with no checkout."""

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = work_dir
        self.cache: dict[tuple[str, str], Graph] = {}

    @staticmethod
    def listing_of(code_root: Path, tree: str = "") -> Listing:
        """The snapshot a build reads: the git tree, or the working tree as git sees it (D40)."""
        return TreeListing.of(code_root, tree) if tree else WorktreeListing.of(code_root)

    @staticmethod
    def contract_surfaces(config: Config) -> tuple[ExportPaths, ...]:
        """The files of exports whose shape makes their signatures part of their contract."""
        return tuple(spec.files for spec in config.exports.values() if config.shapes[spec.shape].signatures)

    @staticmethod
    def export_surfaces(config: Config) -> tuple[ExportPaths, ...]:
        """The files of every export: core records what each of them hands out."""
        return tuple(spec.files for spec in config.exports.values())

    def key(self, code_root: Path, config: Config, staged: bool, tree: str = "") -> tuple[str, str]:
        sections = {name: config.sections.get(name) for name in sorted(config.sections)}
        snapshot = f":tree={tree}" if tree else ":staged" if staged else ""
        # The contract and export surfaces come from [exports] and [shapes] and change what core
        # produces.
        surfaces = [(surface.within, surface.paths) for surface in self.contract_surfaces(config)]
        exports = [(surface.within, surface.paths) for surface in self.export_surfaces(config)]
        return f"{code_root}{snapshot}", canonical(
            {"sections": sections, "contract_surfaces": surfaces, "export_surfaces": exports}
        )

    def build(self, code_root: Path, config: Config, staged: bool = False, tree: str = "") -> Graph:
        key = self.key(code_root, config, staged, tree)
        if key not in self.cache:
            self.cache[key] = self.build_fresh(code_root, config, staged, tree)
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
        if "dataclass_decorators" in discovery:
            fields["dataclass_decorators"] = as_tuple(discovery["dataclass_decorators"])
        if "path_callees" in discovery:
            fields["path_callees"] = as_tuple(discovery["path_callees"])
        if "frozen_dataclass_decorators" in discovery:
            fields["frozen_dataclass_decorators"] = as_tuple(discovery["frozen_dataclass_decorators"])
        fields["contract_surfaces"] = GraphSource.contract_surfaces(config)
        fields["export_surfaces"] = GraphSource.export_surfaces(config)
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

    def build_fresh(self, code_root: Path, config: Config, staged: bool, tree: str = "") -> Graph:
        from hoglint.core.analysis import AnalysisOptions, CoreAnalysis  # noqa: PLC0415 - see core_config
        from hoglint.core.reader import python_pack_io  # noqa: PLC0415 - see core_config

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
        analysis = CoreAnalysis(options).run()
        graph = analysis.graph
        reader, sink = python_pack_io(analysis.build, graph)
        run_packs(packs, reader, sink, self.listing_of(code_root, tree))
        return graph
