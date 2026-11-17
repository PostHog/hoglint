"""The pack memo reuses an item only while every read it made has the same answer, and a damaged
store is a miss, never a failed run."""

from __future__ import annotations

import gc
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from hoglint.core.config import CoreConfig
from hoglint.core.memo import FILE, MEMO_FILL, ItemOutput, ItemRun, Items, PackMemo, TracedMap, Tracer, part_of
from hoglint.ledger.cache import FACT_STORE, MEMO, RunStores
from hoglint.model import LANGUAGE_PYTHON, Context, Edge, EntityId, Resolution, Site
from hoglint.native.analysis import LITE_KINDS
from hoglint.native.build import NativeBuild
from hoglint.native.config import link_config
from hoglint.native.graph import EdgeMaker
from hoglint.native.memo_store import MemoStore
from hoglint.native.snapshot import BlobSnapshot
from hoglint.packs.django.models import ModelEntry
from rules.test_compare import git_in, write_files

from native.require import native_module

KIND = "test.answer"


class World(Items):
    """One item per name; an item reads its name's answer and makes one edge that names it."""

    def __init__(self, answers: dict[str, str], tracer: Tracer) -> None:
        self.answers = answers
        self.tracer = tracer
        self.ran: list[str] = []
        self.taken: list[Any] = []

    def keys(self) -> list[str]:
        return list(self.answers)

    def run_item(self, key: str) -> ItemOutput:
        self.ran.append(key)
        answer = self.answers[key]
        self.tracer.note(KIND, key, answer)
        edge = Edge(
            "reference",
            Site(f"{key}.py", key, "<module>", 1),
            answer,
            (),
            Resolution.UNRESOLVED,
            frozenset({Context.PROD}),
            "test@1",
        )
        return ItemOutput([edge, edge], {"answer": answer})

    def take(self, key: str, data: Any) -> None:
        self.taken.append(data)


def check(store: MemoStore, answers: dict[str, str]) -> tuple[World, list[Any]]:
    """One run of the step through the memo, as a pack phase runs it."""
    memo = PackMemo(store, "policy")
    memo.tracer.answer_with(KIND, answers.get)
    world = World(answers, memo.tracer)
    step = memo.step("test@1")
    with memo.tracer.step("test@1"):
        outputs = ItemRun(world, step).outputs()
    memo.end_step(step)
    memo.flush()
    return world, outputs


@pytest.mark.parametrize(
    ("child", "ran"),
    [
        ({"a": "1", "b": "2"}, []),
        ({"a": "1", "b": "3"}, ["b"]),
        ({"a": "1", "b": "2", "c": "4"}, ["c"]),
    ],
)
def test_an_item_runs_again_only_when_an_answer_it_read_changed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], child: dict[str, str], ran: list[str]
) -> None:
    store = MemoStore(tmp_path, "engine")
    check(store, {"a": "1", "b": "2"})
    assert capsys.readouterr().err == MEMO_FILL
    warm, outputs = check(store, child)
    assert capsys.readouterr().err == ""
    cold, expected = check(MemoStore(tmp_path / "cold", "engine"), child)
    assert warm.ran == ran
    assert outputs == expected
    assert outputs[0] is outputs[1]
    assert warm.taken == cold.taken


@pytest.mark.parametrize("damage", ["garbage-part", "garbage-file", "empty-file"])
def test_a_damaged_store_is_a_miss(tmp_path: Path, damage: str) -> None:
    store = MemoStore(tmp_path, "engine")
    check(store, {"a": "1"})
    store.close()
    gc.collect()  # the daily prune's connections sit in reference cycles until a collection
    path = store.database.path
    if damage == "garbage-part":
        with sqlite3.connect(path) as conn:
            conn.execute("UPDATE parts SET body = randomblob(length(body))")
    else:
        for suffix in ("-wal", "-shm"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)
        path.write_bytes(b"not a database at all" * 100 if damage == "garbage-file" else b"")
    world, outputs = check(MemoStore(tmp_path, "engine"), {"a": "1"})
    assert world.ran == ["a"]
    assert [edge.surface for edge in outputs] == ["1", "1"]


