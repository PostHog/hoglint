"""Pack steps as items, and the reads each item makes.

A pack step may run as items: one per file, say, plus folds over the whole repo. Each item runs
inside a frame of the tracer, and every read the pack reader answers while it runs is noted in
that frame: a question (a kind and a key) and a digest of its answer, "not found" included. A
memo that items share (`TracedMemo`) gives each item that takes an entry the reads the entry was
computed from. So an item's reads are everything its output depends on: when every answer is the
same in another snapshot, the item would ask the same questions in the same order and make the
same output.

A file's facts are read so often that a frame notes the file alone (`Frame.files`); the answer is
the file's content key, which the store looks up when it writes the item.

The tracer is one per process (`TRACER`). With no frame open, a read costs one check.
"""

from __future__ import annotations

import os
import time
import hashlib
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol

import orjson

from hoglint.model import Edge
from hoglint.plugins import Refinement, Refinements

Output = Edge | Refinement | Refinements
Read = tuple[str, str]

# The read of a file's facts, noted by file id; its answer is the file's content key.
FILE = "file"
# Test only: reads of this kind are left out of every trace, so a replay must find the miss.
DROP_ENV = "HOGLINT_PACK_MEMO_DROP"
DIGEST_BYTES = 12


def digest(answer: object) -> str:
    """A digest of an answer's `repr`. Callers pass answers whose repr does not depend on the hash
    seed: no sets, or sets made sorted lists first."""
    return hashlib.blake2b(repr(answer).encode(), digest_size=DIGEST_BYTES).hexdigest()


def key_of(*parts: object) -> str:
    """A read's key from its parts: text as it is, other values as JSON, joined by NUL."""
    return "\0".join(part if isinstance(part, str) else orjson.dumps(part).decode() for part in parts)


def edge_form(edge: Edge) -> tuple[object, ...]:
    """An edge as an answer: every field but its run-local handle, contexts sorted."""
    return (
        edge.kind,
        edge.site,
        edge.surface,
        edge.targets,
        edge.resolution.value,
        sorted(edge.contexts),
        edge.producer,
        edge.via,
        edge.use,
        edge.shape,
        edge.shape_detail,
        edge.attrs,
    )


@dataclass(frozen=False, slots=True)
class Frame:
    """The reads of one item, or of one memo entry: questions with digested answers, and the ids
    of the files whose facts it read."""

    reads: set[Read] = field(default_factory=set)
    files: set[int] = field(default_factory=set)

    def add(self, other: Frame) -> None:
        self.reads |= other.reads
        self.files |= other.files


class Tracer:
    """Notes what the running item reads. `answers` keeps the digest of each answer, once per run:
    an answer does not change while the packs run, except an edge query's, whose key names the
    step it ran in."""

    def __init__(self) -> None:
        self.frames: list[Frame] = []
        self.answers: dict[Read, str] = {}
        self.dropped = os.environ.get(DROP_ENV, "")
        self.step = ""  # the running step, which an edge query's key names

    def note(self, kind: str, key: str, answer: object) -> None:
        if not self.frames or kind == self.dropped:
            return
        read = (kind, key)
        self.frames[-1].reads.add(read)
        if read not in self.answers:
            self.answers[read] = digest(answer)

    def note_lazy(self, kind: str, key: str, answer: Callable[[], object]) -> None:
        """`note` for an answer that costs time to build: it is built only once per run."""
        if not self.frames or kind == self.dropped:
            return
        read = (kind, key)
        self.frames[-1].reads.add(read)
        if read not in self.answers:
            self.answers[read] = digest(answer())

    def note_file(self, file: int) -> None:
        if self.frames and self.dropped != FILE:
            self.frames[-1].files.add(file)

    def merge(self, frame: Frame) -> None:
        if self.frames:
            self.frames[-1].add(frame)

    @contextmanager
    def frame(self) -> Iterator[Frame]:
        """A frame for one item or memo entry; its reads also count for the frame around it."""
        frame = Frame()
        self.frames.append(frame)
        try:
            yield frame
        finally:
            self.frames.pop()
            self.merge(frame)

    def reset(self) -> None:
        self.frames.clear()
        self.answers.clear()


TRACER = Tracer()


