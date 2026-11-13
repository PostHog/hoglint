"""Checks the model repository in the native engine and prints, per rule, the rows whose verdict
changed against the reviewed expected file, with the case each row is tied to, and the tables
whose rows or bytes differ from the stored digests (tables.toml).

Usage: bin/model-repo [--refresh] [--keep]
  --refresh  rewrite expected.toml and tables.toml from this run: rows that stay keep their case,
             new rows get case "?"; review the diff and tie every new row to a case before
             committing
  --keep     keep the work directory with the dump and print its path
Exit 0 when every row is as expected and every table as stored, else 1.
"""

from __future__ import annotations

import sys
import shutil
import argparse
import tempfile
from pathlib import Path

from model_repo.harness import (
    EXPECTED,
    TABLES,
    Comparison,
    Dumps,
    Expected,
    changed_tables,
    dumped_tables,
    read_tables,
    render_tables,
)


class CheckCommand:
    def __init__(self, refresh: bool, keep: bool) -> None:
        self.refresh = refresh
        self.keep = keep

    def run(self) -> int:
        work = Path(tempfile.mkdtemp(prefix="hoglint-model-repo-"))
        try:
            dumps = Dumps(work)
            dumps.run()
            sys.stdout.write(f"checked the model repository: {dumps.seconds} s\n")
            expected = Expected.read()
            result = dumps.result()
            comparison = Comparison(expected, result)
            sys.stdout.write(comparison.render() + "\n")
            tables = dumped_tables(dumps.out)
            moved = changed_tables(read_tables(), tables)
            sys.stdout.write("\n".join(moved) + "\n" if moved else "every table as stored\n")
            for wanted, found in comparison.wanted():
                state = "now given" if found else "not yet"
                sys.stdout.write(f"wanted {wanted.case} ({wanted.decision}): {state}\n")
            if self.refresh:
                EXPECTED.write_text(expected.refreshed(result).render())
                TABLES.write_text(render_tables(tables))
                sys.stdout.write(f"rewrote {EXPECTED} and {TABLES}; review the diff and tie each '?' row to a case\n")
            return 0 if comparison.equal() and not moved else 1
        finally:
            if self.keep:
                sys.stdout.write(f"dump kept in {work}\n")
            else:
                shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    sys.exit(CheckCommand(args.refresh, args.keep).run())


if __name__ == "__main__":
    main()
