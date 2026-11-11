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
gaps). Only the two cold checks run.

Usage:
  python -m hoglint.native.replay pairs --repo <git repo> --pairs <tsv> [--pairs <tsv>] --work <dir>
      [--engine native] [--mode today|planted-miss|traces] [--only <name,...>] [--damage all|<name,...>]
  python -m hoglint.native.replay determinism --repo <git repo> --tree <tree> [--tree ...] --work <dir>
      the cold dump of each tree under three hash seeds and one and four threads, each with an
      empty fact cache, compared with the first
  python -m hoglint.native.replay check ...
      one check in this process (the harness runs it)

Exit 0 only when every compared table is equal and no check failed.
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
import contextlib
import statistics
import subprocess
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hoglint.cli import Commands, Run, Snapshots
from hoglint.ledger.scan import SUMMARY_SECTIONS, ScanSummary
from hoglint.model import Finding, Graph
from hoglint.native.config import TRACE_ENV
from hoglint.native.graph import NativeGraph
from hoglint.native.normal import FIELDS, Row
from hoglint.native.parity import (
    LIST_OUTPUTS,
    VALUE_OUTPUTS,
    RowDiff,
    Tables,
    diff_lines,
    row_key,
    row_text,
    row_texts,
    stored_summary,
)
from hoglint.native.predict import (
    KIND_NAMES,
    PairPrediction,
    Prediction as Predicted,
    write_trace_dump,
)
from hoglint.native.source import ENGINES, batch_mode
from hoglint.rules.base import IncompleteItem

# The tables a check writes besides the parity tables, and the ones every check writes.
GRAPH_OUTPUTS = (*LIST_OUTPUTS, "labels", "finding-files", "incomplete-files")
CHECK_OUTPUTS = ("check-summary", "check-report", "check-exit")
# The summary sections a stored summary stands in for a run with (`RunMemo.SECTIONS`).
MEMO_SECTIONS = ("envelope", "incompletes", "findings", "levels")
# Edge rows: where the site path and the producer sit.
EDGE_PATH = FIELDS.index("path")
EDGE_PRODUCER = FIELDS.index("producer")
DIGEST_MOD = 2**128
SEEDS = ("0", "1", "4242")
THREADS = ("1", "4")
SHOWN_ROWS = 5


# ── one check, in its own process ─────────────────────────────────


class FixedSnapshots(Snapshots):
    """Snapshots whose index is `tree` and whose HEAD is `head`, so no checkout and no commit is needed."""

    def __init__(self, repo: Path, work_dir: Path, tree: str, head: str) -> None:
        super().__init__(repo, work_dir)
        self.tree = tree
        self.trees["HEAD"] = head

    def write_tree(self) -> str:
        return self.tree


class RecordingCommands(Commands):
    """`check --index` of a fixed tree that keeps what the run made: its graph when it ran live
    (`write_outputs` gets every live run) and the summary it reported (`report` gets every check)."""

    def __init__(self, repo: Path, work_dir: Path, engine: str, today: datetime.date, tree: str, head: str) -> None:
        super().__init__(repo, work_dir, engine, today)
        self.snapshots = FixedSnapshots(repo, work_dir, tree, head)
        self.baselines.snapshots = self.snapshots
        if self.memo is not None:
            self.memo.snapshots = self.snapshots
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
            write_trace_dump(graph.link, run.result.config, self.out)


# ── dumps and their comparison ────────────────────────────────────


@dataclass(frozen=True)
class CheckOutcome:
    """A finished check: where its files are, its time, and whether a stored summary stood in."""

    out: Path
    wall: float
    cpu: float
    memo: bool


class CheckFailed(Exception):
    """A check that did not finish: the run failed, which a cache problem must never cause."""


