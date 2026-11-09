"""The native engine's fact database and its daily prune.

One SQLite file per facts id, `<cache root>/facts/<facts id>.sqlite`, which the binding reads
and writes (`binding/src/factdb.rs`). Every row records the day a run last read it.

The first native run of each day prunes:
- rows that no run read for 30 days;
- other files under `facts/` (databases of other facts ids, older per-file caches) that are
  older than 30 days;
- then, while the cache root holds more than 2 GB, those other files oldest first, and after
  them the rows read longest ago.

Empty directories are left to the cache root's own prune (`hoglint/ledger/store.py`).
"""

from __future__ import annotations

import os
import time
import sqlite3
import datetime
from dataclasses import dataclass
from pathlib import Path

MAX_BYTES = 2 * 1024**3
MAX_AGE_DAYS = 30
PRUNED_MARK = ".pruned"
DATABASE_SUFFIXES = ("", "-wal", "-shm")
EPOCH = datetime.date(1970, 1, 1)


def files_under(directory: Path) -> list[tuple[float, int, Path]]:
    """(modified time, size, path) of every file under `directory`."""
    out = []
    for parent, _, names in os.walk(directory):
        for name in names:
            path = Path(parent) / name
            try:
                stat = path.stat()
            except OSError:
                continue
            out.append((stat.st_mtime, stat.st_size, path))
    return out


@dataclass(frozen=True)
class FactDatabase:
    """`directory` is the cache root's `facts/` directory; its parent is the root the size bound
    covers."""

    directory: Path
    facts_id: str

    @property
    def path(self) -> Path:
        return self.directory / f"{self.facts_id}.sqlite"

    def own_files(self) -> set[Path]:
        return {Path(f"{self.path}{suffix}") for suffix in DATABASE_SUFFIXES} | {self.directory / PRUNED_MARK}

    def prune_daily(self, today: datetime.date | None = None) -> bool:
        """Prunes when no run pruned today. The mark goes first, so concurrent runs prune once."""
        day = today or datetime.datetime.now(datetime.UTC).date()
        mark = self.directory / PRUNED_MARK
        try:
            if mark.read_text() == day.isoformat():
                return False
        except OSError:
            pass
        self.directory.mkdir(parents=True, exist_ok=True)
        mark.write_text(day.isoformat())
        self.prune(day)
        return True

    def prune(self, day: datetime.date, now: float | None = None) -> None:
        cutoff_day = (day - EPOCH).days - MAX_AGE_DAYS
        cutoff = (now or time.time()) - MAX_AGE_DAYS * 86_400
        if self.path.exists():
            with self.connect() as conn:
                old = [entry_id for (entry_id,) in conn.execute("SELECT id FROM keys WHERE used < ?", (cutoff_day,))]
                self.delete(conn, old)
        kept = []
        for modified, size, path in self.other_files():
            if modified < cutoff:
                path.unlink(missing_ok=True)
            else:
                kept.append((size, path))
        excess = sum(size for _, size, _ in files_under(self.directory.parent)) - MAX_BYTES
        for size, path in kept:
            if excess <= 0:
                break
            path.unlink(missing_ok=True)
            excess -= size
        if excess > 0 and self.path.exists():
            self.remove_rows(excess)

    def other_files(self) -> list[tuple[float, int, Path]]:
        """Files under `facts/` that are not this database, oldest first."""
        own = self.own_files()
        return sorted(entry for entry in files_under(self.directory) if entry[2] not in own)

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    @staticmethod
    def delete(conn: sqlite3.Connection, ids: list[int]) -> None:
        """Deletes the entries `ids` from every table."""
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS doomed (id INTEGER PRIMARY KEY)")
        conn.execute("DELETE FROM doomed")
        conn.executemany("INSERT OR IGNORE INTO doomed VALUES (?)", [(entry_id,) for entry_id in ids])
        conn.execute("DELETE FROM facts WHERE id IN (SELECT id FROM doomed)")
        conn.execute("DELETE FROM records WHERE id IN (SELECT id FROM doomed)")
        conn.execute("DELETE FROM keys WHERE id IN (SELECT id FROM doomed)")

    def remove_rows(self, excess: int) -> None:
        """Removes the entries read longest ago until `excess` bytes are free, and gives the
        pages back to the file system."""
        with self.connect() as conn:
            doomed = []
            for entry_id, size in conn.execute("SELECT id, bytes FROM keys ORDER BY used, id"):
                if excess <= 0:
                    break
                doomed.append(entry_id)
                excess -= size
            self.delete(conn, doomed)
        with self.connect() as conn:
            conn.execute("PRAGMA incremental_vacuum")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
