"""What a run reads besides its policy: its sources and its side inputs, and the files of one git
tree, read by blob id with no checkout.

A staged run captures the index once with `git write-tree`, and every later read uses that tree:
the sources, the policy and the side inputs. `TreeFiles` writes chosen files of a tree's listing
(listing.py) into directories, and `WorktreeFiles` those of the working tree.

Side inputs are the files a run reads besides its sources and its policy files: what providers
and coverage adapters declare (`inputs` in plugins.py), the files layout rules ask about, and
the ledger. `RunInputs` holds their path patterns, which depend on the config alone, so they are
known before a run.
"""

from __future__ import annotations

import os
import re
import hashlib
import tempfile
from collections.abc import Iterable
from pathlib import Path

from hoglint.config.model import Config
from hoglint.globs import CAPTURE_RE, Braces, translate
from hoglint.ledger.listing import Listing, TreeListing, WorktreeListing
from hoglint.plugins import PLUGIN_ROOTS, REGISTRY, declared_inputs

SOURCE_INPUTS = ("**/*.py", "**/*.pyi")
INPUTS_DIGEST_CHARS = 40
PATTERN_DIGEST_CHARS = 16


class InputPatterns:
    """Path patterns as one regex: `**` spans directories, `*` and `?` stay in one segment."""

    def __init__(self, patterns: Iterable[str]) -> None:
        self.patterns = tuple(sorted(set(patterns)))
        self.regex = re.compile("|".join(f"(?:{translate(pattern, Braces.LITERAL)})" for pattern in self.patterns))

    def matches(self, path: str) -> bool:
        return bool(self.patterns) and self.regex.fullmatch(path) is not None

    def digest(self) -> str:
        return hashlib.sha256("\0".join(self.patterns).encode()).hexdigest()[:PATTERN_DIGEST_CHARS]


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
    def listing(self) -> Listing:
        return TreeListing.of(self.repo, self.tree)

    def write(self, target: Path, paths: Iterable[str]) -> None:
        """Writes the paths the snapshot holds under `target`; a path it does not hold is left
        out, and one it holds but cannot read is an error."""
        unreadable = self.listing.write(target, paths)
        if unreadable:
            more = f" and {len(unreadable) - 1} more" if len(unreadable) > 1 else ""
            raise OSError(f"cannot read {unreadable[0]}{more}")

    def scratch(self, prefix: str) -> Path:
        return Path(tempfile.mkdtemp(prefix=prefix, dir=self.work_dir)).resolve()

    def run_root(self) -> Path:
        """An empty directory that stands for the snapshot: rules and providers that get it as
        their repo root read the snapshot through `PLUGIN_ROOTS`."""
        root = self.scratch("tree-")
        PLUGIN_ROOTS.add(root, self)
        return root

    def plugin_root(self, patterns: tuple[str, ...]) -> Path:
        found = self.plugin_roots.get(patterns)
        if found is not None:
            return found
        root = self.plugin_roots[patterns] = self.scratch("plugin-")
        wanted = InputPatterns(patterns)
        self.write(root, [path for path in self.listing.paths if wanted.matches(path)])
        PLUGIN_ROOTS.add(root, self)
        return root

    def holds(self, path: str) -> bool:
        return self.listing.holds(path)


class WorktreeFiles(TreeFiles):
    """Files of the working tree as git sees it (D40): the index's blobs overlaid with what git
    status reports, so a plugin of a working tree run never reads an ignored file or a file
    behind a skip-worktree bit."""

    def __init__(self, repo: Path, work_dir: Path | None = None) -> None:
        super().__init__(repo, "", work_dir)

    @property
    def listing(self) -> Listing:
        return WorktreeListing.of(self.repo)


class InputDigests:
    """The `inputs` part of a summary key: a digest of (path, blob oid) over every file of a git
    tree that the run's input patterns match, sorted by path. A tree is immutable, so the digest
    of each (tree, patterns) pair is kept under `<cache>/inputs`, and a tree seen before needs
    no listing. That file is a cache: one that does not read is computed again."""

    def __init__(self, cache: Path) -> None:
        self.root = cache / "inputs"
        self.known: dict[tuple[str, str], str] = {}

    @staticmethod
    def compute(listing: TreeListing, patterns: InputPatterns) -> str:
        hasher = hashlib.sha256()
        for path in sorted((path for path in listing.blobs if patterns.matches(path)), key=str.encode):
            hasher.update(f"{path}\0{listing.blobs[path]}\0".encode())
        return hasher.hexdigest()[:INPUTS_DIGEST_CHARS]

    def of(self, repo: Path, tree: str, patterns: InputPatterns) -> str:
        """The digest for `tree`; empty when there is no tree."""
        if not tree:
            return ""
        key = (tree, patterns.digest())
        found = self.known.get(key)
        if found is not None:
            return found
        path = self.root / f"{tree}-{key[1]}"
        try:
            found = path.read_text().strip()
        except OSError:
            found = ""
        if len(found) != INPUTS_DIGEST_CHARS:
            found = self.compute(TreeListing.of(repo, tree), patterns)
            self.keep(path, found)
        self.known[key] = found
        return found

    @staticmethod
    def keep(path: Path, digest: str) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            written = path.with_name(f".{path.name}.{os.getpid()}")
            written.write_text(digest)
            written.replace(path)
        except OSError:
            return
