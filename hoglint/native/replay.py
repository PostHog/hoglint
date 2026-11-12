"""Replays pairs of git trees and checks that a check which reuses stored work reports exactly
what a check from nothing reports.

A pair is (name, parent tree, child tree) in a git repo that holds both trees. Each check runs as
`check --index` runs after `git write-tree`, in its own process, with HEAD standing at the parent
tree, so the child's check is a developer's next commit. Per pair:

- cold parent: the parent in a new, empty state directory;
- cold child: the child in a new, empty state directory (it computes its baseline, the parent);
- warm child: the parent, then the child, in one state directory;
- back again: after the warm child, the parent again in that directory (A, B, A).

Warm child is compared with cold child, back again with cold parent: every table, in order and
as a multiset (`parity.diff_lines`). A state directory is `XDG_CACHE_HOME` for a check: the stored
summaries, the inputs digests and the pack caches. The per-blob fact cache is keyed by content,
so one private fact cache is shared by every check of a session, linked into each state
directory. A check that a stored summary stands in for (the run memo) builds no graph: then only
the check's own tables (its summary, report and exit code) compare.

From the two cold dumps the replay also computes which files truly changed (the truth set) and
checks the mode's predicted files against it. With `--damage`, it damages the state directory
between the parent's check and the child's, or runs two child checks at once, or kills a check
in its writes and runs it again: the child's tables must stay equal and no run may fail (D28).

With `--mode traces` the replay is the certificate of stage S5: the cold checks record link
traces (`hoglint.native.predict`), the prediction from the parent's traces must hold every file
of the truth set, and each read kind is left out once to show which pairs need it (the planted
gaps). Only the two cold checks run, so a prediction that holds prints HELD, not EQUAL: no
table was compared.

Usage:
  python -m hoglint.native.replay pairs --repo <git repo> --pairs <tsv> [--pairs <tsv>] --work <dir>
      [--engine native] [--mode today|planted-miss|traces|pack-memo-miss] [--only <name,...>]
      [--damage all|<name,...>] [--lay <path in tree>=<local file> ...]
      `--lay` replaces that path of both trees of every pair by the local file's bytes (new trees
      in the repo), so a sample whose trees carry an old preset is checked with the current one
  python -m hoglint.native.replay determinism --repo <git repo> --tree <tree> [--tree ...] --work <dir>
      the cold dump of each tree under three hash seeds and one and four threads, each with an
      empty fact cache, compared with the first
  python -m hoglint.native.replay check ...
      one check in this process (the harness runs it)

Exit 0 only when at least one pair ran, every compared table is equal, no check failed, each
cold check shows that it reused nothing, each warm check shows that it reused stored work, every
pair has each verdict its mode owes, and a certificate's prediction holds every item of the
truth set.
"""

from __future__ import annotations

import io
import os
import re
import sys
import json
import time
import random
import shutil
import signal
import sqlite3
import argparse
import datetime
import resource
import tempfile
import contextlib
import statistics
import subprocess
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from hoglint.cli import Commands, Run, RunMemo, Snapshots
from hoglint.core.memo import (
    DROP_ENV as PACK_MEMO_DROP,
    MODE_ENV as PACK_MEMO_SWITCH,
    OFF as PACK_MEMO_OFF,
    STATS_ENV as PACK_MEMO_STATS,
    PackMemo,
)
from hoglint.core.modules import longest_prefix
from hoglint.ledger.cache import (
    ANSWERS,
    OFF_SWITCH as ANSWER_CACHE_SWITCH,
    AnswerStore,
)
from hoglint.ledger.envelope import Envelope
from hoglint.ledger.listing import git, records
from hoglint.ledger.scan import ALL_SECTIONS, SUMMARY_SECTIONS, ScanSummary
from hoglint.ledger.store import FACTS, MEMO, SummaryStore
from hoglint.model import Finding, Graph
from hoglint.native.config import TRACE_ENV
from hoglint.native.graph import NativeGraph
from hoglint.native.memo_store import MemoStore
from hoglint.native.normal import FIELDS, Row
from hoglint.native.parity import (
    LIST_OUTPUTS,
    RowDiff,
    Tables,
    compare_dumps,
    row_key,
    row_texts,
    seed_env,
    stored_summary,
)
from hoglint.native.predict import PairPrediction, Prediction, write_trace_dump
from hoglint.native.source import ENGINES, NativeGraphSource, batch_mode
from hoglint.rules.base import IncompleteItem

# The tables a check writes besides the parity tables, and the ones every check writes.
GRAPH_OUTPUTS = (*LIST_OUTPUTS, "labels", "finding-files", "incomplete-files")
CHECK_OUTPUTS = ("check-summary", "check-report", "check-exit")
# The summary sections a stored summary stands in for a run with (`RunMemo.SECTIONS`).
MEMO_SECTIONS = ("envelope", "incompletes", "findings", "levels")
if set(MEMO_SECTIONS) != RunMemo.SECTIONS:
    raise ImportError(f"MEMO_SECTIONS {MEMO_SECTIONS} differs from RunMemo.SECTIONS {sorted(RunMemo.SECTIONS)}")
# Edge rows: where the site path and the producer sit.
EDGE_PATH = FIELDS.index("path")
EDGE_PRODUCER = FIELDS.index("producer")
DIGEST_MOD = 2**128
SEEDS = ("0", "1", "4242")
THREADS = ("1", "4")
# The kinds of side a check runs on (`Side`).
COLD, WARM, DAMAGED, FILL = "cold", "warm", "damaged", "fill"


# ── the stores of a run ───────────────────────────────────────────
# Kept by hand, together: a new store of a run adds itself to each list in this block.

# A cold check runs with every store of reused answers switched off. Its own baseline fills the
# cold state directory first, so a store left on would hand the cold child the same stored
# answers as the warm child, and a wrong answer would compare equal. Every new store of reused
# answers adds its off switch here. The per-blob fact cache stays on: its entries are what the
# extractor makes of a file's bytes, keyed by those bytes.
COLD_SWITCHES = {PACK_MEMO_SWITCH: PACK_MEMO_OFF, ANSWER_CACHE_SWITCH: "1"}
# Every switch that turns a store of reused answers or a recording on or off. A check never
# takes them from the caller's shell: the replay sets what each side needs, so a warm side
# reuses what it stored. Every new store adds its switch here too.
REUSE_SWITCHES = (*COLD_SWITCHES, PACK_MEMO_DROP, PACK_MEMO_STATS, TRACE_ENV)
# A cold check may share only the stores whose entries are keyed by the content they come from:
# the per-blob fact cache (`facts/`) and the migration summaries under `packs/`. `cold_problem`
# reads the check's own reuse record: every other store must have been off or unread there.
# The files a check reads from the cache root, by store: (store, part, file pattern). A damage
# case makes each kind garbage, empty and cut to half.
CACHE_FILES = (
    ("summaries", "summaries", "*"),
    ("inputs", "inputs", "*"),
    ("packs", "packs", "*"),
    ("fact-database", FACTS, "*.sqlite"),
    ("fact-marks", FACTS, "*.sqlite.*"),
    ("pack-memo", MEMO, "*.sqlite"),
    ("pack-memo-wal", MEMO, "*.sqlite-*"),
    ("answers", ANSWERS, "*.json"),
)
DAMAGE_HOW = ("garbage", "empty", "truncate")


def check_env(caller: dict[str, str], side: dict[str, str]) -> dict[str, str]:
    """The caller's environment with no reuse switch, and then the side's own settings."""
    out = {name: value for name, value in caller.items() if name not in REUSE_SWITCHES}
    return {**out, **side}


# ── one check, in its own process ─────────────────────────────────


class FixedSnapshots(Snapshots):
    """Snapshots whose index is `tree` and whose HEAD is `head`, so no checkout and no commit is needed."""

    def __init__(self, repo: Path, work_dir: Path, tree: str, head: str) -> None:
        super().__init__(repo, work_dir)
        self.tree = tree
        self.trees["HEAD"] = head

    def write_tree(self) -> str:
        return self.tree


