"""hoglint command line: check, explain, query, compare, map, explain-config.

Exit codes: 0 clean, 1 violations (new findings, a ledger that is not equal to the scan, code
growth, a stale exception), 2 blocking incompleteness (a failed producer, an incomplete result
the change introduced or cannot tell from an old one, a base engine that cannot run) or a config
error, 3 hoglint could not run (a missing or stale native module or extractor, an engine error,
a crash or a Rust panic), with one line on stderr. An incomplete result never hides violations:
both are printed.
"""

from __future__ import annotations

import gc
import os
import sys
import json
import shlex
import shutil
import tomllib
import argparse
import datetime
import tempfile
import posixpath
import traceback
import subprocess
import dataclasses
import importlib.metadata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

from hoglint.compare import Comparer, CompareReport, RawPolicy, Scans
from hoglint.config import CONFIG_FILE, Config, ConfigError, load_config
from hoglint.config.explain import ConfigExplainer
from hoglint.config.loader import PACK_PREFIX
from hoglint.config.model import VERSION_SLOT
from hoglint.config.schema import as_tuple
from hoglint.facts import find_binary
from hoglint.index import GraphIndex, index_of
from hoglint.labels import CandidateCache
from hoglint.ledger.cache import RunStores
from hoglint.ledger.envelope import (
    ENGINE,
    ENGINE_NATIVE,
    ENGINE_SOURCES,
    NATIVE,
    PROTOCOL,
    BinaryIds,
    EngineError,
    EngineId,
    EngineSources,
    Envelope,
    Framework,
    PolicyFiles,
    policy_digest,
)
from hoglint.ledger.errors import RepoRootError
from hoglint.ledger.file import ABSENT, LedgerFile, LineChange, change_row, grown_lines, shrunk_lines
from hoglint.ledger.growth import Renames
from hoglint.ledger.inputs import InputDigests, RunInputs, TreeFiles
from hoglint.ledger.listing import TreeListing, WorktreeStatus, check_root, git, records
from hoglint.ledger.obligations import attribute
from hoglint.ledger.scan import Obligation, ProtocolError, ScanSummary
from hoglint.ledger.store import ExtractorStore, SummaryStore, cache_root
from hoglint.model import Finding, Graph, IncompleteCause
from hoglint.native.bridge import engine_errors
from hoglint.native.labels import NativeLabeler
from hoglint.native.source import NativeGraphSource, batch_mode
from hoglint.outputs.tach_map import TachMap
from hoglint.report import HOOK_VIEW, Attributed, CheckReport, Policy, ReadTimeOwners, View
from hoglint.rules.base import EdgeRule
from hoglint.rules.engine import Engine, RunResult, primitive_of
from hoglint.rules.exceptions import ExceptionsFile
from hoglint.rules.page import RulePage
from hoglint.source import GraphSource

PACKAGE_DIR = Path(__file__).resolve().parent
# The pages `hoglint help <topic>` prints; they ship in the package, so they match its version.
PAGES = PACKAGE_DIR / "pages"


# ── snapshots ─────────────────────────────────────────────────────


class Snapshots:
    """Captures the index as a git tree, writes a tree's policy into a scratch directory, checks
    out commits, and names their trees."""

    def __init__(self, repo: Path, work_dir: Path) -> None:
        self.repo = repo
        self.work_dir = work_dir
        self.worktrees: list[Path] = []
        self.trees: dict[str, str] = {}

    def write_tree(self) -> str:
        """The index as a git tree: the one read of the index a staged run makes. Empty when the
        index holds conflicts, which no tree can hold.

        `git write-tree` takes the index's lock, which another git process or a parallel hoglint
        run may hold, so it runs on a copy of the index. Git replaces the index by renaming a
        new file over it, so the copy is one whole index."""
        index = Path(git(self.repo, "rev-parse", "--path-format=absolute", "--git-path", "index").strip())
        copy = Path(tempfile.mkdtemp(prefix="index-", dir=self.work_dir)) / "index"
        if index.is_file():
            shutil.copyfile(index, copy)
        env = {**os.environ, "GIT_INDEX_FILE": str(copy)}
        command = ["git", "-C", str(self.repo), "write-tree"]
        completed = subprocess.run(command, capture_output=True, text=True, env=env)
        if completed.returncode == 0:
            return completed.stdout.strip()
        if "unmerged" in completed.stderr:
            return ""
        raise EngineError(f"git write-tree failed: {completed.stderr.strip()[-500:]}")

    @staticmethod
    def write_config(files: TreeFiles, target: Path) -> None:
        """Writes the config and every repo file its `extends` chain names. A file that does not
        parse ends the chain there; loading the config reports it."""
        pending = [CONFIG_FILE]
        written: set[str] = set()
        while pending:
            path = posixpath.normpath(pending.pop())
            if path in written:
                continue
            written.add(path)
            files.write(target, [path])
            try:
                extends = tomllib.loads((target / path).read_text()).get("extends", [])
            except (OSError, tomllib.TOMLDecodeError):
                continue
            entries = [extends] if isinstance(extends, str) else extends
            pending.extend(e for e in entries if isinstance(e, str) and not e.startswith(PACK_PREFIX))

    def policy_of(self, tree: str) -> Path:
        """The tree's policy, written alone into a scratch directory: the config and the files it
        extends, then the ledger, the exceptions and the data files the policy names. The code is
        read from the tree by blob id, so nothing else of the tree is written."""
        files = TreeFiles(TreeListing.of(self.repo, tree))
        target = Path(tempfile.mkdtemp(prefix="policy-", dir=self.work_dir))
        self.write_config(files, target)
        if not (target / CONFIG_FILE).is_file():
            return target
        config = load_config(target, target / CONFIG_FILE)
        files.write(target, [config.ledger.file, config.ledger.exceptions, *PolicyFiles(config).paths()])
        return target

    def commit(self, ref: str) -> Path:
        """A detached git worktree of the commit, so discovery can list files with git."""
        target = Path(tempfile.mkdtemp(prefix="base-", dir=self.work_dir)) / "tree"
        git(self.repo, "worktree", "add", "--quiet", "--detach", str(target), ref)
        self.worktrees.append(target)
        return target

    def close(self) -> None:
        for target in self.worktrees:
            subprocess.run(
                ["git", "-C", str(self.repo), "worktree", "remove", "--force", str(target)], capture_output=True
            )
        self.worktrees.clear()

    def ledger_at(self, ref: str, path: str) -> LedgerFile:
        """The ledger file a commit holds; an absent one when it holds none."""
        try:
            return LedgerFile.parse(git(self.repo, "show", f"{ref}:{path}"))
        except subprocess.CalledProcessError:
            return LedgerFile(source=ABSENT)

    def merge_base(self, ref: str) -> str:
        try:
            return git(self.repo, "merge-base", ref, "HEAD").strip()
        except subprocess.CalledProcessError:
            return git(self.repo, "rev-parse", ref).strip()

    def changed_paths(self, ref: str | None, tree: str = "") -> list[str]:
        """Paths `tree` changes against HEAD, else the working tree against `ref` or HEAD."""
        if tree:
            head = self.tree_of("HEAD")
            if not head:
                return sorted(TreeListing.of(self.repo, tree).paths)
            return records(git(self.repo, "diff-tree", "-r", "-z", "--name-only", head, tree))
        if ref is not None:
            return records(git(self.repo, "diff", "-z", "--name-only", ref))
        tracked = records(git(self.repo, "diff", "-z", "--name-only", "HEAD"))
        untracked = records(git(self.repo, "ls-files", "-z", "--others", "--exclude-standard"))
        return tracked + untracked

    def renames(self, ref: str) -> list[tuple[str, str]]:
        out = []
        for line in git(self.repo, "diff", "-M", "--name-status", ref).splitlines():
            parts = line.split("\t")
            if parts and parts[0].startswith("R") and len(parts) == 3:
                out.append((parts[1], parts[2]))
        return out

    def tree_of(self, ref: str) -> str:
        """The git tree id of a commit; empty when there is none. Asked once per ref and run."""
        found = self.trees.get(ref)
        if found is not None:
            return found
        try:
            found = git(self.repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{tree}}").strip()
        except subprocess.CalledProcessError:
            found = ""
        self.trees[ref] = found
        return found

    def clean(self) -> bool:
        return not WorktreeStatus.of(self.repo).records

    def head_tree_in_worktree(self, config: Config) -> str:
        """HEAD's tree when git status shows no change to a run input under `config` (a source,
        a declared side input, the ledger), so a run of the working tree is a run of HEAD's tree;
        empty otherwise. Any other file may differ (D40)."""
        tree = self.tree_of("HEAD")
        if not tree:
            return ""
        return "" if WorktreeStatus.of(self.repo).drift(RunInputs.of(config).patterns.matches) else tree


