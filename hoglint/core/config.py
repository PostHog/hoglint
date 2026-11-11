"""Core configuration: where sources live and how paths map to contexts. No project names."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cached_property

from hoglint.globs import ExportPaths

DEFAULT_TEST_PATHS = ("**/tests/**", "**/test/**", "**/test_*.py", "**/*_test.py", "**/conftest.py")
DEFAULT_DYNAMIC_LOADERS = ("importlib.import_module", "builtins.__import__")
DEFAULT_DATACLASS_DECORATORS = ("dataclasses.dataclass",)
DEFAULT_PATCH_CALLEES = ("patch",)


class PathGlobs:
    """Gitignore-style globs over repo-relative paths.

    A pattern without a slash matches one path component at any depth. A pattern with a slash is
    anchored at the repo root. `*` stays inside one component, `**` spans any number of them.
    A pattern matches a path when it matches the path itself or one of its parent directories.
    """

    @staticmethod
    def translate_segment(segment: str) -> str:
        out = []
        i = 0
        while i < len(segment):
            char = segment[i]
            if char == "*":
                out.append("[^/]*")
            elif char == "?":
                out.append("[^/]")
            elif char == "[":
                end = segment.find("]", i + 1)
                if end == -1:
                    out.append(re.escape(char))
                else:
                    body = segment[i + 1 : end].replace("\\", "\\\\")
                    if body.startswith("!"):
                        body = "^" + body[1:]
                    out.append(f"[{body}]")
                    i = end
            else:
                out.append(re.escape(char))
            i += 1
        return "".join(out)

    @classmethod
    def translate(cls, pattern: str) -> str:
        segments = pattern.split("/")
        regex = ""
        for i, segment in enumerate(segments):
            last = i == len(segments) - 1
            if segment == "**":
                regex += ".*" if last else "(?:[^/]+/)*"
            else:
                regex += cls.translate_segment(segment) + ("" if last else "/")
        return "^" + regex + "$"

    def __init__(self, patterns: tuple[str, ...]) -> None:
        self.patterns = patterns
        self.component_res: list[re.Pattern[str]] = []
        self.anchored_res: list[re.Pattern[str]] = []
        for pattern in patterns:
            stripped = pattern.strip("/")
            if not stripped:
                continue
            if "/" in stripped:
                self.anchored_res.append(re.compile(self.translate(stripped)))
            else:
                self.component_res.append(re.compile(self.translate(stripped)))

    def matches(self, path: str) -> bool:
        parts = path.split("/")
        if self.component_res and any(regex.match(part) for part in parts for regex in self.component_res):
            return True
        if not self.anchored_res:
            return False
        for end in range(1, len(parts) + 1):
            prefix = "/".join(parts[:end])
            if any(regex.match(prefix) for regex in self.anchored_res):
                return True
        return False


@dataclass(frozen=True)
class CoreConfig:
    """What the core needs to know about a repo. Presets fill it; the defaults fit plain Python.

    - source_roots: directories whose children are top-level modules ("." is the repo root).
    - first_party: top-level module names the extractor treats as first-party when it decides
      which references to record per site. Empty means every top-level module under the roots.
    - excludes: gitignore-style globs for files that are not analyzed. They stay resolvable as
      import targets, because the files still exist and other code may import them.
    - test_paths, migration_paths: globs that give edges from a file the test or migration
      context. Every other analyzed file is prod.
    - external_packages: site-packages roots whose sources are read, so re-exports and class
      ancestry resolve through third-party code (for example a web framework's base classes).
    - dynamic_loaders: callables that import a module named at runtime.
    - dataclass_decorators: decorators whose class fields form the generated `__init__`.
    - patch_callees: last names of callees whose first argument is a dotted patch target.
    - contract_surfaces: the files of exports whose signatures are part of their contract. Their
      callables expose every slot: unannotated ones and builtin types too, and the slots of what
      they hand out from other modules.
    """

    source_roots: tuple[str, ...] = (".",)
    first_party: tuple[str, ...] = ()
    excludes: tuple[str, ...] = ()
    test_paths: tuple[str, ...] = DEFAULT_TEST_PATHS
    migration_paths: tuple[str, ...] = ()
    external_packages: tuple[str, ...] = ()
    dynamic_loaders: tuple[str, ...] = DEFAULT_DYNAMIC_LOADERS
    dataclass_decorators: tuple[str, ...] = DEFAULT_DATACLASS_DECORATORS
    patch_callees: tuple[str, ...] = DEFAULT_PATCH_CALLEES
    contract_surfaces: tuple[ExportPaths, ...] = ()
    include_hidden: bool = False

    @cached_property
    def exclude_globs(self) -> PathGlobs:
        return PathGlobs(self.excludes)

    @cached_property
    def test_globs(self) -> PathGlobs:
        return PathGlobs(self.test_paths)

    @cached_property
    def migration_globs(self) -> PathGlobs:
        return PathGlobs(self.migration_paths)

    def surface_roots(self, path: str) -> frozenset[tuple[int, str]]:
        """(export, root) of every contract surface that covers a path."""
        found = ((at, surface.covered_root(path)) for at, surface in enumerate(self.contract_surfaces))
        return frozenset((at, root) for at, root in found if root is not None)

    def loader_callee_names(self) -> tuple[str, ...]:
        """Last dotted parts of the dynamic loaders; the extractor records calls to these."""
        return tuple(sorted({loader.rpartition(".")[2] for loader in self.dynamic_loaders}))