class RecordingNativeSource(NativeGraphSource):
    """A native graph source that keeps the pack memo of each build, so a check can say whether
    its pack memo was on and its store opened."""

    def __init__(self, work_dir: Path) -> None:
        super().__init__(work_dir)
        self.memos: list[PackMemo | None] = []

    def pack_memo(self, options: Any, build: Any, config_key: str) -> PackMemo | None:
        memo = super().pack_memo(options, build, config_key)
        self.memos.append(memo)
        return memo

    def memo_states(self) -> list[str]:
        """Per build: `off`, `no store` (the trace mode, or no cache root), or the store's state."""
        states = []
        for memo in self.memos:
            if memo is None:
                states.append("off")
            elif isinstance(memo.store, MemoStore):
                states.append(memo.store.state())
            else:
                states.append("no store")
        return states


class CountingSummaryStore(SummaryStore):
    """A summary store that counts the stored summaries it handed out, so a cold check can show
    that it read none."""

    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    def get(self, key: Envelope, sections: frozenset[str] = ALL_SECTIONS) -> ScanSummary | None:
        found = super().get(key, sections)
        if found is not None:
            self.reads += 1
        return found


class RecordingCommands(Commands):
    """`check --index` of a fixed tree that keeps what the run made: its graph when it ran live
    (`write_outputs` gets every live run), the summary it reported (`report` gets every check)
    and its pack memos."""

    def __init__(self, repo: Path, work_dir: Path, engine: str, today: datetime.date, tree: str, head: str) -> None:
        super().__init__(repo, work_dir, engine, today)
        self.recording: RecordingNativeSource | None = None
        if isinstance(self.source, NativeGraphSource):
            self.recording = RecordingNativeSource(work_dir)
            self.source = self.runner.source = self.recording
        self.snapshots = FixedSnapshots(repo, work_dir, tree, head)
        self.summaries = self.baselines.store = CountingSummaryStore()
        self.baselines.snapshots = self.snapshots
        if self.memo is not None:
            self.memo.snapshots = self.snapshots
            self.memo.store = self.summaries
        self.live: Run | None = None
        self.reported: ScanSummary | None = None
        self.against = ""

    def write_outputs(self, run: Run) -> None:
        self.live = run
        super().write_outputs(run)

    def report(
        self,
        summary: ScanSummary,
        config: Any,
        policy_root: Path,
        files_root: Path,
        touched: set[str] | None,
        attributed: Any,
    ) -> int:
        self.reported = summary
        self.against = attributed.against if attributed is not None else ""
        return super().report(summary, config, policy_root, files_root, touched, attributed)

    def reuse(self) -> dict[str, Any]:
        """Which stores of reused work this check had: the answer stores, the summary store and
        how many stored summaries it read, and per native build its pack memo (`memo_states`;
        absent on the Python engine)."""
        summaries = self.summaries
        out: dict[str, Any] = {"answers": not AnswerStore.off(), "summaries": not summaries.broken}
        if isinstance(summaries, CountingSummaryStore):
            out["summary_reads"] = summaries.reads
        if self.recording is not None:
            out["pack_memo"] = self.recording.memo_states()
        return out


def summary_rows(summary: ScanSummary) -> list[Row]:
    """The sections a stored summary stands in for a run with, one row per field."""
    data = summary.to_json()
    return [[section, name, data[name]] for section in MEMO_SECTIONS for name in SUMMARY_SECTIONS[section]]


def finding_files(finding: Finding, modules: dict[str, str]) -> list[str]:
    """Every file a finding names: its site, its witnesses' sites and its subjects."""
    files = {edge.site.path for edge in finding.witnesses}
    if finding.site is not None:
        files.add(finding.site.path)
    files.update(modules[subject] for subject in finding.subjects if subject in modules)
    return sorted(files)


def incomplete_files(item: IncompleteItem, modules: dict[str, str]) -> list[str]:
    if item.edge is not None:
        return [item.edge.site.path]
    return sorted({modules[subject] for subject in item.subjects if subject in modules})


class TreeCheck:
    """One check of `tree` with HEAD at `head`; its state directory is `XDG_CACHE_HOME`. Writes
    `check.json` (time, memo) into `out`, and with `tables` every table it can."""

    def __init__(
        self, repo: Path, engine: str, today: datetime.date, tree: str, head: str, out: Path, tables: bool
    ) -> None:
        self.repo = repo
        self.engine = engine
        self.today = today
        self.tree = tree
        self.head = head
        self.out = out
        self.tables = tables

    def run(self, work: Path) -> None:
        batch_mode(self.engine)
        commands = RecordingCommands(self.repo, work, self.engine, self.today, self.tree, self.head)
        wall, cpu = time.perf_counter(), self.cpu()
        printed = io.StringIO()
        try:
            with contextlib.redirect_stdout(printed):
                code = commands.check(index=True, write_ledger=False, changed=False)
        finally:
            commands.snapshots.close()
        timing = {"wall": round(time.perf_counter() - wall, 3), "cpu": round(self.cpu() - cpu, 3)}
        self.out.mkdir(parents=True, exist_ok=True)
        meta = {
            "exit": code,
            "memo": commands.live is None,
            "against": commands.against,
            "engine": commands.engine.id,
            "reuse": commands.reuse(),
            **timing,
        }
        (self.out / "check.json").write_text(json.dumps(meta))
        if not self.tables:
            return
        self.write_tables(commands, code, printed.getvalue())
        self.unplace(work)

    def unplace(self, work: Path) -> None:
        """A finding message can name a file of the check's own scratch policy directory, whose
        path differs in every process, so the tables name it `<work>/policy`."""
        places = "|".join(re.escape(place) for place in sorted({str(work), str(work.resolve())}, key=len, reverse=True))
        # A scratch directory is `mkdtemp(prefix="<name>-")`: eight random characters follow.
        scratch = re.compile(rf"(?:{places})/([a-z]+)-[a-z0-9_]{{8}}")
        marker = work.name
        for table in sorted(self.out.glob("*.json*")):
            if table.name == "check.json":
                continue
            moved = table.with_name(f"{table.name}.tmp")
            with open(table) as source, open(moved, "w") as target:
                for line in source:
                    target.write(scratch.sub(r"<work>/\1", line) if marker in line else line)
            moved.replace(table)

    @staticmethod
    def cpu() -> float:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        return usage.ru_utime + usage.ru_stime

    def write_tables(self, commands: RecordingCommands, code: int, printed: str) -> None:
        tables = Tables(self.out)
        if commands.reported is None:
            raise RuntimeError("the check reported no summary")
        tables.write_lines("check-summary", summary_rows(commands.reported))
        # The baseline's origin is named in the report; it differs between cold and warm by design.
        report = printed.replace(commands.against, "<baseline>") if commands.against else printed
        tables.write_lines("check-report", [[line] for line in report.splitlines()])
        tables.write_lines("check-exit", [[code]])
        run = commands.live
        if run is None:
            return
        roots = (run.policy_root or run.root, run.root, run.files_root or run.root)
        graph: Graph = run.graph
        summary = stored_summary(run.result, graph, roots, run.exceptions)
        tables.write(graph, run.result, summary)
        modules = graph.modules
        labeler = run.result.labeler
        labels = [[module, path, [list(row) for row in labeler.explain(module)]] for module, path in modules.items()]
        tables.write_lines("labels", labels)
        tables.write_lines("finding-files", [finding_files(f, modules) for f in run.result.findings()])
        tables.write_lines("incomplete-files", [incomplete_files(i, modules) for i in run.result.incomplete()])
        if os.environ.get(TRACE_ENV) == "1" and isinstance(graph, NativeGraph):
            write_trace_dump(graph.link, run.config, self.out)


# ── dumps and their comparison ────────────────────────────────────


class CheckFailed(Exception):
    """A check that did not finish: the run failed, which a cache problem must never cause."""


