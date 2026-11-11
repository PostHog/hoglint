"""The hoglint cache root, and answers stored by the content they depend on.

The root is `$XDG_CACHE_HOME/hoglint`, else `~/.cache/hoglint` (see store.py for what it holds).

An answer store keeps one kind of answer that depends only on what its key names: the blob ids
of the files it reads, or the state of the directories it walks, and the code that computes it.
It is a cache (D28): an entry that is missing, not whole, or does not match its checksum is a
miss; the caller computes the answer again and puts it, which replaces the entry. An I/O error
warns once and is a miss. Entries live under `answers/<kind>/`, inside the root's size and age
bounds (see `CacheRoot` in store.py). `HOGLINT_NO_ANSWER_CACHE=1` turns every answer store off,
so a run computes everything.
"""

from __future__ import annotations

import os
import sys
import hashlib
import tempfile
from pathlib import Path
from typing import Any

import orjson

OFF_SWITCH = "HOGLINT_NO_ANSWER_CACHE"
ANSWERS = "answers"
CHECKSUM_CHARS = 64


def cache_root() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "hoglint"


class CacheWarning:
    """One stderr line per process for a cache that cannot be used."""

    warned = False

    @staticmethod
    def warn(what: str, error: OSError) -> None:
        if CacheWarning.warned:
            return
        CacheWarning.warned = True
        sys.stderr.write(f"hoglint: cannot use the cache ({what}: {error}), so this run goes on without it\n")


def write_atomic(path: Path, content: bytes) -> None:
    """Writes through a temporary file in the same directory, so a reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    with os.fdopen(handle, "wb") as out:
        out.write(content)
    Path(temporary).replace(path)


def answer_key(*parts: str | bytes) -> str:
    """A key made of `parts`, in order."""
    hasher = hashlib.sha256()
    for part in parts:
        hasher.update(part.encode() if isinstance(part, str) else part)
        hasher.update(b"\0")
    return hasher.hexdigest()


class AnswerStore:
    """Answers of one kind by key. An entry is the sha256 of its JSON body, a newline, and the body."""

    def __init__(self, kind: str, root: Path | None = None) -> None:
        self.kind = kind
        self.root = root

    @staticmethod
    def off() -> bool:
        return bool(os.environ.get(OFF_SWITCH))

    def path(self, key: str) -> Path:
        return (self.root or cache_root()) / ANSWERS / self.kind / f"{key}.json"

    def get(self, key: str) -> Any | None:
        """The stored answer; None for a miss."""
        if self.off():
            return None
        path = self.path(key)
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as error:
            CacheWarning.warn(f"reading a stored {self.kind} answer", error)
            return None
        checksum, body = data[:CHECKSUM_CHARS], data[CHECKSUM_CHARS + 1 :]
        if data[CHECKSUM_CHARS : CHECKSUM_CHARS + 1] != b"\n" or hashlib.sha256(body).hexdigest().encode() != checksum:
            return None
        try:
            answer = orjson.loads(body)
        except orjson.JSONDecodeError:
            return None
        self.touch(path)
        return answer

    def put(self, key: str, answer: Any) -> None:
        if self.off():
            return
        body = orjson.dumps(answer)
        try:
            write_atomic(self.path(key), hashlib.sha256(body).hexdigest().encode() + b"\n" + body)
        except OSError as error:
            CacheWarning.warn(f"storing a {self.kind} answer", error)

    @staticmethod
    def touch(path: Path) -> None:
        """Refreshes the entry's time, so the pruner keeps an answer in use."""
        try:
            os.utime(path)
        except OSError:
            return