# ── one run ───────────────────────────────────────────────────────


@dataclass(frozen=False, slots=True)
class Run:
    """`files_root` is where the run's rules and providers looked for files: the root that stands
    for the run's snapshot, a git tree or the working tree (see `TreeFiles.run_root`)."""

    root: Path
    config: Config
    graph: Graph
    result: RunResult
    ledger: LedgerFile
    exceptions: ExceptionsFile
    policy_root: Path | None = None
    files_root: Path | None = None


class Runner:
    """Runs policy over code. One GraphIndex per graph serves every run that reads that graph, and
    one CandidateCache every run."""

    def __init__(self, source: GraphSource, today: datetime.date | None = None) -> None:
        self.source = source
        self.today = today
        self.indexes: dict[int, tuple[Graph, GraphIndex]] = {}
        self.candidates = CandidateCache()

    def index_of(self, graph: Graph) -> GraphIndex:
        held = self.indexes.get(id(graph))
        if held is None or held[0] is not graph:
            held = self.indexes[id(graph)] = (graph, index_of(graph))
        return held[1]

    def files_root(self, repo: Path, tree: str, config: Config) -> Path:
        """The root that stands for a git tree, or for the working tree when `tree` is empty,
        where rules and providers look for files. It answers whether a path exists only for the
        paths `config`'s input patterns cover. `config` is the policy as loaded, before its data
        files are anchored, as the run's key reads it."""
        inputs = RunInputs.of(config)
        listing = GraphSource.listing_of(repo, tree)
        return TreeFiles(listing, self.source.work_dir, inputs.patterns, inputs.plugin_patterns).run_root()

    def evaluate(self, config: Config, code_root: Path, policy_root: Path) -> tuple[Graph, RunResult]:
        """One policy over the working tree of one code root, with no ledger and no exceptions."""
        judged = PolicyFiles(config).anchored(policy_root, code_root)
        graph = self.source.build(code_root, judged)
        files_root = self.files_root(code_root, "", config)
        engine = Engine(judged, graph, files_root, self.today, self.index_of(graph), self.candidates)
        return graph, engine.evaluate()

    def run(
        self,
        policy_root: Path,
        code_root: Path | None = None,
        staged: bool = False,
        rewrite_ledger: bool = False,
        tree: str = "",
        ledger: LedgerFile | None = None,
        files_root: Path | None = None,
    ) -> Run:
        """Policy (config, ledger, exceptions) from one root, code from another, or both from one.

        The policy's data files are read from the policy root, whatever the code root. `tree`
        reads the code and every side input from that git tree by blob id; without it they come
        from the code root's working tree as git sees it. `files_root` is the snapshot's root when
        the caller made one. `ledger` replaces the policy root's ledger. With `rewrite_ledger` the
        run is judged against the ledger it would write, not the file."""
        code_root = code_root or policy_root
        config = load_config(policy_root, policy_root / CONFIG_FILE)
        files_root = files_root or self.files_root(code_root, tree, config)
        judged = PolicyFiles(config).anchored(policy_root, code_root, always=bool(tree))
        graph = self.source.build(code_root, judged, staged, tree)
        if ledger is None:
            ledger = LedgerFile.read(policy_root / config.ledger.file)
        exceptions = ExceptionsFile.read(policy_root / config.ledger.exceptions, config)
        engine = Engine(judged, graph, files_root, self.today, self.index_of(graph), self.candidates)
        result = engine.evaluate(ledger, exceptions, rewrite_ledger)
        return Run(code_root, config, graph, result, result.ledger or ledger, exceptions, policy_root, files_root)


def native_id_of(crate: Path) -> str:
    """The `[native]` id of a crate's sources."""
    sources = EngineSources.read(crate)
    if sources is None:
        raise EngineError(f"{crate} has no {ENGINE_SOURCES}: the engine predates run summary protocol {PROTOCOL}")
    return sources.digest(NATIVE)


def summary_of(
    run: Run,
    envelope: Envelope,
    scope: frozenset[str] = frozenset(),
    with_triples: bool = True,
    touched: set[str] | None = None,
) -> ScanSummary:
    roots = (run.policy_root or run.root, run.root, run.files_root or run.root)
    return ScanSummary.from_run(
        run.result, run.graph, envelope, scope, with_triples, roots, run.exceptions, run.ledger, touched
    )


# ── rendering ─────────────────────────────────────────────────────


def where(finding: Finding) -> str:
    if finding.site is None:
        return ""
    return f"{finding.site.path}:{finding.site.line}"


def policy_note(written: dict[str, str], name: str) -> str:
    """Where a selector's text came from when the config wrote it from a shape policy, so `explain`
    never shows text that is in no file."""
    return f"  ({written[name]})" if name in written else ""


def finding_line(finding: Finding) -> str:
    owners = f"  [{', '.join(finding.owners)}]" if finding.owners else ""
    return f"{finding.rule}  {' '.join(finding.identity)}\n    {finding.message}  {where(finding)}{owners}"


# What `--write-ledger` tells the reader of lines it does not add without the switch.
RECORD_GROWTH_WAY_OUT = (
    "new debt needs a person: fix the findings, or stop and tell the person; a person who accepts "
    "the debt, or a policy change that finds more, records it with --write-ledger --record-growth"
)
# What a command that could not run at all prints, in one line (D104.3, D111). The hook's command
# lets the commit through on exit 3; CI runs the full check.
COULD_NOT_RUN = 3
HOOK_COULD_NOT_RUN = (
    "hoglint did not check this commit, because hoglint could not run: {reason}. The commit continues. "
    "CI runs the full check. To repair hoglint, see `hoglint help hook`."
)
COMMAND_COULD_NOT_RUN = "hoglint could not run: {reason}. To repair hoglint, see `hoglint help hook`."
TRACEBACK_ENV = "HOGLINT_TRACEBACK"
# ── engines and baselines ─────────────────────────────────────────