@dataclass(frozen=True)
class CheckDump:
    """A finished check: the directory of its tables, the `check.json` it wrote there, and the
    kind of side it ran on."""

    path: Path
    meta: dict[str, Any]
    side: str

    @staticmethod
    def load(path: Path, side: str) -> CheckDump:
        return CheckDump(path, json.loads((path / "check.json").read_text()), side)

    @property
    def engine(self) -> str:
        return str(self.meta.get("engine", ""))

    @property
    def memo(self) -> bool:
        """A stored summary stood in for the check (the run memo)."""
        return bool(self.meta.get("memo"))

    @property
    def wall(self) -> float:
        return float(self.meta["wall"])

    @property
    def cpu(self) -> float:
        return float(self.meta["cpu"])

    def has(self, table: str) -> bool:
        return (self.path / f"{table}.jsonl").is_file()


def reuse_problem(meta: dict[str, Any]) -> str:
    """Why a warm check reused no stored work, or "" when it could. A check the run memo served
    reused a stored summary. Otherwise every store must have been on, and each pack memo's store
    opened and read, or never asked because no step had items: a count of reused items cannot
    be the test, since a pair that changes the config rightly reuses none."""
    if meta.get("memo"):
        return ""
    reuse = meta.get("reuse")
    if not isinstance(reuse, dict):
        return "the check did not say what it reused"
    if not reuse.get("answers"):
        return "the answer stores were off"
    if not reuse.get("summaries"):
        return "the summary store failed"
    memos = reuse.get("pack_memo")
    if memos is None:
        return ""
    if not memos:
        return "no build made a pack memo"
    bad = [state for state in memos if state not in ("open", "unused")]
    return f"the pack memo was {bad[0]}" if bad else ""


def cold_problem(meta: dict[str, Any]) -> str:
    """Why a cold check may have reused stored work, or "" when it shows it reused none: no
    stored summary stood in for it or served as its baseline, and every answer store and pack
    memo was off. See the stores a cold check may share, next to `COLD_SWITCHES`."""
    if meta.get("memo"):
        return "the run memo served it"
    reuse = meta.get("reuse")
    if not isinstance(reuse, dict):
        return "the check did not say what it reused"
    if reuse.get("answers"):
        return "the answer stores were on"
    if reuse.get("summary_reads", 1):
        return "it read a stored summary"
    on = [state for state in reuse.get("pack_memo", []) if state != "off"]
    return f"the pack memo was {on[0]}" if on else ""


class Outcome:
    """One verdict of a pair. Each kind has a fixed `word`, the word the pair's status shows for
    it ("" for none), and a fixed `ok`; only `Compared` computes `ok`. No kind is ok by default."""

    word: ClassVar[str] = ""
    label: str

    @property
    def ok(self) -> bool:
        return False

    @property
    def memo(self) -> bool:
        return False

    def short(self) -> str:
        raise NotImplementedError

    def details(self) -> str | None:
        """The rows of each differing table, or None when there is nothing to print."""
        return None


@dataclass(frozen=True)
class TableOutcome(Outcome):
    """An outcome that holds the tables it compared, and prints the rows of each that differs."""

    label: str
    diffs: tuple[RowDiff, ...]

    def details(self) -> str | None:
        if not self.diffs or self.ok:
            return None
        return "\n".join(f"  [{self.label}] {diff.render()}" for diff in self.diffs if not diff.equal)


@dataclass(frozen=True)
class Compared(TableOutcome):
    """Every table of two dumps compared. It is ok only when it compared at least one table and
    every table is equal; `memo` says the other side was served by the run memo."""

    served_by_memo: bool

    @property
    def ok(self) -> bool:
        return bool(self.diffs) and all(diff.equal for diff in self.diffs)

    @property
    def memo(self) -> bool:
        return self.served_by_memo

    def short(self) -> str:
        if not self.ok:
            different = ",".join(diff.name for diff in self.diffs if not diff.equal)
            return f"{self.label}: DIFFERENT ({different})"
        if self.served_by_memo:
            return f"{self.label}: equal, served by the run memo ({len(self.diffs)} tables compared, no graph table)"
        return f"{self.label}: equal ({len(self.diffs)} tables)"


@dataclass(frozen=True)
class Refused(Outcome):
    """Two checks of different engines: a replay across two engine versions proves nothing."""

    label: str
    engines: tuple[str, str]

    def short(self) -> str:
        left, right = self.engines
        return f"{self.label}: REFUSED: the two checks ran different engines ({left}, {right})"


@dataclass(frozen=True)
class Failed(Outcome):
    """A check that did not finish."""

    label: str
    error: str

    def short(self) -> str:
        return f"{self.label}: FAILED RUN: {self.error}"


@dataclass(frozen=True)
class NotCold(Outcome):
    """A cold check that cannot show it reused nothing, so it is no baseline to compare with."""

    word: ClassVar[str] = "NOT COLD"
    label: str
    problem: str

    def short(self) -> str:
        return f"{self.label}: {self.word} ({self.problem})"


@dataclass(frozen=True)
class NotCompared(TableOutcome):
    """A side lacks a table every check writes, so it did not finish its dump."""

    word: ClassVar[str] = "NOT COMPARED"
    missing: tuple[str, ...]

    def short(self) -> str:
        return f"{self.label}: {self.word} (missing {','.join(self.missing)})"


@dataclass(frozen=True)
class NoReuse(TableOutcome):
    """A warm side that cannot show it reused stored work."""

    word: ClassVar[str] = "NO REUSE"
    problem: str

    def short(self) -> str:
        return f"{self.label}: {self.word} ({self.problem})"


@dataclass(frozen=True)
class NoGraphTables(TableOutcome):
    """A warm side that ran live and still wrote no graph table."""

    word: ClassVar[str] = "NO GRAPH TABLES"

    def short(self) -> str:
        return f"{self.label}: {self.word} (the check ran live)"


@dataclass(frozen=True)
class Missed(Outcome):
    """A certificate whose prediction left out files of the truth set."""

    word: ClassVar[str] = "MISSED"
    label: str
    files: int

    def short(self) -> str:
        return f"{self.label}: {self.word} ({self.files} files of the truth set not predicted)"


@dataclass(frozen=True)
class NoPrediction(Outcome):
    """A certificate with no prediction certified nothing."""

    word: ClassVar[str] = "NO PREDICTION"
    label: str

    def short(self) -> str:
        return f"{self.label}: {self.word} (the mode made no prediction)"


@dataclass(frozen=True)
class Held(Outcome):
    """A certificate whose prediction holds every item of the truth set. Nothing was compared, so
    its word is HELD, not EQUAL."""

    word: ClassVar[str] = "HELD"
    label: str
    truth: int

    @property
    def ok(self) -> bool:
        return True

    def short(self) -> str:
        return f"{self.label}: {self.word} ({self.truth} files of the truth set predicted)"


@dataclass(frozen=True)
class Lacking(Outcome):
    """A verdict the mode owes that the pair does not have."""

    word: ClassVar[str] = "NO VERDICT"
    label: str

    def short(self) -> str:
        return f"{self.label}: {self.word} (the mode owes it)"


def labelled(name: str, what: str) -> str:
    """A verdict's label with what its case did, as `name (what)`."""
    return f"{name} ({what})" if what else name


def graph_tables(diffs: tuple[RowDiff, ...]) -> int:
    return sum(diff.name not in CHECK_OUTPUTS for diff in diffs)


def compare(label: str, base: CheckDump, other: CheckDump) -> Outcome:
    """The only maker of an outcome that compared tables. `base` is a cold check. Every table both
    dumps hold is compared in order and as a multiset; a warm side must show that it reused
    stored work."""
    if base.side != COLD:
        raise ValueError(f"a comparison stands on a cold check, not a {base.side} one")
    if base.engine != other.engine:
        return Refused(label, (base.engine, other.engine))
    for dump in (base, other):
        problem = cold_problem(dump.meta) if dump.side == COLD else ""
        if problem:
            return NotCold(label, problem)
    diffs = tuple(compare_dumps(base.path, other.path, (*CHECK_OUTPUTS, *GRAPH_OUTPUTS), skip_missing=True))
    # Every check writes these tables; one missing on either side leaves nothing to stand on.
    missing = tuple(name for name in CHECK_OUTPUTS if not (base.has(name) and other.has(name)))
    if missing:
        return NotCompared(label, diffs, missing)
    if other.side == WARM:
        problem = reuse_problem(other.meta)
        if problem:
            return NoReuse(label, diffs, problem)
        if not other.memo and graph_tables(diffs) == 0:
            return NoGraphTables(label, diffs)
    return Compared(label, diffs, other.memo)