def test_a_lost_newest_state_is_a_miss_and_the_next_state_takes_a_new_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(RunStores, "warned", set())
    store = MemoStore(tmp_path, "engine")
    check(store, {"a": "1"})
    store.close()
    gc.collect()
    path = store.database.path
    with sqlite3.connect(path) as conn:
        lost = conn.execute("SELECT max(id) FROM blobs").fetchone()[0]
        conn.execute("DELETE FROM blobs WHERE id = ?", (lost,))
    again = MemoStore(tmp_path, "engine")
    world, _ = check(again, {"a": "2"})
    assert (world.ran, again.state()) == (["a"], "open")
    world, outputs = check(again, {"a": "2"})
    assert (world.ran, [edge.surface for edge in outputs]) == ([], ["2", "2"])
    assert [found.name for found in path.parent.iterdir() if ".corrupt-" in found.name] == []
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT min(id) FROM blobs").fetchone()[0] > lost


@pytest.mark.parametrize("damage", ["stray-key-row", "garbage-file"])
def test_a_store_damaged_from_outside_is_moved_aside_and_the_next_run_stores_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    monkeypatch.setattr(RunStores, "warned", set())
    store = MemoStore(tmp_path, "engine")
    check(store, {"a": "1"})
    store.close()
    gc.collect()
    path = store.database.path
    if damage == "stray-key-row":
        # A key at the id the next state's manifest takes: its insert breaks the unique id.
        with sqlite3.connect(path) as conn:
            taken = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'blobs'").fetchone()[0]
            conn.execute("INSERT INTO keys VALUES (?, 'stray', 'stray', 0, 0, 0)", (taken + 1,))
    else:
        for suffix in ("-wal", "-shm"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)
        path.write_bytes(b"not a database at all" * 100)
    damaged = MemoStore(tmp_path, "engine")
    check(damaged, {"a": "2"})
    assert damaged.state() == "failed"
    healed = MemoStore(tmp_path, "engine")
    check(healed, {"a": "2"})
    world, outputs = check(healed, {"a": "2"})
    assert (world.ran, healed.state()) == ([], "open")
    assert [edge.surface for edge in outputs] == ["2", "2"]


class Folded(World):
    """A fold that builds a state, and items in the fold's own part that read it."""

    STATE = "test.state"

    def folds(self) -> list[str]:
        return ["fold"]

    def keys(self) -> list[str]:
        return [key for key in (f"item{at}" for at in range(400)) if part_of(key) == part_of("fold")]

    def run_item(self, key: str) -> ItemOutput:
        self.ran.append(key)
        if key == "fold":
            return ItemOutput([], {"state": "s"})
        self.tracer.note(self.STATE, "state", self.answers["state"])
        return ItemOutput([])

    def take(self, key: str, data: Any) -> None:
        if key == "fold":
            self.tracer.answer_with(self.STATE, lambda _: data["state"])


def test_items_that_read_a_fold_of_their_own_part_are_reused(tmp_path: Path) -> None:
    store = MemoStore(tmp_path, "engine")
    for _ in range(2):
        memo = PackMemo(store, "policy")
        world = Folded({"state": "s"}, memo.tracer)
        step = memo.step("test@1")
        with memo.tracer.step("test@1"):
            ItemRun(world, step).outputs()
        memo.end_step(step)
        memo.flush()
    assert world.ran == []


class WholeMap(Items):
    """One item that reads a traced map as a whole, by `read`."""

    def __init__(self, read: Callable[[], Any]) -> None:
        self.read = read
        self.ran: list[str] = []

    def keys(self) -> list[str]:
        return ["a"]

    def run_item(self, key: str) -> ItemOutput:
        self.ran.append(key)
        return ItemOutput([], {"seen": self.read()})


def model(name: str) -> tuple[EntityId, ModelEntry]:
    ref = EntityId(LANGUAGE_PYTHON, "app.models", name)
    return ref, ModelEntry(ref, "app/models.py", 1, "app", False, False, ref, {})


def module_index(names: list[str], tracer: Tracer) -> TracedMap[str, str]:
    return TracedMap(tracer, "module-path", {name: f"{name}.py" for name in names})


def models(names: list[str], tracer: Tracer) -> TracedMap[EntityId, ModelEntry]:
    return TracedMap(tracer, "django.model", dict(model(name) for name in names), key=str)


