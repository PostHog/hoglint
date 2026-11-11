"""The pack memo's store: what each pack step's items read and made, by content.

One SQLite file per engine id and table layout, `<cache root>/memo/<engine id>-l<layout>.sqlite`,
kept like the fact database (`factdb.py`): every run that opens it touches its `.opened` mark,
the first run of the day prunes states no run read for 30 days and, while the cache root is over
its size bound, the states read longest ago, never one read since yesterday; other engines'
databases go whole.

A step's state is a manifest (part -> digest) and its parts (hoglint/core/memo.py):
- `keys`: one row per stored manifest: the step key (step and policy), the digest of the
  manifest's bytes, the day a run last read it (`used`), its size with the parts first written
  with it, and when a run last wrote or read it (`touched`), which orders a step's states.
- `blobs`: the manifest's bytes under the same id.
- `parts`: each part's bytes by their digest, shared by every manifest that names it, and
  `refs` which manifests name it; a part no manifest names goes with the last one.
Every body is zlib-compressed JSON and is never changed: a new state is new rows. A body whose
bytes do not hash to its digest is damage.

The store is a cache (D28): a state or part that cannot be read, decoded or checked is a miss,
any error warns once and the run goes on without the store, and a database SQLite calls damaged
is moved aside.
"""

from __future__ import annotations

import time
import zlib
import hashlib
import sqlite3
import datetime
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, ClassVar

import orjson

from hoglint.core.memo import Written
from hoglint.ledger.envelope import EngineId, EngineSources
from hoglint.ledger.store import MEMO
from hoglint.native import bridge
from hoglint.native.factdb import EPOCH, CacheWarning, FactDatabase, is_corrupt

CRATE = bridge.CRATE
PACKAGE = CRATE / "hoglint"
ENGINE_NATIVE = "native"
LAYOUT = 2
# States of a step past the newest few are not read again and go.
STATES_KEPT = 4
ZLIB_LEVEL = 1
SCHEMA = (
    "CREATE TABLE keys (id INTEGER PRIMARY KEY, step TEXT NOT NULL, digest TEXT NOT NULL, used INTEGER NOT NULL,"
    " bytes INTEGER NOT NULL, touched REAL NOT NULL, UNIQUE (step, digest))",
    "CREATE INDEX keys_by_step ON keys (step, touched)",
    "CREATE TABLE blobs (id INTEGER PRIMARY KEY, body BLOB NOT NULL)",
    "CREATE TABLE parts (digest TEXT PRIMARY KEY, body BLOB NOT NULL) WITHOUT ROWID",
    "CREATE TABLE refs (state INTEGER NOT NULL, part TEXT NOT NULL)",
    "CREATE INDEX refs_by_state ON refs (state)",
    "CREATE INDEX refs_by_part ON refs (part)",
)


def today() -> int:
    return (datetime.datetime.now(datetime.UTC).date() - EPOCH).days


def body_digest(body: bytes) -> str:
    return hashlib.blake2b(body, digest_size=16).hexdigest()


def decoded(found: str, body: object) -> Any:
    """A stored body's JSON value, or None when its bytes do not check or do not decode."""
    if not isinstance(body, bytes) or body_digest(body) != found:
        return None
    try:
        return orjson.loads(zlib.decompress(body))
    except Exception:  # noqa: BLE001 - bytes that do not decode are a miss, whatever the error
        return None


class MemoWarning(CacheWarning):
    """Logs that the pack memo is not usable, once per process. It has its own flag and text, so
    stderr tells it from the fact cache, and a later fact cache failure still logs."""

    logged = False
    text = "the pack memo is not usable, so this run runs every pack item and stores none"


@dataclass(frozen=True)
class MemoDatabase(FactDatabase):
    """The memo's database; the fact database's prune with the memo's tables."""

    warning: ClassVar[type[CacheWarning]] = MemoWarning

    @staticmethod
    def delete(conn: sqlite3.Connection, ids: list[int]) -> None:
        """Deletes the states `ids`, and the parts no other state names."""
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS doomed (id INTEGER PRIMARY KEY)")
        conn.execute("DELETE FROM doomed")
        conn.executemany("INSERT OR IGNORE INTO doomed VALUES (?)", [(entry_id,) for entry_id in ids])
        conn.execute("DELETE FROM blobs WHERE id IN (SELECT id FROM doomed)")
        conn.execute("DELETE FROM keys WHERE id IN (SELECT id FROM doomed)")
        conn.execute("DELETE FROM refs WHERE state IN (SELECT id FROM doomed)")
        conn.execute("DELETE FROM parts WHERE digest NOT IN (SELECT part FROM refs)")


