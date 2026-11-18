"""The hoglint cache root, the run summary store, and extractor binaries by native id.

Root: `$XDG_CACHE_HOME/hoglint`, else `~/.cache/hoglint`, one per machine and shared by every
worktree. It holds the fact cache (`facts/`, namespaced by the `[facts]` id), run summaries
(`summaries/`), the inputs digests of known trees (`inputs/`, see inputs.py), answers stored by
the content they depend on (`answers/`, see cache.py), extractor binaries (`extractors/`) and the
engines base commits pin (`engines/`, see `EngineInstalls` in cli.py).

Summaries: one manifest per exact key, `summaries/<engine id>/<inputs>/<policy>-<framework>.json`,
and its sections, `summaries/<engine id>/sections/<sha>.zst`. The key is the envelope (inputs
digest, engine id, policy hash, framework digest; see `envelope.py`), and the engine id names the
namespace, so an old engine's summaries age out together. The manifest is JSON: the protocol, the
envelope section inline, and the sha256 (first 32 hex) of every other section's JSON (see
`SUMMARY_SECTIONS` in scan.py). A section file is that JSON, zstd-compressed, named by its own
hash: a reader loads only the sections it reads (a baseline reads incompletes, and equality for a
commit), and a section equal to a stored one is never written twice. `get` answers only the exact
key, and the envelope inside the manifest must match it too: a corrupt or missing file, another
protocol or another envelope is a miss, never a result. A summary of a working tree has no inputs
digest and is never stored. The ledger never stands in for a summary: it lacks incompletes,
triples, couplings and every finding the ledger does not count. A CI job may later publish
summaries of master trees as `hoglint summary export` files (the whole summary in one zstd JSON
file) for readers to import by `Envelope.key`; nothing here fetches or publishes.

Pruning: the first store use of each day removes every file under the root that is older than
30 days, then the oldest files until the root holds 1 GB at most. A summary hit refreshes the
file's time, so a summary in use stays. A fact cache entry that goes away is only a re-parse.
The first process of the day to create that day's mark (`O_EXCL`) prunes; the others do not.

The store is a cache, so it never fails a run (D28): an I/O error in a read, a write or the
prune warns once on stderr, and the run goes on without the store. A file that is not there is
a miss, not an error.
"""

from __future__ import annotations

import os
import time
import shutil
import hashlib
import datetime
import tempfile
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import orjson
from compression import zstd

from hoglint.facts import BINARY_NAME
from hoglint.ledger.cache import (
    EXTRACTOR_STORE,
    EXTRACTORS,
    FACTS as FACTS,  # the replay reads the store directories from here
    MEMO as MEMO,
    STORES,
    SUMMARIES,
    SUMMARY_STORE,
    RunStores,
    StoreList,
    cache_root,
    write_atomic,
)
from hoglint.ledger.envelope import NATIVE, PROTOCOL, BinaryIds, EngineError, Envelope
from hoglint.ledger.scan import ALL_SECTIONS, ZSTD_LEVEL, ProtocolError, ScanSummary

ENVELOPE_SECTION = "envelope"
SECTION_DIGEST_CHARS = 32
MAX_BYTES = 1024**3
MAX_AGE = datetime.timedelta(days=30)
PRUNED_MARK = ".pruned-"  # then the day, one mark per day


@dataclass(frozen=True)
class Walk:
    """Every file, as (modified time, size, path), and every directory under `top`, from one walk:
    a prune reads sizes, ages and empty directories from it instead of walking again."""

    top: Path
    files: list[tuple[float, int, Path]]
    directories: list[Path]

    @staticmethod
    def of(top: Path, skipped: Collection[Path] = ()) -> Walk:
        """The walk of `top`, without the `skipped` directories and everything under them."""
        files = []
        directories = []
        for parent, subdirectories, names in os.walk(top):
            here = Path(parent)
            directories.append(here)
            subdirectories[:] = [name for name in subdirectories if here / name not in skipped]
            for name in names:
                path = here / name
                try:
                    stat = path.stat()
                except OSError:
                    continue
                files.append((stat.st_mtime, stat.st_size, path))
        return Walk(top, files, directories)

    def remove_empty_directories(self, cutoff: float, emptied: set[Path] | None = None) -> None:
        """Removes the empty directories under `top`, deepest first, that nothing changed since
        `cutoff`: a run about to write into a new one keeps it. With `emptied` (the directories a
        prune emptied), those go whatever their age, and so does each parent a removal empties."""
        for directory in sorted(self.directories, key=lambda path: len(path.parts), reverse=True):
            if directory == self.top:
                continue
            try:
                if any(directory.iterdir()):
                    continue
                if (emptied is None or directory not in emptied) and directory.stat().st_mtime >= cutoff:
                    continue
                directory.rmdir()
            except OSError:
                continue
            if emptied is not None:
                emptied.add(directory.parent)


