"""Runs an old tool on the monorepo tree, each in its own process, with this interpreter.

The interpreter must be the monorepo's own: it holds tach, import-linter, the product lint and
Django. A driver of `drivers.py` prints its result as JSON on stdout.
"""

from __future__ import annotations

import os
import sys
import json
import subprocess
from pathlib import Path
from typing import Any

HOGLINT_ROOT = Path(__file__).resolve().parents[4]
# Where the product lint's package (`hogli_commands`) sits in the monorepo.
PRODUCT_LINT_ROOT = "tools/hogli-commands"


class OldToolError(RuntimeError):
    pass


def run(root: Path, args: list[str], timeout: int = 1800) -> str:
    """Runs `args` in `root` and returns stdout. A non-zero exit is an error with its stderr."""
    paths = [HOGLINT_ROOT, root, root / PRODUCT_LINT_ROOT]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(str(path) for path in paths)}
    done = subprocess.run(args, cwd=root, env=env, capture_output=True, text=True, timeout=timeout)
    if done.returncode != 0:
        raise OldToolError(f"{' '.join(args)} exited {done.returncode}:\n{done.stderr[-4000:]}")
    return done.stdout


def driver(root: Path, name: str, *args: str) -> Any:
    """Runs one driver of `drivers.py` in `root` and returns the JSON it printed."""
    out = run(root, [sys.executable, "-m", "hoglint.packs.posthog.parity.drivers", name, *args])
    return json.loads(out)


def tach_map(root: Path, out: Path) -> dict[str, list[str]]:
    """`tach map --direction dependents`, as the monorepo's test selection runs it."""
    run(root, [sys.executable, "-m", "tach", "map", "--direction", "dependents", "--output", str(out)])
    return json.loads(out.read_text())


SKIP_LIST_SCRIPT = """
const { execFileSync } = require('child_process')
const fs = require('fs')
const { getIsolatedProducts } = require('./.github/scripts/turbo-discover')
const bin = fs.existsSync('./node_modules/.bin/turbo') ? './node_modules/.bin/turbo' : 'turbo'
const raw = execFileSync(bin, ['run', 'backend:contract-check', '--dry-run=json'], {
    encoding: 'utf-8', maxBuffer: 512 * 1024 * 1024, stdio: ['pipe', 'pipe', 'pipe'],
})
const tasks = JSON.parse(raw).tasks.filter((t) => !/NONEXISTENT/.test(t.command))
process.stdout.write(JSON.stringify([...getIsolatedProducts(tasks, process.cwd())].sort()))
"""


def skip_list(root: Path) -> list[str]:
    """The products the monorepo's test selection lets skip the full suite, read the way
    `.github/scripts/turbo-discover.js` reads them: its own `getIsolatedProducts` over turbo's
    `backend:contract-check` tasks. Names come back as product directories."""
    found = json.loads(run(root, ["node", "-e", SKIP_LIST_SCRIPT]))
    return sorted(name.replace("-", "_") for name in found)
