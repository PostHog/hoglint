"""Snapshots as git lists them: every source file as (path, blob oid), with no checkout.

- The index: `git ls-files -s` (it honours GIT_INDEX_FILE). Conflict entries make the run
  incomplete.
- A commit's tree: `git ls-tree -r`.
- The working tree: the index, then `git status --porcelain=v2 -uall` for what differs; a dirty
  or untracked file is hashed from its raw bytes (`git hash-object --no-filters`), as the
  extractor reads them, and its bytes come from the working tree.

Paths come only from git, never from listing or stat-ing the file system, so a case-insensitive
file system cannot change a result.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from hoglint.ledger.status import WorktreeStatus

SOURCE_SUFFIXES = (".py", ".pyi")
SUBMODULE_MODE = "160000"


def git(repo: Path, *args: str, stdin: str | None = None) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], input=stdin, capture_output=True, text=True, check=True
    ).stdout


def records(output: str) -> list[str]:
    return [record for record in output.split("\0") if record]


def is_source(path: str) -> bool:
    return path.endswith(SOURCE_SUFFIXES)


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
        for record in records(git(repo, "ls-tree", "-r", "-z", tree)):
            meta, _, path = record.partition("\t")
            _, kind, oid = meta.split()
            if kind == "blob" and is_source(path):
                snapshot.blobs[path] = (oid, False)
        return snapshot

    @staticmethod
    def worktree(repo: Path) -> BlobSnapshot:
        snapshot = BlobSnapshot.index(repo)
        snapshot.conflicts = []
        dirty: list[str] = []
        status = WorktreeStatus.of(repo).records  # shared with the summary store's side-input check
        at = 0
        while at < len(status):
            record = status[at]
            at += 1
            kind = record[0]
            if kind == "?":
                if is_source(record[2:]):
                    dirty.append(record[2:])
                continue
            if kind == "2":
                at += 1  # the next record is the original path of a rename or copy
            fields = record.split(" ", {"1": 8, "2": 9, "u": 10}.get(kind, 1))
            if kind not in ("1", "2", "u") or not is_source(fields[-1]):
                continue
            path, worktree_mode, change = fields[-1], fields[5] if kind != "u" else fields[6], fields[1][1]
            if change == "D" or worktree_mode == "000000":
                snapshot.blobs.pop(path, None)
                continue
            if kind == "u" or change != ".":
                dirty.append(path)
        if dirty:
            paths = "".join(f"{path}\n" for path in dirty)
            oids = git(repo, "hash-object", "--no-filters", "--stdin-paths", stdin=paths).split()
            for path, oid in zip(dirty, oids):
                snapshot.blobs[path] = (oid, True)
        return snapshot
