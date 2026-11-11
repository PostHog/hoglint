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

With a store (hoglint/native/memo_store.py), each item is kept with its reads and output, and a
later run takes the stored output when every read has the same answer again (`Tracer.current`):
the reader answers its own reads again, a fold answers the reads of its state.

The tracer is one per process (`TRACER`). With no frame open, a read costs one check.
"""

from __future__ import annotations

import os
import sys
import time
import zlib
import hashlib
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Protocol

import orjson

from hoglint.model import LANGUAGE_PYTHON, Context, Edge, EntityId, Resolution, Site
from hoglint.plugins import Refinement, Refinements

Output = Edge | Refinement | Refinements
Read = tuple[str, str]

# The read of a file's facts, noted by file id; its answer is the file's content key.
FILE = "file"
# Test only: reads of these kinds (comma-separated, or "*" for every kind) are left out of every
# trace, so a replay must find the miss.
DROP_ENV = "HOGLINT_PACK_MEMO_DROP"
DIGEST_BYTES = 12
ALL_KINDS = "*"
# A step's stored state is split by item key into this many parts.
PARTS = 64
ZLIB_LEVEL = 1


@lru_cache(maxsize=1 << 16)
def text_digest(text: str) -> str:
    """Answers repeat (None, a label, an empty list), so a text's digest is kept."""
    return hashlib.blake2b(text.encode(), digest_size=DIGEST_BYTES).hexdigest()


def digest(answer: object) -> str:
    """A digest of an answer's `repr`. Callers pass answers whose repr does not depend on the hash
    seed: no sets, or sets made sorted lists first."""
    return text_digest(repr(answer))


def key_of(*parts: object) -> str:
    """A read's key from its parts, as a JSON list: `parts_of` reads it back."""
    return orjson.dumps(parts).decode()


def parts_of(key: str) -> list[Any]:
    return orjson.loads(key)


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


class FileKeys(Protocol):
    """A snapshot's files by id: the path, and the content key a file read is answered by (None
    for a file read from no stable content, which no stored read can match)."""

    def path(self, file: int) -> str: ...

    def key(self, path: str) -> str | None: ...


class FileKeysMap:
    """`FileKeys` over a list of paths and a map of content keys by path."""

    def __init__(self, paths: list[str], keys: dict[str, str | None]) -> None:
        self.paths = paths
        self.keys = keys

    def path(self, file: int) -> str:
        return self.paths[file]

    def key(self, path: str) -> str | None:
        return self.keys.get(path)


class AllKinds(frozenset[str]):
    """Every read kind, for `HOGLINT_PACK_MEMO_DROP=*`."""

    def __contains__(self, kind: object) -> bool:
        return True


ALL_DROPPED = AllKinds()


