"""At most K hoglint evaluations at once per machine (D36).

A run takes one of K lock files under `<cache root>/slots` with `flock` before it evaluates code,
and keeps it until it ends; the OS releases the lock when the process ends, however it ends. A
run that reads a stored summary evaluates nothing and takes no slot. K is one slot per 4 GB of
memory, from 1 to 4, unless HOGLINT_MAX_RUNS sets it. A run that waits more than a second says so
on stderr. The slots are part of the cache: when they cannot be used, the run warns once and
goes on without one.
"""

from __future__ import annotations

import os
import sys
import time
import fcntl
from pathlib import Path
from typing import IO

from hoglint.ledger.store import CacheWarning

GB_PER_SLOT = 4
MAX_SLOTS = 4
NOTICE_SECONDS = 1.0
POLL_SECONDS = 0.1


class EvaluationSlots:
    def __init__(self, cache: Path, count: int | None = None) -> None:
        self.root = cache / "slots"
        self.count = count or self.default_count()
        self.held: IO[bytes] | None = None
        self.given_up = False

    @staticmethod
    def default_count() -> int:
        override = os.environ.get("HOGLINT_MAX_RUNS")
        if override:
            return max(1, int(override))
        memory = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        return max(1, min(MAX_SLOTS, memory // (GB_PER_SLOT * 1024**3)))

    def try_one(self) -> IO[bytes] | None:
        """A free slot, locked; None when every slot is held."""
        for number in range(self.count):
            handle = open(self.root / f"slot-{number}.lock", "ab")  # noqa: SIM115 — held until the run ends
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                handle.close()
                continue
            return handle
        return None

    def take(self) -> None:
        """Waits for a slot, unless this run holds one already."""
        if self.held is not None or self.given_up:
            return
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            started = time.monotonic()
            told = False
            while (handle := self.try_one()) is None:
                if not told and time.monotonic() - started > NOTICE_SECONDS:
                    told = True
                    sys.stderr.write(
                        f"hoglint: waiting for one of {self.count} evaluation slots ({self.root}): "
                        "other hoglint runs hold them all\n"
                    )
                time.sleep(POLL_SECONDS)
        except OSError as error:
            self.given_up = True
            CacheWarning.warn("taking an evaluation slot", error)
            return
        self.held = handle

    def release(self) -> None:
        if self.held is not None:
            self.held.close()
            self.held = None
