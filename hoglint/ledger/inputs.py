"""The files of one git tree, read by blob id with no checkout.

A staged run captures the index once with `git write-tree`, and every later read uses that tree:
the sources, the policy and the side inputs. `TreeListing` lists a tree once per process, and
`TreeFiles` writes chosen files of it into a directory.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar


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
    """Files of one git tree, written into directories on request."""

    def __init__(self, repo: Path, tree: str) -> None:
        self.repo = repo
        self.tree = tree

    @property
    def listing(self) -> TreeListing:
        return TreeListing.of(self.repo, self.tree)

    def write(self, target: Path, paths: Iterable[str]) -> None:
        """Writes the paths the tree holds under `target`; a path it does not hold is left out."""
        blobs = self.listing.blobs
        write_blobs(self.repo, target, {path: blobs[path] for path in paths if path in blobs})
