"""The native engine's fact database and its daily prune.

One SQLite file per facts id and table layout, `<cache root>/facts/<facts id>-l<layout>.sqlite`,
which the binding reads and writes (`binding/src/factdb.rs`). Every row records the day a run
last read it. Every run that opens a database touches its `.opened` mark first.

A database is one unit: the main file, its `-wal` and `-shm` files and its marks. A unit is
deleted whole or not at all, because SQLite replays a stale `-wal` into a new main file and a
live run loses its writes when its `-wal` goes.

The first native run of each day, per database, prunes:
- rows that no run read for 30 days;
- other units no run opened for 30 days, and other files under `facts/` older than 30 days;
- then, while the cache root holds more than 2 GB: other units no run opened since yesterday,
  and other files, oldest first; after them the rows read longest ago.

The cache root's own prune (`hoglint/ledger/store.py`) leaves `facts/` to this one.
"""

from __future__ import annotations

import os
import sys
import time
import sqlite3
import datetime
from dataclasses import dataclass
from pathlib import Path

MAX_BYTES = 2 * 1024**3
MAX_AGE_DAYS = 30
IN_USE_DAYS = 1
DATABASE = ".sqlite"
UNIT_SUFFIXES = ("", "-wal", "-shm", ".opened", ".pruned")
EPOCH = datetime.date(1970, 1, 1)
CORRUPT = (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB)


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


def unit_files(database: Path) -> list[Path]:
    return [Path(f"{database}{suffix}") for suffix in UNIT_SUFFIXES]


def database_of(path: Path) -> Path | None:
    """The database whose unit `path` belongs to, by name; None for a file of no unit."""
    text = str(path)
    for suffix in UNIT_SUFFIXES:
        if text.endswith(DATABASE + suffix):
            return Path(text.removesuffix(suffix)) if suffix else path
    return None


def is_corrupt(error: Exception) -> bool:
    return isinstance(error, sqlite3.DatabaseError) and getattr(error, "sqlite_errorcode", None) in CORRUPT


@dataclass(frozen=True)
class Unit:
    """Another database under `facts/`: when a run last opened it, and its size."""

    database: Path
    opened: float
    size: int

    def delete(self) -> None:
        for path in unit_files(self.database):
            path.unlink(missing_ok=True)


class CacheWarning:
    """Logs that the fact cache is not usable, once per process: the run goes on without it."""

    logged = False

    @classmethod
    def log(cls, error: Exception) -> None:
        if not cls.logged:
            cls.logged = True
            sys.stderr.write(f"hoglint: the fact cache is not usable, so this run extracts without it: {error}\n")


@dataclass(frozen=True)
class FactDatabase:
    """`directory` is the cache root's `facts/` directory; its parent is the root the size bound
    covers. `layout` is the binding's table layout (`FACT_LAYOUT`)."""

    directory: Path
    facts_id: str
    layout: int

    @property
    def path(self) -> Path:
        return self.directory / f"{self.facts_id}-l{self.layout}{DATABASE}"

    @property
    def opened_mark(self) -> Path:
        return Path(f"{self.path}.opened")

    @property
    def pruned_mark(self) -> Path:
        return Path(f"{self.path}.pruned")

    def open_for_run(self, today: datetime.date | None = None) -> bool:
        """Marks the database opened and prunes once a day. False when the cache is not usable:
        the error is logged once, a damaged database is moved aside, and the run goes on
        without the cache."""
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self.opened_mark.touch()
            self.prune_daily(today)
        except (OSError, sqlite3.Error) as error:
            CacheWarning.log(error)
            if is_corrupt(error):
                self.move_aside()
            return False
        return True

    def move_aside(self) -> None:
        stamp = int(time.time())
        for suffix in ("", "-wal", "-shm"):
            source = Path(f"{self.path}{suffix}")
            if source.exists():
                source.rename(Path(f"{self.path}.corrupt-{stamp}{suffix}"))

    def prune_daily(self, today: datetime.date | None = None) -> bool:
        """Prunes when no run pruned this database today. The mark goes first, so concurrent runs
        prune once."""
        day = today or datetime.datetime.now(datetime.UTC).date()
        try:
            if self.pruned_mark.read_text() == day.isoformat():
                return False
        except OSError:
            pass
        self.directory.mkdir(parents=True, exist_ok=True)
        self.pruned_mark.write_text(day.isoformat())
        self.prune(day)
        return True

    def prune(self, day: datetime.date, now: float | None = None) -> None:
        now = now or time.time()
        cutoff_day = (day - EPOCH).days - MAX_AGE_DAYS
        if self.path.exists():
            with self.connect() as conn:
                old = [entry_id for (entry_id,) in conn.execute("SELECT id FROM keys WHERE used < ?", (cutoff_day,))]
                self.delete(conn, old)
        old_age = now - MAX_AGE_DAYS * 86_400
        units, files = self.others()
        for unit in units:
            if unit.opened < old_age:
                unit.delete()
        for modified, _, path in files:
            if modified < old_age:
                path.unlink(missing_ok=True)
        excess = sum(size for _, size, _ in files_under(self.directory.parent)) - MAX_BYTES
        in_use = now - IN_USE_DAYS * 86_400
        idle: list[tuple[float, int, Unit | Path]] = [
            (unit.opened, unit.size, unit) for unit in units if old_age <= unit.opened < in_use
        ]
        idle.extend((modified, size, path) for modified, size, path in files if modified >= old_age)
        for _, size, item in sorted(idle, key=lambda entry: entry[0]):
            if excess <= 0:
                break
            if isinstance(item, Unit):
                item.delete()
            else:
                item.unlink(missing_ok=True)
            excess -= size
        if excess > 0 and self.path.exists():
            self.remove_rows(excess)

    def others(self) -> tuple[list[Unit], list[tuple[float, int, Path]]]:
        """The other databases under `facts/` as units, and the files of no unit. A unit was last
        opened when its `.opened` mark was touched, else when its newest file changed."""
        own = set(unit_files(self.path))
        members: dict[Path, list[tuple[float, int, Path]]] = {}
        loose = []
        for entry in files_under(self.directory):
            if entry[2] in own:
                continue
            database = database_of(entry[2])
            if database is None:
                loose.append(entry)
            else:
                members.setdefault(database, []).append(entry)
        units = []
        for database, entries in members.items():
            marks = [modified for modified, _, path in entries if path.name.endswith(".opened")]
            opened = max(marks) if marks else max(modified for modified, _, _ in entries)
            units.append(Unit(database, opened, sum(size for _, size, _ in entries)))
        return units, loose

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
        pages back to the file system. `incremental_vacuum` frees one page per result row, so it
        is stepped to the end."""
        with self.connect() as conn:
            doomed = []
            for entry_id, size in conn.execute("SELECT id, bytes FROM keys ORDER BY used, id"):
                if excess <= 0:
                    break
                doomed.append(entry_id)
                excess -= size
            self.delete(conn, doomed)
        with self.connect() as conn:
            conn.execute("PRAGMA incremental_vacuum").fetchall()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
