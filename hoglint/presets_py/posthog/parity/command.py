"""Runs each comparison of hoglint with an old boundary tool on one monorepo tree, prints one
verdict table, and writes a file per tool that lists every difference and its reason.

Exit 0 only when no comparison has an unexplained difference and none failed to run.
"""

from __future__ import annotations

import sys
import time
import argparse
import tempfile
import traceback
from collections.abc import Callable
from pathlib import Path

from hoglint.presets_py.posthog.parity import (
    crossings,
    import_linter,
    isolated,
    migrations,
    tach_check,
    tach_map,
    watched,
)
from hoglint.presets_py.posthog.parity.hoglint_check import HoglintCheck
from hoglint.presets_py.posthog.parity.verdict import Comparison, Verdict, verdict_table, write_files

# In this order: each later one reads more of hoglint's rules.
TOOLS = (
    tach_map.TOOL,
    tach_check.TOOL,
    import_linter.TOOL,
    crossings.TOOL,
    isolated.TOOL,
    watched.TOOL,
    migrations.TOOL,
)
# The comparisons that read the shared hoglint check; the others build their own graph.
READS_CHECK = frozenset({tach_check.TOOL, import_linter.TOOL, isolated.TOOL, watched.TOOL, migrations.TOOL})


def parser() -> argparse.ArgumentParser:
    found = argparse.ArgumentParser(prog="bin/parity", description=__doc__)
    found.add_argument("root", type=Path, help="the monorepo tree, with its root hoglint.toml")
    found.add_argument("--only", action="append", choices=TOOLS, help="run only this comparison (repeatable)")
    found.add_argument("--out", type=Path, help="where the per-tool files go (default: a new temporary directory)")
    return found


def timed(tool: str, run: Callable[[], Comparison]) -> Comparison:
    started = time.perf_counter()
    try:
        comparison = run()
    except Exception:  # noqa: BLE001 - a comparison that cannot run is a failed verdict, not a crash
        comparison = Comparison(tool, error=traceback.format_exc())
    comparison.seconds = time.perf_counter() - started
    sys.stderr.write(f"{tool}: {comparison.verdict_text()} in {comparison.seconds:.1f} s\n")
    return comparison


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    root = args.root.resolve()
    selected = [tool for tool in TOOLS if not args.only or tool in args.only]
    out = args.out or Path(tempfile.mkdtemp(prefix="hoglint-parity-"))
    work = Path(tempfile.mkdtemp(prefix="hoglint-parity-work-"))
    check = HoglintCheck(root, work)
    if READS_CHECK & set(selected):
        started = time.perf_counter()
        facts = check.facts
        sys.stderr.write(f"hoglint check: {len(facts.findings)} findings in {time.perf_counter() - started:.1f} s\n")
    runs: dict[str, Callable[[], Comparison]] = {
        tach_map.TOOL: lambda: tach_map.run(root, work),
        tach_check.TOOL: lambda: tach_check.run(root, check.facts),
        import_linter.TOOL: lambda: import_linter.run(root, check.facts),
        crossings.TOOL: lambda: crossings.run(root, work),
        isolated.TOOL: lambda: isolated.run(root, check.facts),
        watched.TOOL: lambda: watched.run(root, check.facts),
        migrations.TOOL: lambda: migrations.run(root, check.facts),
    }
    comparisons = [timed(tool, runs[tool]) for tool in selected]
    write_files(comparisons, out)
    sys.stdout.write(verdict_table(comparisons))
    sys.stdout.write(f"files: {out}\n")
    good = (Verdict.EQUAL, Verdict.EXPLAINED)
    return 0 if all(c.verdict in good for c in comparisons) else 1