class CacheRoot:
    """Keeps the cache root within its age and size bounds, once a day. A store that prunes
    itself (`StoreList.pruned_apart`: the fact cache and the pack memo, `hoglint/native/factdb.py`)
    deletes a database only with its `-wal` and `-shm` files, so this one never walks it."""

    def __init__(
        self,
        root: Path,
        max_bytes: int = MAX_BYTES,
        max_age: datetime.timedelta = MAX_AGE,
        listed: StoreList = STORES,
    ) -> None:
        self.root = root
        self.max_bytes = max_bytes
        self.max_age = max_age
        self.listed = listed

    def claim(self, stamp: str) -> bool:
        """Creates the day's mark; False when another run created it first, or when a run of a
        later day pruned already (a run started before midnight that ends after it). Only marks
        of earlier days go."""
        self.root.mkdir(parents=True, exist_ok=True)
        mark = f"{PRUNED_MARK}{stamp}"
        if any(other.name > mark for other in self.root.glob(f"{PRUNED_MARK}*")):
            return False
        try:
            os.close(os.open(self.root / mark, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        except FileExistsError:
            return False
        for old in self.root.glob(f"{PRUNED_MARK}*"):
            if old.name < mark:
                old.unlink(missing_ok=True)
        return True

    def prune_daily(self, today: datetime.date | None = None) -> bool:
        """Prunes when this run is the first of the day to claim the prune."""
        if not self.claim((today or datetime.date.today()).isoformat()):
            return False
        self.prune()
        return True

    def prune(self, now: float | None = None) -> None:
        """Deletes the files older than the age bound, then the oldest until the root is within
        the size bound, then the empty directories made before the age bound. The marks stay."""
        cutoff = (now or time.time()) - self.max_age.total_seconds()
        walk = Walk.of(self.root, skipped=tuple(self.root / directory for directory in self.listed.pruned_apart()))
        kept = []
        for modified, size, path in walk.files:
            if path.parent == self.root and path.name.startswith(PRUNED_MARK):
                continue
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
        walk.remove_empty_directories(cutoff)


class SummaryStore:
    """`broken` is set by the first I/O error: from then on every read misses and nothing is
    written."""

    def __init__(self, root: Path | None = None, stores: RunStores | None = None) -> None:
        self.stores = stores or RunStores.current()
        self.cache = CacheRoot(root or cache_root(), listed=self.stores.listed)
        self.root = self.cache.root / SUMMARIES
        self.pruned = False
        self.broken = False
        # Sections decoded in this process by file, whose name is their digest: a check reads
        # the same ones for its baseline and for its stored summary.
        self.sections: dict[Path, dict[str, Any]] = {}

    def fail(self, what: str, error: OSError) -> None:
        self.broken = True
        self.stores.fail(SUMMARY_STORE.name, error, what)

    def prune_once(self) -> None:
        if self.pruned or self.broken:
            return
        self.pruned = True
        try:
            self.cache.prune_daily()
        except OSError as error:
            self.fail("pruning", error)

    @staticmethod
    def touch(path: Path) -> None:
        """Refreshes a file's time, so the pruner keeps it. A file pruned meanwhile needs none."""
        try:
            os.utime(path)
        except FileNotFoundError:
            return

    def path(self, key: Envelope) -> Path:
        return self.root / key.engine / key.inputs / f"{key.policy}-{key.framework}.json"

    def section_path(self, key: Envelope, digest: str) -> Path:
        return self.root / key.engine / "sections" / f"{digest}.zst"

    def get(self, key: Envelope, sections: frozenset[str] = ALL_SECTIONS) -> ScanSummary | None:
        """The stored summary with exactly this key, holding `sections` and the envelope, else None."""
        self.prune_once()
        if not key.inputs or self.broken:
            return None
        self.stores.opened(SUMMARY_STORE.name)
        path = self.path(key)
        try:
            manifest = orjson.loads(path.read_bytes())
            parts = {ENVELOPE_SECTION: manifest["sections"][ENVELOPE_SECTION]}
            for name in sorted(sections - {ENVELOPE_SECTION}):
                parts[name] = self.read_section(key, manifest["sections"][name])
            summary = ScanSummary.from_sections(parts)
            if summary.envelope != key:
                return None
            self.touch(path)
        except FileNotFoundError:
            return None
        except OSError as error:
            self.fail("reading a summary", error)
            return None
        except (KeyError, TypeError, ValueError, ProtocolError, zstd.ZstdError):
            return None
        return summary

    def read_section(self, key: Envelope, digest: str) -> dict[str, Any]:
        path = self.section_path(key, digest)
        found = self.sections.get(path)
        if found is not None:
            return found
        raw = zstd.decompress(path.read_bytes())
        if hashlib.sha256(raw).hexdigest()[:SECTION_DIGEST_CHARS] != digest:
            raise ValueError(f"{path} does not hold the section its name says")
        self.touch(path)
        part = orjson.loads(raw)
        if not isinstance(part, dict):
            raise ValueError(f"{path} is not a summary section")
        self.sections[path] = part
        return part

    def put(self, key: Envelope, summary: ScanSummary) -> Path | None:
        """Stores a summary under its own envelope. A summary with no inputs digest is not stored,
        and nor is one of a run that could not read a source: it holds less than the tree. Only
        the sections no stored summary holds yet are written."""
        if summary.envelope != key:
            raise ValueError(f"a summary of {summary.envelope} cannot be stored under {key}")
        self.prune_once()
        if not key.inputs or summary.unread or self.broken:
            return None
        self.stores.opened(SUMMARY_STORE.name)
        try:
            return self.write(key, summary)
        except OSError as error:
            self.fail("writing a summary", error)
            return None

    def write(self, key: Envelope, summary: ScanSummary) -> Path:
        parts = summary.to_sections()
        manifest: dict[str, Any] = {ENVELOPE_SECTION: parts.pop(ENVELOPE_SECTION)}
        for name, part in parts.items():
            raw = orjson.dumps(part)
            digest = hashlib.sha256(raw).hexdigest()[:SECTION_DIGEST_CHARS]
            section = self.section_path(key, digest)
            if section.is_file():
                self.touch(section)
            else:
                write_atomic(section, zstd.compress(raw, ZSTD_LEVEL))
            manifest[name] = digest
        path = self.path(key)
        write_atomic(path, orjson.dumps({"protocol": PROTOCOL, "sections": manifest}))
        return path


class ExtractorStore:
    def __init__(self, root: Path | None = None, stores: RunStores | None = None) -> None:
        self.root = root or cache_root() / EXTRACTORS
        self.stores = stores or RunStores.current()

    def path(self, native_id: str) -> Path:
        return self.root / native_id / BINARY_NAME

    def find(self, native_id: str) -> Path | None:
        path = self.path(native_id)
        self.stores.opened(EXTRACTOR_STORE.name)
        if not path.is_file():
            return None
        try:
            if BinaryIds.of(path, NATIVE) != native_id:
                return None
        except EngineError:
            return None
        SummaryStore.touch(path)
        return path

    def keep(self, binary: Path, native_id: str) -> None:
        """Copies a binary in under its native id, unless one is already there. A cache that
        cannot take it only means a later base engine is not found there."""
        path = self.path(native_id)
        self.stores.opened(EXTRACTOR_STORE.name)
        if path.is_file():
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".extractor-")
            os.close(handle)
            shutil.copy(binary, temporary)
            Path(temporary).replace(path)
        except OSError as error:
            self.stores.fail(EXTRACTOR_STORE.name, error, "keeping the extractor")