class Tracer:
    """Notes what the running item reads. `answers` keeps the digest of each answer, once per run:
    an answer does not change while the packs run, except an edge query's, whose key names the
    step it ran in.

    `verifiers` answer a read again in this run, as a digest, so a stored item is checked: the
    reader registers its kinds, a pack the kinds of the state it folds. A read of a kind no one
    answers does not hold."""

    def __init__(self) -> None:
        self.frames: list[Frame] = []
        self.answers: dict[Read, str] = {}
        dropped = os.environ.get(DROP_ENV, "")
        self.dropped = ALL_DROPPED if dropped == ALL_KINDS else frozenset(filter(None, dropped.split(",")))
        self.step = ""  # the running step, which an edge query's key names
        self.verifiers: dict[str, Callable[[str], str | None]] = {}
        self.maps: dict[str, tuple[dict[str, str], str]] = {}
        self.files: FileKeys | None = None

    def answer_with(self, kind: str, answer: Callable[[str], object]) -> None:
        """Answers reads of `kind` again by `answer(key)`, the same answer its note digests."""
        self.verifiers[kind] = lambda key: digest(answer(key))

    def verify_by_map(self, kind: str, digests: dict[str, str], default: str) -> None:
        """Answers reads of `kind` again from a map of digests by key, `default` for a key it
        lacks: what a part checks without a call per read."""
        self.maps[kind] = (digests, default)
        self.verifiers[kind] = lambda key: digests.get(key, default)

    def current(self, kind: str, key: str) -> str | None:
        """The digest of a read's answer in this run; None when no one can answer it."""
        read = (kind, key)
        found = self.answers.get(read)
        if found is not None:
            return found
        if kind == FILE:
            return self.files.key(key) if self.files is not None else None
        verifier = self.verifiers.get(kind)
        if verifier is None:
            return None
        try:
            found = verifier(key)
        except Exception:  # noqa: BLE001 - a read that cannot be answered again does not hold
            return None
        if found is not None:
            self.answers[read] = found
        return found

    def stored_reads(self, frame: Frame) -> list[list[str]] | None:
        """A frame's reads as (kind, key, digest), files by path and content key; None when one
        has no digest, so the item is not stored."""
        out: list[list[str]] = []
        for kind, key in frame.reads:
            found = self.answers.get((kind, key))
            if found is None:
                return None
            out.append([kind, key, found])
        if frame.files and self.files is None:
            return None
        for file in frame.files:
            assert self.files is not None
            path = self.files.path(file)
            content = self.files.key(path)
            if content is None:
                return None
            out.append([FILE, path, content])
        return out

    def note(self, kind: str, key: str, answer: object) -> None:
        if not self.frames or kind in self.dropped:
            return
        read = (kind, key)
        self.frames[-1].reads.add(read)
        if read not in self.answers:
            self.answers[read] = digest(answer)

    def note_lazy(self, kind: str, key: str, answer: Callable[[], object]) -> None:
        """`note` for an answer that costs time to build: it is built only once per run."""
        if not self.frames or kind in self.dropped:
            return
        read = (kind, key)
        self.frames[-1].reads.add(read)
        if read not in self.answers:
            self.answers[read] = digest(answer())

    def note_file(self, file: int) -> None:
        if self.frames and FILE not in self.dropped:
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
        self.verifiers.clear()
        self.maps.clear()
        self.files = None
        self.step = ""


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


# The output of most items: no edge and no data, shared so a reused item makes no object.
NOTHING = ItemOutput([])


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
    changed: dict[str, int] = field(default_factory=dict)  # read kind -> stored reads whose answer changed

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
            "changed": self.changed,
        }


def encode_outputs(outputs: list[Output]) -> list[Any] | None:
    """Edges as JSON rows, an edge object met again as the index of its first row; None when an
    output is a refinement, which names an edge by its run-local handle."""
    rows: list[Any] = []
    seen: dict[int, int] = {}
    for output in outputs:
        if not isinstance(output, Edge):
            return None
        at = seen.get(id(output))
        if at is not None:
            rows.append(at)
            continue
        seen[id(output)] = len(rows)
        site = output.site
        rows.append(
            [
                output.kind,
                [site.path, site.module, site.scope, site.line, site.column],
                output.surface,
                [[target.module, target.symbol] for target in output.targets],
                output.resolution.value,
                sorted(output.contexts),
                output.producer,
                [[hop.module, hop.symbol] for hop in output.via],
                output.use,
                output.shape,
                output.shape_detail,
                [list(pair) for pair in output.attrs],
            ]
        )
    return rows


def decode_outputs(rows: list[Any]) -> list[Output]:
    """The edges of `encode_outputs`, one object per row and the same object where it repeats."""
    out: list[Output] = []
    for row in rows:
        if isinstance(row, int):
            out.append(out[row])
            continue
        kind, site, surface, targets, resolution, contexts, producer, via, use, shape, detail, attrs = row
        out.append(
            Edge(
                kind,
                Site(*site),
                surface,
                tuple(EntityId(LANGUAGE_PYTHON, module, symbol) for module, symbol in targets),
                Resolution(resolution),
                frozenset(Context(context) for context in contexts),
                producer,
                tuple(EntityId(LANGUAGE_PYTHON, module, symbol) for module, symbol in via),
                use,
                shape,
                detail,
                tuple((name, value) for name, value in attrs),
            )
        )
    return out