class LocalEngine:
    """The engine this process runs: its id, its extractor, and the envelope of one of its runs."""

    def __init__(self, digests: InputDigests | None = None, stores: RunStores | None = None) -> None:
        self.name = ENGINE_NATIVE
        self.frameworks: dict[tuple[str, ...], str] = {}
        self.digests = digests or InputDigests(cache_root(), stores)

    @cached_property
    def binary(self) -> Path:
        try:
            return find_binary()
        except FileNotFoundError as error:
            raise EngineError(str(error)) from error

    @cached_property
    def native_id(self) -> str:
        """The `[native]` id the extractor binary was built from."""
        return BinaryIds.of(self.binary, NATIVE)

    @cached_property
    def id(self) -> str:
        crate = EngineId.crate_of(PACKAGE_DIR)
        if crate is not None:
            return EngineId.of_running(crate, self.name)
        return EngineId.installed(self.name, BinaryIds.of(self.binary, ENGINE))

    def framework(self, config: Config) -> str:
        packages = Framework.packages_of(config)
        if packages not in self.frameworks:
            self.frameworks[packages] = Framework.digest(packages)
        return self.frameworks[packages]

    def inputs(self, repo: Path, tree: str, config: Config) -> str:
        """The inputs digest of a git tree under `config`; empty when there is no tree."""
        return self.digests.of(repo, tree, RunInputs.of(config).patterns)

    def envelope(self, run: Run, tree: str) -> Envelope:
        """The envelope of a run whose inputs are those of `tree` in the run's repo."""
        root = run.policy_root or run.root
        policy = policy_digest(run.config, run.exceptions.raw, run.exceptions.problems, root)
        return Envelope(self.inputs(run.root, tree, run.config), self.id, policy, self.framework(run.config))

    def envelope_at(self, policy_root: Path, repo: Path, tree: str) -> tuple[Envelope, Config]:
        """The envelope a run of `tree` in `repo` under the policy at `policy_root` would have,
        before the run."""
        config = load_config(policy_root, policy_root / CONFIG_FILE)
        exceptions = ExceptionsFile.read(policy_root / config.ledger.exceptions, config)
        policy = policy_digest(config, exceptions.raw, exceptions.problems, policy_root)
        return Envelope(self.inputs(repo, tree, config), self.id, policy, self.framework(config)), config


# The installed distribution whose version a base commit's lock file pins.
DISTRIBUTION = "hoglint"
# Variables that point a process at an engine's sources, extractor or native module: passed on to
# a pinned engine, they would run this engine's code under the pinned engine's name.
ENGINE_LOCATORS = frozenset(
    {"PYTHONPATH", "HOGLINT_FACTS_BIN", "HOGLINT_NATIVE_DIR", "HOGLINT_BASE_FACTS_BIN", "CARGO_TARGET_DIR"}
)


@dataclass(frozen=True, slots=True)
class BaseEngine:
    """The engine of the base commit, and how its `scan` runs: `command` starts its `hoglint`, with
    `env` as the whole environment and `cwd` as the directory. No command means this process's
    own engine."""

    id: str
    command: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    cwd: Path | None = None

    @property
    def in_process(self) -> bool:
        return not self.command


class LockPin:
    """The hoglint version a lock file pins: the version of its `[[package]]` entry named hoglint."""

    @staticmethod
    def read(lock: Path) -> str | None:
        """None when the file or its hoglint entry is absent. A file that does not read, or that
        pins more than one version, is an EngineError: the base engine is unknown, not absent."""
        try:
            data = tomllib.loads(lock.read_text())
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            raise EngineError(
                f"{lock.name} does not read, so the {DISTRIBUTION} it pins is unknown: {error}"
            ) from error
        entries = [e for e in data.get("package", []) if isinstance(e, dict) and e.get("name") == DISTRIBUTION]
        versions = sorted({str(entry.get("version", "")) for entry in entries})
        if not versions:
            return None
        if len(versions) > 1 or not versions[0]:
            raise EngineError(f"{lock.name} pins {DISTRIBUTION} at {versions}, so the base engine is unknown")
        return versions[0]

    @staticmethod
    def running() -> str | None:
        """The version of the installed hoglint this process runs; None when none is installed."""
        try:
            return importlib.metadata.version(DISTRIBUTION)
        except importlib.metadata.PackageNotFoundError:
            return None


class Version(argparse.Action):
    """`hoglint --version`. The one source is `[project] version` in pyproject.toml: a checkout
    reads it there, an installed wheel from its metadata, as `LockPin.running` does. It is read
    only when asked, so no other command pays for the lookup."""

    @staticmethod
    def text() -> str:
        crate = EngineId.crate_of(PACKAGE_DIR)
        if crate is None:
            return f"{DISTRIBUTION} {LockPin.running() or 'unknown'}"
        project = tomllib.loads((crate / "pyproject.toml").read_text())["project"]
        return f"{DISTRIBUTION} {project['version']} (checkout {crate})"

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        sys.stdout.write(self.text() + "\n")
        parser.exit()


