"""`claimed_by = { adapter = "yaml-key", file = "...", key = "..." }`: a level's claim read from a key of
a YAML file, such as `isolated: true` in a product's manifest.

A directory is claimed when the nearest such file at or above it sets the key to true. A file
without the key, or with false, claims nothing for the directories under it. A value that is not a
boolean, or a file that does not read as a YAML mapping, is a claim error: the run fails, because
an unknown claim is never clean.

The adapter answers claims only. It lists no paths, so a coverage rule cannot name it in `inputs`:
a coverage rule with no inputs records the paths it needs for `hoglint watched` instead.
"""

from __future__ import annotations

import posixpath
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from hoglint.ledger.listing import WorktreeListing
from hoglint.plugins import PLUGIN_ROOTS, REGISTRY


class YamlKeyError(ValueError):
    """A claim file whose key hoglint cannot read as a boolean."""


@dataclass(frozen=True, slots=True)
class YamlClaims:
    """Per directory that holds a claim file: the file's path when its key is true, else None."""

    file_name: str
    by_directory: dict[str, str | None]

    def narrowed(self, directory: str) -> str | None:
        """The claim file that claims `directory`: the nearest one at or above it, when it sets the key."""
        current = directory
        while True:
            if current in self.by_directory:
                return self.by_directory[current]
            if not current:
                return None
            current = posixpath.dirname(current)


def claim_value(path: str, text: str, key: str) -> bool:
    """The key's value in one file: False when the file does not set it."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise YamlKeyError(f"{path} does not read as YAML: {error}") from error
    if data is None:
        return False
    if not isinstance(data, dict):
        raise YamlKeyError(f"{path} is no YAML mapping, so its {key!r} cannot be read")
    value = data.get(key, False)
    if not isinstance(value, bool):
        raise YamlKeyError(f"{path} sets {key!r} to {value!r}, which is not true or false")
    return value


class YamlKeyAdapter:
    name = "yaml-key"
    allowed_options = frozenset({"file", "key"})
    # A missing watched file is not fixed by adding it to a list a person keeps: there is none.
    adds_paths = False
    claims_only = True

    @staticmethod
    def file_name(options: Mapping[str, Any]) -> str:
        return str(options.get("file", ""))

    def inputs(self, options: Mapping[str, Any]) -> tuple[str, ...]:
        name = self.file_name(options)
        return (name, f"**/{name}")

    def open(self, options: Mapping[str, Any], root: Path) -> YamlClaims:
        """The claims of every file of the name under `root`, a directory that holds exactly the
        declared files (a run of a git tree)."""
        name = self.file_name(options)
        paths = sorted(str(path.relative_to(root)) for path in root.rglob(name) if path.is_file())
        return self.read(options, root, paths)

    def open_run(self, options: Mapping[str, Any], run_root: Path) -> YamlClaims:
        """`open` for a run whose root is `run_root`. A run of the working tree reads the files of
        the name that git lists, rather than walking every directory of the repo."""
        if PLUGIN_ROOTS.tree(run_root) is not None:
            return self.open(options, PLUGIN_ROOTS.of(run_root, self, options))
        name = self.file_name(options)
        listed = WorktreeListing.of(run_root).paths
        paths = sorted(path for path in listed if posixpath.basename(path) == name)
        return self.read(options, run_root, paths)

    def read(self, options: Mapping[str, Any], root: Path, paths: list[str]) -> YamlClaims:
        key = str(options.get("key", ""))
        by_directory: dict[str, str | None] = {}
        problems: list[str] = []
        for path in paths:
            try:
                claimed = claim_value(path, (root / path).read_text(), key)
            except (OSError, YamlKeyError) as error:
                problems.append(str(error))
                continue
            by_directory[posixpath.dirname(path)] = path if claimed else None
        if problems:
            more = f" (and {len(problems) - 1} more)" if len(problems) > 1 else ""
            raise YamlKeyError(problems[0] + more)
        return YamlClaims(self.file_name(options), by_directory)

    def covered(self, handle: YamlClaims, path: str) -> tuple[bool, str]:
        raise YamlKeyError("the yaml-key adapter answers a level's claim only, not a coverage rule's inputs")

    def narrowed(self, handle: YamlClaims, directory: str) -> str | None:
        return handle.narrowed(directory)


REGISTRY.add_coverage_adapter(YamlKeyAdapter())
