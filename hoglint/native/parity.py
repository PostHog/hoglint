"""Semantic parity of the native engine against the Python engine, field by field.

Each engine runs a full check in its own process and dumps normalized outputs: every edge
(core and pack) with every field and attr, symbol facts, modules, failed producers, findings
with their witnesses, incompletes, levels, scan lines, the coupling count of every edge, and the
run summary as the store keeps it, built by the code `check` and `compare` use (with triples,
and the couplings and witnesses of a sample of modules). The dumps are then compared in order
and as multisets. Exit 0 when nothing differs. A dump step that fails fails the run: no output
is ever left empty because something went wrong.

Usage:
  python -m hoglint.native.parity [--repo <root>] [--work <dir>]
      run both engines and compare
  python -m hoglint.native.parity --determinism N [--repo <root>] [--work <dir>]
      run the native engine N times, each with its own hash seed, thread count and empty fact
      cache (so extraction runs at that thread count too), and compare each to the first
  python -m hoglint.native.parity dump --engine native|python --out <dir> [--repo <root>]
"""

from __future__ import annotations

import os
import sys
import json
import time
import argparse
import resource
import tempfile
import subprocess
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hoglint.config import CONFIG_FILE, load_config
from hoglint.ledger.envelope import Envelope
from hoglint.ledger.file import LedgerFile
from hoglint.ledger.scan import ScanSummary
from hoglint.model import Finding, Graph
from hoglint.native.normal import FIELDS, Row, edge_row
from hoglint.native.source import ENGINES, batch_mode, graph_source
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
# The stored summary's key: both engines' summaries are stored under one, so they compare.
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


