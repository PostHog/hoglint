"""The ten parity tables of a check, and the compare of two dumps, field by field.

A check runs in its own process and dumps normalized outputs: every edge
(core and pack) with every field and attr, symbol facts, modules, failed producers, findings
with their witnesses, incompletes, levels, scan lines, the coupling count of every edge, and the
run summary as the store keeps it, built by the code `check` and `compare` use (with triples,
and the couplings and witnesses of a sample of modules). The dumps are then compared in order
and as multisets. Exit 0 when nothing differs. A dump step that fails fails the run: no output
is ever left empty because something went wrong.

Usage:
  python -m hoglint.native.parity --determinism N [--repo <root>] [--work <dir>]
      run N checks, each with its own hash seed, thread count and empty fact cache (so
      extraction runs at that thread count too), and compare each to the first
  python -m hoglint.native.parity dump --out <dir> [--repo <root>]
"""

from __future__ import annotations

import os
import sys
import json
import time
import hashlib
import argparse
import resource
import tempfile
import itertools
import subprocess
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hoglint.cli import Runner
from hoglint.config import CONFIG_FILE, load_config
from hoglint.ledger.envelope import Envelope
from hoglint.ledger.file import LedgerFile
from hoglint.ledger.scan import ScanSummary
from hoglint.model import Finding, Graph
from hoglint.native.normal import FIELDS, Row, edge_row
from hoglint.native.source import NativeGraphSource, batch_mode
from hoglint.rules.base import IncompleteItem
from hoglint.rules.engine import Engine, RunResult
from hoglint.rules.exceptions import ExceptionsFile

EXAMPLES = 5
LIST_OUTPUTS = (
    "edges",
    "symbols",
    "modules",
    "failed",
    "findings",
    "incomplete",
    "scan-lines",
    "couplings",
    "stored-summary",
)
VALUE_OUTPUTS = ("levels",)
# The stored summary's key: every dump's summary is stored under one, so two dumps compare.
DUMP_ENVELOPE = Envelope("dump-tree", "dump-engine", "dump-policy", "dump-framework")
# Every n-th module, sorted, is the rename scope whose couplings and witnesses the summary keeps.
SCOPE_STEP = 50


@dataclass(frozen=True)
class RowDiff:
    """Two row lists compared in order and as multisets; `fields` counts positional differences."""

    name: str
    left: int
    right: int
    same_position: int
    missing: int
    extra: int
    fields: tuple[tuple[str, int], ...]
    examples: tuple[tuple[Row, Row], ...]

    @property
    def equal(self) -> bool:
        return self.left == self.right == self.same_position and not self.missing and not self.extra

    def render(self) -> str:
        verdict = "equal" if self.equal else "DIFFERENT"
        out = [
            f"{self.name}: {verdict}  left {self.left}  right {self.right}  same position {self.same_position}"
            f"  missing {self.missing}  extra {self.extra}"
        ]
        out.extend(f"  field {name}: {count}" for name, count in self.fields)
        for left, right in self.examples:
            out.append(f"  left  {json.dumps(left, ensure_ascii=False)}")
            out.append(f"  right {json.dumps(right, ensure_ascii=False)}")
        return "\n".join(out)


def row_text(row: Row) -> str:
    return json.dumps(row, ensure_ascii=False)


def row_key(text: str) -> bytes:
    return hashlib.blake2b(text.encode(), digest_size=16).digest()


def diff_lines(name: str, left: Iterable[str], right: Iterable[str], fields: Sequence[str] = ()) -> RowDiff:
    """Two row lists, one JSON text per row, compared in order and as multisets, a row at a time.
    A row equal to the row at its position on the other side is in both multisets, so it cancels
    out; only the other rows are kept, by digest, to count what is missing and extra."""
    left_count = right_count = same = 0
    left_only: Counter[bytes] = Counter()
    right_only: Counter[bytes] = Counter()
    counts: Counter[str] = Counter()
    examples: list[tuple[Row, Row]] = []
    for a, b in itertools.zip_longest(left, right):
        left_count += a is not None
        right_count += b is not None
        if a == b:
            same += 1
            continue
        if a is not None:
            left_only[row_key(a)] += 1
        if b is not None:
            right_only[row_key(b)] += 1
        if a is None or b is None:
            continue
        a_row, b_row = json.loads(a), json.loads(b)
        if a_row == b_row:
            continue
        if len(examples) < EXAMPLES:
            examples.append((a_row, b_row))
        for field, x, y in zip(fields, a_row, b_row):
            if x != y:
                counts[field] += 1
    return RowDiff(
        name,
        left_count,
        right_count,
        same,
        sum((left_only - right_only).values()),
        sum((right_only - left_only).values()),
        tuple(counts.most_common()),
        tuple(examples),
    )


