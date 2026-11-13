"""What every check of `bin/prove` shares: where it writes, the environment it runs in, and how
it runs a command into its own log file."""

from __future__ import annotations

import os
import shutil
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Where a tree's root config finds the preset, as in the monorepo the preset is written for.
PRESET_IN_TREE = "tools/hoglint/presets/posthog.toml"


@dataclass(frozen=True)
class Outcome:
    """One line of the verdict table."""

    check: str
    passed: bool
    line: str


@dataclass(frozen=True)
class Native:
    """A private copy of a built native module and extractor binary."""

    module_dir: Path
    facts_bin: Path

    @staticmethod
    def copy(binary: Path, into: Path) -> Native:
        """Copies what `bin/build` built next to `binary` (its printed path) into `into`."""
        module = binary.parent.parent / "python" / "hoglint_native"
        shutil.rmtree(into, ignore_errors=True)
        into.mkdir(parents=True)
        shutil.copytree(module, into / "hoglint_native")
        shutil.copy2(binary, into / binary.name)
        return Native(into, into / binary.name)

    def env(self) -> dict[str, str]:
        return {"HOGLINT_NATIVE_DIR": str(self.module_dir), "HOGLINT_FACTS_BIN": str(self.facts_bin)}


class Context:
    """The repo, the Python that runs the checks, the work directory (scratch is deleted at the
    end, logs are kept) and the environment every child gets."""

    def __init__(self, root: Path, python: Path, work: Path, cargo_dir: Path) -> None:
        self.root = root
        self.python = python
        self.work = work
        self.logs = work / "logs"
        self.scratch = work / "scratch"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.scratch.mkdir(parents=True, exist_ok=True)
        self.cargo_dir = cargo_dir
        self.native: Native | None = None

    def tool(self, name: str) -> Path:
        """A tool of the Python environment that runs the checks (ruff, ty)."""
        return self.python.parent / name

    def env(self, cache: str, extra: dict[str, str] | None = None) -> dict[str, str]:
        """The environment of a child: the pinned cargo first on PATH, the private native copy,
        a native module required, and a private cache directory named `cache`."""
        env = dict(os.environ)
        # bin/prove's own import path is not the checks': ty, for one, reads it and resolves more.
        env.pop("PYTHONPATH", None)
        env["PATH"] = f"{self.cargo_dir}{os.pathsep}{env.get('PATH', '')}"
        env["HOGLINT_REQUIRE_NATIVE"] = "1"
        env["XDG_CACHE_HOME"] = str(self.cache(cache))
        if self.native is not None:
            env.update(self.native.env())
        env.update(extra or {})
        return env

    def cache(self, name: str) -> Path:
        path = self.scratch / "cache" / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def log(self, name: str) -> Path:
        return self.logs / f"{name}.log"

    def run(self, log: Path, argv: list[str], env: dict[str, str], cwd: Path | None = None) -> int:
        """Runs `argv` with its output appended to `log`; returns the exit code."""
        with open(log, "a") as handle:
            handle.write(f"$ {' '.join(argv)}\n")
            handle.flush()
            done = subprocess.run(argv, cwd=cwd or self.root, env=env, stdout=handle, stderr=subprocess.STDOUT)
            handle.write(f"exit {done.returncode}\n")
        return done.returncode


def clone(source: Path, target: Path) -> None:
    """A copy of a directory tree that shares its blocks where the file system can: `cp -c` on
    macOS, a reflink elsewhere. A snapshot is never changed in place."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if platform.system() == "Darwin":
        argv = ["cp", "-Rc", str(source), str(target)]
    else:
        argv = ["cp", "-R", "--reflink=auto", str(source), str(target)]
    subprocess.run(argv, check=True)


def last_lines(log: Path, count: int) -> list[str]:
    lines = [line for line in log.read_text(errors="replace").splitlines() if line.strip()]
    return lines[-count:]


class Check:
    """One row (or a few) of the verdict table. A subclass runs its commands through the context
    and says what passed in one line."""

    name = ""

    def run(self, context: Context) -> list[Outcome]:
        raise NotImplementedError
