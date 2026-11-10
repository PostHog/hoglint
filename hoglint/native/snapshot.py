"""Snapshots as git lists them: every source file as (path, blob oid), with no checkout.

- The index: `git ls-files -s` (it honours GIT_INDEX_FILE). Conflict entries make the run
  incomplete.
- A git tree (a commit's, or the index's captured by `git write-tree`): `git ls-tree -r`, once
  per process.
- The working tree: the index's blobs overlaid with what `git status --porcelain=v2 -uall`
  reports (`WorktreeListing`, D40). A reported file is hashed from its raw bytes
  (`git hash-object --no-filters`), as the extractor reads them, and its bytes come from the
  working tree. A file git cannot read gets the null id, so the extractor reads it, fails, and
  records a read error for it.

A symlink is read as the file it leads to (D41). One that dangles or leaves the snapshot gets the
null id from git's object store, which has no such blob, so it is a read error too.

Paths come only from git, never from listing or stat-ing the file system, so a case-insensitive
file system cannot change a result.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from hoglint.ledger.listing import TreeListing, WorktreeListing

SOURCE_SUFFIXES = (".py", ".pyi")
SUBMODULE_MODE = "160000"
NULL_OID = "0" * 40  # no blob has it, so no stored facts answer for it


def git(repo: Path, *args: str, stdin: str | None = None) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], input=stdin, capture_output=True, text=True, check=True
    ).stdout


def records(output: str) -> list[str]:
    return [record for record in output.split("\0") if record]


def is_source(path: str) -> bool:
    return path.endswith(SOURCE_SUFFIXES)


def hash_one(repo: Path, path: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), "hash-object", "--no-filters", "--", path], capture_output=True, text=True
    )
    return done.stdout.strip() if done.returncode == 0 else NULL_OID


def hash_worktree_files(repo: Path, paths: list[str]) -> list[str]:
    """The blob ids of working tree files, from their raw bytes. One unreadable file fails the
    whole batch, so then each file is hashed alone, and the ones git cannot read get the null id."""
    listed = "".join(f"{path}\n" for path in paths)
    try:
        return git(repo, "hash-object", "--no-filters", "--stdin-paths", stdin=listed).split()
    except subprocess.CalledProcessError:
        return [hash_one(repo, path) for path in paths]


@dataclass(frozen=False)
class BlobSnapshot:
    """path -> (blob oid, read from the working tree) for every source file of one snapshot."""

    repo: Path
    blobs: dict[str, tuple[str, bool]] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)

    @property
    def root(self) -> Path:
        return self.repo

    def list(self, patterns: tuple[str, ...]) -> list[str]:
        """The source paths, sorted; `patterns` are the source patterns every snapshot lists."""
        return sorted(self.blobs)

    def close(self) -> None:
        return None

    @staticmethod
    def index(repo: Path) -> BlobSnapshot:
        snapshot = BlobSnapshot(repo)
        for record in records(git(repo, "ls-files", "-s", "-z")):
            meta, _, path = record.partition("\t")
            mode, oid, stage = meta.split()
            if not is_source(path) or mode == SUBMODULE_MODE:
                continue
            if stage != "0":
                if path not in snapshot.conflicts:
                    snapshot.conflicts.append(path)
                continue
            snapshot.blobs[path] = (oid, False)
        return snapshot

    @staticmethod
    def tree(repo: Path, tree: str) -> BlobSnapshot:
        snapshot = BlobSnapshot(repo)
        listing = TreeListing.of(repo, tree)
        for path in listing.paths:
            if is_source(path):
                snapshot.blobs[path] = (listing.blobs.get(path, NULL_OID), False)
        return snapshot

    @staticmethod
    def worktree(repo: Path) -> BlobSnapshot:
        snapshot = BlobSnapshot(repo)
        listing = WorktreeListing.of(repo)
        for path in listing.paths:
            if is_source(path):
                snapshot.blobs[path] = (listing.blobs.get(path, NULL_OID), False)
        dirty = sorted(path for path in listing.disk if is_source(path))
        if dirty:
            # A link is hashed by the file it leads to; the extractor reads the link, which the
            # operating system follows to that file.
            hashed = hash_worktree_files(repo, [listing.disk[path] for path in dirty])
            for path, oid in zip(dirty, hashed, strict=True):
                snapshot.blobs[path] = (oid, True)
        return snapshot