def finding_row(finding: Finding) -> Row:
    site = finding.site
    return [
        finding.rule,
        list(finding.identity),
        finding.message,
        [site.path, site.module, site.scope, site.line, site.column] if site is not None else None,
        [edge_row(edge) for edge in finding.witnesses],
        [[remedy.kind, remedy.detail] for remedy in finding.remedies],
        list(finding.owners),
        finding.status.value,
        list(finding.subjects),
        finding.ledgerable,
        finding.unknown,
    ]


def incomplete_row(item: IncompleteItem) -> Row:
    return [item.rule, item.key, item.reason, list(item.subjects), item.cause.value, item.kind.value]


def coupling_rows(graph: Graph) -> list[Row]:
    """(src module, dst symbol or surface, kind) -> count over every edge: what move admission reads."""
    counts: Counter[tuple[str, str, str]] = Counter()
    for edge in graph.edges:
        if not edge.targets:
            counts[(edge.site.module, edge.surface, edge.kind)] += 1
            continue
        for target in edge.targets:
            counts[(edge.site.module, str(target), edge.kind)] += 1
    return [[*key, count] for key, count in sorted(counts.items())]


def summary_rows(sections: dict[str, dict[str, Any]]) -> list[Row]:
    """The stored summary, one row per entry: [section, field, item] for a list entry, [section,
    field, key, value] for a mapping entry, [section, field, value] for any other field."""
    out: list[Row] = []
    for section, fields in sections.items():
        for name, value in fields.items():
            if isinstance(value, list):
                out.extend([section, name, item] for item in value)
            elif isinstance(value, dict):
                out.extend([section, name, key, value[key]] for key in sorted(value))
            else:
                out.append([section, name, value])
    return out


def stored_summary(result: RunResult, graph: Graph, roots: tuple[Path, ...], exceptions: ExceptionsFile) -> list[Row]:
    """The run summary as the store writes it (`ScanSummary.to_sections`), built by the code check
    and compare use: with triples, and the couplings and witnesses of a sample of modules."""
    scope = frozenset(sorted(graph.modules)[::SCOPE_STEP])
    summary = ScanSummary.from_run(result, graph, DUMP_ENVELOPE, scope, True, roots, exceptions)
    return summary_rows(summary.to_sections())


def level_row(result: RunResult, name: str) -> list[Any]:
    """A level's passing groups and the reasons of the failing ones; for a level that names a
    claiming input, also each claim and why the claims cannot be read."""
    level = result.levels[name]
    row: list[Any] = [level.passing, sorted(level.failing.items())]
    if result.config.levels[name].claimed_by:
        row += [sorted(level.claims.items()), level.claim_error]
    return row


class Tables:
    """The normalized files of one check's graph and rule results, in `out`. `summary` is the
    run's stored summary as rows (`stored_summary`), built before any file is written, so a
    summary that fails leaves no table behind."""

    def __init__(self, out: Path) -> None:
        self.out = out

    def write_lines(self, name: str, rows: Sequence[Any]) -> None:
        with open(self.out / f"{name}.jsonl", "w") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False))
                handle.write("\n")

    def write(self, graph: Graph, result: RunResult, summary: list[Row]) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        self.write_lines("findings", [finding_row(f) for f in result.findings()])
        self.write_lines("incomplete", [incomplete_row(item) for item in result.incomplete()])
        self.write_lines("scan-lines", [[list(key[1]), key[0], count] for key, count in result.scan.items()])
        self.write_lines("symbols", [[name, sorted(facts.items())] for name, facts in graph.symbols.items()])
        self.write_lines("modules", [[module, path] for module, path in graph.modules.items()])
        failed = [[name, failure.reason, sorted(failure.kinds)] for name, failure in graph.failed_producers.items()]
        self.write_lines("failed", failed)
        levels = {name: level_row(result, name) for name in result.levels}
        (self.out / "levels.json").write_text(json.dumps(levels, ensure_ascii=False))
        self.write_lines("stored-summary", summary)
        self.write_lines("couplings", coupling_rows(graph))
        self.write_lines("edges", [edge_row(edge) for edge in graph.edges])


