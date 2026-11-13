"""Runs the mechanical checks of a change side by side and prints one verdict table: per check,
passed or failed, one line, and the seconds it took. Each check writes its own log file. Exit 0
only when every check passed.

Usage: bin/prove [--jobs N] [--work DIR] [--keep] [--skip-local]
                 [--real SNAPSHOT ...] [--base REV] [--parity] [--replay SAMPLE] [--replay-counts FILE]

Always: the build, the model repository in both engines, pytest with the native engine required,
cargo test, clippy, cargo fmt, ruff check and format, ty against its baseline, determinism (three
hash seeds times one and four threads) and the store modes on the model repository.

  --real SNAPSHOT  a snapshot of the monorepo (repeatable), copied and laid with this repo's preset:
                   the head dumped in the native engine
  --base REV       with --real: REV exported and built into its own target directory, dumped on
                   each snapshot, and every table compared with the head's; the moved rows are
                   written to the log directory
  --parity         with --real: a Python-engine dump of the head, compared on all ten tables
  --replay SAMPLE  the replay sample (a directory with `repo` and `pairs-*.tsv`), in today mode and
                   as the certificate; prints the pairs that are not EQUAL or not HELD
  --replay-counts FILE  the stored truth and predicted counts per pair (`pair<TAB>truth<TAB>predicted`)
                   to compare the certificate's counts with; each run writes its own next to its logs

Environment (no default names a machine):
  HOGLINT_PROVE_PYTHON       the Python for real-size runs and the replay (or --real-python); the
                             monorepo's preset needs a Python that has the monorepo's packages
  HOGLINT_PROVE_BASE_TARGET  the target directory to build the base in (or --base-target)
  CARGO_TARGET_DIR           the head's target directory, as for bin/build
"""

from __future__ import annotations

import os
import sys
import time
import shutil
import argparse
import tempfile
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from prove.base import Check, Context, Outcome
from prove.local import LOCAL_CHECKS, Build
from prove.real import BaseBuild, Engine, RealSize, ReplayRun, Snapshot

ROOT = Path(__file__).resolve().parents[2]


class TimedOutcome:
    def __init__(self, outcome: Outcome, seconds: float) -> None:
        self.outcome = outcome
        self.seconds = seconds

    def render(self) -> str:
        result = "passed" if self.outcome.passed else "FAILED"
        return f"{self.outcome.check:46} {result:7} {self.seconds:6.0f}s  {self.outcome.line}"


def timed(check: Check, context: Context) -> list[TimedOutcome]:
    """Runs one check; an exception is that check's failure, never the run's."""
    started = time.monotonic()
    try:
        outcomes = check.run(context)
    except Exception as error:
        outcomes = [Outcome(check.name, False, f"{type(error).__name__}: {error}")]
    seconds = time.monotonic() - started
    return [TimedOutcome(outcome, seconds) for outcome in outcomes]


def cargo_dir() -> Path:
    """The directory of the cargo pinned in rust-toolchain.toml, as bin/build finds it."""
    done = subprocess.run(["rustup", "which", "cargo"], cwd=ROOT, capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(f"rustup cannot name the pinned cargo: {done.stderr.strip()}")
    return Path(done.stdout.strip()).parent


def required(value: str | None, name: str, variable: str) -> Path:
    value = value or os.environ.get(variable)
    if not value:
        raise SystemExit(f"{name} is needed: pass it or set {variable}")
    return Path(value)


class ProveCommand:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        work = Path(args.work) if args.work else Path(tempfile.mkdtemp(prefix="hoglint-prove-"))
        self.context = Context(ROOT, Path(sys.executable), work.resolve(), cargo_dir())
        self.rows: list[TimedOutcome] = []

    def report(self) -> int:
        sys.stdout.write("\n".join(row.render() for row in self.rows) + "\n")
        sys.stdout.write(f"logs: {self.context.logs}\n")
        return 0 if all(row.outcome.passed for row in self.rows) else 1

    def real_checks(self, head: Engine) -> list[Check]:
        args = self.args
        if not args.real and not args.replay:
            return []
        python = required(args.real_python, "a Python for real-size runs", "HOGLINT_PROVE_PYTHON")
        base = None
        if args.base:
            target = required(args.base_target, "a target directory for the base", "HOGLINT_PROVE_BASE_TARGET")
            started = time.monotonic()
            base = BaseBuild(args.base, target.resolve()).run(self.context)
            self.rows.append(TimedOutcome(Outcome("base build", True, base.label), time.monotonic() - started))
        checks: list[Check] = []
        for index, path in enumerate(args.real, start=1):
            snapshot = Snapshot(Path(path).resolve(), f"{index}-{Path(path).name}")
            checks.append(RealSize(snapshot, python, head, base, args.parity))
        if args.replay:
            stored = Path(args.replay_counts).resolve() if args.replay_counts else None
            for mode in ("today", "traces"):
                checks.append(ReplayRun(Path(args.replay).resolve(), python, mode, stored))
        return checks

    def run(self) -> int:
        self.rows.extend(timed(Build(), self.context))
        if self.context.native is None:
            return self.report()
        head = Engine("head", ROOT, self.context.native)
        try:
            real = self.real_checks(head)
        except RuntimeError as error:
            self.rows.append(TimedOutcome(Outcome("base build", False, str(error)), 0))
            return self.report()
        local = [] if self.args.skip_local else [check() for check in LOCAL_CHECKS]
        checks = [*local, *real]
        with ThreadPoolExecutor(max_workers=self.args.jobs) as pool:
            results = list(pool.map(lambda check: timed(check, self.context), checks))
        for outcomes in results:
            self.rows.extend(outcomes)
        return self.report()

    def clean(self) -> None:
        if not self.args.keep:
            shutil.rmtree(self.context.scratch, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--work")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--skip-local", action="store_true")
    parser.add_argument("--real", action="append", default=[])
    parser.add_argument("--base")
    parser.add_argument("--base-target")
    parser.add_argument("--parity", action="store_true")
    parser.add_argument("--real-python")
    parser.add_argument("--replay")
    parser.add_argument("--replay-counts")
    args = parser.parse_args()
    if (args.base or args.parity) and not args.real:
        parser.error("--base and --parity need --real")
    command = ProveCommand(args)
    try:
        code = command.run()
    finally:
        command.clean()
    sys.exit(code)


if __name__ == "__main__":
    main()
