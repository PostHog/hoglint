"""The files of one snapshot, as git lists them, with no checkout.

- A git tree (a commit's, or the index's captured by `git write-tree`): `TreeListing`, from one
  `git ls-tree -r -z`.
- The working tree (D40): `WorktreeListing`, the index's blobs overlaid with the files
  `git status` reports as modified or untracked. Nothing else on disk is in it: no ignored file,
  no empty directory, and no file behind a `skip-worktree` or `assume-unchanged` bit, whose
  index blob stands for it. Only a reported file is read from disk.

The working tree's git status (`WorktreeStatus`) is read once per process and repo, and holds
the working tree's listing. `check` reads the status to tell whether the working tree holds
HEAD's inputs (sources, declared side inputs, the ledger), so that it can run HEAD's tree
instead. One `git status --porcelain=v2 -uall` answers both.

The engine and every plugin read a snapshot through one of these, and a question whether a
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
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import ClassVar

from hoglint.ledger.errors import EngineError, RepoRootError

# The files the engine reads as Python sources.
SOURCE_SUFFIXES = (".py", ".pyi")
SUBMODULE_MODE = "160000"
LINK_MODE = "120000"
DELETED_MODE = "000000"
FILE_MODES = frozenset({"100644", "100755"})
# More links than this in one path is a loop, as the operating system's limit says.
MAX_LINK_HOPS = 40
# Oids asked of `git cat-file --batch` at a time. A request this small fits an empty pipe even
# with SHA-256 ids, so writing it never waits on git, which may be waiting for its output to be read.
BLOB_CHUNK = 200
# Fields before the path in a porcelain v2 status record, per record kind, and where the working
# tree's file mode sits among them.
FIELDS_BEFORE_PATH = {"1": 8, "2": 9, "u": 10}
WORKTREE_MODE_FIELD = {"1": 5, "2": 5, "u": 6}


def git(repo: Path, *args: str, stdin: str | None = None) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], input=stdin, capture_output=True, text=True, check=True
    ).stdout


def records(output: str) -> list[str]:
    """The records of NUL-separated git output (`-z`)."""
    return [record for record in output.split("\0") if record]


@dataclass(frozen=True, slots=True)
class IndexRecord:
    """One entry of `git ls-files -s -z`: a stage other than "0" is one side of a conflict."""

    mode: str
    oid: str
    stage: str
    path: str


def parse_index(output: str) -> list[IndexRecord]:
    """Every record of `git ls-files -s -z` output, in git's order."""
    out = []
    for record in records(output):
        meta, _, path = record.partition("\t")
        mode, oid, stage = meta.split()
        out.append(IndexRecord(mode, oid, stage, path))
    return out


