"""The pack memo reuses an item only while every read it made has the same answer, and a damaged
store is a miss, never a failed run."""

from __future__ import annotations

import gc
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from hoglint.core.memo import ItemOutput, ItemRun, Items, PackMemo, Tracer, part_of
from hoglint.ledger.cache import FACT_STORE, MEMO, RunStores
from hoglint.model import LANGUAGE_PYTHON, Context, Edge, EntityId, Resolution, Site
from hoglint.native.answers import ReaderAnswers
from hoglint.native.build import TracedDict
from hoglint.native.memo_store import MemoStore
from hoglint.packs.django.models import ModelEntry, TracedModels

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
    tmp_path: Path, child: dict[str, str], ran: list[str]
) -> None:
    store = MemoStore(tmp_path, "engine")
    check(store, {"a": "1", "b": "2"})
    warm, outputs = check(store, child)
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


def module_index(names: list[str], tracer: Tracer) -> tuple[TracedDict, Callable[[], None]]:
    found = TracedDict("module-path", {name: f"{name}.py" for name in names})
    found.tracer = tracer
    return found, lambda: tracer.answer_with("module-path", lambda key: ReaderAnswers.index_answer(found, key))


def models(names: list[str], tracer: Tracer) -> tuple[TracedModels, Callable[[], None]]:
    found = TracedModels(tracer)
    found.update(dict(model(name) for name in names))
    return found, found.answer_reads


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
        found, answer = make(names, memo.tracer)
        answer()
        items = WholeMap(lambda found=found: read(found))
        step = memo.step("whole@1")
        with memo.tracer.step("whole@1"):
            ItemRun(items, step).outputs()
        memo.end_step(step)
        memo.flush()
        ran.append(items.ran)
    assert ran == [["a"], ["a"]]


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
    RunStores.of_environment().fail(FACT_STORE.name, OSError("disk full"))
    assert "the fact cache is not usable" in capsys.readouterr().err