def certify(score: PredictionScore | None) -> Outcome:
    """A certificate's verdict: its prediction must hold every item of the truth set. A mode
    that made no prediction certified nothing."""
    if score is None:
        return NoPrediction("prediction")
    if score.missed:
        return Missed("prediction", len({item.home for item in score.missed}))
    return Held("prediction", len(score.truth))


# ── the truth set ─────────────────────────────────────────────────


@dataclass(frozen=True)
class TruthItem:
    """A row group that differs between two cold dumps. `home` is the file it belongs to, its
    site file; `files` are every file it names, so a prediction that holds any of them covers it."""

    table: str
    key: str
    home: str
    files: frozenset[str]
    # The rule of a finding or an incomplete item, the module of a label row.
    rule: str = ""
    module: str = ""


class FileDigests:
    """An order-free digest per key: the sum of its rows' hashes, so it moves only when its rows do."""

    def __init__(self) -> None:
        self.sums: dict[tuple[str, str], int] = defaultdict(int)

    def add(self, key: tuple[str, str], text: str) -> None:
        self.sums[key] = (self.sums[key] + int.from_bytes(row_key(text))) % DIGEST_MOD


class RowCounts:
    """Rows counted by digest, each with the files it names and its rule."""

    def __init__(self) -> None:
        self.counts: Counter[bytes] = Counter()
        self.files: dict[bytes, tuple[str, list[str]]] = {}
        self.rules: dict[bytes, str] = {}

    def add(self, text: str, home: str, files: list[str], rule: str = "") -> None:
        key = row_key(text)
        self.counts[key] += 1
        self.rules[key] = rule
        known = self.files.get(key)
        self.files[key] = (known[0], sorted({*known[1], *files})) if known else (home, files)


class DumpIndex:
    """What the truth set reads from one cold dump."""

    def __init__(self, dump: Path) -> None:
        self.dump = dump
        self.modules = dict(json.loads(text) for text in row_texts(dump / "modules.jsonl"))

    def edges(self) -> FileDigests:
        out = FileDigests()
        for text in row_texts(self.dump / "edges.jsonl"):
            row = json.loads(text)
            out.add((row[EDGE_PATH], row[EDGE_PRODUCER]), text)
        return out

    def file_of(self, name: str, modules: dict[str, str]) -> str:
        """The file of a dotted name: its longest module prefix's."""
        return modules.get(longest_prefix(name, modules.__contains__) or "", "")

    def symbols(self, modules: dict[str, str]) -> FileDigests:
        out = FileDigests()
        for text in row_texts(self.dump / "symbols.jsonl"):
            out.add((self.file_of(json.loads(text)[0], modules), ""), text)
        return out

    def labels(self) -> FileDigests:
        """Per file, keyed by (path, module)."""
        out = FileDigests()
        for text in row_texts(self.dump / "labels.jsonl"):
            row = json.loads(text)
            out.add((row[1], row[0]), text)
        return out

    def findings(self, by_key: bool = False) -> RowCounts:
        """Whole rows; with `by_key`, only (rule, identity, status), as spike N7 counted them, so
        a finding whose message or witnesses move but whose key stays does not count."""
        out = RowCounts()
        pairs = zip(row_texts(self.dump / "findings.jsonl"), row_texts(self.dump / "finding-files.jsonl"), strict=True)
        for text, files_text in pairs:
            row = json.loads(text)
            files = json.loads(files_text)
            site = row[3][0] if row[3] else (files[0] if files else "")
            out.add(json.dumps([row[0], row[1], row[7]]) if by_key else text, site, files, row[0])
        return out

    def incomplete(self) -> RowCounts:
        out = RowCounts()
        pairs = zip(row_texts(self.dump / "incomplete.jsonl"), row_texts(self.dump / "incomplete-files.jsonl"))
        for text, files_text in pairs:
            files = json.loads(files_text)
            out.add(text, files[0] if files else "", files, json.loads(text)[0])
        return out


class TruthSet:
    """The files whose rows differ between a parent's and a child's cold dumps: edges per site file
    and producer, symbols and labels per file, findings and incomplete items per site file."""

    def __init__(self, parent: Path, child: Path) -> None:
        self.parent = DumpIndex(parent)
        self.child = DumpIndex(child)
        self.items: list[TruthItem] = []
        # The same set with findings counted by key: what the spike's numbers mean.
        self.key_items: list[TruthItem] = []

    def compute(self) -> TruthSet:
        modules = {**self.parent.modules, **self.child.modules}
        self.items.extend(self.digests("edges", self.parent.edges(), self.child.edges()))
        self.items.extend(self.digests("symbols", self.parent.symbols(modules), self.child.symbols(modules)))
        self.items.extend(self.digests("labels", self.parent.labels(), self.child.labels()))
        self.items.extend(self.counts("incomplete", self.parent.incomplete(), self.child.incomplete()))
        self.key_items = [*self.items, *self.counts("findings", self.parent.findings(True), self.child.findings(True))]
        self.items.extend(self.counts("findings", self.parent.findings(), self.child.findings()))
        return self

    @staticmethod
    def digests(table: str, left: FileDigests, right: FileDigests) -> list[TruthItem]:
        out = []
        for key in sorted(set(left.sums) | set(right.sums)):
            if left.sums.get(key) == right.sums.get(key):
                continue
            path, producer = key
            module = producer if table == "labels" else ""
            out.append(TruthItem(table, producer, path, frozenset({path}), module=module))
        return out

    @staticmethod
    def counts(table: str, left: RowCounts, right: RowCounts) -> list[TruthItem]:
        out = []
        for key in sorted(set(left.counts) | set(right.counts)):
            if left.counts[key] == right.counts[key]:
                continue
            home, files = left.files.get(key) or right.files[key]
            rule = left.rules.get(key) or right.rules.get(key, "")
            out.append(TruthItem(table, key.hex()[:12], home, frozenset(files) | {home}, rule=rule))
        return out

    @property
    def files(self) -> set[str]:
        return {item.home for item in self.items}


@dataclass(frozen=True)
class PredictionScore:
    """A predicted set of files against the truth: an item is missed when no file it names is
    predicted; `site_misses` counts the files of the truth set the prediction left out."""

    truth: frozenset[str]
    predicted: frozenset[str]
    missed: tuple[TruthItem, ...]
    missed_by_key: tuple[TruthItem, ...]

    @property
    def site_misses(self) -> frozenset[str]:
        return self.truth - self.predicted

    @property
    def overshoot(self) -> frozenset[str]:
        return self.predicted - self.truth

    def short(self) -> str:
        missed = sorted({f"{item.home} ({item.table})" for item in self.missed})
        text = f"truth {len(self.truth)} predicted {len(self.predicted)} missed {len({i.home for i in self.missed})}"
        text += f" (site-only {len(self.site_misses)}, by finding key {len({i.home for i in self.missed_by_key})})"
        text += f" overshoot {len(self.overshoot)}"
        return text + (f" missed {missed}" if missed else "")

    @staticmethod
    def covers(item: TruthItem, predicted: Prediction) -> bool:
        """A file the item names is predicted; a label row's module is relabelled; a finding or
        an incomplete item belongs to a folded rule that reruns."""
        if item.files & predicted.files:
            return True
        if item.table == "labels":
            return predicted.labels is None or item.module in predicted.labels
        return bool(item.rule) and item.rule in predicted.rerun

    @staticmethod
    def of(truth: TruthSet, predicted: Prediction) -> PredictionScore:
        missed = tuple(item for item in truth.items if not PredictionScore.covers(item, predicted))
        by_key = tuple(item for item in truth.key_items if not PredictionScore.covers(item, predicted))
        return PredictionScore(frozenset(truth.files), predicted.files, missed, by_key)