class Part:
    """One part of a step's stored state: a table of reads, (kind, key, digest), and per item
    the indices of its reads, its encoded outputs and its data. Its reads are checked all at
    once, the first time an item of it is asked for."""

    def __init__(self, name: str, found: str, part: dict[str, Any]) -> None:
        self.name = name
        self.digest = found
        self.reads: list[list[str]] = part["reads"]
        self.items: dict[str, list[Any]] = part["items"]
        self.failing: set[int] | None = None

    def failed_reads(self) -> set[int]:
        """The indices of the reads whose answer changed: file reads and reads answered by a map
        of digests are checked with no call per read."""
        if self.failing is None:
            files = TRACER.files.keys if isinstance(TRACER.files, FileKeysMap) else None
            maps, current = TRACER.maps, TRACER.current
            failing = set()
            for at, (kind, key, wanted) in enumerate(self.reads):
                if kind == FILE and files is not None:
                    held = files.get(key) == wanted
                elif kind in maps:
                    digests, default = maps[kind]
                    held = digests.get(key, default) == wanted
                else:
                    held = current(kind, key) == wanted
                if not held:
                    failing.add(at)
            self.failing = failing
        return self.failing

    @staticmethod
    def output(entry: object, failing: set[int]) -> ItemOutput | None:
        """An entry's stored output when none of its reads failed; None otherwise, and for an
        entry that does not decode."""
        if not isinstance(entry, list) or len(entry) != 3:
            return None
        try:
            if not failing.isdisjoint(entry[0]):
                return None
            outputs, data = entry[1], entry[2]
            return ItemOutput(decode_outputs(outputs), data) if outputs or data is not None else NOTHING
        except Exception:  # noqa: BLE001 - a damaged entry is a miss: the item runs again
            return None

    def reused(self, key: str) -> ItemOutput | None:
        """One item's stored output, its own reads checked now: a fold is checked before the
        state it builds is taken, and the other items of its part read that state."""
        entry = self.items.get(key)
        try:
            indices = entry[0] if isinstance(entry, list) else []
            failing = {at for at in indices if TRACER.current(*self.reads[at][:2]) != self.reads[at][2]}
        except Exception:  # noqa: BLE001 - a damaged read table is a miss
            return None
        return self.output(entry, failing)


def part_of(key: str) -> str:
    """The part of a step's state an item is stored in, by its key."""
    return str(zlib.crc32(key.encode()) % PARTS)


def encode_part(part: dict[str, Any]) -> bytes:
    return zlib.compress(orjson.dumps(part), ZLIB_LEVEL)


class MemoStore(Protocol):
    def manifest(self, step: str) -> tuple[str, dict[str, str]] | None: ...

    def parts(self, digests: list[str]) -> dict[str, dict[str, Any]]: ...

    def save(self, written: list[Written]) -> None: ...


@dataclass(frozen=True, slots=True)
class Written:
    """What a step leaves to store: its new manifest (part -> digest) with the bytes of each new
    part, or None when the manifest it read still stands (`used`)."""

    step: str
    manifest: dict[str, str] | None
    parts: dict[str, bytes]
    used: str | None