class BaseEngines:
    """Pins the engine a base commit's scan runs. An engine inside the repo runs the base's own
    Python sources with an extractor built from the base's own extractor sources. An installed
    engine runs the version the base pins in its lock file (D15). An engine that cannot run as
    pinned is an EngineError, which compare reports as incomplete (exit 2)."""

    def __init__(self, repo: Path, work_dir: Path, local: LocalEngine, extractors: ExtractorStore) -> None:
        self.repo = repo
        self.work_dir = work_dir
        self.local = local
        self.extractors = extractors

    def package(self) -> Path | None:
        """The engine package relative to the repo; None when the engine is installed outside it."""
        repo = self.repo.resolve()
        return PACKAGE_DIR.relative_to(repo) if PACKAGE_DIR.is_relative_to(repo) else None

    @staticmethod
    def installed() -> bool:
        """Whether this engine is installed: no crate sits next to its package. A source checkout
        run from outside the repo has a crate, and no version a lock file could name."""
        return EngineId.crate_of(PACKAGE_DIR) is None

    @staticmethod
    def run(engine: BaseEngine, args: list[str]) -> subprocess.CompletedProcess[str]:
        """Runs the base engine's `hoglint` with `args`; a command that does not start is an EngineError."""
        command = [*engine.command, *args]
        try:
            return subprocess.run(command, env=dict(engine.env), cwd=engine.cwd, capture_output=True, text=True)
        except OSError as error:
            raise EngineError(f"the base engine did not start ({' '.join(engine.command)}): {error}") from error

    def pinned(self, base_root: Path, config: Config) -> BaseEngine | None:
        """The installed engine at the version the base pins in `[engine] lock`: this process when
        it runs that version, else `[engine] runner` with that version, which is asked for its id
        before any scan, so its summaries are stored and found under its own id. None when the
        base pins no version. The runner runs in the repo, without this engine's locators."""
        version = LockPin.read(base_root / config.engine.lock)
        if version is None:
            return None
        if version == LockPin.running():
            return BaseEngine(self.local.id)
        command = tuple(shlex.split(config.engine.runner.replace(VERSION_SLOT, version)))
        env = {name: value for name, value in os.environ.items() if name not in ENGINE_LOCATORS}
        engine = BaseEngine("", command, env, self.repo)
        completed = self.run(engine, ["engine-id"])
        printed = completed.stdout.split()
        if completed.returncode != 0 or not printed:
            raise EngineError(
                f"the base engine {DISTRIBUTION}=={version} gave no engine id (exit {completed.returncode}):\n"
                f"{completed.stderr[-2000:]}"
            )
        return dataclasses.replace(engine, id=printed[-1])

    def id_of(self, base_root: Path, config: Config) -> str | None:
        """The id of the engine a checkout holds or pins under its `config`; None when it predates
        the engine."""
        package = self.package()
        if package is not None:
            if not (base_root / package).is_dir():
                return None
            return EngineId.of(base_root / package.parent, self.local.name)
        if not self.installed():
            return self.local.id
        pinned = self.pinned(base_root, config)
        return None if pinned is None else pinned.id

    def of(self, base_root: Path, config: Config) -> BaseEngine | None:
        """None when the base predates the engine: it holds no engine package of the repo, or it
        pins no version of the installed engine."""
        package = self.package()
        if package is None:
            return self.pinned(base_root, config) if self.installed() else BaseEngine(self.local.id)
        engine_id = self.id_of(base_root, config)
        if engine_id is None:
            return None
        crate = package.parent
        self.check_local(crate)
        if engine_id == self.local.id:
            return BaseEngine(engine_id)
        binary = self.binary(native_id_of(base_root / crate), crate)
        env = {**os.environ, "PYTHONPATH": str(base_root / crate), "HOGLINT_FACTS_BIN": str(binary)}
        return BaseEngine(engine_id, (sys.executable, "-m", "hoglint"), env)

    def check_local(self, crate: Path) -> None:
        """This checkout's extractor must be built from its own Rust sources (`[native]`; a Python
        edit needs no rebuild), and it is kept for later bases. A checkout without its source list
        runs an extractor it does not describe, so there is nothing to check."""
        if EngineSources.read(self.repo / crate) is None:
            return
        expected = native_id_of(self.repo / crate)
        if expected != self.local.native_id:
            raise EngineError(
                f"{self.local.binary} was built from native sources {self.local.native_id}, but the sources in "
                f"{crate} are {expected}; rebuild it with {crate}/bin/build"
            )
        self.extractors.keep(self.local.binary, expected)

    def binary(self, native_id: str, crate: Path) -> Path:
        override = os.environ.get("HOGLINT_BASE_FACTS_BIN")
        if override:
            found = BinaryIds.of(Path(override), NATIVE)
            if found != native_id:
                raise EngineError(f"HOGLINT_BASE_FACTS_BIN is native build {found}; the base needs {native_id}")
            return Path(override)
        if native_id == self.local.native_id:
            return self.local.binary
        stored = self.extractors.find(native_id)
        if stored is not None:
            return stored
        raise EngineError(
            f"no hoglint-facts binary of native build {native_id} for the base engine: build {crate}/bin/build "
            f"in a checkout of the base commit and copy the binary it prints to {self.extractors.path(native_id)}, "
            "or set HOGLINT_BASE_FACTS_BIN"
        )

    def scan(
        self, engine: BaseEngine, base_root: Path, code_root: Path, tree: str, scope: frozenset[str]
    ) -> ScanSummary:
        """Runs `hoglint scan` of the base engine in its own process and reads its summary."""
        if engine.in_process:
            raise EngineError("the base engine runs in this process; it has no scan command to call")
        work = Path(tempfile.mkdtemp(prefix="base-scan-", dir=self.work_dir))
        out, scope_file = work / "scan.json", work / "scope.json"
        scope_file.write_text(json.dumps(sorted(scope)))
        args = ["--repo", str(base_root), "--engine", self.local.name, "scan", "--code-root", str(code_root)]
        args += ["--out", str(out), "--tree", tree, "--scope", str(scope_file)]
        completed = self.run(engine, args)
        if completed.returncode != 0 or not out.is_file():
            raise EngineError(f"the base engine failed (exit {completed.returncode}):\n{completed.stderr[-2000:]}")
        try:
            summary = ScanSummary.read(out)
        except ProtocolError as error:
            raise EngineError(f"the base engine wrote a summary this engine cannot read: {error}") from error
        if summary.envelope.engine != engine.id:
            raise EngineError(f"the base engine ran as {summary.envelope.engine}, not as the pinned {engine.id}")
        return summary


@dataclass(frozen=True, slots=True)
class Baseline:
    """What one check run attributes its incomplete results against.

    `envelope` is the run's own key: its inputs are those of the tree the run reads, the staged
    tree's for a staged run, so the run's summary is stored for the next commit, and HEAD's for a
    working tree that holds HEAD's inputs. A run of the working tree itself has none, so it is
    never stored and never read from the store. `reference` is None when the run itself is the baseline (`is_self`) or
    when there is no commit to compare with. `config` is the run's policy, as the key read it."""

    envelope: Envelope
    against: str
    config: Config
    reference: dict[Obligation, list[str]] | None = None
    is_self: bool = False
    staged: bool = False


class Baselines:
    """Introduced versus inherited outside compare. The baseline is HEAD's tree under the same
    engine and policy as the run, so a policy change never counts as introduced by code. It comes
    from the per-machine summary cache; else the run itself is HEAD; else HEAD's tree is evaluated
    once by blob id, with no checkout, and stored. A staged run stores its own summary under the
    inputs of the index's tree, so the next commit finds its baseline in the cache. A commit that
    changes no input has HEAD's key, so the run is its own baseline."""

    def __init__(self, snapshots: Snapshots, engine: LocalEngine, store: SummaryStore) -> None:
        self.snapshots = snapshots
        self.repo = snapshots.repo
        self.work_dir = snapshots.work_dir
        self.engine = engine
        self.store = store

    def prepare(self, policy_root: Path, tree: str, staged: bool) -> Baseline:
        """`tree` is the tree the run reads: a staged run's, written from the index, or HEAD's for
        a working tree that holds HEAD's inputs; empty for a run of the working tree itself."""
        head_tree = self.snapshots.tree_of("HEAD")
        wanted, config = self.engine.envelope_at(policy_root, self.repo, head_tree)
        own = dataclasses.replace(wanted, inputs=self.engine.inputs(self.repo, tree, config))
        if not head_tree:
            return Baseline(own, "nothing (no commit yet)", config, staged=staged)
        short = head_tree[:12]
        stored = self.store.get(wanted, frozenset({"envelope", "incompletes"}))
        if stored is not None:
            against = f"HEAD (tree {short}, stored summary)"
            return Baseline(own, against, config, stored.obligations(), staged=staged)
        if own.inputs == wanted.inputs:
            this_run = f"HEAD (tree {short}, this run)"
            return Baseline(own, this_run, config, is_self=True, staged=staged)
        summary = self.evaluate(policy_root, config.ledger.file, head_tree, wanted, staged)
        # The baseline's graph sits in reference cycles (a rule's findings hold the rule), and a
        # native run turns the cyclic collector off, so only a collection frees it before the run.
        gc.collect()
        self.store.put(summary.envelope, summary)
        computed = f"HEAD (tree {short}, computed and stored)"
        return Baseline(own, computed, config, summary.obligations(), staged=staged)

    def evaluate(
        self, policy_root: Path, ledger_path: str, head_tree: str, envelope: Envelope, staged: bool
    ) -> ScanSummary:
        """HEAD's tree under the run's policy, with HEAD's own ledger, in a runner of its own so its
        graph can be freed before the run. A commit's baseline takes the staged thread count."""
        ledger = self.snapshots.ledger_at(head_tree, ledger_path)
        work = Path(tempfile.mkdtemp(prefix="baseline-", dir=self.work_dir))
        runner = Runner(NativeGraphSource(work, self.store.stores))
        run = runner.run(policy_root, self.repo, staged=staged, tree=head_tree, ledger=ledger)
        return summary_of(run, envelope, with_triples=False)

    def attribute(self, baseline: Baseline, summary: ScanSummary) -> Attributed:
        head = summary.obligations()
        reference = head if baseline.is_self else baseline.reference
        return Attributed(attribute(head, reference), baseline.against)


