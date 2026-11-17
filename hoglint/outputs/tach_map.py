"""The file-imports view in the `tach map --direction dependents` JSON shape.

Consumers that read tach's map (test selection) keep working unchanged. The adapter follows
tach's own file resolution, not hoglint's: a dotted name maps to `__init__.pyi`, `__init__.py`,
`.pyi`, `.py` under a source root, else the same for its parent; a string counts when it has
at least two dots and each literal part is checked alone. tach also has its own rule for
`if TYPE_CHECKING:`: it skips imports in both branches of that `if`, and keeps strings there.

`hoglint map` prints it, and `check` writes it for an `[outputs]` table of format `tach-map`.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path

from hoglint.config.model import Config
from hoglint.config.schema import as_tuple
from hoglint.core.modules import normalize_root
from hoglint.model import Context, Edge, Graph
from hoglint.views import RefsView, reverse_dependents

STRING_LITERALS = frozenset({"whole", "part"})
TYPE_CHECKING_BRANCHES = frozenset({Context.TYPE_ONLY, Context.TYPE_CHECKING_ELSE})


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
        self.prefixes = [normalize_root(root) + "/" if normalize_root(root) else "" for root in source_roots]
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

    @classmethod
    def of(cls, graph: Graph, config: Config) -> TachMap:
        """The map of a graph built under `config`: the graph's repo modules are the files, under
        the config's source roots."""
        roots = as_tuple(config.discovery.get("source_roots", ["."]))
        return cls(graph, graph.modules.values(), roots)

    @staticmethod
    def counts(edge: Edge) -> bool:
        if edge.site.path.endswith(".pyi"):
            return False
        if edge.kind == "import":
            return not (TYPE_CHECKING_BRANCHES & edge.contexts) and not edge.surface.startswith(".")
        return edge.attr("literal") in STRING_LITERALS and edge.surface.count(".") >= 2

    def edge_pairs(self) -> Iterator[tuple[str, str, Edge]]:
        """(importer, target, the edge that gives the pair) for every edge the map counts."""
        view = RefsView(self.graph, kinds=("import", "dotted-path"))
        for edge in view.edges():
            if not self.counts(edge):
                continue
            target = self.resolver.resolve(edge.surface)
            if target is not None:
                yield edge.site.path, target, edge

    def pairs(self) -> Iterator[tuple[str, str]]:
        for importer, target, _ in self.edge_pairs():
            yield importer, target

    def dependents(self) -> dict[str, list[str]]:
        return reverse_dependents(self.pairs())

    def text(self) -> str:
        return json.dumps(self.dependents(), indent=2) + "\n"

    def write(self, path: Path) -> None:
        path.write_text(self.text())
