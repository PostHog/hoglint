"""What a run reads besides its policy: its sources and its side inputs, and the files of one git
tree, read by blob id with no checkout.

A staged run captures the index once with `git write-tree`, and every later read uses that tree:
the sources, the policy and the side inputs. `TreeListing` lists a tree once per process, and
`TreeFiles` writes chosen files of it into directories.

Side inputs are the files a run reads besides its sources and its policy files: what providers
and coverage adapters declare (`inputs` in plugins.py), the files layout rules ask about, and
the ledger. `RunInputs` holds their path patterns, which depend on the config alone, so they are
known before a run.
"""

from __future__ import annotations

import re
import tempfile
import subprocess
from collections.abc import Iterable
from functools import cached_property
from pathlib import Path
from typing import ClassVar

from hoglint.config.model import Config
from hoglint.globs import CAPTURE_RE, Braces, translate
from hoglint.plugins import PLUGIN_ROOTS, REGISTRY, declared_inputs

SOURCE_INPUTS = ("**/*.py", "**/*.pyi")


class InputPatterns:
    """Path patterns as one regex: `**` spans directories, `*` and `?` stay in one segment."""

    def __init__(self, patterns: Iterable[str]) -> None:
        self.patterns = tuple(sorted(set(patterns)))
        self.regex = re.compile("|".join(f"(?:{translate(pattern, Braces.LITERAL)})" for pattern in self.patterns))

    def matches(self, path: str) -> bool:
        return bool(self.patterns) and self.regex.fullmatch(path) is not None


class RunInputs:
    """The path patterns of everything a run of `config` reads but its policy files."""

    def __init__(self, config: Config) -> None:
        self.config = config

    def side(self) -> tuple[str, ...]:
        """What each provider and coverage adapter declares, and the paths layout rules ask
        about. A layout rule asks only whether a path exists, so its patterns cover every file
        under such a path too."""
        patterns: set[str] = set()
        for spec in self.config.providers.values():
            patterns.update(declared_inputs(REGISTRY.providers.get(spec.use), dict(spec.options)))
        for spec in self.config.rules.values():
            if spec.primitive == "coverage":
                adapter = REGISTRY.coverage_adapters.get(str(spec.input("adapter")))
                options = {key: value for key, value in spec.inputs if key != "adapter"}
                patterns.update(declared_inputs(adapter, options))
            if spec.primitive == "layout" and spec.within:
                within = CAPTURE_RE.sub("*", spec.within)
                for name in (*spec.files, *spec.absent):
                    path = f"{within}/{name.strip('/')}"
                    patterns.update((path, f"{path}/**"))
        return tuple(sorted(patterns))

    def patterns(self) -> InputPatterns:
        """Sources, side inputs and the ledger."""
        return InputPatterns((*SOURCE_INPUTS, *self.side(), self.config.ledger.file))


class TreeListing:
    """path -> blob oid for every file of one git tree, from one `git ls-tree -r -z`. A tree is
    immutable, so each one is listed at most once per process. Submodules are no files."""

    taken: ClassVar[dict[tuple[Path, str], TreeListing]] = {}

    def __init__(self, blobs: dict[str, str]) -> None:
        self.blobs = blobs

    @staticmethod
    def parse(output: str) -> dict[str, str]:
        blobs: dict[str, str] = {}
        for record in output.split("\0"):
            meta, _, path = record.partition("\t")
            parts = meta.split()
            if len(parts) == 3 and parts[1] == "blob":
                blobs[path] = parts[2]
        return blobs

    @staticmethod
    def of(repo: Path, tree: str) -> TreeListing:
        key = (repo.resolve(), tree)
        found = TreeListing.taken.get(key)
        if found is not None:
            return found
        command = ["git", "-C", str(repo), "ls-tree", "-r", "-z", tree]
        output = subprocess.run(command, capture_output=True, text=True, check=True).stdout
        found = TreeListing.taken[key] = TreeListing(TreeListing.parse(output))
        return found


def write_blobs(repo: Path, target: Path, blobs: dict[str, str]) -> None:
    """Writes each path's blob under `target`, all in one `git cat-file --batch`."""
    if not blobs:
        return
    pending = sorted(blobs.items())
    request = "".join(f"{oid}\n" for _, oid in pending).encode()
    command = ["git", "-C", str(repo), "cat-file", "--batch"]
    out = subprocess.run(command, input=request, capture_output=True, check=True).stdout
    at = 0
    for path, oid in pending:
        header_end = out.index(b"\n", at)
        got, kind, size = out[at:header_end].decode().split()
        if got != oid or kind != "blob":
            raise RuntimeError(f"git cat-file returned {got} {kind} for {path}")
        start = header_end + 1
        end = start + int(size)
        written = target / path
        written.parent.mkdir(parents=True, exist_ok=True)
        written.write_bytes(out[start:end])
        at = end + 1


class TreeFiles:
    """Files of one git tree, written into directories on request. As a `TreeRoot` (plugins.py)
    it writes one directory per plugin, each holding exactly the files the plugin declares, into
    `work_dir`."""

    def __init__(self, repo: Path, tree: str, work_dir: Path | None = None) -> None:
        self.repo = repo
        self.tree = tree
        self.work_dir = work_dir
        self.plugin_roots: dict[tuple[str, ...], Path] = {}

    @property
    def listing(self) -> TreeListing:
        return TreeListing.of(self.repo, self.tree)

    @cached_property
    def directories(self) -> frozenset[str]:
        out: set[str] = set()
        for path in self.listing.blobs:
            parts = path.split("/")[:-1]
            out.update("/".join(parts[:end]) for end in range(1, len(parts) + 1))
        return frozenset(out)

    def write(self, target: Path, paths: Iterable[str]) -> None:
        """Writes the paths the tree holds under `target`; a path it does not hold is left out."""
        blobs = self.listing.blobs
        write_blobs(self.repo, target, {path: blobs[path] for path in paths if path in blobs})

    def scratch(self, prefix: str) -> Path:
        return Path(tempfile.mkdtemp(prefix=prefix, dir=self.work_dir)).resolve()

    def run_root(self) -> Path:
        """An empty directory that stands for the tree: rules and providers that get it as their
        repo root read the tree through `PLUGIN_ROOTS`."""
        root = self.scratch("tree-")
        PLUGIN_ROOTS.add(root, self)
        return root

    def plugin_root(self, patterns: tuple[str, ...]) -> Path:
        found = self.plugin_roots.get(patterns)
        if found is not None:
            return found
        root = self.plugin_roots[patterns] = self.scratch("plugin-")
        wanted = InputPatterns(patterns)
        self.write(root, [path for path in self.listing.blobs if wanted.matches(path)])
        PLUGIN_ROOTS.add(root, self)
        return root

    def holds(self, path: str) -> bool:
        return path in self.listing.blobs or path in self.directories