class RunMemo:
    """`check` of a git tree that a run already evaluated, as a lookup: the stored summary of the
    tree stands in for the run when no other input of the output differs.

    The key is the run's envelope (inputs, engine, policy, framework) and the source of its
    ledger. A staged run reads every input from its tree. A check of a working tree that holds
    HEAD's inputs (sources, declared side inputs, the ledger) runs HEAD's tree, and so has HEAD's
    key; the policy digest covers the policy files it reads from the working tree, and the ledger
    source a ledger the tree does not hold, such as an ignored one. Owners and exceptions past
    review depend on other files and on the day, so the report reads them when it renders.

    These stay live: a working tree whose inputs differ from HEAD's, `--changed`,
    `--write-ledger`, a policy with outputs (they are written from the graph), and a summary with
    an extractor failure, which can be transient."""

    SECTIONS = frozenset({"envelope", "incompletes", "findings", "levels"})

    def __init__(self, snapshots: Snapshots, store: SummaryStore) -> None:
        self.snapshots = snapshots
        self.store = store

    def lookup(self, baseline: Baseline, policy_root: Path) -> ScanSummary | None:
        config = baseline.config
        if config.outputs:
            return None
        key = baseline.envelope
        if not key.inputs:
            return None
        summary = self.store.get(key, self.SECTIONS)
        if summary is None or summary.ledger != LedgerFile.source_of(policy_root / config.ledger.file):
            return None
        if any(item.cause is IncompleteCause.EXTRACTOR for item in summary.incomplete):
            return None
        return summary


# ── commands ──────────────────────────────────────────────────────