class StepMemo:
    """One step's view of the memo: the stored output of an item whose reads still hold, and the
    item that ran, with its reads, to store. Its state is split in parts by item key, so a run
    that changes a few items writes only their parts. It reads the newest state only."""

    def __init__(self, name: str, key: str, store: MemoStore | None, stats: StepStats | None) -> None:
        self.name = name
        self.key = key
        self.store = store
        self.stats = stats
        self.manifest: tuple[str, dict[str, str]] | None = None
        self.loaded: dict[str, Part] | None = None
        self.reused: dict[str, Part] = {}  # item key -> the part its stored output came from
        self.fresh: dict[str, list[Any]] = {}  # item key -> the entry of an item that ran
        self.dirty: set[str] = set()  # parts that hold an item that ran

    def parts(self) -> dict[str, Part]:
        if self.loaded is None:
            self.loaded = {}
            if self.store is not None:
                self.manifest = self.store.manifest(self.key)
            if self.manifest is not None and self.store is not None:
                found = self.store.parts(sorted(set(self.manifest[1].values())))
                for name, digest_of in self.manifest[1].items():
                    stored = found.get(digest_of)
                    if stored is not None:
                        self.loaded[name] = Part(name, digest_of, stored)
        return self.loaded

    def stored(self, key: str) -> ItemOutput | None:
        part = self.parts().get(part_of(key))
        found = part.reused(key) if part is not None else None
        if found is not None:
            assert part is not None
            self.reused[key] = part
        return found

    def stored_all(self, keys: list[str]) -> dict[str, ItemOutput]:
        """`stored` for each of `keys`, part by part: the items whose stored output holds."""
        found: dict[str, ItemOutput] = {}
        wanted = set(keys)
        for part in self.parts().values():
            try:
                failing = part.failed_reads()
            except Exception:  # noqa: BLE001 - a damaged read table is a miss for the whole part
                continue
            if self.stats is not None:
                for at in failing:
                    kind = part.reads[at][0]
                    self.stats.changed[kind] = self.stats.changed.get(kind, 0) + 1
            for key, entry in part.items.items():
                if key not in wanted:
                    continue
                output = part.output(entry, failing)
                if output is not None:
                    found[key] = output
                    self.reused[key] = part
        return found

    def keep(self, key: str, reads: Frame, result: ItemOutput) -> ItemOutput:
        """Records an item that ran. Its data comes back as the store gives it, so a later take
        sees the same values whether the item ran or was reused."""
        if self.stats is not None:
            self.stats.reads.append(len(reads.reads))
            self.stats.files.append(len(reads.files))
            self.stats.keys.append(key)
        if self.store is None:
            return result
        self.dirty.add(part_of(key))
        stored = TRACER.stored_reads(reads)
        outputs = encode_outputs(result.outputs)
        try:
            data = orjson.loads(orjson.dumps(result.data))
        except TypeError:
            return result
        if stored is not None and outputs is not None:
            self.fresh[key] = [stored, outputs, data]
        return ItemOutput(result.outputs, data)

    def written(self) -> Written:
        """The step's new state, made once its items ran: a part that holds an item that ran,
        or lost an item, is made again; the rest keep their digests. The parts read are let go."""
        old = self.manifest[1] if self.manifest is not None else {}
        loaded = self.loaded or {}
        kept: dict[str, list[str]] = {}
        for key, part in self.reused.items():
            kept.setdefault(part.name, []).append(key)
        dirty = set(self.dirty)
        for name, part in loaded.items():
            if len(kept.get(name, ())) != len(part.items):
                dirty.add(name)
        for name in old:
            if name not in loaded:
                dirty.add(name)
        manifest: dict[str, str] = {}
        new: dict[str, bytes] = {}
        names = set(kept) | {part_of(key) for key in self.fresh}
        for name in sorted(names):
            if name not in dirty:
                manifest[name] = old[name]
                continue
            keys = [*kept.get(name, ()), *(key for key in self.fresh if part_of(key) == name)]
            body = encode_part(self.part_state(keys))
            found = hashlib.blake2b(body, digest_size=16).hexdigest()
            manifest[name] = found
            new[found] = body
        self.loaded = None
        self.reused.clear()
        self.fresh.clear()
        used = self.manifest[0] if self.manifest is not None else None
        if self.manifest is not None and manifest == old:
            return Written(self.key, None, {}, used)
        return Written(self.key, manifest, new, used)

    def part_state(self, keys: list[str]) -> dict[str, Any]:
        table: dict[tuple[str, ...], int] = {}
        reads: list[list[str]] = []
        items: dict[str, list[Any]] = {}
        for key in keys:
            part = self.reused.get(key)
            if part is None:
                triples, outputs, data = self.fresh[key]
            else:
                indices, outputs, data = part.items[key]
                triples = [part.reads[at] for at in indices]
            positions = []
            for triple in triples:
                found = table.get(tuple(triple))
                if found is None:
                    found = table[tuple(triple)] = len(reads)
                    reads.append(triple)
                positions.append(found)
            items[key] = [positions, outputs, data]
        return {"reads": reads, "items": items}


