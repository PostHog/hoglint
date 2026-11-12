"""The hoglint cache root, the stores of a run under it, and answers stored by the content they
depend on.

The root is `$XDG_CACHE_HOME/hoglint`, else `~/.cache/hoglint` (see store.py for what it holds).

`STORES` lists every store of a run once: its name, its directory under the root, whether its
entries are keyed by content alone (so an unrelated check may share them), its switch, its
warning and who prunes it. `RunStores` is that list in one run: which stores the environment
turned off, and which ones failed, opened or stayed unused. A command builds it once at its entry
and passes it down. What the list says about every store (the cold environment, the switches, the
directories the root's prune skips, the reuse record) comes from the list, never from a second
list of names.

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
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any, ClassVar

import orjson

from hoglint.core.memo import DROP_ENV, MODE_ENV, OFF, STATS_ENV, TRACE

# The directories of the stores under the cache root.
FACTS = "facts"
MEMO = "memo"
SUMMARIES = "summaries"
INPUTS = "inputs"
PACKS = "packs"
ANSWERS = "answers"
EXTRACTORS = "extractors"
OFF_SWITCH = "HOGLINT_NO_ANSWER_CACHE"
# Set to 1, the link records what each file's edges read from other files (`Link.write_traces`).
TRACE_ENV = "HOGLINT_LINK_TRACE"
CHECKSUM_CHARS = 64
# Who prunes a store's directory: the cache root's prune (store.py), or the store's own
# (hoglint/native/factdb.py), which deletes a database only with its `-wal` and `-shm` files.
ROOT_PRUNE = "root"
OWN_PRUNE = "own"
# The state of a store in one run.
STATE_OFF = "off"
STATE_FAILED = "failed"
STATE_OPEN = "open"
STATE_UNUSED = "unused"
CACHE_WARNING = "cannot use the cache ({what}: {error}), so this run goes on without it"


def cache_root() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "hoglint"


def write_atomic(path: Path, content: bytes) -> None:
    """Writes through a temporary file in the same directory, so a reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    with os.fdopen(handle, "wb") as out:
        out.write(content)
    Path(temporary).replace(path)


def answer_key(*parts: str | bytes, chars: int | None = None) -> str:
    """A key made of `parts`, in order; with `chars`, only its first `chars` hex digits."""
    hasher = hashlib.sha256()
    for part in parts:
        hasher.update(part.encode() if isinstance(part, str) else part)
        hasher.update(b"\0")
    return hasher.hexdigest()[:chars]


@dataclass(frozen=True)
class Store:
    """One store of a run, declared once. `shared`: its entries are keyed by content alone, so an
    unrelated check may share them. `warning` is the stderr line when it fails, with `{what}` and
    `{error}`. `switch` turns it off with a value in `off_values`, or with any value when
    `off_values` is empty; `off` is the value that a run which must reuse nothing sets."""

    name: str
    directory: str
    shared: bool
    warning: str
    pruner: str = ROOT_PRUNE
    switch: str = ""
    off: str = ""
    off_values: tuple[str, ...] = ()

    def is_off(self, environ: Mapping[str, str]) -> bool:
        if not self.switch:
            return False
        value = environ.get(self.switch, "")
        if not self.off_values:
            return bool(value)
        return value in self.off_values


@dataclass(frozen=True)
class Recording:
    """A switch that makes a run record what it did; it stores nothing a later run reuses."""

    name: str
    switch: str


@dataclass(frozen=True)
class StoreList:
    stores: tuple[Store, ...]
    recordings: tuple[Recording, ...]

    def named(self, name: str) -> Store:
        for store in self.stores:
            if store.name == name:
                return store
        raise KeyError(name)

    def cold_environment(self) -> dict[str, str]:
        """The environment that turns off every store that is not shared: what a check sets when
        it must reuse nothing that an earlier check stored."""
        return {store.switch: store.off for store in self.stores if store.switch and not store.shared}

    def switches(self) -> tuple[str, ...]:
        """Every switch that turns a store or a recording on or off."""
        names = [store.switch for store in self.stores if store.switch]
        names += [recording.switch for recording in self.recordings]
        return tuple(dict.fromkeys(names))

    def pruned_apart(self) -> tuple[str, ...]:
        """The directories of the stores that prune themselves, which the root's prune skips."""
        return tuple(store.directory for store in self.stores if store.pruner == OWN_PRUNE)


