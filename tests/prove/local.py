"""The checks `bin/prove` always runs: the build, the model repository, the suites and lints, and
determinism and the store modes on the model repository."""

from __future__ import annotations

import re
import json
import subprocess
from pathlib import Path

from hoglint.native.parity import compare_dumps, seed_env
from hoglint.native.replay import SEEDS, THREADS

from prove.base import Check, Context, Native, Outcome, last_lines

# Tests that fail in this repo's environment for a known reason; any other failure fails the suite.
KNOWN_FAILURES = {
    "tests/rules/test_attribution.py::test_the_owners_provider_reads_only_the_ownership_files_it_declares": (
        "the PostHog owners provider imports a package only the monorepo has"
    ),
}
# The diagnostics `ty check` reports on main. A change adds none; lower it when one is fixed.
TY_BASELINE = 24


def build(root: Path, log: Path, env: dict[str, str]) -> Path | None:
    """`bin/build` in `root`; the path of the extractor binary it prints, or None if it failed."""
    with open(log, "a") as handle:
        handle.write(f"$ bin/build in {root}\n")
        handle.flush()
        done = subprocess.run(["bin/build"], cwd=root, env=env, stdout=subprocess.PIPE, stderr=handle, text=True)
        handle.write(done.stdout + f"exit {done.returncode}\n")
    if done.returncode != 0:
        return None
    return Path(done.stdout.strip().splitlines()[-1])


class Build(Check):
    """The head's extractor and native module, copied for this run alone."""

    name = "build"

    def run(self, context: Context) -> list[Outcome]:
        log = context.log("build")
        binary = build(context.root, log, context.env("build"))
        if binary is None:
            return [Outcome(self.name, False, f"bin/build failed: {log}")]
        context.native = Native.copy(binary, context.scratch / "native" / "head")
        built = [line for line in log.read_text().splitlines() if line.startswith("built native id")]
        return [Outcome(self.name, True, built[-1] if built else "built")]


class ModelRepo(Check):
    """The model repository in the native engine: every row as reviewed, every table as stored."""

    name = "model repository"

    def run(self, context: Context) -> list[Outcome]:
        log = context.log("model-repo")
        env = context.env("model-repo", {"PYTHONPATH": f"{context.root}:{context.root / 'tests'}"})
        code = context.run(log, [str(context.python), "-m", "model_repo.check"], env)
        text = log.read_text()
        rows = "every row as expected" if "every row as expected" in text else "ROWS CHANGED"
        tables = "every table as stored" if "every table as stored" in text else "TABLES CHANGED"
        return [Outcome(self.name, code == 0, f"{rows}; {tables}")]


class Suite(Check):
    """The whole pytest suite with the native engine required; the known failures are named."""

    name = "pytest"

    def run(self, context: Context) -> list[Outcome]:
        log = context.log("pytest")
        argv = [str(context.python), "-m", "pytest", "-q", "-rfE", "-p", "no:cacheprovider"]
        argv += ["--basetemp", str(context.scratch / "pytest")]
        code = context.run(log, argv, context.env("pytest"))
        text = log.read_text()
        failed = re.findall(r"^(?:FAILED|ERROR) (.+?)(?: - .*)?$", text, flags=re.MULTILINE)
        known = [test for test in failed if test.split("[")[0] in KNOWN_FAILURES]
        unknown = [test for test in failed if test not in known]
        summary = [line.strip("= ") for line in text.splitlines() if re.search(r"\d+ (passed|failed)", line)]
        line = summary[-1] if summary else "no summary"
        if known:
            line += f"; known: {len(known)} ({'; '.join(sorted({KNOWN_FAILURES[t.split('[')[0]] for t in known}))})"
        if unknown:
            line += f"; {len(unknown)} new, first: {'; '.join(unknown[:3])}"
        return [Outcome(self.name, code in (0, 1) and not unknown and bool(summary), line)]


