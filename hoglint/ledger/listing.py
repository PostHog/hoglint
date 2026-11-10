"""The files of one snapshot, as git lists them, with no checkout.

- A git tree (a commit's, or the index's captured by `git write-tree`): `TreeListing`, from one
  `git ls-tree -r -z`.
- The working tree (D40): `WorktreeListing`, the index's blobs overlaid with the files
  `git status` reports as modified or untracked. Nothing else on disk is in it: no ignored file,
  no empty directory, and no file behind a `skip-worktree` or `assume-unchanged` bit, whose
  index blob stands for it. Only a reported file is read from disk.

Both engines and every plugin read a snapshot through one of these, and a question whether a
path exists is answered from its list, never from the file system.

A symlink is read as its target (D41): a listing resolves it to the file it leads to inside the
snapshot, through chains, as the operating system would. A link that dangles, loops or leaves
the snapshot is unreadable: a source the extractor cannot read, or a plugin input that fails.
"""

from __future__ import annotations

import os
import stat
import shutil
import subprocess
from collections.abc import Iterable
from functools import cached_property
from pathlib import Path
from typing import ClassVar

from hoglint.ledger.status import DELETED_MODE, WorktreeStatus

SUBMODULE_MODE = "160000"
LINK_MODE = "120000"
FILE_MODES = frozenset({"100644", "100755"})
# More links than this in one path is a loop, as the operating system's limit says.
MAX_LINK_HOPS = 40


def read_blobs(repo: Path, oids: dict[str, str]) -> dict[str, bytes]:
    """path -> the bytes of its blob, all in one `git cat-file --batch`."""
    if not oids:
        return {}
    pending = sorted(oids.items())
    request = "".join(f"{oid}\n" for _, oid in pending).encode()
    command = ["git", "-C", str(repo), "cat-file", "--batch"]
    out = subprocess.run(command, input=request, capture_output=True, check=True).stdout
    found: dict[str, bytes] = {}
    at = 0
    for path, oid in pending:
        header_end = out.index(b"\n", at)
        got, kind, size = out[at:header_end].decode().split()
        if got != oid or kind != "blob":
            raise RuntimeError(f"git cat-file returned {got} {kind} for {path}")
        start = header_end + 1
        end = start + int(size)
        found[path] = out[start:end]
        at = end + 1
    return found


class Links:
    """The symlinks of one snapshot, followed component by component as the operating system
    follows them, but never out of the snapshot."""

    def __init__(self, modes: dict[str, str], texts: dict[str, str]) -> None:
        self.modes = modes
        self.texts = texts

    def target(self, path: str) -> str | None:
        """The regular file `path` leads to; None when a link dangles, loops or leaves the tree."""
        done: list[str] = []
        pending = path.split("/")
        hops = 0
        while pending:
            part = pending.pop(0)
            if part in ("", "."):
                continue
            if part == "..":
                if not done:
                    return None
                done.pop()
                continue
            here = "/".join([*done, part])
            if self.modes.get(here) != LINK_MODE:
                done.append(part)
                continue
            hops += 1
            text = self.texts[here]
            if hops > MAX_LINK_HOPS or text.startswith("/"):
                return None
            pending = [*text.split("/"), *pending]
        final = "/".join(done)
        return final if self.modes.get(final) in FILE_MODES else None


class Listing:
    """path -> mode for every file of a snapshot, in git's order, and where each file's bytes
    are: `blobs` holds the paths read from git's object store by blob oid, `disk` the paths read
    from the working tree (path -> the file to read), and `unreadable` the links that lead
    nowhere inside the snapshot. A link's bytes are its target's."""

    def __init__(
        self,
        repo: Path,
        modes: dict[str, str],
        blobs: dict[str, str],
        disk: dict[str, str] | None = None,
        unreadable: Iterable[str] = (),
    ) -> None:
        self.repo = repo
        self.modes = modes
        self.blobs = blobs
        self.disk = disk or {}
        self.unreadable = frozenset(unreadable)

    @property
    def paths(self) -> list[str]:
        return list(self.modes)

    @cached_property
    def directories(self) -> frozenset[str]:
        out: set[str] = set()
        for path in self.modes:
            parts = path.split("/")[:-1]
            out.update("/".join(parts[:end]) for end in range(1, len(parts) + 1))
        return frozenset(out)

    def holds(self, path: str) -> bool:
        """Whether the snapshot holds `path`, as a file or as a directory with a file in it."""
        return path in self.modes or path in self.directories

    def write(self, target: Path, paths: Iterable[str]) -> list[str]:
        """Writes the paths the snapshot holds under `target`; a path it does not hold is left out.
        Returns the paths it holds but cannot read, which are left out too."""
        wanted = [path for path in paths if path in self.modes]
        found = read_blobs(self.repo, {path: self.blobs[path] for path in wanted if path in self.blobs})
        unreadable = []
        for path in wanted:
            written = target / path
            written.parent.mkdir(parents=True, exist_ok=True)
            if path in found:
                written.write_bytes(found[path])
                continue
            try:
                shutil.copyfile(self.repo / self.disk[path], written)
            except (KeyError, OSError):
                unreadable.append(path)
        return unreadable