WHOLE_READS = {
    "len of the module index": (module_index, len),
    "items of the module index": (module_index, lambda found: sorted(found.items())),
    "values of the module index": (module_index, lambda found: sorted(found.values())),
    "len of the models": (models, len),
    "keys of the models": (models, lambda found: sorted(str(ref) for ref in found.keys())),
}


@pytest.mark.parametrize("case", WHOLE_READS)
def test_an_item_that_reads_a_whole_traced_map_runs_again_when_the_map_gains_a_key(tmp_path: Path, case: str) -> None:
    make, read = WHOLE_READS[case]
    store = MemoStore(tmp_path, "engine")
    ran = []
    for names in (["a"], ["a", "b"]):
        memo = PackMemo(store, "policy")
        found = make(names, memo.tracer)
        found.answer_reads()
        items = WholeMap(lambda found=found: read(found))
        step = memo.step("whole@1")
        with memo.tracer.step("whole@1"):
            ItemRun(items, step).outputs()
        memo.end_step(step)
        memo.flush()
        ran.append(items.ran)
    assert ran == [["a"], ["a"]]


# A connect call in `Panel.wire`, def 1 of its file: a lookup of its receiver is asked at def 1.
WIRED = "class Panel:\n    def wire(self):\n        self.button.connect(print)\n"
# The file in another tree. Where def 1 is another method, `self` is a local there too, so the
# lookup asked again at def 1 would give the same answer about another def.
OTHER_TREES = {
    "same content": WIRED,
    "def 1 gone": "def wire(button):\n    button.connect(print)\n",
    "def 1 another method": (
        "class Panel:\n    def other(self):\n        pass\n\n    def wire(self):\n        self.button.connect(print)\n"
    ),
}


def blob_build(root: Path, wiring: str) -> NativeBuild:
    """What packs read of a git index that holds `wiring`, with each file keyed by its blob id."""
    native = native_module()
    write_files(root, {"app/__init__.py": "", "app/wiring.py": wiring})
    git_in(root, "init", "-q")
    git_in(root, "add", ".")
    snapshot = BlobSnapshot.index(root)
    paths = sorted(snapshot.blobs)
    blobs = [(path, *snapshot.blobs[path]) for path in paths]
    extract = {"first_party": ["app"], "kinds": [], "watch_callees": [], "lite_kinds": list(LITE_KINDS)}
    config = json.dumps(link_config(CoreConfig(), tuple(paths), {}))
    link, _ = native.link_blobs(str(root), blobs, [], json.dumps(extract), config, None, 1)
    return NativeBuild(CoreConfig(), link, {}, EdgeMaker())


@pytest.mark.parametrize("other", sorted(OTHER_TREES))
def test_a_lookup_at_a_site_holds_in_another_tree_only_while_its_file_has_the_same_content(
    tmp_path: Path, other: str
) -> None:
    first = blob_build(tmp_path / "first", WIRED)
    tracer = Tracer()
    first.trace_with(tracer)
    with tracer.frame() as frame:
        first.context.value_lookup(first.scope_of["app/wiring.py"], "self.button", 1, 3)
    stored = tracer.stored_reads(frame)
    assert stored is not None
    second = blob_build(tmp_path / "second", OTHER_TREES[other])
    again = Tracer()
    second.trace_with(again)
    lookups = [(kind, key, found) for kind, key, found in stored if kind != FILE]
    assert [again.current(kind, key) == found for kind, key, found in lookups] == [other == "same content"]
    assert [kind for kind, _, _ in stored if kind == FILE] == [FILE]
    scope: Any = second.scope_of["app/wiring.py"]
    with pytest.raises(ValueError, match="no def 9"):
        second.link.value_lookup(scope.scope, "self.button", 9, 3)


def test_a_pack_memo_that_cannot_open_says_so_apart_from_the_fact_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(RunStores, "warned", set())
    (tmp_path / MEMO).write_text("a file where the memo directory goes")
    store = MemoStore(tmp_path, "engine")
    assert store.manifest("step") is None
    assert store.state() == "failed"
    memo_error = capsys.readouterr().err
    assert "the pack memo is not usable" in memo_error
    assert "fact cache" not in memo_error
    monkeypatch.setattr(RunStores, "warned", set())
    RunStores.of_environment().fail(FACT_STORE.name, OSError("disk full"))
    assert "the fact cache is not usable" in capsys.readouterr().err