def read_blobs(repo: Path, oids: dict[str, str]) -> dict[str, bytes]:
    """path -> the bytes of its blob, all from one `git cat-file --batch`, asked a chunk at a
    time, so no copy of every blob's bytes is held besides the result."""
    if not oids:
        return {}
    pending = sorted(oids.items())
    command = ["git", "-C", str(repo), "cat-file", "--batch"]
    found: dict[str, bytes] = {}
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as git:
        requests, answers = git.stdin, git.stdout
        if requests is None or answers is None:
            raise RuntimeError("git cat-file has no pipes")
        try:
            for start in range(0, len(pending), BLOB_CHUNK):
                chunk = pending[start : start + BLOB_CHUNK]
                requests.write("".join(f"{oid}\n" for _, oid in chunk).encode())
                requests.flush()
                for path, oid in chunk:
                    header = answers.readline().decode().split()
                    if not header:
                        raise subprocess.CalledProcessError(git.wait(), command)
                    if header[1:] == ["missing"]:
                        raise EngineError(f"git has no object {oid} for {path}: a partial clone, or a pruned object")
                    got, kind, size = header
                    if got != oid or kind != "blob":
                        raise RuntimeError(f"git cat-file returned {got} {kind} for {path}")
                    found[path] = answers.read(int(size))
                    answers.read(1)
        finally:
            requests.close()
    if git.returncode != 0:
        raise subprocess.CalledProcessError(git.returncode, command)
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
    are: `blobs` holds the regular files read from git's object store by blob oid, `disk` the
    files read from the working tree (a link there by `readlink`), and `link_oids` the links whose
    text a blob holds. A link's bytes are those of the file it leads to; its text is read on the
    first question about a link, for every link at once."""

    def __init__(
        self,
        repo: Path,
        modes: dict[str, str],
        blobs: dict[str, str],
        link_oids: dict[str, str],
        disk: Iterable[str] = (),
    ) -> None:
        self.repo = repo
        self.modes = modes
        self.blobs = blobs
        self.link_oids = link_oids
        self.disk = frozenset(disk)

    @property
    def paths(self) -> list[str]:
        return list(self.modes)

    @cached_property
    def directories(self) -> frozenset[str]:
        out: set[str] = set()
        for path in self.modes:
            parent = path.rpartition("/")[0]
            while parent and parent not in out:
                out.add(parent)
                parent = parent.rpartition("/")[0]
        return frozenset(out)

    def link_texts(self) -> dict[str, str]:
        texts = {path: os.fsdecode(raw) for path, raw in read_blobs(self.repo, self.link_oids).items()}
        for path in self.disk:
            if self.modes[path] != LINK_MODE:
                continue
            try:
                texts[path] = os.readlink(self.repo / path)
            except OSError:
                texts[path] = "/"  # a link that cannot be read leads nowhere
        return texts

    @cached_property
    def targets(self) -> dict[str, str | None]:
        """link path -> the regular file it leads to; None when it leads nowhere in the snapshot."""
        texts = self.link_texts()
        resolver = Links(self.modes, texts)
        return {path: resolver.target(path) for path in texts}

    def target(self, path: str) -> str | None:
        return self.targets[path] if self.modes.get(path) == LINK_MODE else path

    def blob(self, path: str) -> str | None:
        """The blob oid that holds the bytes of `path`; None when they are on disk, or nowhere."""
        target = self.target(path)
        return self.blobs.get(target) if target is not None else None

    def disk_file(self, path: str) -> str | None:
        """The working tree file that holds the bytes of `path`; None when they are in a blob, or nowhere."""
        target = self.target(path)
        return target if target in self.disk else None

    def holds(self, path: str) -> bool:
        """Whether the snapshot holds `path`, as a file or as a directory with a file in it."""
        return path in self.modes or path in self.directories

    def read(self, paths: Iterable[str]) -> dict[str, bytes]:
        """path -> bytes for each of `paths` whose bytes are in a blob, in one `git cat-file`."""
        oids = {path: oid for path in paths if path in self.modes and (oid := self.blob(path)) is not None}
        return read_blobs(self.repo, oids)

    def write(self, target: Path, paths: Iterable[str], known: Mapping[str, bytes] | None = None) -> list[str]:
        """Writes the paths the snapshot holds under `target`; a path it does not hold is left out.
        `known` holds bytes read before (see `read`); only the other blobs are read now. Returns
        the paths it holds but cannot read, which are left out too."""
        wanted = [path for path in paths if path in self.modes]
        known = known or {}
        found = self.read(path for path in wanted if path not in known)
        made: set[Path] = set()
        unreadable = []
        for path in wanted:
            written = target / path
            if written.parent not in made:
                written.parent.mkdir(parents=True, exist_ok=True)
                made.add(written.parent)
            data = known[path] if path in known else found.get(path)
            if data is not None:
                written.write_bytes(data)
                continue
            on_disk = self.disk_file(path)
            try:
                if on_disk is None:
                    raise FileNotFoundError(path)
                shutil.copyfile(self.repo / on_disk, written)
            except OSError:
                unreadable.append(path)
        return unreadable


class TreeListing(Listing):
    """Every file of one git tree, from one `git ls-tree -r -z`. A tree is immutable, so each one
    is listed at most once per process. Submodules are no files."""

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
        entries = TreeListing.parse(git(repo, "ls-tree", "-r", "-z", tree))
        modes = {path: mode for path, (mode, _) in entries.items()}
        blobs = {path: oid for path, (mode, oid) in entries.items() if mode != LINK_MODE}
        links = {path: oid for path, (mode, oid) in entries.items() if mode == LINK_MODE}
        found = TreeListing.taken[key] = TreeListing(repo, modes, blobs, links)
        return found


@dataclass(frozen=True, slots=True)
class StatusEntry:
    path: str
    staged: bool  # HEAD and the index differ
    unstaged: bool  # the index and the working tree differ, or the file is untracked
    mode: str = ""  # the working tree's file mode; "000000" when deleted there, "" when untracked
    renamed_from: bool = False  # the old path of a rename or a copy
    # The old path of a rename in the working tree: the index still holds it, the working tree no
    # longer does. A rename in the index needs no such mark, and a copy keeps its old path.
    left_worktree: bool = False


def parse_status(records: list[str]) -> list[StatusEntry]:
    out = []
    at = 0
    while at < len(records):
        record = records[at]
        at += 1
        kind = record[0]
        if kind == "?":
            out.append(StatusEntry(record[2:], staged=False, unstaged=True))
            continue
        if kind not in FIELDS_BEFORE_PATH:
            continue
        fields = record.split(" ", FIELDS_BEFORE_PATH[kind])
        change = fields[1]
        staged, unstaged = kind == "u" or change[0] != ".", kind == "u" or change[1] != "."
        entry = StatusEntry(fields[-1], staged, unstaged, fields[WORKTREE_MODE_FIELD[kind]])
        out.append(entry)
        if kind == "2":
            left = change[1] == "R"
            out.append(StatusEntry(records[at], staged, unstaged, renamed_from=True, left_worktree=left))
            at += 1
    return out


def check_root(repo: Path) -> None:
    """The repo root must be the top of a git working tree: `git status` names paths from the
    top, `git ls-files` from where it runs, and the working tree's listing mixes the two."""
    done = subprocess.run(["git", "-C", str(repo), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    top = done.stdout.strip()
    if done.returncode != 0 or not top:
        raise RepoRootError(f"{repo} is not in a git working tree; hoglint needs the top of one as its repo root")
    if Path(top).resolve() != repo.resolve():
        raise RepoRootError(f"{repo} is not the top of its git working tree {top}; run hoglint with --repo {top}")


class WorktreeListing(Listing):
    """The working tree as a snapshot: the index's blobs (`git ls-files -s`, which honours
    GIT_INDEX_FILE), then each file `git status` reports as modified, untracked or in conflict,
    read from disk, and without each file it reports deleted or renamed away in the working
    tree. A link the index holds is read by its blob, one git status reports by `readlink`. One
    per repo per process: the listing of the status the rest of the run reads."""

    @staticmethod
    def index_entries(repo: Path) -> dict[str, tuple[str, str]]:
        """path -> (mode, blob oid) of stage 0 entries; a conflicted path is in the status as a
        file to read from disk."""
        return {
            record.path: (record.mode, record.oid)
            for record in parse_index(git(repo, "ls-files", "-s", "-z"))
            if record.stage == "0" and record.mode != SUBMODULE_MODE
        }

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
    def build(repo: Path, status: list[StatusEntry]) -> WorktreeListing:
        entries = WorktreeListing.index_entries(repo)
        dirty: dict[str, str] = {}
        for entry in status:
            if entry.left_worktree:
                entries.pop(entry.path, None)
                continue
            if entry.renamed_from or not entry.unstaged or entry.mode == SUBMODULE_MODE:
                continue
            entries.pop(entry.path, None)
            if entry.mode != DELETED_MODE:
                dirty[entry.path] = entry.mode or WorktreeListing.mode_on_disk(repo / entry.path)
        modes = {**{path: mode for path, (mode, _) in entries.items()}, **dirty}
        blobs = {path: oid for path, (mode, oid) in entries.items() if mode != LINK_MODE}
        links = {path: oid for path, (mode, oid) in entries.items() if mode == LINK_MODE}
        return WorktreeListing(repo, modes, blobs, links, dirty)

    @staticmethod
    def of(repo: Path) -> WorktreeListing:
        return WorktreeStatus.of(repo).listing


class WorktreeStatus:
    """`git status --porcelain=v2 -z --untracked-files=all` of one repo, run at most once per
    process, and the working tree's listing built from it. Both live in one registry, so a test
    that clears it clears both."""

    COMMAND = ("status", "--porcelain=v2", "-z", "--untracked-files=all")
    taken: ClassVar[dict[Path, WorktreeStatus]] = {}

    def __init__(self, repo: Path, records: list[str]) -> None:
        self.repo = repo
        self.records = records
        self.entries = parse_status(records)

    @staticmethod
    def of(repo: Path) -> WorktreeStatus:
        key = repo.resolve()
        status = WorktreeStatus.taken.get(key)
        if status is not None:
            return status
        check_root(repo)
        status = WorktreeStatus.taken[key] = WorktreeStatus(repo, records(git(repo, *WorktreeStatus.COMMAND)))
        return status

    @cached_property
    def listing(self) -> WorktreeListing:
        return WorktreeListing.build(self.repo, self.entries)

    def drift(self, is_input: Callable[[str], bool]) -> list[str]:
        """Run inputs where the working tree differs from HEAD, staged or not."""
        return sorted({entry.path for entry in self.entries if is_input(entry.path)})