# ── modes ─────────────────────────────────────────────────────────


def git_with_index(repo: Path, index: Path, *args: str) -> str:
    """A git command in `repo` that reads and writes the index file `index`."""
    env = {**os.environ, "GIT_INDEX_FILE": str(index)}
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env).stdout


def lay_tree(repo: Path, tree: str, files: dict[str, Path]) -> str:
    """`tree` with each path's blob replaced by the bytes of a local file, written as a new tree."""
    with tempfile.TemporaryDirectory() as scratch:
        index = Path(scratch) / "index"
        git_with_index(repo, index, "read-tree", tree)
        for path, source in files.items():
            blob = git(repo, "hash-object", "-w", str(source)).strip()
            git_with_index(repo, index, "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}")
        return git_with_index(repo, index, "write-tree").strip()


@dataclass(frozen=True)
class Pair:
    name: str
    parent: str
    child: str

    @staticmethod
    def read(path: Path) -> list[Pair]:
        """Tab-separated: name, parent tree, child tree; further columns are ignored."""
        out = []
        for line in path.read_text().splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            name, parent, child = line.split("\t")[:3]
            out.append(Pair(name, parent, child))
        return out

    def laid(self, repo: Path, files: dict[str, Path]) -> Pair:
        """Both trees with the files laid over them. HEAD is the parent tree, so the cold child's
        baseline is checked with them too."""
        if not files:
            return self
        return Pair(self.name, lay_tree(repo, self.parent, files), lay_tree(repo, self.child, files))


def changed_paths(repo: Path, pair: Pair) -> set[str]:
    """The paths the commit from the pair's parent tree to its child tree changed."""
    return set(records(git(repo, "diff-tree", "-r", "-z", "--no-renames", "--name-only", pair.parent, pair.child)))


class Mode:
    """What a check reuses. Today's one mode is the code as it is: the stored summary, the run
    memo and the fact cache. A later mode names itself and gives the environment that switches
    it on; it may predict the files a child changes, and the harness checks that against the truth."""

    name = "today"
    # Only the cold parent and the cold child run: the mode checks a prediction, not reuse.
    cold_only = False
    # The verdicts every pair must have; a pair without one fails.
    owes: tuple[str, ...] = ("child", "back")

    def env(self) -> dict[str, str]:
        return {}

    def predict(self, changed: set[str], dumps: tuple[Path, Path]) -> Prediction:
        """The files the commit changed, with no label module relabelled and no folded rule rerun."""
        return Prediction(frozenset(changed), frozenset(), frozenset())

    def gaps(self, truth: TruthSet) -> str:
        """What the prediction misses with parts of it left out; nothing in today's mode."""
        return ""

    def tamper(self, warm_child: Path, cold_parent: Path, truth: TruthSet) -> str:
        """A test mode's wrong reuse, done to the warm child's dump; today's mode does none."""
        return ""


class PlantedMiss(Mode):
    """Test only: the warm child keeps the parent's edge rows of one file whose rows changed, as a
    wrong reuse would. The replay must report the pair as different."""

    name = "planted-miss"

    def tamper(self, warm_child: Path, cold_parent: Path, truth: TruthSet) -> str:
        changed = sorted(item.home for item in truth.items if item.table == "edges")
        if not changed or not (warm_child / "edges.jsonl").is_file():
            return "nothing to plant"
        planted = changed[0]
        parent_rows = [
            text for text in row_texts(cold_parent / "edges.jsonl") if json.loads(text)[EDGE_PATH] == planted
        ]
        rows: list[str] = []
        placed = False
        for text in row_texts(warm_child / "edges.jsonl"):
            if json.loads(text)[EDGE_PATH] != planted:
                rows.append(text)
                continue
            if not placed:
                rows.extend(parent_rows)
                placed = True
        if not placed:
            rows.extend(parent_rows)
        (warm_child / "edges.jsonl").write_text("".join(f"{text}\n" for text in rows))
        return f"planted the parent's rows of {planted}"


class Traces(Mode):
    """The certificate of stage S5: the prediction from the parent's link traces, and once per
    read kind the prediction with that kind left out of every trace (the planted gaps)."""

    name = "traces"
    cold_only = True
    owes = ("prediction",)

    def __init__(self) -> None:
        self.pair: PairPrediction | None = None

    def env(self) -> dict[str, str]:
        return {TRACE_ENV: "1"}

    def predict(self, changed: set[str], dumps: tuple[Path, Path]) -> Prediction:
        self.pair = PairPrediction(dumps[0], dumps[1], changed)
        return self.pair.predict()

    def gaps(self, truth: TruthSet) -> str:
        """Per read kind left out: the files of the truth set the prediction then misses."""
        if self.pair is None:
            return ""
        found = []
        for kind in self.pair.parent.kinds:
            missed = PredictionScore.of(truth, self.pair.predict([kind])).missed
            if missed:
                found.append(f"{kind}:{len({item.home for item in missed})}")
        return "gaps " + (" ".join(found) or "none")


class PackMemoMiss(Mode):
    """Test only: every pack item's trace leaves out its reads (`HOGLINT_PACK_MEMO_DROP`), as a
    pack memo that stopped checking them would. A pair that changes a stored item's output then
    keeps the stale item, and the replay must report the pair as different. One kind left out
    is not enough on the sample: another read of the same trace carries the same change."""

    name = "pack-memo-miss"

    def env(self) -> dict[str, str]:
        return {PACK_MEMO_DROP: "*"}


MODES: dict[str, type[Mode]] = {mode.name: mode for mode in (Mode, PlantedMiss, Traces, PackMemoMiss)}


# ── state directories and sides ───────────────────────────────────


def size_of(path: Path) -> int:
    """A file's size; 0 for one a running check removed meanwhile."""
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


class State:
    """A check's `XDG_CACHE_HOME`. Its fact cache is the session's shared one, a private copy of
    it, or a private empty one."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.cache = root / "hoglint"

    @property
    def facts(self) -> Path:
        return self.cache / FACTS

    def create(self, shared_facts: Path, facts: str = "shared") -> State:
        if self.root.exists():
            shutil.rmtree(self.root)
        self.cache.mkdir(parents=True)
        if facts == "shared":
            shared_facts.mkdir(parents=True, exist_ok=True)
            self.facts.symlink_to(shared_facts, target_is_directory=True)
        elif facts == "copy" and shared_facts.is_dir():
            # A copy-on-write clone on APFS: a private fact cache that costs no disk until written.
            subprocess.run(["cp", "-cR", str(shared_facts), str(self.facts)], check=True)
        return self

    def files(self, part: str, pattern: str = "*") -> list[Path]:
        return sorted(path for path in (self.cache / part).rglob(pattern) if path.is_file())


class States:
    """The state directories of a session, each made fresh by name, and the one fact cache they
    share."""

    def __init__(self, work: Path) -> None:
        self.root = work / "states"
        self.shared_facts = work / FACTS

    def fresh(self, name: str, facts: str = "shared") -> State:
        return State(self.root / name).create(self.shared_facts, facts)

    def remove(self, name: str) -> None:
        shutil.rmtree(self.root / name, ignore_errors=True)

    def clear(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


@dataclass(frozen=True)
class Filled:
    """A state directory in which a parent's check ran, to its end or until it was killed."""

    state: State


class Side:
    """Where one check runs, with which switches, and what its dump must show.

    - cold: a fresh state with every store of reused answers off;
    - warm: a state a parent's check filled, every store on; it must show that it reused stored work;
    - damaged: a filled state that a damage case touched; a store may fail there, so it shows neither;
    - fill: the parent's check that fills a state; its dump is never compared.
    """

    def __init__(self, kind: str, state: State, env: dict[str, str]) -> None:
        self.kind = kind
        self.state = state
        self.env = env

    @staticmethod
    def cold(states: States, name: str, facts: str = "shared", env: dict[str, str] | None = None) -> Side:
        return Side(COLD, states.fresh(name, facts), {**(env or {}), **COLD_SWITCHES})

    @staticmethod
    def warm(filled: Filled) -> Side:
        return Side(WARM, Side.filled_state(filled), {})

    @staticmethod
    def damaged(filled: Filled) -> Side:
        return Side(DAMAGED, Side.filled_state(filled), {})

    @staticmethod
    def filling(state: State) -> Side:
        return Side(FILL, state, {})

    @staticmethod
    def filled_state(filled: Filled) -> State:
        """A warm or damaged side stands only on a state a parent's check filled: a bare state
        would make it a second cold check that reuses nothing."""
        if not isinstance(filled, Filled):
            raise TypeError(f"a side that reuses stored work needs a filled state, not {type(filled).__name__}")
        return filled.state


