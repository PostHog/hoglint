"""Core configuration: where sources live and how paths map to contexts. No project names."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from hoglint.globs import ExportPaths

DEFAULT_TEST_PATHS = ("**/tests/**", "**/test/**", "**/test_*.py", "**/*_test.py", "**/conftest.py")
DEFAULT_DYNAMIC_LOADERS = ("importlib.import_module", "builtins.__import__")
DEFAULT_DATACLASS_DECORATORS = ("dataclasses.dataclass",)
DEFAULT_PATCH_CALLEES = ("patch",)


def as_tuple(value: Any) -> tuple[str, ...]:
    """A config list of names; a bare string is a list of one name."""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


@dataclass(frozen=True, slots=True)
class DataclassDecorators:
    """The decorators that make a dataclass (`make`), and those of them that always make it frozen
    (`freeze`); any other of them freezes it when its call passes `frozen=True`."""

    make: frozenset[str]
    freeze: frozenset[str]


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
    - dataclass_bases: base classes whose subclasses' annotated class attributes are their
      `__init__` fields as well (a model library's base class).
    - frozen_dataclass_decorators: those of them that always make a frozen dataclass; any other
      freezes it only when its call passes `frozen=True`.
    - patch_callees: last names of callees whose first argument is a dotted patch target.
    - path_callees: callees whose first argument is a dotted path (an import by name, a URL
      include), by last name, or by last dotted parts for an entry with a dot (`patch.dict`).
      There, in an element of a list or tuple there, at a loader's first argument and at a patch
      target, a string is a dotted-path edge, and one that resolves to nothing stays an
      unresolved edge.
    - path_modules: modules (dotted names or `*` globs) whose strings are paths wherever they
      stand, such as settings modules; packs declare them (`Pack.path_modules`). There, in the
      values of a module's lazy (PEP 562) map, where its text is what one of the callees above
      takes through a loop over a literal sequence, and anywhere in a file that hands a computed
      value to one of those callees, a string that names a module is a dotted-path edge. Anywhere
      else a string is text, whatever it names.
    - reference_names: full dotted names whose every use makes a reference edge, one per scope
      and attribute chain, at its first use. Core makes one for each use of a first-party class
      and none for a third-party name or a first-party function, since most of those no rule
      reads; a rule that reads where such a name is used (a transaction opened, a scope left)
      names it here. A use counts when the name resolves to it, also through an alias or a
      re-export. An entry that names nothing, or names a re-export, is a `config:stale-name` item.
    - contract_surfaces: the files of exports whose signatures are part of their contract. Their
      callables expose every slot: unannotated ones and builtin types too, and the slots of what
      they hand out from other modules.
    - export_surfaces: the files of every export. Core records what each of them hands out by
      import (`Graph.hand_outs`), so every rule reads one notion of what a module exports.
    - registries: (file glob, attribute, loader) of each `[registries]` entry, in config order. Core
      reads what the attribute's value registers in each such file, as it reads a module-level list,
      and records each call of a loader that names none of its registries (`config:undeclared-registry`).
    """

    source_roots: tuple[str, ...] = (".",)
    first_party: tuple[str, ...] = ()
    excludes: tuple[str, ...] = ()
    test_paths: tuple[str, ...] = DEFAULT_TEST_PATHS
    migration_paths: tuple[str, ...] = ()
    external_packages: tuple[str, ...] = ()
    dynamic_loaders: tuple[str, ...] = DEFAULT_DYNAMIC_LOADERS
    dataclass_decorators: tuple[str, ...] = DEFAULT_DATACLASS_DECORATORS
    frozen_dataclass_decorators: tuple[str, ...] = ()
    dataclass_bases: tuple[str, ...] = ()
    patch_callees: tuple[str, ...] = DEFAULT_PATCH_CALLEES
    path_callees: tuple[str, ...] = ()
    path_modules: tuple[str, ...] = ()
    reference_names: tuple[str, ...] = ()
    contract_surfaces: tuple[ExportPaths, ...] = ()
    export_surfaces: tuple[ExportPaths, ...] = ()
    registries: tuple[tuple[str, str, str], ...] = ()
    include_hidden: bool = False

    @classmethod
    def from_discovery(
        cls,
        discovery: Mapping[str, Any],
        contract_surfaces: tuple[ExportPaths, ...] = (),
        export_surfaces: tuple[ExportPaths, ...] = (),
        path_modules: tuple[str, ...] = (),
        registries: tuple[tuple[str, str, str], ...] = (),
    ) -> CoreConfig:
        """A config's `[discovery]`, parsed: a bare string is a list of one name, and a key it
        does not set keeps core's default."""
        fields: dict[str, Any] = {
            "source_roots": as_tuple(discovery.get("source_roots", ["."])),
            "first_party": as_tuple(discovery.get("first_party")),
            "excludes": as_tuple(discovery.get("exclude")),
            "external_packages": as_tuple(discovery.get("external")),
        }
        for key, field_name in (
            ("tests", "test_paths"),
            ("migrations", "migration_paths"),
            ("dataclass_decorators", "dataclass_decorators"),
            ("path_callees", "path_callees"),
            ("frozen_dataclass_decorators", "frozen_dataclass_decorators"),
            ("dataclass_bases", "dataclass_bases"),
            ("reference_names", "reference_names"),
        ):
            if key in discovery:
                fields[field_name] = as_tuple(discovery[key])
        return cls(
            **fields,
            contract_surfaces=contract_surfaces,
            export_surfaces=export_surfaces,
            path_modules=path_modules,
            registries=registries,
        )

    @cached_property
    def dataclass_kinds(self) -> DataclassDecorators:
        return DataclassDecorators(frozenset(self.dataclass_decorators), frozenset(self.frozen_dataclass_decorators))

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

    def in_export(self, path: str) -> bool:
        """Whether an export surface covers a path."""
        return any(surface.covered_root(path) is not None for surface in self.export_surfaces)

    def watched_callee_names(self) -> tuple[str, ...]:
        """Last dotted parts of the dynamic loaders, the path callees, the patch callees and the
        registry loaders; the extractor records calls to these, so the link sees which files hand
        one a computed name, and which name each registry loader call hands its loader."""
        loaders = tuple(loader for _, _, loader in self.registries if loader)
        names = (*self.dynamic_loaders, *self.path_callees, *self.patch_callees, *loaders)
        return tuple(sorted({name.rpartition(".")[2] for name in names}))