class CommandCheck(Check):
    """A check that is one command: passed when it exits 0, its line the command's last line."""

    log_name = ""

    def argv(self, context: Context) -> list[str]:
        raise NotImplementedError

    def line(self, log: Path) -> str:
        lines = last_lines(log, 2)
        if not lines or lines[0].startswith("$ "):
            return "clean"
        return lines[0]

    def run(self, context: Context) -> list[Outcome]:
        log = context.log(self.log_name)
        code = context.run(log, self.argv(context), context.env(self.log_name))
        return [Outcome(self.name, code == 0, self.line(log))]


class CargoTest(CommandCheck):
    name = "cargo test"
    log_name = "cargo-test"

    def argv(self, context: Context) -> list[str]:
        return ["cargo", "test"]

    def line(self, log: Path) -> str:
        results = re.findall(r"test result: (\w+)\. (\d+) passed; (\d+) failed", log.read_text())
        passed = sum(int(count) for _, count, _ in results)
        failed = sum(int(count) for _, _, count in results)
        return f"{passed} passed, {failed} failed"


class Clippy(CommandCheck):
    name = "clippy"
    log_name = "clippy"

    def argv(self, context: Context) -> list[str]:
        return ["cargo", "clippy", "--workspace", "--all-targets", "--", "-D", "warnings"]


class CargoFmt(CommandCheck):
    name = "cargo fmt"
    log_name = "cargo-fmt"

    def argv(self, context: Context) -> list[str]:
        return ["cargo", "fmt", "--all", "--check"]


class RuffCheck(CommandCheck):
    name = "ruff check"
    log_name = "ruff-check"

    def argv(self, context: Context) -> list[str]:
        return [str(context.tool("ruff")), "check", "."]


class RuffFormat(CommandCheck):
    name = "ruff format"
    log_name = "ruff-format"

    def argv(self, context: Context) -> list[str]:
        return [str(context.tool("ruff")), "format", "--check", "."]


class Ty(Check):
    """`ty check`: no more diagnostics than the baseline."""

    name = "ty"

    def run(self, context: Context) -> list[Outcome]:
        log = context.log("ty")
        context.run(log, [str(context.tool("ty")), "check", "--output-format", "concise"], context.env("ty"))
        found = re.findall(r"Found (\d+) diagnostics?", log.read_text())
        count = int(found[-1]) if found else 0
        if not found and "All checks passed" not in log.read_text():
            return [Outcome(self.name, False, f"no count in {log}")]
        line = f"{count} diagnostics, baseline {TY_BASELINE}"
        if count < TY_BASELINE:
            line += ": lower TY_BASELINE"
        return [Outcome(self.name, count <= TY_BASELINE, line)]


class ModelRepoDumps:
    """Native dumps of one copy of the model repository, each in its own process."""

    def __init__(self, context: Context, name: str) -> None:
        self.context = context
        self.name = name
        self.work = context.scratch / name
        self.repo = self.work / "repo"
        self.log = context.log(name)
        self.pythonpath = f"{context.root}:{context.root / 'tests'}"

    def copy(self) -> None:
        env = self.context.env(self.name, {"PYTHONPATH": self.pythonpath})
        argv = [str(self.context.python), "-m", "model_repo.dump", "copy", "--repo", str(self.repo)]
        if self.context.run(self.log, argv, env) != 0:
            raise RuntimeError(f"could not copy the model repository: {self.log}")

    def dump(self, label: str, cache: Path, extra: dict[str, str]) -> Path:
        out = self.work / label
        argv = [str(self.context.python), "-m", "model_repo.dump", "dump", "--repo", str(self.repo)]
        argv += ["--out", str(out), "--work", str(self.work / f"work-{label}")]
        env = self.context.env(self.name, {"PYTHONPATH": self.pythonpath, **extra, "XDG_CACHE_HOME": str(cache)})
        if self.context.run(self.log, argv, env) != 0:
            raise RuntimeError(f"the {label} dump failed: {self.log}")
        return out

    def fresh_cache(self, label: str) -> Path:
        cache = self.work / f"cache-{label}"
        cache.mkdir(parents=True)
        return cache

    def differing(self, first: Path, other: Path) -> list[str]:
        return [diff.name for diff in compare_dumps(first, other) if not diff.equal]