def diff_rows(name: str, left: Sequence[Row], right: Sequence[Row], fields: Sequence[str] = ()) -> RowDiff:
    left_text = [json.dumps(row, ensure_ascii=False) for row in left]
    right_text = [json.dumps(row, ensure_ascii=False) for row in right]
    same = sum(1 for a, b in zip(left_text, right_text) if a == b)
    missing = Counter(left_text) - Counter(right_text)
    extra = Counter(right_text) - Counter(left_text)
    counts: Counter[str] = Counter()
    examples: list[tuple[Row, Row]] = []
    for a, b in zip(left, right):
        if a == b:
            continue
        if len(examples) < EXAMPLES:
            examples.append((a, b))
        for field, x, y in zip(fields, a, b):
            if x != y:
                counts[field] += 1
    return RowDiff(
        name,
        len(left),
        len(right),
        same,
        sum(missing.values()),
        sum(extra.values()),
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
    return [item.rule, item.key, item.reason, list(item.subjects), item.cause.value]


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


def stored_summary(result: RunResult, graph: Graph, repo: Path, exceptions: ExceptionsFile) -> list[Row]:
    """The run summary as the store writes it (`ScanSummary.to_sections`), built by the code check
    and compare use: with triples, and the couplings and witnesses of a sample of modules."""
    scope = frozenset(sorted(graph.modules)[::SCOPE_STEP])
    summary = ScanSummary.from_run(result, graph, DUMP_ENVELOPE, scope, True, (repo,), exceptions)
    return summary_rows(summary.to_sections())


class Dump:
    """One engine's check over a repo, written as normalized files into `out`."""

    def __init__(self, repo: Path, engine: str, out: Path, work: Path) -> None:
        self.repo = repo
        self.engine = engine
        self.out = out
        self.work = work
        self.timings: dict[str, float] = {}

    def lap(self, name: str, started: float) -> float:
        now = time.perf_counter()
        self.timings[name] = round(now - started, 3)
        return now

    def check(self) -> tuple[Graph, RunResult, ExceptionsFile]:
        batch_mode(self.engine)
        started = time.perf_counter()
        config = load_config(self.repo, self.repo / CONFIG_FILE)
        graph = graph_source(self.engine, self.work).build(self.repo, config)
        if not graph.modules:
            raise RuntimeError(f"the {self.engine} engine read no module under {self.repo}: nothing to compare")
        started = self.lap("graph", started)
        ledger = LedgerFile.read(self.repo / config.ledger.file)
        exceptions = ExceptionsFile.read(self.repo / config.ledger.exceptions, config)
        result = Engine(config, graph, self.repo).evaluate(ledger, exceptions)
        self.lap("rules", started)
        return graph, result, exceptions

    def write_lines(self, name: str, rows: Sequence[Any]) -> None:
        with open(self.out / f"{name}.jsonl", "w") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False))
                handle.write("\n")

    def run(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        graph, result, exceptions = self.check()
        started = time.perf_counter()
        summary = stored_summary(result, graph, self.repo, exceptions)
        started = self.lap("summary", started)
        self.write_lines("findings", [finding_row(f) for f in result.findings()])
        self.write_lines("incomplete", [incomplete_row(item) for item in result.incomplete()])
        self.write_lines("scan-lines", [[list(key[1]), key[0], count] for key, count in result.scan.items()])
        self.write_lines("symbols", [[name, sorted(facts.items())] for name, facts in graph.symbols.items()])
        self.write_lines("modules", [[module, path] for module, path in graph.modules.items()])
        failed = [[name, failure.reason, sorted(failure.kinds)] for name, failure in graph.failed_producers.items()]
        self.write_lines("failed", failed)
        levels = {name: [level.passing, sorted(level.failing.items())] for name, level in result.levels.items()}
        (self.out / "levels.json").write_text(json.dumps(levels, ensure_ascii=False))
        self.write_lines("stored-summary", summary)
        self.write_lines("couplings", coupling_rows(graph))
        self.write_lines("edges", [edge_row(edge) for edge in graph.edges])
        self.lap("write", started)
        self.timings["maxrss_gb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9, 3)
        (self.out / "timings.json").write_text(json.dumps(self.timings))


def read_lines(path: Path) -> list[Row]:
    with open(path) as handle:
        return [json.loads(line) for line in handle]


def compare_dumps(left: Path, right: Path) -> list[RowDiff]:
    diffs = []
    for name in LIST_OUTPUTS:
        fields = FIELDS if name == "edges" else ()
        diffs.append(diff_rows(name, read_lines(left / f"{name}.jsonl"), read_lines(right / f"{name}.jsonl"), fields))
    for name in VALUE_OUTPUTS:
        a = json.loads((left / f"{name}.json").read_text())
        b = json.loads((right / f"{name}.json").read_text())
        diffs.append(diff_rows(name, [a], [b]))
    return diffs


def dump_in_process(repo: Path, engine: str, out: Path, env: dict[str, str] | None = None) -> None:
    command = [sys.executable, "-m", "hoglint.native.parity", "dump", "--engine", engine, "--out", str(out)]
    subprocess.run([*command, "--repo", str(repo)], check=True, env={**os.environ, **(env or {})})


def determinism_env(work: Path, run: int) -> dict[str, str]:
    """Run `run` of the determinism check: its own hash seed, thread count (extraction and link)
    and an empty fact cache, so every run extracts every file at its own thread count."""
    threads = (1, 2, 4, 8, 0)[run % 5]
    return {
        "PYTHONHASHSEED": str(run * 7919),
        "HOGLINT_THREADS": str(threads),
        "RAYON_NUM_THREADS": str(threads or 16),
        "XDG_CACHE_HOME": str(work / f"cache-{run}"),
    }


def report(title: str, diffs: list[RowDiff], left: Path, right: Path) -> bool:
    sys.stdout.write(f"{title}\n")
    sys.stdout.write("\n".join(diff.render() for diff in diffs) + "\n")
    for side in (left, right):
        sys.stdout.write(f"timings {side.name}: {(side / 'timings.json').read_text()}\n")
    return all(diff.equal for diff in diffs)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", choices=("compare", "dump"), default="compare")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--work", type=Path)
    parser.add_argument("--engine", choices=ENGINES, default="native")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--determinism", type=int, default=0)
    args = parser.parse_args()
    repo = args.repo.resolve()
    work = args.work or Path(tempfile.mkdtemp(prefix="hoglint-native-parity-"))
    work.mkdir(parents=True, exist_ok=True)
    if args.command == "dump":
        Dump(repo, args.engine, args.out or work / args.engine, work).run()
        return
    ok = True
    if args.determinism:
        first = work / "native-0"
        dump_in_process(repo, "native", first, determinism_env(work, 0))
        for run in range(1, args.determinism):
            other = work / f"native-{run}"
            env = determinism_env(work, run)
            dump_in_process(repo, "native", other, env)
            ok = report(f"native run 0 vs run {run} ({env})", compare_dumps(first, other), first, other) and ok
    else:
        python, native = work / "python", work / "native"
        dump_in_process(repo, "python", python)
        dump_in_process(repo, "native", native)
        ok = report("python (left) vs native (right)", compare_dumps(python, native), python, native)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