class Commands:
    """`today` replaces the date in output that depends on it. With `memo` off, every check runs live.
    `stores` are the run's stores, read from the environment once here and passed down."""

    def __init__(
        self,
        repo: Path,
        work_dir: Path,
        today: datetime.date | None = None,
        memo: bool = True,
    ) -> None:
        self.repo = repo
        self.work_dir = work_dir
        self.stores = RunStores.of_environment()
        self.source: GraphSource = NativeGraphSource(work_dir, self.stores)
        self.runner = Runner(self.source, today)
        self.snapshots = Snapshots(repo, work_dir)
        self.engine = LocalEngine(stores=self.stores)
        self.summaries = SummaryStore(stores=self.stores)
        self.baselines = Baselines(self.snapshots, self.engine, self.summaries)
        self.base_engines = BaseEngines(repo, work_dir, self.engine, ExtractorStore(stores=self.stores))
        self.memo = RunMemo(self.snapshots, self.summaries) if memo else None

    def worktree_tree(self, config: Config) -> str:
        """The tree a run of the working tree reads (D40): HEAD's, when git status shows no change
        to a run input under `config`, the working tree's policy; else empty, and the run reads
        the working tree as git sees it."""
        return self.snapshots.head_tree_in_worktree(config)

    def ledger_growth(self, policy_root: Path, config: Config) -> list[LineChange]:
        """The rule lines the run's ledger adds or raises against HEAD's when the policy is HEAD's:
        new debt recorded by hand or by a re-record, which needs a person. A policy change can
        move lines, so its lines go to compare's growth check."""
        head = self.snapshots.ledger_at("HEAD", config.ledger.file)
        if not head.exists or head.policy != config.policy_hash:
            return []
        return grown_lines(head, LedgerFile.read(policy_root / config.ledger.file))

    def write_ledger(self, run: Run, record_growth: bool) -> int | None:
        """Writes the run's ledger over the file, and says what it added and removed. It refuses
        to add or raise a line without `record_growth` and returns the exit code then."""
        path = self.repo / run.config.ledger.file
        before = LedgerFile.read(path)
        grown, shrunk = grown_lines(before, run.ledger), shrunk_lines(before, run.ledger)
        if grown and not record_growth:
            sys.stdout.write(f"not written: the scan adds {len(grown)} lines to {run.config.ledger.file}\n")
            sys.stdout.write("".join(f"  {change_row(change)}\n" for change in grown))
            sys.stdout.write(f"  {RECORD_GROWTH_WAY_OUT}\n")
            return 1
        run.ledger.write(path)
        sys.stdout.write(
            f"wrote {len(run.ledger.lines)} lines to {run.config.ledger.file}: "
            f"{len(grown)} added or raised, {len(shrunk)} removed or lowered\n"
        )
        sys.stdout.write("".join(f"  added  {change_row(change)}\n" for change in grown))
        return None

    def shrink_ledger(self) -> int:
        """`ledger shrink`: removes the ledger's stale lines and lowers its counts to the scan of
        the working tree. It never adds or raises a line, so it needs no person."""
        config = load_config(self.repo, self.repo / CONFIG_FILE)
        path = self.repo / config.ledger.file
        before = LedgerFile.read(path)
        if not before.exists:
            sys.stdout.write(f"{config.ledger.file} does not exist, so there is nothing to shrink\n")
            return 0
        run = self.runner.run(self.repo, tree=self.worktree_tree(config))
        after = before.shrunk(run.result.scan)
        removed = shrunk_lines(before, after)
        if not removed:
            sys.stdout.write(f"{config.ledger.file} has no stale lines\n")
            return 0
        after.write(path)
        sys.stdout.write(f"removed or lowered {len(removed)} lines of {config.ledger.file}\n")
        sys.stdout.write("".join(f"  {change_row(change)}\n" for change in removed))
        return 0

    def check(
        self, index: bool, write_ledger: bool, changed: bool, record_growth: bool = False, view: View = HOOK_VIEW
    ) -> int:
        """With `index`, the index is captured once as a git tree, and the run reads everything
        from that tree: code, policy, side inputs and the owners it prints. Without it, the run
        reads HEAD's tree when the working tree holds HEAD's inputs, else the working tree."""
        policy_root = self.repo
        if index:
            tree = self.snapshots.write_tree()
            if not tree:
                sys.stderr.write("the index holds merge conflicts, so there is no staged tree to check\n")
                return 2
            policy_root = self.snapshots.policy_of(tree)
            config = load_config(policy_root, policy_root / CONFIG_FILE)
        else:
            config = load_config(self.repo, self.repo / CONFIG_FILE)
            tree = self.worktree_tree(config)
        files_root = self.runner.files_root(self.repo, tree, config)
        grown = [] if changed or write_ledger else self.ledger_growth(policy_root, config)
        baseline = None if changed else self.baselines.prepare(policy_root, tree, index)
        if baseline is not None and self.memo is not None and not write_ledger:
            stored = self.memo.lookup(baseline, policy_root)
            if stored is not None:
                attributed = self.baselines.attribute(baseline, stored)
                return self.report(stored, baseline.config, policy_root, files_root, None, attributed, grown, view)
        run = self.runner.run(
            policy_root, self.repo, staged=index, rewrite_ledger=write_ledger, tree=tree, files_root=files_root
        )
        if write_ledger:
            if index:
                sys.stderr.write("--write-ledger writes the working tree file, not the index\n")
            refused = self.write_ledger(run, record_growth)
            if refused is not None:
                return refused
        self.write_outputs(run)
        if baseline is None:  # --changed: advisory, so it always exits 0
            touched = set(self.snapshots.changed_paths(None, tree if index else ""))
            touched |= {m for m, p in run.graph.modules.items() if p in touched}
            summary = summary_of(run, Envelope(), with_triples=False, touched=touched)
            self.report(summary, run.config, policy_root, files_root, touched, None, view=view)
            return 0
        summary = summary_of(run, baseline.envelope, with_triples=False)
        if not write_ledger:
            self.summaries.put(summary.envelope, summary)
        attributed = self.baselines.attribute(baseline, summary)
        return self.report(summary, run.config, policy_root, files_root, None, attributed, grown, view)

    def report(
        self,
        summary: ScanSummary,
        config: Config,
        policy_root: Path,
        files_root: Path,
        touched: set[str] | None,
        attributed: Attributed | None,
        grown: list[LineChange] | None = None,
        view: View = HOOK_VIEW,
    ) -> int:
        """Prints the check report of a summary and returns its exit code. Owners are read from
        `files_root`, the run's own files."""
        owners = ReadTimeOwners(PolicyFiles(config).anchored(policy_root, self.repo), summary.modules, files_root)
        today = self.runner.today or datetime.date.today()
        policy = Policy.of(config, CONFIG_FILE)
        report = CheckReport(summary, owners, today, policy, view, touched, attributed, grown)
        sys.stdout.write(report.render())
        return report.exit_code()

    def write_outputs(self, run: Run) -> None:
        for output in run.config.outputs.values():
            path = run.root / output.path
            path.parent.mkdir(parents=True, exist_ok=True)
            if output.format == "tach-map":
                TachMap.of(run.graph, run.config).write(path)
                continue
            edges = [
                {"kind": e.kind, "src": e.site.path, "surface": e.surface, "targets": [str(t) for t in e.targets]}
                for e in run.graph.edges
            ]
            path.write_text(json.dumps(edges))

    def map(self, out: Path | None) -> int:
        """`map`: the dependents map in tach's JSON shape, of the tree a check would read, to stdout
        or to `out`. It reads core's import and dotted-path edges alone, so it runs no pack, rule
        or provider: the same map as an `[outputs]` table of format `tach-map`."""
        config = load_config(self.repo, self.repo / CONFIG_FILE)
        graph = self.source.build_core(self.repo, config, self.worktree_tree(config))
        text = TachMap.of(graph, config).text()
        if out is None:
            sys.stdout.write(text)
            return 0
        out.write_text(text)
        return 0

    def explain(self, target: str) -> int:
        """A rule's page by its name (D113), with no run; else the labels of a path or module, or
        why an edge was selected."""
        config = load_config(self.repo, self.repo / CONFIG_FILE)
        rule = config.rules.get(target)
        if rule is not None:
            sys.stdout.write(RulePage(rule, config).render())
            return 0
        run = self.runner.run(self.repo)
        if target.startswith("edge:"):
            return self.explain_edge(run, target.removeprefix("edge:"))
        labeler = run.result.labeler
        module = labeler.path_to_module.get(target, target)
        if module not in run.graph.modules:
            sys.stderr.write(f"{target}: not a module or path hoglint knows\n")
            return 2
        sys.stdout.write(f"module   {module}\n")
        for name, value, source in labeler.explain(module):
            sys.stdout.write(f"{name:8} {value:30} <- {source}\n")
        for finding in run.result.findings():
            if module in finding.subjects:
                sys.stdout.write(f"finding  [{finding.status.value}] {finding_line(finding)}\n")
        return 0

    def explain_edge(self, run: Run, run_id: str) -> int:
        """Each rule over edges that sees the edge, and per destination the truth of each of its
        selectors, as the evaluator reads them."""
        labeler = run.result.labeler
        if not isinstance(labeler, NativeLabeler) or labeler.evaluator is None:
            raise EngineError("explain edge: needs a run of the native evaluator")
        ordinals = labeler.index.ordinals
        edge = next((e for e in run.graph.edges if e.run_id(ordinals.get(e.handle, 0)) == run_id), None)
        if edge is None:
            sys.stderr.write(f"no edge with id {run_id}\n")
            return 2
        sys.stdout.write(
            f"edge     {edge.kind} {edge.site.path}:{edge.site.line} {edge.surface} -> {[str(t) for t in edge.targets]}\n"
        )
        sys.stdout.write(
            f"         resolution={edge.resolution.value} contexts={sorted(c.value for c in edge.contexts)}\n"
        )
        for at, spec in enumerate(run.config.rules.values()):
            if not issubclass(primitive_of(spec), EdgeRule):
                continue
            with engine_errors():
                found = labeler.evaluator.explain_edge(at, edge.handle)
            written = dict(spec.written)
            for dst_symbol, dst_module, truths in found or ():
                parts = [
                    f"{name.rstrip('_')}: {getattr(spec, name).source} -> {truth}{policy_note(written, name)}"
                    for name, truth in truths
                ]
                sys.stdout.write(f"rule     {spec.id} (endpoint {spec.endpoint}, {dst_symbol or dst_module})\n")
                sys.stdout.write("".join(f"           {part}\n" for part in parts))
        for finding in run.result.findings():
            if edge in finding.witnesses:
                sys.stdout.write(f"finding  [{finding.status.value}] {finding.rule} {' '.join(finding.identity)}\n")
        return 0

    def query(self, by: str) -> int:
        summary, config, files_root = self.current_summary()
        owners = ReadTimeOwners(config, summary.modules, files_root) if by == "owners" else None
        counts: dict[str, Counter[str]] = {}
        for status, rule, owner, target in summary.findings.open_rows():
            keys = (owners.of_module(owner) or ("(no owner)",)) if owners is not None else (target,)
            for key in keys:
                counts.setdefault(key, Counter())[f"{status.value}:{rule}"] += 1
        for key in sorted(counts, key=lambda k: (-sum(counts[k].values()), k)):
            total = sum(counts[key].values())
            detail = ", ".join(f"{name} {count}" for name, count in counts[key].most_common())
            sys.stdout.write(f"{key}  {total}  ({detail})\n")
        return 0

    def current_summary(self) -> tuple[ScanSummary, Config, Path]:
        """The summary of the working tree and the root of its files: the stored summary of
        HEAD's tree when the working tree holds HEAD's inputs, else a run of that tree, which is
        stored, or of the working tree, which is not."""
        tree = self.worktree_tree(load_config(self.repo, self.repo / CONFIG_FILE))
        key, config = self.engine.envelope_at(self.repo, self.repo, tree)
        stored = self.summaries.get(key, frozenset({"envelope", "findings"}))
        if stored is not None:
            return stored, config, self.runner.files_root(self.repo, tree, config)
        run = self.runner.run(self.repo, tree=tree)
        summary = summary_of(run, self.engine.envelope(run, tree), with_triples=False)
        self.summaries.put(summary.envelope, summary)
        return summary, run.config, run.files_root or run.root

    def explain_config(self) -> int:
        config = load_config(self.repo, self.repo / CONFIG_FILE)
        sys.stdout.write(ConfigExplainer(config).render())
        return 0

    # ── compare ──

    def compare(self, ref: str, view: View = HOOK_VIEW) -> int:
        base_sha = self.snapshots.merge_base(ref)
        base_tree = self.snapshots.tree_of(base_sha)
        base_root = self.snapshots.commit(base_sha)
        head_tree = self.worktree_tree(load_config(self.repo, self.repo / CONFIG_FILE))
        head = self.runner.run(self.repo, tree=head_tree)
        renames = self.module_renames(base_sha, head.config)
        head_key = self.engine.envelope(head, head_tree)
        scans = Scans(head=summary_of(head, head_key, renames.modules()))
        self.summaries.put(scans.head.envelope, scans.head)
        base_config = load_config(base_root, base_root / CONFIG_FILE) if (base_root / CONFIG_FILE).is_file() else None
        base_exceptions = ExceptionsFile()
        same_policy = False
        if base_config is not None:
            base_exceptions = ExceptionsFile.read(base_root / base_config.ledger.exceptions, base_config)
            base_policy = policy_digest(base_config, base_exceptions.raw, base_exceptions.problems, base_root)
            same_policy = base_policy == scans.head.envelope.policy
            try:
                scans.base, scans.head_base, scans.base_source = self.base_scans(
                    base_tree, base_root, base_config, base_policy, head_tree, scans.head, same_policy
                )
            except EngineError as error:
                scans.base_failure = str(error)
        if scans.base is None or not same_policy:
            # Head policy on base code: the reference when no base scan exists, and the evidence
            # that labels each policy change.
            base_graph, base_head = self.runner.evaluate(head.config, base_root, self.repo)
            envelope = Envelope("", self.engine.id, scans.head.envelope.policy, scans.head.envelope.framework)
            scans.base_head = ScanSummary.from_run(base_head, base_graph, envelope, roots=(self.repo, base_root))
        touched_paths = set(self.snapshots.changed_paths(base_sha))
        touched = {module for module, path in head.graph.modules.items() if path in touched_paths}
        base_files = PolicyFiles(base_config).digests(base_root) if base_config is not None else {}
        report: CompareReport = Comparer(
            scans,
            LedgerFile.read(base_root / head.config.ledger.file),
            head.ledger,
            RawPolicy(base_config.expanded if base_config is not None else {}, base_exceptions.raw, base_files),
            RawPolicy(head.config.expanded, head.exceptions.raw, PolicyFiles(head.config).digests(self.repo)),
            touched,
            renames,
        ).report()
        owners = ReadTimeOwners(head.config, scans.head.modules, head.files_root or head.root)
        today = self.runner.today or datetime.date.today()
        policy = Policy.of(head.config, CONFIG_FILE)
        attributed = Attributed(report.labels, "the base")
        head_view = dataclasses.replace(view, rerun=f"hoglint compare --base {ref}")
        code = report.exit_code(head.result.failed(), bool(head.result.blocking_incomplete()))
        sys.stdout.write(report.render_all() if view.all else report.render(code, head_view.rerun))
        sys.stdout.write(CheckReport(scans.head, owners, today, policy, head_view, attributed=attributed).render())
        return code

    def module_renames(self, base_sha: str, config: Config) -> Renames:
        from hoglint.core.modules import ModuleNamer  # noqa: PLC0415 — the extractor stack loads only when needed

        namer = ModuleNamer(as_tuple(config.discovery.get("source_roots", ["."])))
        mapping = {}
        for old, new in self.snapshots.renames(base_sha):
            if old.endswith(".py") and new.endswith(".py"):
                mapping[namer.module_of(new)[0]] = namer.module_of(old)[0]
        return Renames(mapping)

    def stored(self, key: Envelope, scope: frozenset[str], triples: bool = False) -> ScanSummary | None:
        """The stored summary for `key` when its couplings and triples serve this comparison."""
        summary = self.summaries.get(key)
        return summary if summary is not None and summary.covers(scope, triples) else None

    def base_scans(
        self,
        base_tree: str,
        base_root: Path,
        base_config: Config,
        base_policy: str,
        head_tree: str,
        head: ScanSummary,
        same_policy: bool,
    ) -> tuple[ScanSummary | None, ScanSummary | None, str]:
        """Base code and head code, both under the base engine and the base policy; (None, None)
        when the base predates hoglint, and where the base summary came from. Raises EngineError
        when the base engine cannot run. `head_tree` is HEAD's tree when the working tree holds
        HEAD's inputs, else empty. Keys name inputs under the base policy's patterns.

        The base engine runs in this process when it is this engine, else in its own process: the
        base's own sources, or the runner of the version the base pins. A stored summary with the
        exact key and the needed couplings replaces either scan; head code has a key only when the
        working tree holds HEAD's inputs, and is then read from HEAD's tree. When the engine and
        the policy did not change, the head scan already is head code under the base policy.
        """
        engine = self.base_engines.of(base_root, base_config)
        if engine is None:
            return None, None, ""
        scope, framework = head.scope, head.envelope.framework
        base_key = Envelope(self.engine.inputs(self.repo, base_tree, base_config), engine.id, base_policy, framework)
        found = self.summaries.get(base_key)
        base = found if found is not None and found.covers(scope, triples=True) else None
        source = f"the stored summary {base_key.key()}"
        if base is None:
            if found is not None:
                lacks = "this rename's couplings" if not found.covers(scope) else "the group edges compare lists"
                source = f"the stored summary {base_key.key()} lacks {lacks}, so the full base scan ran"
            else:
                missing = "no CI summary was found" if os.environ.get("CI") else "no stored summary was found"
                source = f"{missing} for {base_key.key()}, so the full base scan ran"
            if engine.in_process:
                run = self.runner.run(base_root, tree=base_tree)
                base = summary_of(run, base_key, scope)
            else:
                base = self.base_engines.scan(engine, base_root, base_root, base_tree, scope)
            self.summaries.put(base.envelope, base)
        if same_policy and engine.in_process:
            return base, head, source
        head_inputs = self.engine.inputs(self.repo, head_tree, base_config)
        head_base_key = Envelope(head_inputs, engine.id, base_policy, framework)
        head_base = self.stored(head_base_key, scope)
        if head_base is None:
            if engine.in_process:
                run = self.runner.run(base_root, code_root=self.repo, tree=head_tree)
                head_base = summary_of(run, head_base_key, scope, with_triples=False)
            else:
                head_base = self.base_engines.scan(engine, base_root, self.repo, head_tree, scope)
            self.summaries.put(head_base.envelope, head_base)
        return base, head_base, source

    def scan(self, code_root: Path, out: Path, tree: str, scope_file: Path | None) -> int:
        """One summary of code under this repo's policy and this engine: what a newer engine asks
        of a base engine (see `BaseEngines.scan`). `tree` is the code's git tree id, if it is one;
        then the run reads the code and its side inputs from that tree, through `code_root`'s git."""
        scope = frozenset(json.loads(scope_file.read_text())) if scope_file is not None else frozenset()
        run = self.runner.run(self.repo, code_root=code_root, tree=tree)
        summary_of(run, self.engine.envelope(run, tree), scope).write(out)
        return 0

    # ── the summary cache, for CI steps ──

    def summary(self, action: str, ref: str, file: Path | None) -> int:
        """`key` prints the cache key of the commit's tree under the engine and policy that commit
        holds; `export` writes that summary to a file, from the store or from a run; `import` puts
        a summary file into the store under its own envelope. A file that does not read is a miss."""
        if action == "import":
            if file is None:
                raise EngineError("summary import needs --file")
            try:
                summary = ScanSummary.read(file)
            except ProtocolError as error:
                sys.stderr.write(f"summary not imported: {error}\n")
                return 0
            self.summaries.put(summary.envelope, summary)
            sys.stdout.write(f"{summary.envelope.key()}\n")
            return 0
        key, root, tree = self.commit_key(ref)
        if action == "export":
            if file is None:
                raise EngineError("summary export needs --file")
            summary = self.summaries.get(key) or self.summarize(key, root, tree)
            summary.write(file)
        sys.stdout.write(f"{key.key()}\n")
        return 0

    def commit_key(self, ref: str) -> tuple[Envelope, Path, str]:
        """The key of a commit's summary, a root that holds the commit's files (the repo when it
        is a clean checkout of that commit, else a checkout of it), and the commit's tree."""
        tree = self.snapshots.tree_of(ref)
        if not tree:
            raise EngineError(f"{ref} names no commit")
        root = self.repo if tree == self.snapshots.tree_of("HEAD") and self.snapshots.clean() else None
        root = root or self.snapshots.commit(ref)
        key, config = self.engine.envelope_at(root, self.repo, tree)
        engine_id = self.base_engines.id_of(root, config)
        if engine_id is None:
            raise EngineError(f"{ref} has no hoglint engine")
        return dataclasses.replace(key, engine=engine_id), root, tree

    def summarize(self, key: Envelope, root: Path, tree: str) -> ScanSummary:
        engine = self.base_engines.of(root, load_config(root, root / CONFIG_FILE))
        if engine is None:
            raise EngineError("the commit has no hoglint engine")
        if engine.in_process:
            run = self.runner.run(root, tree=tree)
            summary = summary_of(run, key)
        else:
            summary = self.base_engines.scan(engine, root, root, tree, frozenset())
        self.summaries.put(summary.envelope, summary)
        return summary


