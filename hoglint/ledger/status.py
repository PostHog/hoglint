"""The working tree's git status, read once per process and shared.

The working tree listing (listing.py) reads it for the files to read from disk. `check` reads it
to tell whether the working tree holds HEAD's inputs (sources, declared side inputs, the ledger),
so that it can run HEAD's tree instead. One `git status --porcelain=v2 -uall` answers both.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

# Fields before the path in a porcelain v2 record, per record kind, and where the working tree's
# file mode sits among them.
FIELDS_BEFORE_PATH = {"1": 8, "2": 9, "u": 10}
WORKTREE_MODE_FIELD = {"1": 5, "2": 5, "u": 6}
DELETED_MODE = "000000"


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


def parse(records: list[str]) -> list[StatusEntry]:
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


class WorktreeStatus:
    """`git status --porcelain=v2 -z --untracked-files=all` of one repo, run at most once. `start`
    runs it in the background, so it costs nothing while hoglint evaluates the code."""

    COMMAND = ("status", "--porcelain=v2", "-z", "--untracked-files=all")
    taken: ClassVar[dict[Path, WorktreeStatus]] = {}
    started: ClassVar[dict[Path, subprocess.Popen[str]]] = {}

    def __init__(self, records: list[str]) -> None:
        self.records = records
        self.entries = parse(records)

    @staticmethod
    def start(repo: Path) -> None:
        key = repo.resolve()
        if key not in WorktreeStatus.taken and key not in WorktreeStatus.started:
            command = ["git", "-C", str(repo), *WorktreeStatus.COMMAND]
            WorktreeStatus.started[key] = subprocess.Popen(command, stdout=subprocess.PIPE, text=True)

    @staticmethod
    def of(repo: Path) -> WorktreeStatus:
        key = repo.resolve()
        status = WorktreeStatus.taken.get(key)
        if status is not None:
            return status
        WorktreeStatus.start(repo)
        process = WorktreeStatus.started.pop(key)
        output, _ = process.communicate()
        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, process.args)
        status = WorktreeStatus.taken[key] = WorktreeStatus([record for record in output.split("\0") if record])
        return status

    def drift(self, is_input: Callable[[str], bool]) -> list[str]:
        """Run inputs where the working tree differs from HEAD, staged or not."""
        return sorted({entry.path for entry in self.entries if is_input(entry.path)})