def compare_tables(left: Path, right: Path) -> tuple[list[RowDiff], list[str]]:
    """Every table both dumps hold, in order and as multisets; and the tables only one holds."""
    diffs, skipped = [], []
    for name in (*CHECK_OUTPUTS, *GRAPH_OUTPUTS):
        a, b = left / f"{name}.jsonl", right / f"{name}.jsonl"
        if not a.is_file() or not b.is_file():
            skipped.append(name)
            continue
        fields = FIELDS if name == "edges" else ()
        diffs.append(diff_lines(name, row_texts(a), row_texts(b), fields))
    for name in VALUE_OUTPUTS:
        a, b = left / f"{name}.json", right / f"{name}.json"
        if not a.is_file() or not b.is_file():
            skipped.append(name)
            continue
        diffs.append(diff_lines(name, [row_text(json.loads(a.read_text()))], [row_text(json.loads(b.read_text()))]))
    return diffs, skipped


@dataclass(frozen=True)
class Verdict:
    """One comparison of two dumps, as one short text and the rows of each differing table."""

    label: str
    diffs: tuple[RowDiff, ...]
    skipped: tuple[str, ...]
    note: str = ""

    @property
    def equal(self) -> bool:
        return not self.note and all(diff.equal for diff in self.diffs)

    def short(self) -> str:
        if self.note:
            return f"{self.label}: {self.note}"
        if self.equal:
            memo = f", {len(self.skipped)} graph tables not built (memo)" if self.skipped else ""
            return f"{self.label}: equal ({len(self.diffs)} tables{memo})"
        different = ",".join(diff.name for diff in self.diffs if not diff.equal)
        return f"{self.label}: DIFFERENT ({different})"

    def details(self) -> str:
        return "\n".join(f"  [{self.label}] {diff.render()}" for diff in self.diffs if not diff.equal)

    @staticmethod
    def of(label: str, left: Path, right: Path) -> Verdict:
        """Refuses two checks of different engines: a replay across two engine versions proves nothing."""
        engines = [json.loads((side / "check.json").read_text()).get("engine", "") for side in (left, right)]
        if engines[0] != engines[1]:
            return Verdict(label, (), (), f"REFUSED: the two checks ran different engines ({engines[0]}, {engines[1]})")
        diffs, skipped = compare_tables(left, right)
        return Verdict(label, tuple(diffs), tuple(skipped))

    @staticmethod
    def failed(label: str, error: str) -> Verdict:
        return Verdict(label, (), (), f"FAILED RUN: {error}")


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
        module = name
        while module and module not in modules:
            module = module.rpartition(".")[0]
        return modules.get(module, "")

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
class Prediction:
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
    def covers(item: TruthItem, predicted: set[str] | Predicted) -> bool:
        """A file the item names is predicted; a label row's module is relabelled; a finding or
        an incomplete item belongs to a folded rule that reruns."""
        if isinstance(predicted, set):
            return bool(item.files & predicted)
        if item.files & predicted.files:
            return True
        if item.table == "labels":
            return predicted.labels is None or item.module in predicted.labels
        return bool(item.rule) and item.rule in predicted.rerun

    @staticmethod
    def of(truth: TruthSet, predicted: set[str] | Predicted) -> Prediction:
        missed = tuple(item for item in truth.items if not Prediction.covers(item, predicted))
        by_key = tuple(item for item in truth.key_items if not Prediction.covers(item, predicted))
        files = predicted if isinstance(predicted, set) else set(predicted.files)
        return Prediction(frozenset(truth.files), frozenset(files), missed, by_key)


# ── modes ─────────────────────────────────────────────────────────


class Git:
    def __init__(self, repo: Path) -> None:
        self.repo = repo

    def run(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True, text=True).stdout

    def changed(self, parent: str, child: str) -> set[str]:
        out = self.run("diff-tree", "-r", "-z", "--no-renames", "--name-only", parent, child)
        return {path for path in out.split("\0") if path}