class Determinism(Check):
    """Every hash seed times one and four threads, each with an empty cache: all tables equal."""

    name = "determinism (model repository)"

    def run(self, context: Context) -> list[Outcome]:
        dumps = ModelRepoDumps(context, "determinism")
        dumps.copy()
        first: Path | None = None
        unequal = []
        for seed in SEEDS:
            for threads in THREADS:
                label = f"seed{seed}-threads{threads}"
                out = dumps.dump(label, dumps.fresh_cache(label), seed_env(seed, threads, threads))
                if first is None:
                    first = out
                    continue
                unequal.extend(f"{label}: {name}" for name in dumps.differing(first, out))
        runs = len(SEEDS) * len(THREADS)
        if unequal:
            return [Outcome(self.name, False, f"{runs} dumps; differ: {', '.join(unequal)}")]
        return [
            Outcome(self.name, True, f"{runs} dumps ({len(SEEDS)} seeds x {', '.join(THREADS)} threads): all equal")
        ]


# Each store switch against a cold dump with the defaults.
STORE_SWITCHES = {
    "memo off": {"HOGLINT_PACK_MEMO": "off"},
    "answers off": {"HOGLINT_NO_ANSWER_CACHE": "1"},
    "trace on": {"HOGLINT_LINK_TRACE": "1"},
}


class StoreModes(Check):
    """Memo off, answers off, trace on, a fill and a warm check on the filled cache: all tables
    equal to a cold dump, and the warm check reused stored work. The warm check is of the same
    tree, so a step that reuses items runs none of its items again: an item that runs every time
    has a read no later run can answer, or an output the memo cannot store."""

    name = "store modes (model repository)"

    def run(self, context: Context) -> list[Outcome]:
        dumps = ModelRepoDumps(context, "store-modes")
        dumps.copy()
        cold = dumps.dump("cold", dumps.fresh_cache("cold"), {})
        unequal = []
        for label, switch in STORE_SWITCHES.items():
            out = dumps.dump(label.replace(" ", "-"), dumps.fresh_cache(label.replace(" ", "-")), switch)
            unequal.extend(f"{label}: {name}" for name in dumps.differing(cold, out))
        shared = dumps.fresh_cache("fill")
        stats = dumps.work / "warm-stats.jsonl"
        for label in ("fill", "warm"):
            extra = {"HOGLINT_PACK_MEMO_STATS": str(stats)} if label == "warm" else {}
            out = dumps.dump(label, shared, extra)
            unequal.extend(f"{label}: {name}" for name in dumps.differing(cold, out))
        steps = [json.loads(line) for line in stats.read_text().splitlines()] if stats.exists() else []
        reused = sum(step["reused"] for step in steps)
        reran = [f"{step['step']} ran {step['ran']}" for step in steps if step["reused"] and step["ran"]]
        if unequal:
            return [Outcome(self.name, False, f"differ: {', '.join(unequal)}")]
        if not reused:
            return [Outcome(self.name, False, "all equal, but the warm check reused nothing")]
        if reran:
            return [
                Outcome(self.name, False, f"all equal, but the warm check ran stored items again: {', '.join(reran)}")
            ]
        return [Outcome(self.name, True, f"5 modes equal to a cold dump; warm reused {reused} steps")]


LOCAL_CHECKS: tuple[type[Check], ...] = (
    ModelRepo,
    Suite,
    CargoTest,
    Clippy,
    CargoFmt,
    RuffCheck,
    RuffFormat,
    Ty,
    Determinism,
    StoreModes,
)
