"""The file-imports view in the `tach map --direction dependents` JSON shape.

Consumers that read tach's map (test selection) keep working unchanged. The adapter follows
tach's own file resolution, not hoglint's: a dotted name maps to `__init__.pyi`, `__init__.py`,
`.pyi`, `.py` under a source root, else the same for its parent; a string counts when it has
at least two dots and each literal part is checked alone.

Usage: python -m hoglint.outputs.tach_map --repo <root> --out <map.json>
           [--tach-toml <tach.toml>] [--index] [--no-cache] [--case-insensitive]
           [--compare <oracle.json>]
"""

from __future__ import annotations

import sys
import json
import tomllib
import argparse
import tempfile
from collections.abc import Iterable
from pathlib import Path

from hoglint.core.analysis import AnalysisOptions, CoreAnalysis
from hoglint.core.config import CoreConfig
from hoglint.model import Context, Edge, Graph
from hoglint.views import RefsView

STRING_LITERALS = frozenset({"whole", "part"})


def is_potential_module_path(value: str) -> bool:
    if not value:
        return False
    for part in value.split("."):
        if not part or not all(char.isalnum() or char in "_*" for char in part):
            return False
    return True


class TachResolver:
    """tach's module_to_file_path with member fallback, against an inventory of known files.

    On a case-insensitive filesystem tach's existence check matches any spelling; pass
    case_insensitive=True to reproduce that, including the spelled path it reports.
    """

    def __init__(self, files: Iterable[str], source_roots: tuple[str, ...], case_insensitive: bool) -> None:
        self.files = set(files)
        self.lower = {path.lower() for path in self.files} if case_insensitive else None
        self.prefixes = ["" if root.strip("/") in ("", ".") else root.strip("/") + "/" for root in source_roots]
        self.cache: dict[str, str | None] = {}

    def exists(self, candidate: str) -> bool:
        if self.lower is not None:
            return candidate.lower() in self.lower
        return candidate in self.files

    def first_existing(self, base: str) -> str | None:
        for prefix in self.prefixes:
            for suffix in ("/__init__.pyi", "/__init__.py", ".pyi", ".py"):
                candidate = prefix + base + suffix
                if self.exists(candidate):
                    return candidate
        return None

    def resolve(self, dotted: str) -> str | None:
        if dotted in self.cache:
            return self.cache[dotted]
        result = None
        if is_potential_module_path(dotted):
            fs_path = dotted.replace(".", "/")
            result = self.first_existing(fs_path)
            if result is None and "/" in fs_path:
                result = self.first_existing(fs_path.rsplit("/", 1)[0])
        self.cache[dotted] = result
        return result


class TachMap:
    def __init__(
        self, graph: Graph, files: Iterable[str], source_roots: tuple[str, ...] = (".",), case_insensitive: bool = False
    ) -> None:
        self.graph = graph
        self.resolver = TachResolver(files, source_roots, case_insensitive)

    @staticmethod
    def counts(edge: Edge) -> bool:
        if edge.kind == "import":
            return not edge.surface.startswith(".")
        return edge.attr("literal") in STRING_LITERALS and edge.surface.count(".") >= 2

    def dependents(self) -> dict[str, list[str]]:
        view = RefsView(self.graph, kinds=("import", "dotted-path"), contexts_out=(Context.TYPE_ONLY,))
        reverse: dict[str, set[str]] = {}
        for edge in view.edges():
            if not self.counts(edge):
                continue
            target = self.resolver.resolve(edge.surface)
            if target is not None:
                reverse.setdefault(target, set()).add(edge.site.path)
        return {target: sorted(importers) for target, importers in sorted(reverse.items())}

    def write(self, path: Path) -> dict[str, list[str]]:
        mapping = self.dependents()
        path.write_text(json.dumps(mapping, indent=2) + "\n")
        return mapping


def config_from_tach_toml(path: Path) -> CoreConfig:
    data = tomllib.loads(path.read_text())
    return CoreConfig(
        source_roots=tuple(data.get("source_roots", ["."])),
        excludes=tuple(data.get("exclude", ())),
    )


def compare(mine: dict[str, list[str]], oracle_path: Path) -> dict[str, object]:
    oracle = json.loads(oracle_path.read_text())
    theirs = {(importer, target) for target, importers in oracle.items() for importer in importers}
    ours = {(importer, target) for target, importers in mine.items() for importer in importers}
    return {
        "oracle_edges": len(theirs),
        "edges": len(ours),
        "missing": len(theirs - ours),
        "extra": len(ours - theirs),
        "missing_sample": sorted(theirs - ours)[:10],
        "extra_sample": sorted(ours - theirs)[:10],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--tach-toml", type=Path)
    parser.add_argument(
        "--index", action="store_true", help="read staged blobs from the git index, never the working tree"
    )
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--case-insensitive", action="store_true")
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve()
    config = config_from_tach_toml(args.tach_toml or repo / "tach.toml")
    with tempfile.TemporaryDirectory(prefix="hoglint-tach-map-") as work:
        options = AnalysisOptions(repo=repo, config=config, work_dir=Path(work), staged=args.index)
        if args.no_cache:
            options = AnalysisOptions(repo=repo, config=config, work_dir=Path(work), staged=args.index, cache_dir=None)
        result = CoreAnalysis(options).run()
    output = TachMap(result.graph, result.inventory.files, config.source_roots, args.case_insensitive)
    mapping = output.write(args.out)
    if args.compare:
        sys.stdout.write(json.dumps(compare(mapping, args.compare), indent=1) + "\n")


if __name__ == "__main__":
    main()
