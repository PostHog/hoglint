"""`inputs = { adapter = "globs", paths = [...], narrowed = [...] }`: a plain list of globs a coverage
rule checks against, and the directories a level's claim reads as narrowed, each with every
directory under it."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from hoglint.globs import glob_match
from hoglint.plugins import REGISTRY


def globs(options: Mapping[str, Any], key: str) -> tuple[str, ...]:
    found = options.get(key, ())
    return (found,) if isinstance(found, str) else tuple(str(glob) for glob in found)


class GlobsAdapter:
    name = "globs"
    allowed_options = frozenset({"paths", "narrowed"})

    def open(self, options: Mapping[str, Any], root: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return globs(options, "paths"), globs(options, "narrowed")

    def covered(self, handle: tuple[tuple[str, ...], tuple[str, ...]], path: str) -> tuple[bool, str]:
        return any(glob_match(glob, path) is not None for glob in handle[0]), "inputs.paths"

    def narrowed(self, handle: tuple[tuple[str, ...], tuple[str, ...]], directory: str) -> str | None:
        inside = any(directory == top or directory.startswith(f"{top}/") for top in handle[1])
        return "inputs.narrowed" if inside else None


REGISTRY.add_coverage_adapter(GlobsAdapter())