class TracedMemo[K, V]:
    """A memo that items share. An entry keeps the reads it was computed from, and an item that
    takes the entry gets those reads too, so the memo hides no read."""

    def __init__(self) -> None:
        self.entries: dict[K, tuple[V, Frame | None]] = {}

    def get(self, key: K, compute: Callable[[], V]) -> V:
        found = self.entries.get(key)
        if found is not None:
            value, reads = found
            if reads is not None:
                TRACER.merge(reads)
            return value
        if not TRACER.frames:
            value = compute()
            self.entries[key] = (value, None)
            return value
        with TRACER.frame() as reads:
            value = compute()
        self.entries[key] = (value, reads)
        return value

    def __contains__(self, key: object) -> bool:
        return key in self.entries


@dataclass(frozen=True, slots=True)
class ItemOutput:
    """What one item makes: edges and refinements in order, and data the step keeps (`take`)."""

    outputs: list[Output]
    data: Any = None


class StepItems(Protocol):
    """A step's items. `folds` run one after another, each item's data taken before the next one
    runs, so a later item may read what an earlier one built. The items of `keys` read no other
    item of the step: they run after the folds, `prepare` first gets the keys that will run, and
    their data is taken in key order. A step's output is the folds' outputs, then the keys'
    outputs, then `finish`."""

    def folds(self) -> Sequence[str]: ...

    def keys(self) -> Sequence[str]: ...

    def prepare(self, keys: Sequence[str]) -> None: ...

    def run_item(self, key: str) -> ItemOutput: ...

    def take(self, key: str, data: Any) -> None: ...

    def finish(self) -> Iterable[Output]: ...


class Items:
    """A base for `StepItems`: no folds, nothing to prepare, take or finish."""

    def folds(self) -> Sequence[str]:
        return ()

    def keys(self) -> Sequence[str]:
        return ()

    def prepare(self, keys: Sequence[str]) -> None:
        return None

    def run_item(self, key: str) -> ItemOutput:
        raise NotImplementedError

    def take(self, key: str, data: Any) -> None:
        return None

    def finish(self) -> Iterable[Output]:
        return ()


MODE_ENV = "HOGLINT_PACK_MEMO"
STATS_ENV = "HOGLINT_PACK_MEMO_STATS"
OFF = "off"
TRACE = "trace"


@dataclass(frozen=False, slots=True)
class StepStats:
    """What one step's items did, for `HOGLINT_PACK_MEMO_STATS`: the items that ran and their
    read counts, the items reused, and the seconds spent running and checking items."""

    name: str
    reads: list[int] = field(default_factory=list)
    files: list[int] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)
    reused: int = 0
    run_wall: float = 0.0
    run_cpu: float = 0.0
    check_wall: float = 0.0
    check_cpu: float = 0.0

    def row(self) -> dict[str, Any]:
        counts = [r + f for r, f in zip(self.reads, self.files, strict=True)]
        worst = sorted(zip(counts, self.files, self.keys, strict=True), reverse=True)[:5]
        return {
            "step": self.name,
            "worst": worst,
            "ran": len(self.reads),
            "reused": self.reused,
            "reads": sorted(r + f for r, f in zip(self.reads, self.files, strict=True)),
            "files": sorted(self.files),
            "run": [round(self.run_wall, 4), round(self.run_cpu, 4)],
            "check": [round(self.check_wall, 4), round(self.check_cpu, 4)],
        }


class StepMemo:
    """One step's view of the memo: the stored output of an item whose reads still hold, and the
    item that ran, with its reads, to store."""

    def __init__(self, name: str, stats: StepStats | None) -> None:
        self.name = name
        self.stats = stats

    def stored(self, key: str) -> ItemOutput | None:
        return None

    def keep(self, key: str, reads: Frame, result: ItemOutput) -> None:
        if self.stats is not None:
            self.stats.reads.append(len(reads.reads))
            self.stats.files.append(len(reads.files))
            self.stats.keys.append(key)