# ── the checks ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Running:
    """A started check, and the directory it writes its dump into."""

    process: subprocess.Popen[str]
    out: Path
    side: Side


class Checker:
    """Starts checks, each in its own process, with its side's state directory as its cache root."""

    def __init__(self, repo: Path, engine: str, mode: Mode, work: Path, today: datetime.date) -> None:
        self.repo = repo
        self.engine = engine
        self.mode = mode
        self.work = work
        self.today = today
        self.count = 0

    def command(self, tree: str, head: str, out: Path, tables: bool) -> list[str]:
        command = [sys.executable, "-m", "hoglint.native.replay", "check", "--repo", str(self.repo)]
        command += ["--engine", self.engine, "--today", self.today.isoformat(), "--tree", tree, "--head", head]
        return [*command, "--out", str(out), *(["--tables"] if tables else [])]

    def start(self, side: Side, tree: str, head: str, label: str, tables: bool = True) -> Running:
        self.count += 1
        out = self.work / "dumps" / f"{self.count:04d}-{label}"
        if out.exists():
            shutil.rmtree(out)
        env = check_env(dict(os.environ), {**self.mode.env(), **side.env, "XDG_CACHE_HOME": str(side.state.root)})
        process = subprocess.Popen(
            self.command(tree, head, out, tables),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        return Running(process, out, side)

    def finish(self, running: Running) -> CheckDump:
        process = running.process
        _, stderr = process.communicate()
        if process.returncode != 0 or not (running.out / "check.json").is_file():
            raise CheckFailed(f"exit {process.returncode}: {stderr.strip()[-1500:]}")
        return CheckDump.load(running.out, running.side.kind)

    def check(self, side: Side, tree: str, head: str, label: str, tables: bool = True) -> CheckDump:
        return self.finish(self.start(side, tree, head, label, tables))

    def fill(self, state: State, pair: Pair, label: str) -> Filled:
        """The parent's check in `state`, which a warm or damaged side then reuses."""
        self.check(Side.filling(state), pair.parent, pair.parent, label, tables=False)
        return Filled(state)

    def killed(self, side: Side, tree: str, head: str, label: str, ready: Callable[[], bool]) -> str:
        """Starts a check and kills its process group as soon as `ready` holds; says what happened."""
        running = self.start(side, tree, head, label, tables=False)
        process = running.process
        what = "finished before the kill (not exercised)"
        try:
            while process.poll() is None:
                if ready():
                    what = "killed in its writes"
                    break
                time.sleep(0.002)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            shutil.rmtree(running.out, ignore_errors=True)
        return what


# ── damage ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DamageCase:
    """What a damage case of one pair works with: the cold dumps its checks must equal."""

    checker: Checker
    states: States
    pair: Pair
    cold_parent: CheckDump
    cold_child: CheckDump
    rng: random.Random

    def fill(self, name: str, facts: str = "shared") -> Filled:
        return self.checker.fill(self.states.fresh(name, facts), self.pair, f"{name}-parent")

    def child(self, side: Side, label: str) -> CheckDump:
        return self.checker.check(side, self.pair.child, self.pair.parent, label)

    @staticmethod
    def against(label: str, cold: CheckDump, other: CheckDump) -> Outcome:
        """The comparison, made once with its whole label, so no field is lost to a relabel."""
        outcome = compare(label, cold, other)
        shutil.rmtree(other.path, ignore_errors=True)
        return outcome


class Damage:
    """One damage case, run in a state directory of its own name: the checks after the damage
    must report what the cold checks report, and none may fail (D28)."""

    name = ""

    def run(self, case: DamageCase) -> list[Outcome]:
        raise NotImplementedError


class StoreDamage(Damage):
    """Done to the stored files under `part` of the cache root, after the parent's check and
    before the child's. A damage to the fact cache works on a private copy of it."""

    part = ""

    @property
    def facts(self) -> str:
        return "copy" if self.part == FACTS else "shared"

    def apply(self, state: State, rng: random.Random) -> list[str]:
        raise NotImplementedError

    def run(self, case: DamageCase) -> list[Outcome]:
        filled = case.fill(self.name, self.facts)
        done = self.apply(filled.state, case.rng)
        child = case.child(Side.damaged(filled), f"{self.name}-child")
        what = f"{len(done)} damaged" if done else "nothing to damage"
        return [case.against(labelled(self.name, what), case.cold_child, child)]


class FileDamage(StoreDamage):
    """Every stored file of one kind, under `part` of the cache root, made garbage, empty, cut to
    half, or (for a stored digest) text of the right length that is not a digest."""

    HOW = ("garbage", "empty", "truncate", "not-a-digest")

    def __init__(self, store: str, part: str, pattern: str, how: str) -> None:
        if how not in self.HOW:
            raise ValueError(f"no damage {how}")
        self.name = f"{how}-{store}"
        self.part = part
        self.pattern = pattern
        self.how = how

    def apply(self, state: State, rng: random.Random) -> list[str]:
        return [self.damage(path, rng) for path in state.files(self.part, self.pattern)]

    def damage(self, path: Path, rng: random.Random) -> str:
        size = path.stat().st_size
        if self.how == "garbage":
            path.write_bytes(rng.randbytes(max(size, 16)))
        elif self.how == "empty":
            path.write_bytes(b"")
        elif self.how == "truncate":
            with open(path, "r+b") as handle:
                handle.truncate(size // 2)
        else:
            path.write_bytes(b"z" * size)
        return f"{path.name}: {self.how} ({size} bytes before)"


class RemoveSummarySection(StoreDamage):
    """Removes the section every baseline reads (incompletes) of each stored summary."""

    name = "remove-summary-section"
    part = "summaries"

    def apply(self, state: State, rng: random.Random) -> list[str]:
        done = []
        for manifest in state.files(self.part, "*.json"):
            digest = json.loads(manifest.read_bytes())["sections"]["incompletes"]
            for section in state.files(self.part, f"{digest}.zst"):
                section.unlink()
                done.append(f"removed {section.name}")
        return done


class RemoveInputs(StoreDamage):
    name = "remove-inputs"
    part = "inputs"

    def apply(self, state: State, rng: random.Random) -> list[str]:
        done = []
        for path in state.files(self.part):
            path.unlink()
            done.append(f"removed {path.name}")
        return done


class RemovePackEntry(StoreDamage):
    """Removes one stored migration summary of each pack cache file."""

    name = "remove-pack-entry"
    part = "packs"

    def apply(self, state: State, rng: random.Random) -> list[str]:
        done = []
        for path in state.files(self.part, "*.json"):
            entries = json.loads(path.read_bytes())
            if not entries:
                continue
            key = sorted(entries)[0]
            del entries[key]
            path.write_text(json.dumps(entries))
            done.append(f"removed entry {key[:12]} of {path.name}")
        return done


class SqliteRows(StoreDamage):
    """Damage to rows of every SQLite database under `part`: the fact cache or the pack memo's
    stored parts and states (hoglint/native/memo_store.py)."""

    def apply(self, state: State, rng: random.Random) -> list[str]:
        done = []
        for path in state.files(self.part, "*.sqlite"):
            with contextlib.closing(sqlite3.connect(path)) as conn:
                changed = self.damage(conn)
                conn.commit()
            done.append(f"{path.name}: {changed} rows")
        return done

    def damage(self, conn: sqlite3.Connection) -> int:
        raise NotImplementedError


class RemoveFactRows(SqliteRows):
    """Removes stored fact bodies and records whose key rows stay: a key that leads nowhere."""

    name = "remove-fact-rows"
    part = FACTS

    def damage(self, conn: sqlite3.Connection) -> int:
        removed = conn.execute("DELETE FROM facts WHERE id % 10 = 3").rowcount
        return removed + conn.execute("DELETE FROM records WHERE id % 10 = 7").rowcount


class GarbageFactRows(SqliteRows):
    name = "garbage-fact-rows"
    part = FACTS

    def damage(self, conn: sqlite3.Connection) -> int:
        changed = conn.execute("UPDATE facts SET body = randomblob(length(body)) WHERE id % 10 = 3").rowcount
        return changed + conn.execute("UPDATE records SET json = randomblob(length(json)) WHERE id % 10 = 7").rowcount


class GarbageMemoParts(SqliteRows):
    name = "garbage-memo-parts"
    part = MEMO

    def damage(self, conn: sqlite3.Connection) -> int:
        changed = conn.execute("UPDATE parts SET body = randomblob(length(body)) WHERE digest < '8'").rowcount
        return changed + conn.execute("UPDATE blobs SET body = randomblob(length(body)) WHERE id % 3 = 1").rowcount


class RemoveMemoParts(SqliteRows):
    """Removes parts that states still name, and some states' manifests: names that lead nowhere."""

    name = "remove-memo-parts"
    part = MEMO

    def damage(self, conn: sqlite3.Connection) -> int:
        removed = conn.execute("DELETE FROM parts WHERE digest < '8'").rowcount
        return removed + conn.execute("DELETE FROM blobs WHERE id % 3 = 2").rowcount


class TwoChildrenAtOnce(Damage):
    """Two child checks at once in one filled state."""

    name = "two-children-at-once"

    def run(self, case: DamageCase) -> list[Outcome]:
        filled = case.fill(self.name)
        checker, pair = case.checker, case.pair
        first = checker.start(Side.damaged(filled), pair.child, pair.parent, f"{self.name}-a")
        second = checker.start(Side.damaged(filled), pair.child, pair.parent, f"{self.name}-b")
        dumps = [checker.finish(first), checker.finish(second)]
        return [case.against(f"{self.name}-{n}", case.cold_child, dump) for n, dump in zip("ab", dumps, strict=True)]


class KilledInFactWrites(Damage):
    """The parent's check, killed while it writes the fact cache, then run again, then the child."""

    name = "killed-in-fact-writes"

    def run(self, case: DamageCase) -> list[Outcome]:
        state = case.states.fresh(self.name, facts="empty")
        checker, pair = case.checker, case.pair
        parent = f"{self.name}-parent"
        what = checker.killed(Side.filling(state), pair.parent, pair.parent, parent, lambda: self.writing_facts(state))
        filled = Filled(state)
        again = checker.check(Side.damaged(filled), pair.parent, pair.parent, f"{parent}-again")
        child = case.child(Side.damaged(filled), f"{self.name}-child")
        return [
            case.against(labelled(parent, what), case.cold_parent, again),
            case.against(f"{self.name}-child", case.cold_child, child),
        ]

    @staticmethod
    def writing_facts(state: State) -> bool:
        """The fact database has taken a few megabytes of writes, so the check is in its writes."""
        return any(size_of(path) > 4_000_000 for path in state.files(FACTS, "*.sqlite*"))


class KilledInSummaryWrite(Damage):
    """The child's check, killed while it writes a summary, then run again."""

    name = "killed-in-summary-write"

    def run(self, case: DamageCase) -> list[Outcome]:
        filled = case.fill(self.name)
        state = filled.state
        known = set(state.files("summaries"))
        checker, pair = case.checker, case.pair
        child = f"{self.name}-child"
        what = checker.killed(Side.damaged(filled), pair.child, pair.parent, child, lambda: self.new_file(state, known))
        again = case.child(Side.damaged(filled), f"{child}-again")
        return [case.against(labelled(self.name, what), case.cold_child, again)]

    @staticmethod
    def new_file(state: State, known: set[Path]) -> bool:
        root = state.cache / "summaries"
        return root.is_dir() and any(path not in known for path in root.rglob("*"))


DAMAGES: tuple[Damage, ...] = (
    *(FileDamage(store, part, pattern, how) for store, part, pattern in CACHE_FILES for how in DAMAGE_HOW),
    FileDamage("inputs", "inputs", "*", "not-a-digest"),
    RemoveSummarySection(),
    RemoveInputs(),
    RemovePackEntry(),
    RemoveFactRows(),
    GarbageFactRows(),
    GarbageMemoParts(),
    RemoveMemoParts(),
    TwoChildrenAtOnce(),
    KilledInFactWrites(),
    KilledInSummaryWrite(),
)


# ── the harness ───────────────────────────────────────────────────


@dataclass(frozen=False)
class PairResult:
    pair: Pair
    verdicts: list[Outcome] = field(default_factory=list)
    prediction: PredictionScore | None = None
    cold: CheckDump | None = None
    warm: CheckDump | None = None
    notes: list[str] = field(default_factory=list)
    # The labels of the verdicts the mode owes (`Mode.owes`).
    owed: tuple[str, ...] = ()

    def outcomes(self) -> list[Outcome]:
        """The verdicts, and a NO VERDICT for each owed one the pair lacks. A failing verdict
        already says why later ones are missing, so then nothing is added."""
        if not all(verdict.ok for verdict in self.verdicts):
            return list(self.verdicts)
        labels = {verdict.label for verdict in self.verdicts}
        return [*self.verdicts, *(Lacking(label) for label in self.owed if label not in labels)]

    @property
    def ok(self) -> bool:
        outcomes = self.outcomes()
        return bool(outcomes) and all(outcome.ok for outcome in outcomes)

    @property
    def memo(self) -> bool:
        """The warm child was served by the run memo, so no graph table of it was compared."""
        return any(verdict.label == "child" and verdict.memo for verdict in self.verdicts)

    def status(self) -> str:
        """The words of the failing outcomes; else NOT EQUAL for a failure with no word of its
        own; else the words of the outcomes that hold, such as HELD; else MEMO or EQUAL."""
        outcomes = self.outcomes()
        failed = [outcome.word for outcome in outcomes if not outcome.ok and outcome.word]
        if failed:
            return " ".join(dict.fromkeys(failed))
        if not self.ok:
            return "NOT EQUAL"
        held = [outcome.word for outcome in outcomes if outcome.word]
        if held:
            return " ".join(dict.fromkeys(held))
        return "MEMO" if self.memo else "EQUAL"

    def timing(self) -> str:
        if self.cold is None or self.warm is None:
            return ""
        return f"cold {self.cold.wall:.1f}s/{self.cold.cpu:.1f}cpu warm {self.warm.wall:.1f}s/{self.warm.cpu:.1f}cpu"

    def line(self) -> str:
        parts = [self.pair.name, self.status(), *(outcome.short() for outcome in self.outcomes())]
        parts.append(self.timing())
        if self.prediction is not None:
            parts.append(self.prediction.short())
        parts.extend(self.notes)
        return "  ".join(part for part in parts if part)


class Replay:
    """Replays pairs: cold parent, cold child, warm child, back again, and the damage cases."""

    def __init__(self, checker: Checker, damages: tuple[Damage, ...]) -> None:
        self.checker = checker
        self.damages = damages
        self.work = checker.work
        self.states = States(self.work)
        self.rng = random.Random(0)

    def run(self, pair: Pair) -> PairResult:
        result = PairResult(pair, owed=self.checker.mode.owes)
        try:
            self.replay(pair, result)
        except CheckFailed as error:
            result.verdicts.append(Failed("check", str(error)))
        finally:
            shutil.rmtree(self.work / "dumps", ignore_errors=True)
            self.states.clear()
        return result

    def replay(self, pair: Pair, result: PairResult) -> None:
        checker = self.checker
        cold_parent = checker.check(Side.cold(self.states, "cold-parent"), pair.parent, pair.parent, "cold-parent")
        cold_child = checker.check(Side.cold(self.states, "cold-child"), pair.child, pair.parent, "cold-child")
        result.cold = cold_child
        for label, dump in (("cold-parent", cold_parent), ("cold-child", cold_child)):
            problem = cold_problem(dump.meta)
            if problem:
                result.verdicts.append(NotCold(label, problem))
                return
        truth = TruthSet(cold_parent.path, cold_child.path).compute()
        mode = checker.mode
        predicted = mode.predict(changed_paths(checker.repo, pair), (cold_parent.path, cold_child.path))
        result.prediction = PredictionScore.of(truth, predicted)
        gaps = mode.gaps(truth)
        if gaps:
            result.notes.append(gaps)
        if mode.cold_only:
            result.verdicts.append(certify(result.prediction))
            return
        filled = checker.fill(self.states.fresh("warm"), pair, "warm-parent")
        result.warm = warm_child = checker.check(Side.warm(filled), pair.child, pair.parent, "warm-child")
        planted = mode.tamper(warm_child.path, cold_parent.path, truth)
        if planted:
            result.notes.append(planted)
        result.verdicts.append(compare("child", cold_child, warm_child))
        back = checker.check(Side.warm(filled), pair.parent, pair.parent, "back")
        result.verdicts.append(compare("back", cold_parent, back))
        shutil.rmtree(warm_child.path)
        shutil.rmtree(back.path)
        case = DamageCase(checker, self.states, pair, cold_parent, cold_child, self.rng)
        for damage in self.damages:
            result.verdicts.extend(self.damaged(damage, case))

    def damaged(self, damage: Damage, case: DamageCase) -> list[Outcome]:
        """One damage case; a check that fails is the finding, reported as a failed run."""
        try:
            return damage.run(case)
        except CheckFailed as error:
            return [Failed(damage.name, str(error))]
        finally:
            self.states.remove(damage.name)


class PairsReport:
    """The per-pair lines, the differing rows, and the time of the cold and warm child checks."""

    def __init__(self) -> None:
        self.results: list[PairResult] = []

    def add(self, result: PairResult) -> None:
        self.results.append(result)
        sys.stdout.write(result.line() + "\n")
        for verdict in result.verdicts:
            details = verdict.details()
            if details is not None:
                sys.stdout.write(details + "\n")
        sys.stdout.flush()

    def times(self, pick: Callable[[PairResult], CheckDump | None]) -> str:
        outcomes = [o for o in (pick(r) for r in self.results) if o is not None]
        if not outcomes:
            return "no checks"
        walls, cpus = [o.wall for o in outcomes], [o.cpu for o in outcomes]
        return (
            f"wall median {statistics.median(walls):.2f}s worst {max(walls):.2f}s, "
            f"cpu median {statistics.median(cpus):.2f}s worst {max(cpus):.2f}s"
        )

    def close(self) -> bool:
        equal = sum(result.ok for result in self.results)
        memo = sum(result.ok and result.memo for result in self.results)
        sys.stdout.write(f"\n{equal} of {len(self.results)} pairs equal ({memo} of them served by the run memo)\n")
        sys.stdout.write(f"cold child: {self.times(lambda r: r.cold)}\n")
        sys.stdout.write(f"warm child: {self.times(lambda r: r.warm)}\n")
        predictions = [r for r in self.results if r.prediction is not None]
        clean = [r.pair.name for r in predictions if r.prediction is not None and not r.prediction.missed]
        missed = [r.pair.name for r in predictions if r.prediction is not None and r.prediction.missed]
        sys.stdout.write(f"prediction: no miss in {len(clean)} of {len(predictions)} pairs; misses in {missed}\n")
        by_key = [r.pair.name for r in predictions if r.prediction is not None and r.prediction.missed_by_key]
        sys.stdout.write(f"prediction, findings by key: misses in {len(by_key)} pairs: {by_key}\n")
        # A run that compared no pair proves nothing, so it fails.
        return bool(self.results) and equal == len(self.results)


class Determinism:
    """The cold dump of a tree under every hash seed and thread count, each with an empty fact
    cache, compared with the first."""

    def __init__(self, checker: Checker) -> None:
        self.checker = checker
        self.work = checker.work
        self.states = States(self.work)

    def run(self, tree: str) -> bool:
        ok = True
        first: CheckDump | None = None
        try:
            for seed in SEEDS:
                for threads in THREADS:
                    label = f"seed{seed}-threads{threads}"
                    side = Side.cold(self.states, label, facts="empty", env=seed_env(seed, threads, threads))
                    try:
                        dump = self.checker.check(side, tree, tree, label)
                    except CheckFailed as error:
                        sys.stdout.write(f"{tree[:11]} {label}: FAILED RUN: {error}\n")
                        ok = False
                        continue
                    finally:
                        self.states.remove(label)
                    if first is None:
                        first = dump
                        sys.stdout.write(f"{tree[:11]} {label}: first ({dump.wall:.1f}s)\n")
                        continue
                    verdict = compare(label, first, dump)
                    shutil.rmtree(dump.path)
                    sys.stdout.write(f"{tree[:11]} {verdict.short()} ({dump.wall:.1f}s)\n")
                    if not verdict.ok:
                        sys.stdout.write((verdict.details() or "") + "\n")
                    ok = ok and verdict.ok
                    sys.stdout.flush()
        finally:
            shutil.rmtree(self.work / "dumps", ignore_errors=True)
            self.states.clear()
        return ok


def pick_damages(text: str) -> tuple[Damage, ...]:
    if not text:
        return ()
    if text == "all":
        return DAMAGES
    by_name = {damage.name: damage for damage in DAMAGES}
    names = tuple(name.strip() for name in text.split(","))
    unknown = [name for name in names if name not in by_name]
    if unknown:
        raise SystemExit(f"unknown damage {unknown}; known: {', '.join(by_name)}")
    return tuple(by_name[name] for name in names)


def laid_files(entries: list[str]) -> dict[str, Path]:
    files = {}
    for entry in entries:
        path, sep, source = entry.partition("=")
        if not sep or not Path(source).is_file():
            raise SystemExit(f"--lay takes <path in tree>=<existing local file>, not {entry!r}")
        files[path] = Path(source).resolve()
    return files


def run_pairs(args: argparse.Namespace, checker: Checker) -> bool:
    pairs = [pair for path in args.pairs for pair in Pair.read(path)]
    if args.only:
        wanted = set(args.only.split(","))
        pairs = [pair for pair in pairs if pair.name in wanted]
    files = laid_files(args.lay)
    pairs = [pair.laid(checker.repo, files) for pair in pairs]
    replay = Replay(checker, pick_damages(args.damage))
    report = PairsReport()
    for pair in pairs:
        report.add(replay.run(pair))
    return report.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("pairs", "determinism", "check"))
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--engine", choices=ENGINES, default="native")
    parser.add_argument("--work", type=Path)
    parser.add_argument("--pairs", type=Path, action="append", default=[])
    parser.add_argument("--only", default="")
    parser.add_argument("--mode", choices=sorted(MODES), default=Mode.name)
    parser.add_argument("--damage", default="")
    parser.add_argument("--lay", action="append", default=[])
    parser.add_argument("--tree", action="append", default=[])
    parser.add_argument("--head", default="")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--tables", action="store_true")
    parser.add_argument("--today", default="")
    args = parser.parse_args()
    repo = args.repo.resolve()
    today = datetime.date.fromisoformat(args.today) if args.today else datetime.date.today()
    if args.command == "check":
        if args.out is None or len(args.tree) != 1:
            raise SystemExit("check needs --out and one --tree")
        out = args.out.resolve()
        work = out.parent / f"{out.name}.work"
        work.mkdir(parents=True, exist_ok=True)
        try:
            TreeCheck(repo, args.engine, today, args.tree[0], args.head or args.tree[0], out, args.tables).run(work)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return
    if args.work is None:
        raise SystemExit("--work is required")
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    checker = Checker(repo, args.engine, MODES[args.mode](), work, today)
    if args.command == "determinism":
        results = [Determinism(checker).run(tree) for tree in args.tree]
        ok = bool(results) and all(results)
    else:
        ok = run_pairs(args, checker)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