def engine_name(value: str) -> str:
    """`--engine native` is the one engine: a newer `compare` passes it to an older base's scan.
    Any other name is refused."""
    if value != ENGINE_NATIVE:
        raise argparse.ArgumentTypeError(f"there is one engine, native; the {value} engine is gone")
    return value


def add_view_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--all", action="store_true", help="print every row: each finding, advisory row and unknown")
    command.add_argument("--rule", default="", help="print the rows of one rule")


def view_of(args: argparse.Namespace, rerun: str) -> View:
    """The rows a report prints; `rerun` is the command, which the output names for more rows."""
    if getattr(args, "index", False):
        rerun += " --index"
    return View(rerun=rerun, all=args.all, rule=args.rule)


def parser() -> argparse.ArgumentParser:
    """Each subcommand sets `handler`, a function of (Commands, parsed args) that returns the exit code."""
    main = argparse.ArgumentParser(
        prog="hoglint", description="Boundary linter: facts, edges, rules and a generated ledger."
    )
    main.add_argument("--version", action=Version, nargs=0, help="print the version and exit")
    main.add_argument("--repo", type=Path, default=Path.cwd(), help="repo root that holds hoglint.toml")
    main.add_argument(
        "--engine", type=engine_name, default=ENGINE_NATIVE, help="native, the one engine; a base scan is passed it"
    )
    sub = main.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="run every rule; exit 0 clean, 1 violations, 2 blocking incomplete")
    check.add_argument("--index", action="store_true", help="check the staged snapshot (git index), config included")
    check.add_argument("--write-ledger", action="store_true", help="rewrite the ledger from the current scan")
    check.add_argument(
        "--record-growth",
        action="store_true",
        help="with --write-ledger, also add and raise lines: new debt, which needs a person",
    )
    check.add_argument(
        "--changed", action="store_true", help="advisory: only what the touched files show; always exits 0"
    )
    add_view_arguments(check)
    check.set_defaults(
        handler=lambda commands, args: commands.check(
            args.index, args.write_ledger, args.changed, args.record_growth, view_of(args, "hoglint check")
        )
    )
    page = sub.add_parser("help", help="print a page: hook, what the pre-commit hook checks and what to do")
    page.add_argument("topic", choices=("hook",))
    ledger = sub.add_parser("ledger", help="shrink: remove stale ledger lines; it never adds a line")
    ledger.add_argument("action", choices=("shrink",))
    ledger.set_defaults(handler=lambda commands, args: commands.shrink_ledger())
    explain = sub.add_parser(
        "explain",
        help="a rule's page by its name, the labels of a path or module, or why an edge (edge:<id>) was selected",
    )
    explain.add_argument("target")
    explain.set_defaults(handler=lambda commands, args: commands.explain(args.target))
    query = sub.add_parser("query", help="open findings and accepted debt, grouped")
    query.add_argument("--by", choices=("owners", "target"), required=True)
    query.set_defaults(handler=lambda commands, args: commands.query(args.by))
    compare = sub.add_parser("compare", help="PR mode against the merge base of a ref")
    compare.add_argument("--base", required=True)
    add_view_arguments(compare)
    compare.set_defaults(handler=lambda commands, args: commands.compare(args.base, view_of(args, "hoglint compare")))
    dependents = sub.add_parser(
        "map", help="print the dependents map (tach map's JSON shape) of the repo's Python files; runs no rule"
    )
    dependents.add_argument("--out", type=Path, help="write the map to this file instead of stdout")
    dependents.set_defaults(handler=lambda commands, args: commands.map(args.out))
    explain_config = sub.add_parser("explain-config", help="the expanded config, each key with its source")
    explain_config.set_defaults(handler=lambda commands, args: commands.explain_config())
    scan = sub.add_parser(
        "scan", help=f"write a scan summary (protocol {PROTOCOL}) of code under this policy and engine"
    )
    scan.add_argument("--code-root", type=Path, required=True)
    scan.add_argument("--out", type=Path, required=True)
    scan.add_argument("--tree", default="", help="the git tree id of the code, when it is one")
    scan.add_argument("--scope", type=Path, help="JSON list of modules whose couplings move admission reads")
    scan.set_defaults(
        handler=lambda commands, args: commands.scan(args.code_root.resolve(), args.out, args.tree, args.scope)
    )
    sub.add_parser("engine-id", help="print this engine's id, which names the summaries it stores")
    summary = sub.add_parser("summary", help="the run summary cache for CI: print a commit's key, export or import")
    summary.add_argument("action", choices=("key", "export", "import"))
    summary.add_argument("--ref", default="HEAD", help="the commit whose inputs the key names (key, export)")
    summary.add_argument("--file", type=Path, help="the summary file to write (export) or read (import)")
    summary.set_defaults(handler=lambda commands, args: commands.summary(args.action, args.ref, args.file))
    return main


