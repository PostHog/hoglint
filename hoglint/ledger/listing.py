"""The files of one snapshot, as git lists them, with no checkout.

- A git tree (a commit's, or the index's captured by `git write-tree`): `TreeListing`, from one
  `git ls-tree -r -z`.
- The working tree (D40): `WorktreeListing`, the index's blobs overlaid with the files
  `git status` reports as modified or untracked. Nothing else on disk is in it: no ignored file,
  no empty directory, and no file behind a `skip-worktree` or `assume-unchanged` bit, whose
  index blob stands for it. Only a reported file is read from disk.

Both engines and every plugin read a snapshot through one of these, and a question whether a
path exists is answered from its list, never from the file system.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterable
from functools import cached_property
from pathlib import Path
from typing import ClassVar

from hoglint.ledger.status import DELETED_MODE, WorktreeStatus

SUBMODULE_MODE = "160000"


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


class Listing:
    """path -> where its bytes are: `blobs` holds the paths read from git's object store by blob
    oid, `disk` the paths read from the working tree. Paths keep git's order."""

    def __init__(self, repo: Path, blobs: dict[str, str], disk: Iterable[str] = ()) -> None:
        self.repo = repo
        self.blobs = blobs
        self.disk = frozenset(disk)

    @cached_property
    def paths(self) -> list[str]:
        return [*self.blobs, *sorted(self.disk)]

    @cached_property
    def directories(self) -> frozenset[str]:
        out: set[str] = set()
        for path in self.paths:
            parts = path.split("/")[:-1]
            out.update("/".join(parts[:end]) for end in range(1, len(parts) + 1))
        return frozenset(out)

    def holds(self, path: str) -> bool:
        """Whether the snapshot holds `path`, as a file or as a directory with a file in it."""
        return path in self.blobs or path in self.disk or path in self.directories

    def write(self, target: Path, paths: Iterable[str]) -> list[str]:
        """Writes the paths the snapshot holds under `target`; a path it does not hold is left out.
        Returns the paths it holds but could not read, which are left out too."""
        wanted = list(paths)
        write_blobs(self.repo, target, {path: self.blobs[path] for path in wanted if path in self.blobs})
        unreadable = []
        for path in wanted:
            if path not in self.disk:
                continue
            written = target / path
            written.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copyfile(self.repo / path, written)
            except OSError:
                unreadable.append(path)
        return unreadable


class TreeListing(Listing):
    """Every file of one git tree, from one `git ls-tree -r -z`. A tree is immutable, so each one
    is listed at most once per process. Submodules are no files."""

    taken: ClassVar[dict[tuple[Path, str], TreeListing]] = {}

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
        found = TreeListing.taken[key] = TreeListing(repo, TreeListing.parse(output))
        return found


class WorktreeListing(Listing):
    """The working tree as a snapshot: the index's blobs (`git ls-files -s`, which honours
    GIT_INDEX_FILE), then each file `git status` reports as modified, untracked or in conflict,
    read from disk, and without each file it reports deleted. One per repo per process, read
    with the status the rest of the run reads."""

    taken: ClassVar[dict[Path, WorktreeListing]] = {}

    @staticmethod
    def index_blobs(repo: Path) -> dict[str, str]:
        """Stage 0 entries; a conflicted path is in the status as a file to read from disk."""
        command = ["git", "-C", str(repo), "ls-files", "-s", "-z"]
        output = subprocess.run(command, capture_output=True, text=True, check=True).stdout
        blobs: dict[str, str] = {}
        for record in output.split("\0"):
            meta, _, path = record.partition("\t")
            parts = meta.split()
            if len(parts) == 3 and parts[2] == "0" and parts[0] != SUBMODULE_MODE:
                blobs[path] = parts[1]
        return blobs

    @staticmethod
    def of(repo: Path) -> WorktreeListing:
        key = repo.resolve()
        found = WorktreeListing.taken.get(key)
        if found is not None:
            return found
        blobs = WorktreeListing.index_blobs(repo)
        disk: set[str] = set()
        for entry in WorktreeStatus.of(repo).entries:
            if entry.renamed_from or not entry.unstaged or entry.mode == SUBMODULE_MODE:
                continue
            blobs.pop(entry.path, None)
            if entry.mode != DELETED_MODE:
                disk.add(entry.path)
        found = WorktreeListing.taken[key] = WorktreeListing(repo, blobs, disk)
        return found
