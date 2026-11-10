"""Which source files exist in a snapshot: the working tree, or the staged index."""

from __future__ import annotations

import shutil
import fnmatch
import tempfile
import subprocess
from dataclasses import dataclass
from pathlib import Path

from hoglint.core.config import CoreConfig
from hoglint.ledger.listing import Listing, TreeListing, WorktreeListing, read_blobs

SOURCE_PATTERNS = ("*.py", "*.pyi")


@dataclass(frozen=True)
class Inventory:
    """`files` all exist and resolve as modules; only `analyzed` files produce edges."""

    root: Path
    files: tuple[str, ...]
    analyzed: tuple[str, ...]


class StagedIndex:
    """Staged blobs written to a temp tree. Never reads the working tree."""

    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.root = Path(tempfile.mkdtemp(prefix="hoglint-index-"))
        self.written: set[str] = set()

    def entries(self, patterns: tuple[str, ...]) -> dict[str, str]:
        out = subprocess.run(
            ["git", "ls-files", "-s", "-z", "--", *patterns],
            cwd=self.repo,
            capture_output=True,
            check=True,
        ).stdout.decode()
        blobs: dict[str, str] = {}
        for entry in out.split("\0"):
            if not entry:
                continue
            meta, _, rel = entry.partition("\t")
            mode, sha, stage = meta.split()
            if stage != "0" or mode == "160000":
                continue
            blobs[rel] = sha
        return blobs

    def materialize(self, blobs: dict[str, str]) -> None:
        pending = {rel: sha for rel, sha in blobs.items() if rel not in self.written}
        for rel, data in read_blobs(self.repo, pending).items():
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            self.written.add(rel)

    def list(self, patterns: tuple[str, ...]) -> list[str]:
        blobs = self.entries(patterns)
        self.materialize(blobs)
        return sorted(blobs)

    def read_bytes(self, rel: str) -> bytes:
        if rel not in self.written:
            self.materialize(self.entries((rel,)))
        return (self.root / rel).read_bytes()

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


class ListedSnapshot:
    """The source files of one listed snapshot (listing.py), written into a temp tree on first
    read. Never reads a file the listing does not name."""

    def __init__(self, listing: Listing) -> None:
        self.listing = listing
        self.root = Path(tempfile.mkdtemp(prefix="hoglint-snapshot-"))
        self.written: set[str] = set()

    def list(self, patterns: tuple[str, ...]) -> list[str]:
        paths = [rel for rel in self.listing.paths if any(fnmatch.fnmatchcase(rel, p) for p in patterns)]
        self.listing.write(self.root, [rel for rel in paths if rel not in self.written])
        self.written.update(paths)
        return sorted(paths)

    def read_bytes(self, rel: str) -> bytes:
        if rel not in self.written:
            self.listing.write(self.root, [rel])
            self.written.add(rel)
        return (self.root / rel).read_bytes()

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


class GitTree(ListedSnapshot):
    """The blobs of one git tree. Never reads the index or the working tree."""

    def __init__(self, repo: Path, tree: str) -> None:
        super().__init__(TreeListing.of(repo, tree))


class WorkingTree(ListedSnapshot):
    """The working tree as git sees it (D40): the index's blobs, overlaid with the files git
    status reports. An ignored file, or a file behind a skip-worktree bit, is never read."""

    def __init__(self, repo: Path) -> None:
        super().__init__(WorktreeListing.of(repo))


Snapshot = WorkingTree | StagedIndex | GitTree


class Discovery:
    """Lists source files and splits them into analyzed files and resolvable-only files."""

    def __init__(self, config: CoreConfig) -> None:
        self.config = config

    def is_skipped(self, rel: str) -> bool:
        if self.config.include_hidden:
            return False
        return any(part.startswith(".") for part in rel.split("/"))

    def run(self, snapshot: Snapshot) -> Inventory:
        files = [rel for rel in snapshot.list(SOURCE_PATTERNS) if not self.is_skipped(rel)]
        excludes = self.config.exclude_globs
        analyzed = [rel for rel in files if not excludes.matches(rel)]
        return Inventory(root=snapshot.root, files=tuple(files), analyzed=tuple(analyzed))
