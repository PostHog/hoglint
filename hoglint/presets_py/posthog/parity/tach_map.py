"""Comparison 1: tach's file map against hoglint's map in tach's shape.

Test selection reads `tach map --direction dependents`: for each file, the files that import it.
hoglint writes the same map from its graph (`hoglint.outputs.tach_map`). A pair is (importer file,
imported file). A pair only one map has is explained only by a check of that pair:

- a string that only looks like a module path (a logger name, a task name, a check id): tach maps
  it to a file by dropping its last part; hoglint keeps no edge for a string that names no module
  and no symbol, and no import of the file gives the pair;
- a lazy name map (PEP 562 `__getattr__` over a dict of names to modules): hoglint reads each
  entry as an import of that module; tach sees no import statement.
"""

from __future__ import annotations

import ast
import tempfile
from collections import defaultdict
from collections.abc import Callable, Iterable
from pathlib import Path

from hoglint.core.analysis import AnalysisOptions
from hoglint.facts import FactNeeds
from hoglint.model import Edge, Graph
from hoglint.native.analysis import NativeAnalysis
from hoglint.outputs.tach_map import TachMap, TachResolver, config_from_tach_toml, is_potential_module_path
from hoglint.presets_py.posthog.parity import old_tools
from hoglint.presets_py.posthog.parity.verdict import HOGLINT_ONLY, OLD_ONLY, Comparison, Difference, Effect, Reason

TOOL = "tach-map"
STRING_NAMES_NOTHING = Reason(
    "string-names-nothing",
    "The pair comes from a string that only looks like a module path: it names no module and no "
    "symbol, and tach maps it to a file by dropping its last part. No import of the file gives the "
    "pair, so no code of the target runs because of it.",
    Effect.SAME,
)
LAZY_NAME_MAP = Reason(
    "lazy-name-map",
    "hoglint reads each entry of a lazy name map (PEP 562 `__getattr__` over a dict of names to "
    "modules) as an import of that module, which runs when the name is first read. tach sees no "
    "import statement there.",
    Effect.MORE,
)

Pair = tuple[str, str]


def module_parts(path: str) -> list[str]:
    parts = path.removesuffix(".pyi").removesuffix(".py").split("/")
    return parts[:-1] if parts[-1] == "__init__" else parts


class SourceNames:
    """The dotted names one file writes, as tach reads them: what its import statements name and
    the string constants that could be module paths."""

    def __init__(self, path: str, text: str) -> None:
        self.imports: list[tuple[str, int]] = []
        self.strings: list[tuple[str, int]] = []
        tree = ast.parse(text, filename=path)
        package = module_parts(path) if path.endswith(("/__init__.py", "/__init__.pyi")) else module_parts(path)[:-1]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.imports += [(alias.name, node.lineno) for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    head = package[: len(package) - node.level + 1] if node.level > 1 else package
                    base = ".".join([*head, base] if base else head)
                self.imports.append((base, node.lineno))
                self.imports += [(f"{base}.{alias.name}", node.lineno) for alias in node.names]
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if is_potential_module_path(node.value):
                    self.strings.append((node.value, node.lineno))


def names_nothing(names: SourceNames, target: str, resolver: TachResolver, known: Callable[[str], bool]) -> str | None:
    """Evidence that only strings naming nothing give `target`, else None."""
    if any(resolver.resolve(name) == target for name, _ in names.imports):
        return None
    hits = [
        (value, line) for value, line in names.strings if value.count(".") >= 2 and resolver.resolve(value) == target
    ]
    if not hits or any(known(value) for value, _ in hits):
        return None
    return "; ".join(f"line {line}: '{value}'" for value, line in hits)


def lazy_only(edges: list[Edge]) -> str | None:
    """Evidence that every hoglint edge behind a pair is an entry of a lazy name map, else None."""
    if not edges or any(edge.kind != "import" or edge.attr("lazy") != "1" for edge in edges):
        return None
    lines = sorted({edge.site.line for edge in edges})
    return "lazy entries at line " + ", ".join(str(line) for line in lines)


class HoglintMap:
    """hoglint's pairs in tach's shape, with the edges behind each pair."""

    def __init__(
        self, graph: Graph, files: Iterable[str], source_roots: tuple[str, ...], case_insensitive: bool
    ) -> None:
        self.graph = graph
        self.map = TachMap(graph, files, source_roots, case_insensitive)
        self.edges: dict[Pair, list[Edge]] = defaultdict(list)
        for importer, target, edge in self.map.edge_pairs():
            self.edges[(importer, target)].append(edge)

    def known(self, name: str) -> bool:
        return name in self.graph.modules or name in self.graph.symbols


def oracle_pairs(oracle: dict[str, list[str]]) -> set[Pair]:
    return {(importer, target) for target, importers in oracle.items() for importer in importers}


def compare(
    tach: set[Pair],
    hoglint: HoglintMap,
    read_source: Callable[[str], str],
) -> Comparison:
    comparison = Comparison(TOOL, old_items=len(tach), hoglint_items=len(hoglint.edges))
    ours = set(hoglint.edges)
    comparison.unlisted_matches = len(tach & ours)
    for importer, target in sorted(tach - ours):
        names = SourceNames(importer, read_source(importer))
        evidence = names_nothing(names, target, hoglint.map.resolver, hoglint.known)
        reason = STRING_NAMES_NOTHING if evidence is not None else None
        comparison.differences.append(Difference(f"{importer} -> {target}", OLD_ONLY, reason, evidence or ""))
    for importer, target in sorted(ours - tach):
        evidence = lazy_only(hoglint.edges[(importer, target)])
        reason = LAZY_NAME_MAP if evidence is not None else None
        comparison.differences.append(Difference(f"{importer} -> {target}", HOGLINT_ONLY, reason, evidence or ""))
    return comparison


def filesystem_ignores_case(root: Path) -> bool:
    """Whether the tree sits on a file system that ignores case, as tach's existence checks then do."""
    probe = root / "tach.toml"
    return probe.exists() and (root / probe.name.upper()).exists()


def run(root: Path, work: Path) -> Comparison:
    config = config_from_tach_toml(root / "tach.toml")
    oracle = old_tools.tach_map(root, work / "tach-map.json")
    case_insensitive = filesystem_ignores_case(root)
    with tempfile.TemporaryDirectory(prefix="hoglint-parity-map-", dir=work) as scratch:
        options = AnalysisOptions(repo=root, config=config, work_dir=Path(scratch), needs=FactNeeds())
        result = NativeAnalysis(options).run()
    hoglint = HoglintMap(result.graph, result.inventory.files, config.source_roots, case_insensitive)
    comparison = compare(oracle_pairs(oracle), hoglint, lambda path: (root / path).read_text())
    if case_insensitive:
        comparison.notes.append(
            "the tree sits on a file system that ignores case, so tach finds files by any spelling; "
            "hoglint's map resolves the same way for this run (CI runs on a case-sensitive one)"
        )
    return comparison