class Dump:
    """One check over a repo, written as normalized files into `out`."""

    def __init__(self, repo: Path, out: Path, work: Path) -> None:
        self.repo = repo
        self.out = out
        self.work = work
        self.timings: dict[str, float] = {}

    def lap(self, name: str, started: float) -> float:
        now = time.perf_counter()
        self.timings[name] = round(now - started, 3)
        return now

    def check(self) -> tuple[Graph, RunResult, ExceptionsFile, Path]:
        batch_mode()
        started = time.perf_counter()
        config = load_config(self.repo, self.repo / CONFIG_FILE)
        source = NativeGraphSource(self.work)
        graph = source.build(self.repo, config)
        if not graph.modules:
            raise RuntimeError(f"the engine read no module under {self.repo}: nothing to compare")
        started = self.lap("graph", started)
        ledger = LedgerFile.read(self.repo / config.ledger.file)
        exceptions = ExceptionsFile.read(self.repo / config.ledger.exceptions, config)
        # The root `check` gives rules and providers: plugin directories of declared inputs, and
        # existence questions answered from the snapshot's listing.
        files_root = Runner(source).files_root(self.repo, "", config)
        result = Engine(config, graph, files_root).evaluate(ledger, exceptions)
        self.lap("rules", started)
        return graph, result, exceptions, files_root

    def run(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        graph, result, exceptions, files_root = self.check()
        started = time.perf_counter()
        summary = stored_summary(result, graph, (self.repo, files_root), exceptions)
        started = self.lap("summary", started)
        Tables(self.out).write(graph, result, summary)
        self.lap("write", started)
        self.timings["maxrss_gb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9, 3)
        (self.out / "timings.json").write_text(json.dumps(self.timings))


def row_texts(path: Path) -> Iterator[str]:
    """The rows of a dump file as the JSON text `Dump.write_lines` wrote, one at a time."""
    with open(path) as handle:
        for line in handle:
            yield line.removesuffix("\n")


def read_rows(path: Path) -> Iterator[Row]:
    """The rows of a dump file, one at a time."""
    for text in row_texts(path):
        yield json.loads(text)


def read_lines(path: Path) -> list[Row]:
    return list(read_rows(path))


def compare_dumps(
    left: Path,
    right: Path,
    lists: Sequence[str] = LIST_OUTPUTS,
    values: Sequence[str] = VALUE_OUTPUTS,
    skip_missing: bool = False,
) -> list[RowDiff]:
    """Table by table and row by row: a dump is too large to hold two of in memory. With
    `skip_missing`, a table that either side lacks is left out instead of failing the read."""
    diffs = []
    for name in lists:
        a, b = left / f"{name}.jsonl", right / f"{name}.jsonl"
        if skip_missing and not (a.is_file() and b.is_file()):
            continue
        fields = FIELDS if name == "edges" else ()
        diffs.append(diff_lines(name, row_texts(a), row_texts(b), fields))
    for name in values:
        a, b = left / f"{name}.json", right / f"{name}.json"
        if skip_missing and not (a.is_file() and b.is_file()):
            continue
        diffs.append(diff_lines(name, [row_text(json.loads(a.read_text()))], [row_text(json.loads(b.read_text()))]))
    return diffs


def dump_in_process(repo: Path, out: Path, env: dict[str, str] | None = None) -> None:
    command = [sys.executable, "-m", "hoglint.native.parity", "dump", "--out", str(out)]
    subprocess.run([*command, "--repo", str(repo)], check=True, env={**os.environ, **(env or {})})


def seed_env(seed: str, threads: str, link_threads: str) -> dict[str, str]:
    """A hash seed, and the thread counts of extraction and of the link."""
    return {"PYTHONHASHSEED": seed, "HOGLINT_THREADS": threads, "RAYON_NUM_THREADS": link_threads}


def determinism_env(work: Path, run: int) -> dict[str, str]:
    """Run `run` of the determinism check: its own hash seed, thread count (extraction and link)
    and an empty fact cache, so every run extracts every file at its own thread count."""
    threads = (1, 2, 4, 8, 0)[run % 5]
    return {**seed_env(str(run * 7919), str(threads), str(threads or 16)), "XDG_CACHE_HOME": str(work / f"cache-{run}")}


def report(title: str, diffs: list[RowDiff], left: Path, right: Path) -> bool:
    sys.stdout.write(f"{title}\n")
    sys.stdout.write("\n".join(diff.render() for diff in diffs) + "\n")
    for side in (left, right):
        sys.stdout.write(f"timings {side.name}: {(side / 'timings.json').read_text()}\n")
    return all(diff.equal for diff in diffs)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", choices=("dump",))
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--work", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--determinism", type=int, default=0)
    args = parser.parse_args()
    repo = args.repo.resolve()
    work = args.work or Path(tempfile.mkdtemp(prefix="hoglint-native-parity-"))
    work.mkdir(parents=True, exist_ok=True)
    if args.command == "dump":
        Dump(repo, args.out or work / "native", work).run()
        return
    if not args.determinism:
        parser.error("give `dump` or `--determinism N`")
    ok = True
    first = work / "native-0"
    dump_in_process(repo, first, determinism_env(work, 0))
    for run in range(1, args.determinism):
        other = work / f"native-{run}"
        env = determinism_env(work, run)
        dump_in_process(repo, other, env)
        ok = report(f"run 0 vs run {run} ({env})", compare_dumps(first, other), first, other) and ok
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
