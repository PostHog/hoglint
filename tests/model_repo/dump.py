"""Dumps the model repository in one engine as the ten parity tables, in a process of its own, so
a hash seed, a thread count or a store switch set in the environment holds for the whole check.
`bin/prove` runs it for its determinism and store checks.

Usage:
  python -m model_repo.dump copy --repo <new dir>
      a copy of the tree, staged in a new git repo; dump the same copy every time
  python -m model_repo.dump dump --repo <copy> --engine native|python --out <dir> --work <dir>
"""

from __future__ import annotations

import argparse
from pathlib import Path

from hoglint.native.source import ENGINES

from model_repo.harness import copy_tree, dump_tree


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("copy", "dump"))
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--engine", choices=ENGINES, default="native")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--work", type=Path)
    args = parser.parse_args()
    if args.command == "copy":
        copy_tree(args.repo)
        return
    if args.out is None or args.work is None:
        raise SystemExit("dump needs --out and --work")
    args.work.mkdir(parents=True, exist_ok=True)
    dump_tree(args.repo, args.engine, args.out, args.work)


if __name__ == "__main__":
    main()