class MemoStore:
    """Step states by step key. Every method is a cache operation: it never raises."""

    def __init__(self, root: Path, engine: str) -> None:
        self.database = MemoDatabase(root / MEMO, engine, LAYOUT)
        self.conn: sqlite3.Connection | None = None
        self.broken = False

    def fail(self, error: Exception) -> None:
        self.broken = True
        MemoWarning.log(error)
        if is_corrupt(error):
            self.close()
            self.database.move_aside()

    def state(self) -> str:
        """`failed` once an error turned the store off, `open` once a run read it, else `unused`."""
        if self.broken:
            return "failed"
        return "open" if self.conn is not None else "unused"

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.close()
            except sqlite3.Error:
                pass
            self.conn = None

    def connect(self) -> sqlite3.Connection | None:
        if self.broken:
            return None
        if self.conn is not None:
            return self.conn
        try:
            self.database.directory.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.database.path, timeout=10, isolation_level=None)
            self.conn = conn
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            if conn.execute("PRAGMA user_version").fetchone()[0] != LAYOUT:
                self.set_up(conn)
        except (OSError, sqlite3.Error) as error:
            self.fail(error)
            return None
        if not self.database.open_for_run():
            self.close()
            self.broken = True
            return None
        return self.conn

    @staticmethod
    def set_up(conn: sqlite3.Connection) -> None:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute("PRAGMA user_version").fetchone()[0] != LAYOUT:
                for table in ("keys", "blobs", "parts", "refs"):
                    conn.execute(f"DROP TABLE IF EXISTS {table}")
                for statement in SCHEMA:
                    conn.execute(statement)
                conn.execute(f"PRAGMA user_version = {LAYOUT}")
            conn.execute("COMMIT")
        except sqlite3.Error:
            conn.execute("ROLLBACK")
            raise

    def manifest(self, step: str) -> tuple[str, dict[str, str]] | None:
        """The newest state of `step`: its digest and manifest; None when there is none, or
        when it does not read back whole."""
        conn = self.connect()
        if conn is None:
            return None
        try:
            row = conn.execute(
                "SELECT k.digest, b.body FROM keys k JOIN blobs b ON b.id = k.id"
                " WHERE k.step = ? ORDER BY k.touched DESC, k.id DESC LIMIT 1",
                (step,),
            ).fetchone()
        except sqlite3.Error as error:
            self.fail(error)
            return None
        if row is None:
            return None
        manifest = decoded(row[0], row[1])
        if not isinstance(manifest, dict) or not all(isinstance(v, str) for v in manifest.values()):
            return None
        return row[0], manifest

    def parts(self, digests: list[str]) -> dict[str, dict[str, Any]]:
        """The parts of `digests` that read back whole, by digest; a missing or damaged one is
        left out."""
        conn = self.connect()
        if conn is None or not digests:
            return {}
        try:
            marks = ",".join("?" * len(digests))
            rows = conn.execute(f"SELECT digest, body FROM parts WHERE digest IN ({marks})", digests).fetchall()
        except sqlite3.Error as error:
            self.fail(error)
            return {}
        out = {}
        for found, body in rows:
            part = decoded(found, body)
            if isinstance(part, dict) and isinstance(part.get("reads"), list) and isinstance(part.get("items"), dict):
                out[found] = part
        return out

    def save(self, written: list[Written]) -> None:
        """Stores each step's new state in one transaction; a state used again is marked read."""
        if not written:
            return
        conn = self.connect()
        if conn is None:
            return
        now = time.time()
        day = today()
        try:
            conn.execute("BEGIN IMMEDIATE")
            for step in written:
                if step.manifest is not None:
                    self.insert(conn, step, now, day)
                elif step.used is not None:
                    conn.execute(
                        "UPDATE keys SET touched = ?, used = ? WHERE step = ? AND digest = ?",
                        (now, day, step.step, step.used),
                    )
            conn.execute("COMMIT")
        except sqlite3.Error as error:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            self.fail(error)

    @staticmethod
    def insert(conn: sqlite3.Connection, step: Written, now: float, day: int) -> None:
        """A new state: its new parts (a stored copy of one is replaced, so a damaged copy
        goes), its manifest under its digest, and which parts it names. The step's states past
        the newest few go."""
        assert step.manifest is not None
        for found, body in step.parts.items():
            conn.execute("INSERT OR REPLACE INTO parts (digest, body) VALUES (?, ?)", (found, body))
        body = zlib.compress(orjson.dumps(step.manifest), ZLIB_LEVEL)
        found = body_digest(body)
        old = conn.execute("SELECT id FROM keys WHERE step = ? AND digest = ?", (step.step, found)).fetchone()
        if old is not None:
            conn.execute("DELETE FROM blobs WHERE id = ?", old)
            conn.execute("DELETE FROM keys WHERE id = ?", old)
            conn.execute("DELETE FROM refs WHERE state = ?", old)
        cursor = conn.execute("INSERT INTO blobs (body) VALUES (?)", (body,))
        state = cursor.lastrowid
        size = len(body) + sum(len(part) for part in step.parts.values())
        conn.execute(
            "INSERT INTO keys (id, step, digest, used, bytes, touched) VALUES (?, ?, ?, ?, ?, ?)",
            (state, step.step, found, day, size, now),
        )
        conn.executemany(
            "INSERT INTO refs (state, part) VALUES (?, ?)", [(state, part) for part in set(step.manifest.values())]
        )
        stale = conn.execute(
            "SELECT id FROM keys WHERE step = ? ORDER BY touched DESC, id DESC LIMIT -1 OFFSET ?",
            (step.step, STATES_KEPT),
        ).fetchall()
        if stale:
            MemoDatabase.delete(conn, [row[0] for row in stale])


@cache
def engine_id() -> str:
    """The id of the code that makes a stored state: the `[engine]` id of the checkout's
    sources, else a digest of the installed package's Python files and its native module's id."""
    if EngineSources.read(CRATE) is not None:
        return EngineId.of(CRATE, ENGINE_NATIVE)
    hasher = hashlib.sha256(bridge.load().NATIVE_ID.encode())
    for path in sorted(PACKAGE.rglob("*.py")):
        hasher.update(path.relative_to(PACKAGE).as_posix().encode())
        hasher.update(path.read_bytes())
    return f"{ENGINE_NATIVE}-{hasher.hexdigest()[:16]}"