def command(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    batch_mode()
    with tempfile.TemporaryDirectory(prefix="hoglint-") as work:
        commands = Commands(repo, Path(work))
        try:
            check_root(repo)
            with commands.stores.as_current():
                return args.handler(commands, args)
        except (ConfigError, RepoRootError) as error:
            sys.stderr.write("config error:\n" + "".join(f"  {problem}\n" for problem in error.problems))
            return 2
        finally:
            commands.snapshots.close()


def could_not_run(args: argparse.Namespace, reason: str, trace: str = "") -> int:
    """The one line of a command that could not run at all; the hook's command says that the
    commit goes on. HOGLINT_TRACEBACK=1 adds the traceback of a crash."""
    hook = args.command == "check" and getattr(args, "index", False)
    line = (HOOK_COULD_NOT_RUN if hook else COMMAND_COULD_NOT_RUN).format(reason=" ".join(reason.split()).rstrip("."))
    sys.stderr.write(line + "\n")
    if trace and os.environ.get(TRACEBACK_ENV) == "1":
        sys.stderr.write(trace)
    return COULD_NOT_RUN


def show_page(topic: str) -> int:
    """Prints a page; it needs no repo, so a reader can run it where the hook failed."""
    sys.stdout.write((PAGES / f"{topic}.md").read_text())
    return 0


def show_engine_id() -> int:
    """Prints this engine's id. It needs no repo: `compare` asks a pinned engine for it before
    that engine's scan, so it finds the summary that engine stored."""
    sys.stdout.write(LocalEngine().id + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Exit 1 means violations, so a run that could not finish exits 3, never 1: an engine error, a
    crash, and a Rust panic, which PyO3 raises as a BaseException."""
    args = parser().parse_args(argv)
    if args.command == "help":
        return show_page(args.topic)
    try:
        if args.command == "engine-id":
            return show_engine_id()
        return command(args)
    except (KeyboardInterrupt, SystemExit):
        raise
    except EngineError as error:
        return could_not_run(args, str(error))
    except BaseException as error:  # noqa: BLE001 - a crash or a panic must exit 3, never 1
        return could_not_run(args, f"{type(error).__name__}: {error}", traceback.format_exc())


def entry() -> int:
    """The `hoglint` command of the wheel, and `python -m hoglint`."""
    code = main()
    # The collection the interpreter runs at exit walks every live object and frees nothing an exiting process needs back.
    gc.freeze()
    return code
