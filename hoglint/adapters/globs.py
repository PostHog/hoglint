"""`inputs = { adapter = "globs", paths = [...] }`: a plain list of globs a coverage rule checks against."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from hoglint.globs import glob_match
from hoglint.plugins import REGISTRY


class GlobsAdapter:
    name = "globs"
    allowed_options = frozenset({"paths"})

    def open(self, options: Mapping[str, Any], root: Path) -> tuple[str, ...]:
        paths = options.get("paths", ())
        return (paths,) if isinstance(paths, str) else tuple(str(path) for path in paths)

    def covered(self, handle: tuple[str, ...], path: str) -> tuple[bool, str]:
        return any(glob_match(glob, path) is not None for glob in handle), "inputs.paths"


REGISTRY.add_coverage_adapter(GlobsAdapter())
