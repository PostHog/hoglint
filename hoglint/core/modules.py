"""Module names for paths under source roots, namespace packages included."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

from hoglint.model import Resolution


def longest_prefix(dotted: str, exists: Callable[[str], bool]) -> str | None:
    """The longest dotted prefix of `dotted` (itself included) that `exists` accepts."""
    parts = dotted.split(".")
    for end in range(len(parts), 0, -1):
        candidate = ".".join(parts[:end])
        if exists(candidate):
            return candidate
    return None


class FirstParty:
    """Top-level module names that are the repo's own code."""

    def __init__(self, roots: Iterable[str]) -> None:
        self.roots = frozenset(roots)

    def contains(self, module: str) -> bool:
        return module.partition(".")[0] in self.roots

    def resolution(self, module: str) -> Resolution:
        """A found target: resolved when it is first-party code, external otherwise."""
        return Resolution.RESOLVED if self.contains(module) else Resolution.EXTERNAL


def normalize_root(root: str) -> str:
    """A source root as a path prefix without slashes; the repo root is ""."""
    stripped = root.strip("/")
    return "" if stripped in ("", ".") else stripped


class ModuleNamer:
    """Dotted module name of a path. The longest matching source root wins.

    `external` maps a third-party file to its path inside its site-packages root.
    """

    def __init__(self, source_roots: tuple[str, ...], external: Mapping[str, str] | None = None) -> None:
        self.roots = sorted((normalize_root(root) for root in source_roots), key=len, reverse=True)
        self.external = external or {}

    def relative(self, path: str) -> str:
        inside_site = self.external.get(path)
        if inside_site is not None:
            return inside_site
        for root in self.roots:
            if not root:
                return path
            if path.startswith(root + "/"):
                return path[len(root) + 1 :]
        return path

    def module_of(self, path: str) -> tuple[str, bool]:
        """(dotted module, is_package)."""
        rel = self.relative(path)
        stem = rel.removesuffix(".pyi") if rel.endswith(".pyi") else rel.removesuffix(".py")
        parts = stem.split("/")
        is_package = parts[-1] == "__init__"
        if is_package:
            parts = parts[:-1]
        return ".".join(parts), is_package


class ModuleIndex:
    """Module name <-> path over repo files and site-packages files.

    Repo files win over site-packages, `.py` wins over `.pyi` (the stub has no bodies). A
    directory that holds modules but no `__init__` is a namespace package: it exists, has no path.
    """

    def __init__(self, paths: list[str], namer: ModuleNamer, first_party: tuple[str, ...]) -> None:
        self.namer = namer
        self.path_of: dict[str, str] = {}
        self.module_of: dict[str, str] = {}
        self.package: dict[str, bool] = {}
        self.dirs: set[str] = set()
        self.stub_of: dict[str, str] = {}
        self.repo_heads: set[str] = set(first_party)
        self.site_paths = frozenset(path for path in paths if path in namer.external)
        for path in sorted(paths, key=lambda p: (p in self.site_paths, p.endswith(".pyi"), p)):
            module, is_package = namer.module_of(path)
            if not module:
                continue
            self.module_of[path] = module
            parts = module.split(".")
            if path not in self.site_paths:
                self.repo_heads.add(parts[0])
            for end in range(1, len(parts)):
                self.dirs.add(".".join(parts[:end]))
            if module in self.path_of:
                if path.endswith(".pyi") and not self.path_of[module].endswith(".pyi"):
                    self.stub_of.setdefault(module, path)
                continue
            self.path_of[module] = path
            self.package[module] = is_package

    def exists(self, module: str) -> bool:
        return module in self.path_of or module in self.dirs

    def is_file_module(self, module: str) -> bool:
        return module in self.path_of

    def is_package(self, module: str) -> bool:
        return self.package.get(module, module in self.dirs)

    def is_external(self, module: str) -> bool:
        """Third-party: loaded from site-packages, or named under a head that no repo file has."""
        path = self.path_of.get(module)
        if path is not None:
            return path in self.site_paths
        return module.split(".")[0] not in self.repo_heads

    def longest_module(self, dotted: str) -> str | None:
        """The longest prefix of `dotted` that is a module or a namespace package."""
        return longest_prefix(dotted, self.exists)

    def repo_modules(self) -> dict[str, str]:
        return {module: path for module, path in self.path_of.items() if path not in self.site_paths}