class PackMemo:
    """The memo of one run's packs. A step's states are stored under its name and `policy`, the
    digest of everything outside the snapshot its items depend on. `HOGLINT_PACK_MEMO=off` runs
    items with no frames, reads and writes nothing; `trace` runs them in frames and stores
    nothing. `stats` names a file that gets one JSON line per step."""

    def __init__(self, store: MemoStore | None, policy: str, stats: str = "") -> None:
        self.store = store
        self.policy = policy
        self.stats_path = stats
        self.stats: list[StepStats] = []
        self.written: list[Written] = []

    @staticmethod
    def mode() -> str:
        return os.environ.get(MODE_ENV, "")

    def step(self, name: str, stored: bool = True) -> StepMemo:
        """A step's memo; one that is not `stored` traces its items and keeps nothing."""
        stats = None
        if self.stats_path:
            stats = StepStats(name)
            self.stats.append(stats)
        return StepMemo(name, digest([self.policy, name]), self.store if stored else None, stats)

    def end_step(self, step: StepMemo) -> None:
        """Makes a step's new state once its items ran, so the parts it read are let go before
        the next step runs."""
        if step.store is not None:
            try:
                self.written.append(step.written())
            except Exception as error:  # noqa: BLE001 - the memo is a cache: it never fails a run
                sys.stderr.write(f"hoglint: the pack memo of a step could not be made, so it is not stored: {error}\n")

    def flush(self) -> None:
        """Writes the steps' new states, after every pack ran."""
        if self.store is not None:
            try:
                self.store.save(self.written)
            except Exception as error:  # noqa: BLE001 - the memo is a cache: it never fails a run
                sys.stderr.write(f"hoglint: the pack memo could not be written, so it is left as it was: {error}\n")
        self.written.clear()
        self.write_stats()

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
        result = self.memo.keep(key, reads, result)
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

    def reused_all(self, keys: list[str]) -> dict[str, ItemOutput]:
        if self.memo is None:
            return {}
        stats = self.memo.stats
        wall, cpu = (time.perf_counter(), time.process_time()) if stats is not None else (0.0, 0.0)
        found = self.memo.stored_all(keys)
        if stats is not None:
            stats.check_wall += time.perf_counter() - wall
            stats.check_cpu += time.process_time() - cpu
            stats.reused += len(found)
        return found

    def outputs(self) -> list[Output]:
        out: list[Output] = []
        for key in self.items.folds():
            result = self.reused(key) or self.run(key)
            self.items.take(key, result.data)
            out.extend(result.outputs)
        keys = list(self.items.keys())
        found = self.reused_all(keys)
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
        self.by_key: dict[str, K] | None = None

    def answer_again(self, key: str) -> object:
        """A read's answer from the map as it is now: what `answer_with` asks."""
        if key == "*":
            return [(self.key(k), self.answer(v)) for k, v in self.entries.items()]
        if self.by_key is None:
            self.by_key = {self.key(k): k for k in self.entries}
        found = self.by_key.get(key)
        return None if found is None else self.answer(self.entries[found])

    def answer_reads(self) -> None:
        """Answers this map's reads again in this run (`Tracer.answer_with`)."""
        TRACER.answer_with(self.kind, self.answer_again)

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
