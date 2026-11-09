"""The hoglint cache root, the run summary store, and extractor binaries by native id.

Root: `$XDG_CACHE_HOME/hoglint`, else `~/.cache/hoglint`, one per machine and shared by every
worktree. It holds the fact cache (`facts/`, namespaced by the `[facts]` id), run summaries
(`summaries/`) and extractor binaries (`extractors/`).

Summaries: `summaries/<engine id>/<tree>/<policy>-<framework>.json.gz`. The key is the envelope
(code tree oid, engine id, policy hash, framework digest; see `envelope.py`), and the engine id
names the namespace, so an old engine's summaries age out together. The file is the
gzip-compressed JSON that `ScanSummary.write` writes, `protocol` first; `Envelope.key` names it
as one string. `get` answers only the exact key, and the envelope inside the file must match it
too: a corrupt file, another protocol or another envelope is a miss, never a result. A summary of a working tree has no tree oid and is never stored. The
ledger never stands in for a summary: it lacks incompletes, triples, couplings and every finding
the ledger does not count. A CI job may later publish summaries of master trees under the same key
and format for readers to fetch read-only; nothing here fetches or publishes.

Pruning: the first store use of each day removes every file under the root that is older than
30 days, then the oldest files until the root holds 2 GB at most. A summary hit refreshes the
file's time, so a summary in use stays. A fact cache entry that goes away is only a re-parse.
"""

from __future__ import annotations

import os
import time
import shutil
import datetime
import tempfile
from pathlib import Path

from hoglint.facts import BINARY_NAME
from hoglint.ledger.envelope import NATIVE, BinaryIds, EngineError, Envelope
from hoglint.ledger.scan import ProtocolError, ScanSummary

MAX_BYTES = 2 * 1024**3
MAX_AGE = datetime.timedelta(days=30)
PRUNED_MARK = ".pruned"


def cache_root() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "hoglint"


class CacheRoot:
    """Keeps the cache root within its age and size bounds, once a day."""

    def __init__(self, root: Path, max_bytes: int = MAX_BYTES, max_age: datetime.timedelta = MAX_AGE) -> None:
        self.root = root
        self.max_bytes = max_bytes
        self.max_age = max_age

    def prune_daily(self, today: datetime.date | None = None) -> bool:
        """Prunes when no run pruned today. The mark goes first, so concurrent runs prune once."""
        stamp = (today or datetime.date.today()).isoformat()
        mark = self.root / PRUNED_MARK
        try:
            if mark.read_text() == stamp:
                return False
        except OSError:
            pass
        self.root.mkdir(parents=True, exist_ok=True)
        mark.write_text(stamp)
        self.prune()
        return True

    def files(self) -> list[tuple[float, int, Path]]:
        """(modified time, size, path) of every file under the root but the mark."""
        out = []
        for directory, _, names in os.walk(self.root):
            for name in names:
                path = Path(directory) / name
                if path.parent == self.root and name == PRUNED_MARK:
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                out.append((stat.st_mtime, stat.st_size, path))
        return out

    def prune(self, now: float | None = None) -> None:
        cutoff = (now or time.time()) - self.max_age.total_seconds()
        kept = []
        for modified, size, path in self.files():
            if modified < cutoff:
                path.unlink(missing_ok=True)
            else:
                kept.append((modified, size, path))
        total = sum(size for _, size, _ in kept)
        for _, size, path in sorted(kept, key=lambda entry: entry[0]):
            if total <= self.max_bytes:
                break
            path.unlink(missing_ok=True)
            total -= size
        self.remove_old_empty_directories(cutoff)

    def remove_old_empty_directories(self, cutoff: float) -> None:
        """A directory made within the age bound stays, so a run about to write into it never loses it."""
        directories = sorted((Path(d) for d, _, _ in os.walk(self.root)), key=lambda d: len(d.parts), reverse=True)
        for directory in directories:
            try:
                if directory != self.root and directory.stat().st_mtime < cutoff and not any(directory.iterdir()):
                    directory.rmdir()
            except OSError:
                continue


class SummaryStore:
    def __init__(self, root: Path | None = None) -> None:
        self.cache = CacheRoot(root or cache_root())
        self.root = self.cache.root / "summaries"
        self.pruned = False

    def prune_once(self) -> None:
        if not self.pruned:
            self.pruned = True
            self.cache.prune_daily()

    def path(self, key: Envelope) -> Path:
        return self.root / key.engine / key.tree / f"{key.policy}-{key.framework}.json.gz"

    def get(self, key: Envelope) -> ScanSummary | None:
        """The stored summary with exactly this key, else None."""
        self.prune_once()
        if not key.tree:
            return None
        path = self.path(key)
        if not path.is_file():
            return None
        try:
            summary = ScanSummary.read(path)
        except ProtocolError:
            return None
        if summary.envelope != key:
            return None
        os.utime(path)
        return summary

    def put(self, key: Envelope, summary: ScanSummary) -> Path | None:
        """Stores a summary under its own envelope; a summary of no tree is not stored."""
        if summary.envelope != key:
            raise ValueError(f"a summary of {summary.envelope} cannot be stored under {key}")
        self.prune_once()
        if not key.tree:
            return None
        path = self.path(key)
        summary.write(path)
        return path


class ExtractorStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or cache_root() / "extractors"

    def path(self, native_id: str) -> Path:
        return self.root / native_id / BINARY_NAME

    def find(self, native_id: str) -> Path | None:
        path = self.path(native_id)
        if not path.is_file():
            return None
        try:
            if BinaryIds.of(path, NATIVE) != native_id:
                return None
        except EngineError:
            return None
        os.utime(path)
        return path

    def keep(self, binary: Path, native_id: str) -> None:
        """Copies a binary in under its native id, unless one is already there."""
        path = self.path(native_id)
        if path.is_file():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".extractor-")
        os.close(handle)
        shutil.copy(binary, temporary)
        Path(temporary).replace(path)
