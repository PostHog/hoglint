"""Checks the model repository in both engines and prints, per rule, the rows whose verdict
changed against the reviewed expected file, with the case each row is tied to.

Usage: bin/model-repo [--engine python|native] [--refresh] [--keep]
  --engine   run one engine only (no engine comparison)
  --refresh  rewrite expected.toml from this run: rows that stay keep their case, new rows get
             case "?"; review the diff and tie every new row to a case before committing
  --keep     keep the work directory with the dumps and print its path
Exit 0 when both engines agree and every row is as expected, else 1.
"""

from __future__ import annotations

import sys
import shutil
import argparse
import tempfile
from pathlib import Path

from hoglint.native.source import ENGINES

from model_repo.harness import EXPECTED, Comparison, Dumps, Expected


class CheckCommand:
    def __init__(self, engines: tuple[str, ...], refresh: bool, keep: bool) -> None:
        self.engines = engines
        self.refresh = refresh
        self.keep = keep

    def report_engines(self, dumps: Dumps) -> bool:
        timing = ", ".join(f"{engine} {seconds} s" for engine, seconds in dumps.seconds.items())
        sys.stdout.write(f"checked the model repository: {timing}\n")
        if len(self.engines) < 2:
            return True
        differences = dumps.differences()
        if not differences:
            sys.stdout.write("both engines give the same ten tables\n")
            return True
        sys.stdout.write("the engines differ:\n" + "\n".join(diff.render() for diff in differences) + "\n")
        return False

    def run(self) -> int:
        work = Path(tempfile.mkdtemp(prefix="hoglint-model-repo-"))
        try:
            dumps = Dumps(work)
            dumps.run(self.engines)
            engines_agree = self.report_engines(dumps)
            expected = Expected.read()
            result = dumps.result(self.engines[-1])
            comparison = Comparison(expected, result)
            sys.stdout.write(comparison.render() + "\n")
            for wanted, found in comparison.wanted():
                state = "now given" if found else "not yet"
                sys.stdout.write(f"wanted {wanted.case} ({wanted.decision}): {state}\n")
            if self.refresh:
                EXPECTED.write_text(expected.refreshed(result).render())
                sys.stdout.write(f"rewrote {EXPECTED}; review the diff and tie each '?' row to a case\n")
            return 0 if engines_agree and comparison.equal() else 1
        finally:
            if self.keep:
                sys.stdout.write(f"dumps kept in {work}\n")
            else:
                shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--engine", choices=ENGINES)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    engines = (args.engine,) if args.engine else ENGINES
    sys.exit(CheckCommand(engines, args.refresh, args.keep).run())


if __name__ == "__main__":
    main()