class Mode:
    """What a check reuses. Today's one mode is the code as it is: the stored summary, the run
    memo and the fact cache. A later mode names itself and gives the environment that switches
    it on; it may predict the files a child changes, and the harness checks that against the truth."""

    name = "today"
    # Only the cold parent and the cold child run: the mode checks a prediction, not reuse.
    cold_only = False

    def env(self) -> dict[str, str]:
        return {}

    def predict(self, git: Git, pair: Pair, dumps: tuple[Path, Path]) -> set[str] | Predicted | None:
        """The files the commit changed."""
        return git.changed(pair.parent, pair.child)

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

    def __init__(self) -> None:
        self.pair: PairPrediction | None = None

    def env(self) -> dict[str, str]:
        return {TRACE_ENV: "1"}

    def predict(self, git: Git, pair: Pair, dumps: tuple[Path, Path]) -> Predicted:
        self.pair = PairPrediction(dumps[0], dumps[1], git.changed(pair.parent, pair.child))
        return self.pair.predict()

    def gaps(self, truth: TruthSet) -> str:
        """Per read kind left out: the files of the truth set the prediction then misses."""
        if self.pair is None:
            return ""
        found = []
        for kind in KIND_NAMES:
            missed = Prediction.of(truth, self.pair.predict([kind])).missed
            if missed:
                found.append(f"{kind}:{len({item.home for item in missed})}")
        return "gaps " + (" ".join(found) or "none")
class PackMemoMiss(Mode):
    """Test only: every pack item's trace leaves out the files it read (`HOGLINT_PACK_MEMO_DROP`),
    as a pack memo that forgot a read would. A pair that edits a file whose own pack item output
    changes (a models file whose classes move, say) then keeps the stale item, and the replay
    must report the pair as different."""

    name = "pack-memo-miss"

    def env(self) -> dict[str, str]:
        return {"HOGLINT_PACK_MEMO_DROP": "file"}


MODES: dict[str, type[Mode]] = {mode.name: mode for mode in (Mode, PlantedMiss, Traces, PackMemoMiss)}


