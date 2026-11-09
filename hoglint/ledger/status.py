"""The working tree's git status, read once per process and shared.

The native snapshot reads it for dirty and untracked sources. The summary store reads it for
side inputs: files other than Python sources that hoglint reads from the working tree, which
must equal the tree a summary is filed under. One `git status --porcelain=v2 -uall` answers both.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

SOURCE_SUFFIXES = (".py", ".pyi")
# Fields before the path in a porcelain v2 record, per record kind.
FIELDS_BEFORE_PATH = {"1": 8, "2": 9, "u": 10}


@dataclass(frozen=True, slots=True)
class StatusEntry:
    path: str
    staged: bool  # HEAD and the index differ
    unstaged: bool  # the index and the working tree differ, or the file is untracked


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
        entry = StatusEntry(
            fields[-1], staged=kind == "u" or change[0] != ".", unstaged=kind == "u" or change[1] != "."
        )
        out.append(entry)
        if kind == "2":
            out.append(StatusEntry(records[at], entry.staged, entry.unstaged))  # the path it was renamed from
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

    def side_input_drift(self, against_index: bool) -> list[str]:
        """Files other than Python sources where the working tree differs from HEAD, or from the
        index when `against_index`."""
        return sorted(
            {
                entry.path
                for entry in self.entries
                if not entry.path.endswith(SOURCE_SUFFIXES) and (entry.unstaged or (entry.staged and not against_index))
            }
        )
