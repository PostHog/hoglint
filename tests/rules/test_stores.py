from __future__ import annotations

import os
from pathlib import Path

import pytest

from hoglint.ledger.cache import (
    FACT_STORE,
    MEMO_STORE,
    OWN_PRUNE,
    STORES,
    SUMMARY_STORE,
    AnswerStore,
    Recording,
    RunStores,
    Store,
    StoreList,
)
from hoglint.ledger.store import CacheRoot
from hoglint.native.replay import COLD_SWITCHES, REUSE_SWITCHES

NEW = Store(
    "new-store",
    "new",
    shared=False,
    warning="the new store is not usable: {error}",
    pruner=OWN_PRUNE,
    switch="HOGLINT_TEST_NEW_STORE",
    off="1",
)
OLD = 1_000_000.0


def test_a_store_added_to_the_list_reaches_every_view_with_no_other_edit(tmp_path: Path) -> None:
    listed = StoreList((*STORES.stores, NEW), (*STORES.recordings, Recording("new-recording", "HOGLINT_TEST_RECORD")))
    assert listed.cold_environment() == {**STORES.cold_environment(), "HOGLINT_TEST_NEW_STORE": "1"}
    assert set(listed.switches()) == {*STORES.switches(), "HOGLINT_TEST_NEW_STORE", "HOGLINT_TEST_RECORD"}
    assert RunStores({}, listed).record()["new-store"] == "unused"
    assert RunStores({"HOGLINT_TEST_NEW_STORE": "1"}, listed).record()["new-store"] == "off"
    for directory in ("new", "summaries"):
        (tmp_path / directory).mkdir()
        (tmp_path / directory / "entry").write_text("old")
        os.utime(tmp_path / directory / "entry", (OLD, OLD))
    CacheRoot(tmp_path, listed=listed).prune()
    assert (tmp_path / "new" / "entry").exists()
    assert not (tmp_path / "summaries" / "entry").exists()


def test_the_replay_switches_are_the_views_of_the_store_list() -> None:
    assert STORES.cold_environment() == COLD_SWITCHES
    assert set(STORES.switches()) == set(REUSE_SWITCHES)


def test_a_failed_store_warns_once_with_its_own_text_and_leaves_other_warnings(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(RunStores, "warned", set())
    stores = RunStores({})
    stores.fail(MEMO_STORE.name, OSError("locked"))
    stores.fail(MEMO_STORE.name, OSError("locked again"))
    stores.fail(SUMMARY_STORE.name, OSError("disk full"), "writing a summary")
    RunStores({}).fail(FACT_STORE.name, OSError("damaged"))
    assert capsys.readouterr().err.splitlines() == [
        "hoglint: the pack memo is not usable, so this run runs every pack item and stores none: locked",
        "hoglint: cannot use the cache (writing a summary: disk full), so this run goes on without it",
        "hoglint: the fact cache is not usable, so this run extracts without it: damaged",
    ]
    assert stores.record()[MEMO_STORE.name] == "failed"
    assert stores.record()[FACT_STORE.name] == "unused"


@pytest.mark.parametrize(
    "environ, off, memo_mode, trace",
    [
        ({}, set(), "", False),
        ({"HOGLINT_PACK_MEMO": "trace", "HOGLINT_LINK_TRACE": "1"}, {"pack-memo"}, "trace", True),
        (
            {"HOGLINT_PACK_MEMO": "off", "HOGLINT_NO_ANSWER_CACHE": "1"},
            {"pack-memo", "site-packages", "turbo"},
            "off",
            False,
        ),
    ],
)
def test_the_run_stores_read_each_switch_once_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, environ: dict[str, str], off: set[str], memo_mode: str, trace: bool
) -> None:
    stores = RunStores(environ)
    assert {name for name, state in stores.record().items() if state == "off"} == off
    assert (stores.memo_mode(), stores.trace_on()) == (memo_mode, trace)
    for name in STORES.switches():
        monkeypatch.delenv(name, raising=False)
    with stores.as_current():
        assert AnswerStore.off() is ("turbo" in off)
    assert not AnswerStore.off()