# ── state directories ─────────────────────────────────────────────


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
        return self.cache / "facts"

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

    def remove(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


# ── damage ────────────────────────────────────────────────────────


class Damage:
    """Done to a state directory after the parent's check and before the child's. `facts` says
    which fact cache the state needs: `copy` when the damage writes into it."""

    name = ""
    facts = "shared"

    def apply(self, state: State, rng: random.Random) -> list[str]:
        raise NotImplementedError


class FileDamage(Damage):
    """Every stored file of one kind, under `part` of the cache root, made garbage, empty, cut to
    half, or (for a stored digest) text of the right length that is not a digest."""

    HOW = ("garbage", "empty", "truncate", "not-a-digest")

    def __init__(self, store: str, part: str, pattern: str, how: str, facts: str = "shared") -> None:
        if how not in self.HOW:
            raise ValueError(f"no damage {how}")
        self.name = f"{how}-{store}"
        self.part = part
        self.pattern = pattern
        self.how = how
        self.facts = facts

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


class RemoveSummarySection(Damage):
    """Removes the section every baseline reads (incompletes) of each stored summary."""

    name = "remove-summary-section"

    def apply(self, state: State, rng: random.Random) -> list[str]:
        done = []
        for manifest in state.files("summaries", "*.json"):
            digest = json.loads(manifest.read_bytes())["sections"]["incompletes"]
            for section in state.files("summaries", f"{digest}.zst"):
                section.unlink()
                done.append(f"removed {section.name}")
        return done


class RemoveInputs(Damage):
    name = "remove-inputs"

    def apply(self, state: State, rng: random.Random) -> list[str]:
        done = []
        for path in state.files("inputs"):
            path.unlink()
            done.append(f"removed {path.name}")
        return done


class RemovePackEntry(Damage):
    """Removes one stored migration summary of each pack cache file."""

    name = "remove-pack-entry"

    def apply(self, state: State, rng: random.Random) -> list[str]:
        done = []
        for path in state.files("packs", "*.json"):
            entries = json.loads(path.read_bytes())
            if not entries:
                continue
            key = sorted(entries)[0]
            del entries[key]
            path.write_text(json.dumps(entries))
            done.append(f"removed entry {key[:12]} of {path.name}")
        return done


class FactRows(Damage):
    """Damage to every tenth stored fact row, in a private copy of the fact cache."""

    facts = "copy"

    def apply(self, state: State, rng: random.Random) -> list[str]:
        done = []
        for path in state.files("facts", "*.sqlite"):
            with contextlib.closing(sqlite3.connect(path)) as conn:
                changed = self.damage(conn)
                conn.commit()
            done.append(f"{path.name}: {changed} rows")
        return done

    def damage(self, conn: sqlite3.Connection) -> int:
        raise NotImplementedError


class RemoveFactRows(FactRows):
    """Removes stored fact bodies and records whose key rows stay: a key that leads nowhere."""

    name = "remove-fact-rows"

    def damage(self, conn: sqlite3.Connection) -> int:
        removed = conn.execute("DELETE FROM facts WHERE id % 10 = 3").rowcount
        return removed + conn.execute("DELETE FROM records WHERE id % 10 = 7").rowcount


class MemoRows(Damage):
    """Damage to the pack memo's stored parts and states (hoglint/native/memo_store.py)."""

    def apply(self, state: State, rng: random.Random) -> list[str]:
        done = []
        for path in state.files("memo", "*.sqlite"):
            with contextlib.closing(sqlite3.connect(path)) as conn:
                changed = self.damage(conn)
                conn.commit()
            done.append(f"{path.name}: {changed} rows")
        return done

    def damage(self, conn: sqlite3.Connection) -> int:
        raise NotImplementedError


class GarbageMemoParts(MemoRows):
    name = "garbage-memo-parts"

    def damage(self, conn: sqlite3.Connection) -> int:
        changed = conn.execute("UPDATE parts SET body = randomblob(length(body)) WHERE digest < '8'").rowcount
        return changed + conn.execute("UPDATE blobs SET body = randomblob(length(body)) WHERE id % 3 = 1").rowcount


class RemoveMemoParts(MemoRows):
    """Removes parts that states still name, and some states' manifests: names that lead nowhere."""

    name = "remove-memo-parts"

    def damage(self, conn: sqlite3.Connection) -> int:
        removed = conn.execute("DELETE FROM parts WHERE digest < '8'").rowcount
        return removed + conn.execute("DELETE FROM blobs WHERE id % 3 = 2").rowcount


class GarbageFactRows(FactRows):
    name = "garbage-fact-rows"

    def damage(self, conn: sqlite3.Connection) -> int:
        changed = conn.execute("UPDATE facts SET body = randomblob(length(body)) WHERE id % 10 = 3").rowcount
        return changed + conn.execute("UPDATE records SET json = randomblob(length(json)) WHERE id % 10 = 7").rowcount


# The files a check reads from the cache root, by store: (store, part, file pattern, fact cache).
CACHE_FILES = (
    ("summaries", "summaries", "*", "shared"),
    ("inputs", "inputs", "*", "shared"),
    ("packs", "packs", "*", "shared"),
    ("fact-database", "facts", "*.sqlite", "copy"),
    ("fact-marks", "facts", "*.sqlite.*", "copy"),
    ("pack-memo", "memo", "*.sqlite", "shared"),
    ("pack-memo-wal", "memo", "*.sqlite-*", "shared"),
)
STATE_DAMAGES: tuple[Damage, ...] = (
    *(
        FileDamage(store, part, pattern, how, facts)
        for store, part, pattern, facts in CACHE_FILES
        for how in ("garbage", "empty", "truncate")
    ),
    FileDamage("inputs", "inputs", "*", "not-a-digest"),
    RemoveSummarySection(),
    RemoveInputs(),
    RemovePackEntry(),
    RemoveFactRows(),
    GarbageFactRows(),
    GarbageMemoParts(),
    RemoveMemoParts(),
)
# Damages that are a way of running checks, not a change to stored files.
RUN_DAMAGES = ("two-children-at-once", "killed-in-fact-writes", "killed-in-summary-write")
DAMAGES = (*(damage.name for damage in STATE_DAMAGES), *RUN_DAMAGES)


# ── the harness ───────────────────────────────────────────────────


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


class Checker:
    """Starts checks, each in its own process, with a state directory as its cache root."""

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

    def start(
        self, tree: str, head: str, state: State, label: str, tables: bool, env: dict[str, str]
    ) -> subprocess.Popen[str]:
        self.count += 1
        out = self.work / "dumps" / f"{self.count:04d}-{label}"
        if out.exists():
            shutil.rmtree(out)
        full_env = {**os.environ, **self.mode.env(), **env, "XDG_CACHE_HOME": str(state.root)}
        return subprocess.Popen(
            self.command(tree, head, out, tables),
            env=full_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )

    @staticmethod
    def out_of(process: subprocess.Popen[str]) -> Path:
        args = process.args
        if not isinstance(args, list):
            raise TypeError("a check is started with an argument list")
        return Path(str(args[args.index("--out") + 1]))

    def finish(self, process: subprocess.Popen[str]) -> CheckOutcome:
        _, stderr = process.communicate()
        out = self.out_of(process)
        if process.returncode != 0 or not (out / "check.json").is_file():
            raise CheckFailed(f"exit {process.returncode}: {stderr.strip()[-1500:]}")
        meta = json.loads((out / "check.json").read_text())
        return CheckOutcome(out, meta["wall"], meta["cpu"], meta["memo"])

    def check(
        self, tree: str, head: str, state: State, label: str, tables: bool = True, env: dict[str, str] | None = None
    ) -> CheckOutcome:
        return self.finish(self.start(tree, head, state, label, tables, env or {}))

    def killed(self, tree: str, head: str, state: State, label: str, ready: Callable[[], bool]) -> str:
        """Starts a check and kills its process group as soon as `ready` holds; says what happened."""
        process = self.start(tree, head, state, label, False, {})
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
            shutil.rmtree(self.out_of(process), ignore_errors=True)
        return what


@dataclass(frozen=False)
class PairResult:
    pair: Pair
    verdicts: list[Verdict] = field(default_factory=list)
    prediction: Prediction | None = None
    cold: CheckOutcome | None = None
    warm: CheckOutcome | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def equal(self) -> bool:
        return bool(self.verdicts) and all(verdict.equal for verdict in self.verdicts)

    def timing(self) -> str:
        if self.cold is None or self.warm is None:
            return ""
        return f"cold {self.cold.wall:.1f}s/{self.cold.cpu:.1f}cpu warm {self.warm.wall:.1f}s/{self.warm.cpu:.1f}cpu"

    def line(self) -> str:
        parts = [self.pair.name, "EQUAL" if self.equal else "NOT EQUAL", *(v.short() for v in self.verdicts)]
        parts.append(self.timing())
        if self.prediction is not None:
            parts.append(self.prediction.short())
        parts.extend(self.notes)
        return "  ".join(part for part in parts if part)


class Replay:
    """Replays pairs: cold parent, cold child, warm child, back again, and the damage cases."""

    def __init__(self, checker: Checker, damages: tuple[str, ...]) -> None:
        self.checker = checker
        self.damages = damages
        self.work = checker.work
        self.shared_facts = self.work / "facts"
        self.git = Git(checker.repo)
        self.rng = random.Random(0)

    def state(self, name: str, facts: str = "shared") -> State:
        return State(self.work / "states" / name).create(self.shared_facts, facts)

    def run(self, pair: Pair) -> PairResult:
        result = PairResult(pair)
        try:
            self.replay(pair, result)
        except CheckFailed as error:
            result.verdicts.append(Verdict.failed("check", str(error)))
        finally:
            shutil.rmtree(self.work / "dumps", ignore_errors=True)
            shutil.rmtree(self.work / "states", ignore_errors=True)
        return result

    def replay(self, pair: Pair, result: PairResult) -> None:
        check = self.checker.check
        cold_parent = check(pair.parent, pair.parent, self.state("cold-parent"), "cold-parent")
        result.cold = cold_child = check(pair.child, pair.parent, self.state("cold-child"), "cold-child")
        truth = TruthSet(cold_parent.out, cold_child.out).compute()
        mode = self.checker.mode
        predicted = mode.predict(self.git, pair, (cold_parent.out, cold_child.out))
        if predicted is not None:
            result.prediction = Prediction.of(truth, predicted)
        gaps = mode.gaps(truth)
        if gaps:
            result.notes.append(gaps)
        if mode.cold_only:
            result.verdicts.append(Verdict("cold", (), ()))
            return
        warm = self.state("warm")
        check(pair.parent, pair.parent, warm, "warm-parent", tables=False)
        result.warm = warm_child = check(pair.child, pair.parent, warm, "warm-child")
        planted = self.checker.mode.tamper(warm_child.out, cold_parent.out, truth)
        if planted:
            result.notes.append(planted)
        result.verdicts.append(Verdict.of("child", cold_child.out, warm_child.out))
        back = check(pair.parent, pair.parent, warm, "back")
        result.verdicts.append(Verdict.of("back", cold_parent.out, back.out))
        shutil.rmtree(warm_child.out)
        shutil.rmtree(back.out)
        for name in self.damages:
            result.verdicts.extend(self.damaged(pair, name, cold_parent.out, cold_child.out))

    def damaged(self, pair: Pair, name: str, cold_parent: Path, cold_child: Path) -> list[Verdict]:
        """One damage case; a check that fails is the finding, reported as a failed run."""
        try:
            return self.damage_case(pair, name, cold_parent, cold_child)
        except CheckFailed as error:
            return [Verdict.failed(name, str(error))]
        finally:
            shutil.rmtree(self.work / "states" / name, ignore_errors=True)

    def damage_case(self, pair: Pair, name: str, cold_parent: Path, cold_child: Path) -> list[Verdict]:
        check = self.checker.check
        if name == "two-children-at-once":
            state = self.state(name)
            check(pair.parent, pair.parent, state, f"{name}-parent", tables=False)
            first = self.checker.start(pair.child, pair.parent, state, f"{name}-a", True, {})
            second = self.checker.start(pair.child, pair.parent, state, f"{name}-b", True, {})
            outcomes = [self.checker.finish(first), self.checker.finish(second)]
            return [self.verdict(f"{name}-{n}", cold_child, o.out) for n, o in zip("ab", outcomes, strict=True)]
        if name == "killed-in-fact-writes":
            state = self.state(name, facts="empty")
            what = self.checker.killed(
                pair.parent, pair.parent, state, f"{name}-parent", lambda: self.writing_facts(state)
            )
            again = check(pair.parent, pair.parent, state, f"{name}-parent-again")
            child = check(pair.child, pair.parent, state, f"{name}-child")
            return [
                self.verdict(f"{name}-parent", cold_parent, again.out, what),
                self.verdict(f"{name}-child", cold_child, child.out),
            ]
        if name == "killed-in-summary-write":
            state = self.state(name)
            check(pair.parent, pair.parent, state, f"{name}-parent", tables=False)
            known = set(state.files("summaries"))
            what = self.checker.killed(
                pair.child, pair.parent, state, f"{name}-child", lambda: self.new_summary_file(state, known)
            )
            child = check(pair.child, pair.parent, state, f"{name}-child-again")
            return [self.verdict(name, cold_child, child.out, what)]
        damage = next(d for d in STATE_DAMAGES if d.name == name)
        state = self.state(name, damage.facts)
        check(pair.parent, pair.parent, state, f"{name}-parent", tables=False)
        done = damage.apply(state, self.rng)
        child = check(pair.child, pair.parent, state, f"{name}-child")
        return [self.verdict(name, cold_child, child.out, f"{len(done)} damaged" if done else "nothing to damage")]

    @staticmethod
    def verdict(label: str, cold: Path, other: Path, what: str = "") -> Verdict:
        verdict = Verdict.of(label, cold, other)
        shutil.rmtree(other, ignore_errors=True)
        if not what:
            return verdict
        return Verdict(f"{label} ({what})", verdict.diffs, verdict.skipped)

    @staticmethod
    def writing_facts(state: State) -> bool:
        """The fact database has taken a few megabytes of writes, so the check is in its writes."""
        return any(size_of(path) > 4_000_000 for path in state.files("facts", "*.sqlite*"))

    @staticmethod
    def new_summary_file(state: State, known: set[Path]) -> bool:
        root = state.cache / "summaries"
        return root.is_dir() and any(path not in known for path in root.rglob("*"))


class PairsReport:
    """The per-pair lines, the differing rows, and the time of the cold and warm child checks."""

    def __init__(self) -> None:
        self.results: list[PairResult] = []

    def add(self, result: PairResult) -> None:
        self.results.append(result)
        sys.stdout.write(result.line() + "\n")
        for verdict in result.verdicts:
            if verdict.diffs and not verdict.equal:
                sys.stdout.write(verdict.details() + "\n")
        sys.stdout.flush()

    def times(self, pick: Callable[[PairResult], CheckOutcome | None]) -> str:
        outcomes = [o for o in (pick(r) for r in self.results) if o is not None]
        if not outcomes:
            return "no checks"
        walls, cpus = [o.wall for o in outcomes], [o.cpu for o in outcomes]
        return (
            f"wall median {statistics.median(walls):.2f}s worst {max(walls):.2f}s, "
            f"cpu median {statistics.median(cpus):.2f}s worst {max(cpus):.2f}s"
        )

    def close(self) -> bool:
        equal = sum(result.equal for result in self.results)
        sys.stdout.write(f"\n{equal} of {len(self.results)} pairs equal\n")
        sys.stdout.write(f"cold child: {self.times(lambda r: r.cold)}\n")
        sys.stdout.write(f"warm child: {self.times(lambda r: r.warm)}\n")
        predictions = [r for r in self.results if r.prediction is not None]
        clean = [r.pair.name for r in predictions if r.prediction is not None and not r.prediction.missed]
        missed = [r.pair.name for r in predictions if r.prediction is not None and r.prediction.missed]
        sys.stdout.write(f"prediction: no miss in {len(clean)} of {len(predictions)} pairs; misses in {missed}\n")
        by_key = [r.pair.name for r in predictions if r.prediction is not None and r.prediction.missed_by_key]
        sys.stdout.write(f"prediction, findings by key: misses in {len(by_key)} pairs: {by_key}\n")
        return equal == len(self.results)


class Determinism:
    """The cold dump of a tree under every hash seed and thread count, each with an empty fact
    cache, compared with the first."""

    def __init__(self, checker: Checker) -> None:
        self.checker = checker
        self.work = checker.work

    def run(self, tree: str) -> bool:
        ok = True
        first: Path | None = None
        try:
            for seed in SEEDS:
                for threads in THREADS:
                    label = f"seed{seed}-threads{threads}"
                    state = State(self.work / "states" / label).create(self.work / "facts", facts="empty")
                    env = {"PYTHONHASHSEED": seed, "HOGLINT_THREADS": threads, "RAYON_NUM_THREADS": threads}
                    try:
                        outcome = self.checker.check(tree, tree, state, label, env=env)
                    except CheckFailed as error:
                        sys.stdout.write(f"{tree[:11]} {label}: FAILED RUN: {error}\n")
                        ok = False
                        continue
                    finally:
                        state.remove()
                    if first is None:
                        first = outcome.out
                        sys.stdout.write(f"{tree[:11]} {label}: first ({outcome.wall:.1f}s)\n")
                        continue
                    verdict = Verdict.of(label, first, outcome.out)
                    shutil.rmtree(outcome.out)
                    sys.stdout.write(f"{tree[:11]} {verdict.short()} ({outcome.wall:.1f}s)\n")
                    if not verdict.equal:
                        sys.stdout.write(verdict.details() + "\n")
                    ok = ok and verdict.equal
                    sys.stdout.flush()
        finally:
            shutil.rmtree(self.work / "dumps", ignore_errors=True)
            shutil.rmtree(self.work / "states", ignore_errors=True)
        return ok


def pick_damages(text: str) -> tuple[str, ...]:
    if not text:
        return ()
    if text == "all":
        return DAMAGES
    names = tuple(name.strip() for name in text.split(","))
    unknown = [name for name in names if name not in DAMAGES]
    if unknown:
        raise SystemExit(f"unknown damage {unknown}; known: {', '.join(DAMAGES)}")
    return names


def run_pairs(args: argparse.Namespace, checker: Checker) -> bool:
    pairs = [pair for path in args.pairs for pair in Pair.read(path)]
    if args.only:
        wanted = set(args.only.split(","))
        pairs = [pair for pair in pairs if pair.name in wanted]
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
        ok = all(results)
    else:
        ok = run_pairs(args, checker)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