def link_texts(repo: Path, oids: dict[str, str]) -> dict[str, str]:
    return {path: os.fsdecode(raw) for path, raw in read_blobs(repo, oids).items()}


class TreeListing(Listing):
    """Every file of one git tree, from one `git ls-tree -r -z`, links resolved by their blobs. A
    tree is immutable, so each one is listed at most once per process. Submodules are no files."""

    taken: ClassVar[dict[tuple[Path, str], TreeListing]] = {}

    @staticmethod
    def parse(output: str) -> dict[str, tuple[str, str]]:
        """path -> (mode, blob oid)."""
        entries: dict[str, tuple[str, str]] = {}
        for record in output.split("\0"):
            meta, _, path = record.partition("\t")
            parts = meta.split()
            if len(parts) == 3 and parts[1] == "blob":
                entries[path] = (parts[0], parts[2])
        return entries

    @staticmethod
    def of(repo: Path, tree: str) -> TreeListing:
        key = (repo.resolve(), tree)
        found = TreeListing.taken.get(key)
        if found is not None:
            return found
        command = ["git", "-C", str(repo), "ls-tree", "-r", "-z", tree]
        output = subprocess.run(command, capture_output=True, text=True, check=True).stdout
        found = TreeListing.taken[key] = TreeListing.resolved(repo, TreeListing.parse(output))
        return found

    @staticmethod
    def resolved(repo: Path, entries: dict[str, tuple[str, str]]) -> TreeListing:
        modes = {path: mode for path, (mode, _) in entries.items()}
        links = {path: oid for path, (mode, oid) in entries.items() if mode == LINK_MODE}
        resolver = Links(modes, link_texts(repo, links))
        blobs: dict[str, str] = {}
        unreadable: list[str] = []
        for path, (mode, oid) in entries.items():
            target = resolver.target(path) if mode == LINK_MODE else path
            if target is None:
                unreadable.append(path)
                continue
            blobs[path] = oid if target == path else entries[target][1]
        return TreeListing(repo, modes, blobs, unreadable=unreadable)


class WorktreeListing(Listing):
    """The working tree as a snapshot: the index's blobs (`git ls-files -s`, which honours
    GIT_INDEX_FILE), then each file `git status` reports as modified, untracked or in conflict,
    read from disk, and without each file it reports deleted. A link the index holds is read by
    its blob, one git status reports by `readlink`. One per repo per process, read with the
    status the rest of the run reads."""

    taken: ClassVar[dict[Path, WorktreeListing]] = {}

    @staticmethod
    def index_entries(repo: Path) -> dict[str, tuple[str, str]]:
        """path -> (mode, blob oid) of stage 0 entries; a conflicted path is in the status as a
        file to read from disk."""
        command = ["git", "-C", str(repo), "ls-files", "-s", "-z"]
        output = subprocess.run(command, capture_output=True, text=True, check=True).stdout
        entries: dict[str, tuple[str, str]] = {}
        for record in output.split("\0"):
            meta, _, path = record.partition("\t")
            parts = meta.split()
            if len(parts) == 3 and parts[2] == "0" and parts[0] != SUBMODULE_MODE:
                entries[path] = (parts[0], parts[1])
        return entries

    @staticmethod
    def mode_on_disk(file: Path) -> str:
        try:
            found = os.lstat(file).st_mode
        except OSError:
            return "100644"  # gone since git status ran: reading it fails, so it is unreadable
        if stat.S_ISLNK(found):
            return LINK_MODE
        return "100755" if found & stat.S_IXUSR else "100644"

    @staticmethod
    def of(repo: Path) -> WorktreeListing:
        key = repo.resolve()
        found = WorktreeListing.taken.get(key)
        if found is not None:
            return found
        entries = WorktreeListing.index_entries(repo)
        dirty: dict[str, str] = {}
        for entry in WorktreeStatus.of(repo).entries:
            if entry.renamed_from or not entry.unstaged or entry.mode == SUBMODULE_MODE:
                continue
            entries.pop(entry.path, None)
            if entry.mode != DELETED_MODE:
                dirty[entry.path] = entry.mode or WorktreeListing.mode_on_disk(repo / entry.path)
        modes = {**{path: mode for path, (mode, _) in entries.items()}, **dirty}
        texts = link_texts(repo, {path: oid for path, (mode, oid) in entries.items() if mode == LINK_MODE})
        for path, mode in dirty.items():
            if mode == LINK_MODE:
                try:
                    texts[path] = os.readlink(repo / path)
                except OSError:
                    texts[path] = "/"  # a link that cannot be read leads nowhere
        resolver = Links(modes, texts)
        blobs: dict[str, str] = {}
        disk: dict[str, str] = {}
        unreadable: list[str] = []
        for path, mode in modes.items():
            target = resolver.target(path) if mode == LINK_MODE else path
            if target is None:
                unreadable.append(path)
            elif target in dirty:
                disk[path] = target
            else:
                blobs[path] = entries[target][1]
        found = WorktreeListing.taken[key] = WorktreeListing(repo, modes, blobs, disk, unreadable)
        return found