FACT_STORE = Store(
    "facts",
    FACTS,
    shared=True,
    warning="the fact cache is not usable, so this run extracts without it: {error}",
    pruner=OWN_PRUNE,
)
MEMO_STORE = Store(
    "pack-memo",
    MEMO,
    shared=False,
    warning="the pack memo is not usable, so this run runs every pack item and stores none: {error}",
    pruner=OWN_PRUNE,
    switch=MODE_ENV,
    off=OFF,
    off_values=(OFF, TRACE),
)
SUMMARY_STORE = Store("summaries", SUMMARIES, shared=False, warning=CACHE_WARNING)
INPUTS_STORE = Store("inputs", INPUTS, shared=True, warning=CACHE_WARNING)
PACKS_STORE = Store("packs", PACKS, shared=True, warning=CACHE_WARNING)
EXTRACTOR_STORE = Store("extractors", EXTRACTORS, shared=True, warning=CACHE_WARNING)
SITE_PACKAGES_ANSWERS = Store(
    "site-packages", f"{ANSWERS}/site-packages", shared=False, warning=CACHE_WARNING, switch=OFF_SWITCH, off="1"
)
TURBO_ANSWERS = Store("turbo", f"{ANSWERS}/turbo", shared=False, warning=CACHE_WARNING, switch=OFF_SWITCH, off="1")
ANSWER_STORES = (SITE_PACKAGES_ANSWERS, TURBO_ANSWERS)
STORES = StoreList(
    (
        FACT_STORE,
        MEMO_STORE,
        SUMMARY_STORE,
        INPUTS_STORE,
        PACKS_STORE,
        EXTRACTOR_STORE,
        SITE_PACKAGES_ANSWERS,
        TURBO_ANSWERS,
    ),
    (
        Recording("trace-dump", TRACE_ENV),
        Recording("pack-memo-stats", STATS_ENV),
        # Test only: leaves reads out of every kept pack item (`RunStores.memo_drop`).
        Recording("pack-memo-drop", DROP_ENV),
    ),
)


class RunStores:
    """The stores of one run: built once from the environment at a command's entry and passed
    down. Code the command cannot pass it to reads it with `current`. A failed store warns once
    per process with its own text, and a failure of one store does not silence another."""

    warned: ClassVar[set[str]] = set()
    running: ClassVar[RunStores | None] = None

    def __init__(self, environ: Mapping[str, str], listed: StoreList = STORES) -> None:
        self.listed = listed
        self.values = {switch: environ.get(switch, "") for switch in listed.switches()}
        self.states = {store.name: STATE_OFF if store.is_off(environ) else STATE_UNUSED for store in listed.stores}

    @staticmethod
    def of_environment() -> RunStores:
        return RunStores(os.environ)

    @staticmethod
    def current() -> RunStores:
        """The stores of the running command; outside one, those the environment sets now."""
        if RunStores.running is not None:
            return RunStores.running
        return RunStores.of_environment()

    @contextmanager
    def as_current(self) -> Iterator[RunStores]:
        """Makes these the stores `current` answers while a command runs."""
        before = RunStores.running
        RunStores.running = self
        try:
            yield self
        finally:
            RunStores.running = before

    def on(self, name: str) -> bool:
        return self.states[name] != STATE_OFF

    def opened(self, name: str) -> None:
        if self.states[name] == STATE_UNUSED:
            self.states[name] = STATE_OPEN

    def fail(self, name: str, error: Exception, what: str = "") -> None:
        if self.states[name] != STATE_OFF:
            self.states[name] = STATE_FAILED
        if name in RunStores.warned:
            return
        RunStores.warned.add(name)
        warning = self.listed.named(name).warning.format(what=what, error=error)
        sys.stderr.write(f"hoglint: {warning}\n")

    def record(self) -> dict[str, str]:
        """Each store's state in this run, for the reuse record of a check."""
        return dict(self.states)

    def memo_mode(self) -> str:
        """`HOGLINT_PACK_MEMO`: off, trace, or empty for a memo with a store."""
        return self.values[MODE_ENV]

    def memo_stats(self) -> str:
        """The file `HOGLINT_PACK_MEMO_STATS` names for the memo's step stats; empty for none."""
        return self.values[STATS_ENV]

    def memo_drop(self) -> str:
        """`HOGLINT_PACK_MEMO_DROP`, test only: the read kinds a kept pack item leaves out."""
        return self.values[DROP_ENV]

    def trace_on(self) -> bool:
        return self.values[TRACE_ENV] == "1"


class AnswerStore:
    """Answers of one kind by key. An entry is the sha256 of its JSON body, a newline, and the body.
    A key starts with the bytes of `code`, the module that computes the answer, and the
    interpreter's version."""

    def __init__(self, store: Store, code: str) -> None:
        self.store = store
        self.kind = Path(store.directory).name
        self.code = Path(code)

    @cached_property
    def code_bytes(self) -> bytes:
        return self.code.read_bytes()

    @staticmethod
    def off() -> bool:
        """Whether this run turned the answer stores off."""
        stores = RunStores.current()
        return not any(stores.on(store.name) for store in ANSWER_STORES)

    def key(self, *parts: str | bytes) -> str:
        return answer_key(self.code_bytes, sys.version, *parts)

    def path(self, key: str) -> Path:
        return cache_root() / self.store.directory / f"{key}.json"

    def get(self, key: str) -> Any | None:
        """The stored answer; None for a miss."""
        stores = RunStores.current()
        if not stores.on(self.store.name):
            return None
        stores.opened(self.store.name)
        path = self.path(key)
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as error:
            stores.fail(self.store.name, error, f"reading a stored {self.kind} answer")
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
        stores = RunStores.current()
        if not stores.on(self.store.name):
            return
        stores.opened(self.store.name)
        body = orjson.dumps(answer)
        try:
            write_atomic(self.path(key), hashlib.sha256(body).hexdigest().encode() + b"\n" + body)
        except OSError as error:
            stores.fail(self.store.name, error, f"storing a {self.kind} answer")

    @staticmethod
    def touch(path: Path) -> None:
        """Refreshes the entry's time, so the pruner keeps an answer in use."""
        try:
            os.utime(path)
        except OSError:
            return
