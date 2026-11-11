"""The pack memo reuses an item only while every read it made has the same answer, and a damaged
store is a miss, never a failed run."""

from __future__ import annotations

import gc
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from hoglint.core.memo import TRACER, ItemOutput, ItemRun, Items, PackMemo, part_of
from hoglint.model import Context, Edge, Resolution, Site
from hoglint.native.memo_store import MemoStore

KIND = "test.answer"


class World(Items):
    """One item per name; an item reads its name's answer and makes one edge that names it."""

    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = answers
        self.ran: list[str] = []
        self.taken: list[Any] = []

    def keys(self) -> list[str]:
        return list(self.answers)

    def run_item(self, key: str) -> ItemOutput:
        self.ran.append(key)
        answer = self.answers[key]
        TRACER.note(KIND, key, answer)
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
    TRACER.reset()
    TRACER.answer_with(KIND, answers.get)
    world = World(answers)
    memo = PackMemo(store, "policy")
    step = memo.step("test@1")
    with TRACER.frame():
        outputs = ItemRun(world, step).outputs()
    memo.end_step(step)
    memo.flush()
    TRACER.reset()
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
        TRACER.note(self.STATE, "state", self.answers["state"])
        return ItemOutput([])

    def take(self, key: str, data: Any) -> None:
        if key == "fold":
            TRACER.answer_with(self.STATE, lambda _: data["state"])


def test_items_that_read_a_fold_of_their_own_part_are_reused(tmp_path: Path) -> None:
    store = MemoStore(tmp_path, "engine")
    for _ in range(2):
        TRACER.reset()
        world = Folded({"state": "s"})
        memo = PackMemo(store, "policy")
        step = memo.step("test@1")
        with TRACER.frame():
            ItemRun(world, step).outputs()
        memo.end_step(step)
        memo.flush()
        TRACER.reset()
    assert world.ran == []