class PackMemo:
    """The memo of one run's packs. `HOGLINT_PACK_MEMO=off` runs items with no frames, reads and
    writes nothing; `trace` runs them in frames and stores nothing. `stats` names a file that
    gets one JSON line per step."""

    def __init__(self, stats: str = "") -> None:
        self.stats_path = stats
        self.stats: list[StepStats] = []

    @staticmethod
    def from_environment() -> PackMemo | None:
        mode = os.environ.get(MODE_ENV, "")
        if mode == OFF:
            return None
        return PackMemo(os.environ.get(STATS_ENV, ""))

    def step(self, name: str) -> StepMemo:
        stats = None
        if self.stats_path:
            stats = StepStats(name)
            self.stats.append(stats)
        return StepMemo(name, stats)

    def write_stats(self) -> None:
        if not self.stats_path or not self.stats:
            return
        with open(self.stats_path, "ab") as handle:
            for stats in self.stats:
                handle.write(orjson.dumps(stats.row()) + b"\n")
        self.stats.clear()


class ItemRun:
    """Runs one step's items in order, each in a frame of its own when there is a memo."""

    def __init__(self, items: StepItems, memo: StepMemo | None) -> None:
        self.items = items
        self.memo = memo

    def run(self, key: str) -> ItemOutput:
        if self.memo is None:
            return self.items.run_item(key)
        stats = self.memo.stats
        wall, cpu = (time.perf_counter(), time.process_time()) if stats is not None else (0.0, 0.0)
        with TRACER.frame() as reads:
            result = self.items.run_item(key)
        self.memo.keep(key, reads, result)
        if stats is not None:
            stats.run_wall += time.perf_counter() - wall
            stats.run_cpu += time.process_time() - cpu
        return result

    def reused(self, key: str) -> ItemOutput | None:
        if self.memo is None:
            return None
        stats = self.memo.stats
        wall, cpu = (time.perf_counter(), time.process_time()) if stats is not None else (0.0, 0.0)
        found = self.memo.stored(key)
        if stats is not None:
            stats.check_wall += time.perf_counter() - wall
            stats.check_cpu += time.process_time() - cpu
            stats.reused += found is not None
        return found

    def outputs(self) -> list[Output]:
        out: list[Output] = []
        for key in self.items.folds():
            result = self.reused(key) or self.run(key)
            self.items.take(key, result.data)
            out.extend(result.outputs)
        keys = list(self.items.keys())
        found: dict[str, ItemOutput] = {}
        for key in keys:
            hit = self.reused(key)
            if hit is not None:
                found[key] = hit
        self.items.prepare([key for key in keys if key not in found])
        for key in keys:
            result = found.get(key) or self.run(key)
            self.items.take(key, result.data)
            out.extend(result.outputs)
        out.extend(self.items.finish())
        return out


class TracedMapping[K, V]:
    """A read-only view of a map that items read: each lookup is a read of `kind`, keyed by
    `key(k)` and answered by `form(value)`, or the value itself when there is no form; an absent
    key answers None. A walk over the whole map is one read of every entry."""

    def __init__(
        self,
        kind: str,
        entries: dict[K, V],
        key: Callable[[K], str] = str,
        form: Callable[[V], object] | None = None,
    ) -> None:
        self.kind = kind
        self.entries = entries
        self.key = key
        self.form = form

    def answer(self, found: V | None) -> object:
        if found is None or self.form is None:
            return found
        return self.form(found)

    def get(self, key: K, default: V | None = None) -> V | None:
        found = self.entries.get(key)
        if TRACER.frames:
            TRACER.note(self.kind, self.key(key), self.answer(found))
        return default if found is None else found

    def __contains__(self, key: K) -> bool:
        return self.get(key) is not None

    def __getitem__(self, key: K) -> V:
        found = self.get(key)
        if found is None:
            raise KeyError(key)
        return found

    def note_all(self) -> None:
        if TRACER.frames:
            TRACER.note_lazy(self.kind, "*", lambda: [(self.key(k), self.answer(v)) for k, v in self.entries.items()])

    def keys(self) -> Iterable[K]:
        self.note_all()
        return self.entries.keys()

    def values(self) -> Iterable[V]:
        self.note_all()
        return self.entries.values()

    def items(self) -> Iterable[tuple[K, V]]:
        self.note_all()
        return self.entries.items()

    def __iter__(self) -> Iterator[K]:
        self.note_all()
        return iter(self.entries)

    def __len__(self) -> int:
        self.note_all()
        return len(self.entries)
