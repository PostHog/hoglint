"""hoglint command line: check, explain, query, compare, explain-config.

Exit codes: 0 clean, 1 violations (new findings, a ledger that is not equal to the scan, code
growth, a stale exception), 2 blocking incompleteness (a failed producer, an incomplete result
the change introduced or cannot tell from an old one, a base engine that cannot run) or a config
error. An incomplete result never hides violations: both are printed.
"""

from __future__ import annotations

import os
import sys
import json
import tomllib
import argparse
import datetime
import tempfile
import posixpath
import subprocess
import dataclasses
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from hoglint.compare import Comparer, CompareReport, RawPolicy, Scans
from hoglint.config import CONFIG_FILE, Config, ConfigError, load_config
from hoglint.config.explain import ConfigExplainer
from hoglint.config.loader import PACK_PREFIX
from hoglint.config.schema import as_tuple
from hoglint.facts import Extractor
from hoglint.index import GraphIndex, index_of
from hoglint.labels import CandidateCache, Labeler
from hoglint.ledger.envelope import (
    ENGINE,
    ENGINE_PYTHON,
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
from hoglint.ledger.file import ABSENT, LedgerFile, render_key
from hoglint.ledger.growth import Renames
from hoglint.ledger.inputs import InputDigests, RunInputs, TreeFiles, TreeListing
from hoglint.ledger.obligations import BLOCKING, Attribution, Labels, attribute
from hoglint.ledger.records import FindingRecord, IncompleteRecord
from hoglint.ledger.scan import Obligation, ProtocolError, ScanSummary
from hoglint.ledger.status import WorktreeStatus
from hoglint.ledger.store import ExtractorStore, SummaryStore, cache_root
from hoglint.model import Finding, FindingStatus, Graph, IncompleteCause
from hoglint.native.source import ENGINES, batch_mode, graph_source
from hoglint.rules.base import EdgeRule, RuleContext
from hoglint.rules.engine import Engine, RunResult, primitive_of
from hoglint.rules.exceptions import ExceptionsFile
from hoglint.selectors import Selector, State
from hoglint.source import GraphSource

PACKAGE_DIR = Path(__file__).resolve().parent


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


# ── snapshots ─────────────────────────────────────────────────────


class Snapshots:
    """Captures the index as a git tree, writes a tree's policy into a scratch directory, checks
    out commits, and names their trees."""

    def __init__(self, repo: Path, work_dir: Path) -> None:
        self.repo = repo
        self.work_dir = work_dir
        self.worktrees: list[Path] = []

    def write_tree(self) -> str:
        """`git write-tree` of the index: the one read of the index a staged run makes. Empty when
        the index holds conflicts, which no tree can hold."""
        completed = subprocess.run(["git", "-C", str(self.repo), "write-tree"], capture_output=True, text=True)
        return completed.stdout.strip() if completed.returncode == 0 else ""

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
        files = TreeFiles(self.repo, tree)
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
                return sorted(TreeListing.of(self.repo, tree).blobs)
            return git(self.repo, "diff-tree", "-r", "--name-only", head, tree).splitlines()
        if ref is not None:
            return git(self.repo, "diff", "--name-only", ref).split()
        tracked = git(self.repo, "diff", "--name-only", "HEAD").split()
        untracked = git(self.repo, "ls-files", "--others", "--exclude-standard").split()
        return tracked + untracked

    def renames(self, ref: str) -> list[tuple[str, str]]:
        out = []
        for line in git(self.repo, "diff", "-M", "--name-status", ref).splitlines():
            parts = line.split("\t")
            if parts and parts[0].startswith("R") and len(parts) == 3:
                out.append((parts[1], parts[2]))
        return out

    def tree_of(self, ref: str) -> str:
        """The git tree id of a commit; empty when there is none."""
        try:
            return git(self.repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{tree}}").strip()
        except subprocess.CalledProcessError:
            return ""

    def clean(self) -> bool:
        return not git(self.repo, "status", "--porcelain", "-z", "--untracked-files=all").strip("\0")

    def head_tree_in_worktree(self, config: Config) -> str:
        """HEAD's tree when the working tree holds HEAD's run inputs under `config` (sources,
        declared side inputs, the ledger), so a run of the working tree is a run of HEAD's tree;
        empty otherwise. Any other file may differ."""
        tree = self.tree_of("HEAD")
        if not tree:
            return ""
        return "" if WorktreeStatus.of(self.repo).drift(RunInputs(config).patterns().matches) else tree


# ── one run ───────────────────────────────────────────────────────


@dataclass(frozen=False, slots=True)
class Run:
    """`files_root` is where the run's rules and providers looked for files: the code root, or
    for a run of a git tree the root that stands for the tree (see `TreeFiles.run_root`)."""

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

    def evaluate(self, config: Config, code_root: Path, policy_root: Path) -> tuple[Graph, RunResult]:
        """One policy over one code root, with no ledger and no exceptions."""
        judged = PolicyFiles(config).anchored(policy_root, code_root)
        graph = self.source.build(code_root, judged)
        engine = Engine(judged, graph, code_root, self.today, self.index_of(graph), self.candidates)
        return graph, engine.evaluate()

    def tree_root(self, repo: Path, tree: str) -> Path:
        """The root that stands for a git tree, where rules and providers look for files."""
        return TreeFiles(repo, tree, self.source.work_dir).run_root()

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
        reads the code and every side input from that git tree by blob id, with `files_root` its
        root when the caller made one; `ledger` replaces the policy root's ledger. With
        `rewrite_ledger` the run is judged against the ledger it would write, not the file."""
        code_root = code_root or policy_root
        if tree and files_root is None:
            files_root = self.tree_root(code_root, tree)
        files_root = files_root or code_root
        config = load_config(policy_root, policy_root / CONFIG_FILE)
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
    shown: Callable[[Finding], bool] | None = None,
) -> ScanSummary:
    roots = (run.policy_root or run.root, run.root, run.files_root or run.root)
    return ScanSummary.from_run(
        run.result, run.graph, envelope, scope, with_triples, roots, run.exceptions, run.ledger, shown
    )


# ── rendering ─────────────────────────────────────────────────────


def where(finding: Finding) -> str:
    if finding.site is None:
        return ""
    return f"{finding.site.path}:{finding.site.line}"


def finding_line(finding: Finding) -> str:
    owners = f"  [{', '.join(finding.owners)}]" if finding.owners else ""
    return f"{finding.rule}  {' '.join(finding.identity)}\n    {finding.message}  {where(finding)}{owners}"


# The causes `RunResult.blocking_incomplete` reads. Keep the two equal, or check and compare exit differently.
BLOCKING_CAUSES = frozenset({IncompleteCause.INTRODUCED, IncompleteCause.EXTRACTOR})


def touches(touched: set[str], paths: Iterable[str], subjects: Iterable[str]) -> bool:
    return not touched.isdisjoint(paths) or not touched.isdisjoint(subjects)


@dataclass(frozen=True, slots=True)
class TouchedDebt:
    """The findings a `--changed` report prints besides new ones: accepted debt in touched files."""

    touched: set[str]

    def shown(self, finding: Finding) -> bool:
        if finding.status is FindingStatus.NEW:
            return True
        paths = (edge.site.path for edge in finding.witnesses)
        return finding.status is FindingStatus.ACCEPTED and touches(self.touched, paths, finding.subjects)


@dataclass(frozen=True, slots=True)
class Attributed:
    """Incomplete items labelled against a baseline summary; `against` names the baseline."""

    labels: Labels
    against: str

    @property
    def blocking(self) -> bool:
        return any(label in BLOCKING for items in self.labels.values() for label in items)

    def causes(self, items: list[IncompleteRecord]) -> list[str]:
        """Each item's owner: a producer failure stays an extractor item, the rest take their label."""
        left = {key: list(labels) for key, labels in self.labels.items()}
        out = []
        for item in items:
            labels = left.get(((item.rule, item.key, item.reason), item.mark), [])
            if item.cause is not IncompleteCause.INHERITED or not labels:
                out.append(item.cause.value)
                continue
            out.append(labels.pop(0).value)
        return out


class CheckReport:
    """What `check` prints and its exit code, read from a run summary: the one a live run makes,
    or the stored one that stands in for the run. Owners and the day are read when it renders."""

    def __init__(
        self,
        summary: ScanSummary,
        owners: ReadTimeOwners,
        today: datetime.date,
        touched: set[str] | None = None,
        attributed: Attributed | None = None,
    ) -> None:
        self.summary = summary
        self.owners = owners
        self.today = today
        self.touched = touched
        self.attributed = attributed

    def keep(self, finding: FindingRecord) -> bool:
        if self.touched is None:
            return True
        return touches(self.touched, (witness.path for witness in finding.witnesses), finding.subjects)

    def new(self) -> list[FindingRecord]:
        return [f for f in self.summary.findings if f.status is FindingStatus.NEW and self.keep(f)]

    def violations(self) -> list[FindingRecord]:
        return [f for f in self.new() if f.rule not in self.summary.advisory]

    def advisory(self) -> list[FindingRecord]:
        return [f for f in self.new() if f.rule in self.summary.advisory]

    def finding_line(self, finding: FindingRecord) -> str:
        shown = finding.shown
        if shown is None:
            raise ValueError(f"the summary keeps no message for {finding.rule} {' '.join(finding.identity)}")
        site = f"{shown.path}:{shown.line}" if shown.path else ""
        owners = self.owners.of(finding)
        listed = f"  [{', '.join(owners)}]" if owners else ""
        return f"{finding.rule}  {' '.join(finding.identity)}\n    {shown.message}  {site}{listed}"

    def incomplete_lines(self) -> list[str]:
        items = self.summary.incomplete
        if self.attributed is None:
            causes = [item.cause.value for item in items]
        else:
            causes = self.attributed.causes(items)
        out = [f"incomplete ({len(items)})"]
        out.extend(f"  {item.rule} [{cause}]  {item.reason}" for item, cause in zip(items, causes, strict=True))
        if self.attributed is not None:
            counts = Counter(causes)
            tally = ", ".join(
                f"{counts[label.value]} {label.value}"
                for label in Attribution
                if counts[label.value] or label in BLOCKING
            )
            out.append(f"incomplete attributed against {self.attributed.against}: {tally}")
        return out

    def render(self) -> str:
        summary = self.summary
        out: list[str] = []
        violations = self.violations()
        out.append(f"violations ({len(violations)})")
        out.extend(f"  {self.finding_line(f)}" for f in violations)
        advisory = self.advisory()
        if advisory:
            out.append(f"advisory ({len(advisory)})")
            out.extend(f"  {self.finding_line(f)}" for f in advisory)
        if self.touched is not None:
            accepted = [f for f in summary.findings if f.status is FindingStatus.ACCEPTED and self.keep(f)]
            out.append(f"existing debt in touched files (advisory) ({len(accepted)})")
            out.extend(f"  {self.finding_line(f)}" for f in accepted)
        out.extend(self.incomplete_lines())
        if summary.unread:
            out.append(f"unreadable sources, so their facts are missing ({len(summary.unread)})")
            out.extend(f"  {path}" for path in summary.unread)
        equality = summary.equality
        if equality is not None and not equality.equal:
            out.append("ledger is not equal to the scan")
            out.extend(f"  {problem}" for problem in equality.header)
            out.extend(f"  new    {render_key(d.key)}  {d.recorded} -> {d.scanned}" for d in equality.new)
            out.extend(f"  stale  {render_key(d.key)}  {d.recorded} -> {d.scanned}" for d in equality.stale)
        for label in summary.exceptions.stale:
            out.append(f"stale exception: {label} matches nothing")
        for review_by, label in summary.exceptions.expired(self.today):
            out.append(f"exception past review_by {review_by}: {label} (reported, not blocking)")
        out.extend(f"exceptions: {problem}" for problem in summary.exceptions.problems)
        for name, passing in summary.levels.items():
            out.append(f"level {name}: {len(passing)} pass, {len(summary.level_failures.get(name, {}))} fail")
        accepted_count = sum(1 for f in summary.findings if f.status is FindingStatus.ACCEPTED)
        out.append(f"{accepted_count} findings accepted by the ledger")
        return "\n".join(out) + "\n"

    def exit_code(self) -> int:
        """`check` without `--changed`: 2 for a blocking incomplete result, 1 when the run failed."""
        summary = self.summary
        if self.attributed is not None and self.attributed.blocking:
            return 2
        if any(item.cause in BLOCKING_CAUSES for item in summary.incomplete):
            return 2
        unequal = summary.equality is not None and not summary.equality.equal
        failed = self.violations() or unequal or summary.exceptions.stale or summary.exceptions.problems
        return 1 if failed else 0


# ── engines and baselines ─────────────────────────────────────────


class LocalEngine:
    """The engine this process runs: its id, its extractor, and the envelope of one of its runs."""

    def __init__(self, name: str = ENGINE_PYTHON, digests: InputDigests | None = None) -> None:
        self.name = name
        self.frameworks: dict[tuple[str, ...], str] = {}
        self.digests = digests or InputDigests(cache_root())

    @cached_property
    def binary(self) -> Path:
        try:
            return Extractor.find_binary()
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
            return EngineId.of(crate, self.name)
        return EngineId.installed(self.name, BinaryIds.of(self.binary, ENGINE))

    def framework(self, config: Config) -> str:
        packages = Framework.packages_of(config)
        if packages not in self.frameworks:
            self.frameworks[packages] = Framework.digest(packages)
        return self.frameworks[packages]

    def inputs(self, repo: Path, tree: str, config: Config) -> str:
        """The inputs digest of a git tree under `config`; empty when there is no tree."""
        return self.digests.of(repo, tree, RunInputs(config).patterns())

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


@dataclass(frozen=True, slots=True)
class BaseEngine:
    """The engine of the base commit. `binary` is None when it is this process's own engine."""

    id: str
    binary: Path | None = None
    python_path: Path | None = None


class BaseEngines:
    """Pins the engine a base commit's scan runs: the base's own Python sources with an extractor
    built from the base's own extractor sources. An engine that cannot run as pinned is an
    EngineError, which compare reports as incomplete (exit 2)."""

    def __init__(self, repo: Path, work_dir: Path, local: LocalEngine, extractors: ExtractorStore) -> None:
        self.repo = repo
        self.work_dir = work_dir
        self.local = local
        self.extractors = extractors

    def package(self) -> Path | None:
        """The engine package relative to the repo; None when the engine is installed outside it."""
        repo = self.repo.resolve()
        return PACKAGE_DIR.relative_to(repo) if PACKAGE_DIR.is_relative_to(repo) else None

    def id_of(self, base_root: Path) -> str | None:
        """The id of the engine a checkout holds; None when it predates the in-repo engine."""
        package = self.package()
        if package is None:
            return self.local.id
        if not (base_root / package).is_dir():
            return None
        return EngineId.of(base_root / package.parent, self.local.name)

    def of(self, base_root: Path) -> BaseEngine | None:
        """None when the base predates the in-repo engine."""
        engine_id = self.id_of(base_root)
        package = self.package()
        if engine_id is None or package is None:
            return None if engine_id is None else BaseEngine(engine_id)
        crate = package.parent
        self.check_local(crate)
        if engine_id == self.local.id:
            return BaseEngine(engine_id)
        return BaseEngine(engine_id, self.binary(native_id_of(base_root / crate), crate), base_root / package.parent)

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
        if engine.binary is None or engine.python_path is None:
            raise EngineError("the base engine runs in this process; it has no scan command to call")
        work = Path(tempfile.mkdtemp(prefix="base-scan-", dir=self.work_dir))
        out, scope_file = work / "scan.json", work / "scope.json"
        scope_file.write_text(json.dumps(sorted(scope)))
        command = [sys.executable, "-m", "hoglint", "--repo", str(base_root), "scan"]
        command += ["--code-root", str(code_root), "--out", str(out), "--tree", tree, "--scope", str(scope_file)]
        env = {**os.environ, "PYTHONPATH": str(engine.python_path), "HOGLINT_FACTS_BIN": str(engine.binary)}
        completed = subprocess.run(command, env=env, capture_output=True, text=True)
        if completed.returncode != 0 or not out.is_file():
            raise EngineError(f"the base engine failed (exit {completed.returncode}):\n{completed.stderr[-2000:]}")
        try:
            summary = ScanSummary.read(out)
        except ProtocolError as error:
            raise EngineError(f"the base engine wrote a summary this engine cannot read: {error}") from error
        if summary.envelope.engine != engine.id:
            raise EngineError(f"the base engine ran as {summary.envelope.engine}, not as the pinned {engine.id}")
        return summary


class TreeSummaries:
    """Stores the summaries of runs whose key names the inputs of a git tree. A run of the tree
    itself reads only the tree. A run of the working tree has the tree's key only when the working
    tree held the tree's inputs (see `Snapshots.head_tree_in_worktree`), and is stored only when
    no source changed while the run read it."""

    def __init__(self, snapshots: Snapshots, store: SummaryStore) -> None:
        self.snapshots = snapshots
        self.store = store

    def put(self, summary: ScanSummary, from_worktree: bool = False) -> None:
        inputs = summary.envelope.inputs
        if not inputs:
            return
        drift = sorted(WorktreeStatus.of(self.snapshots.repo).drifted) if from_worktree else []
        if drift:
            more = f" and {len(drift) - 1} more" if len(drift) > 1 else ""
            sys.stderr.write(
                f"summary of inputs {inputs[:12]} not stored: {drift[0]}{more} changed while this run read it\n"
            )
            return
        self.store.put(summary.envelope, summary)


@dataclass(frozen=True, slots=True)
class Baseline:
    """What one check run attributes its incomplete results against.

    `envelope` is the run's own key: its inputs are the staged tree's for a staged run, so the
    run's summary is stored for the next commit, HEAD's when the working tree holds HEAD's inputs,
    and empty otherwise. `reference` is None when the run itself is the baseline (`is_self`) or
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

    def __init__(self, snapshots: Snapshots, engine: LocalEngine, store: SummaryStore, trees: TreeSummaries) -> None:
        self.snapshots = snapshots
        self.repo = snapshots.repo
        self.work_dir = snapshots.work_dir
        self.engine = engine
        self.store = store
        self.trees = trees

    def prepare(self, policy_root: Path, tree: str) -> Baseline:
        """`tree` is a staged run's tree, written from the index; empty for a working tree run."""
        head_tree = self.snapshots.tree_of("HEAD")
        wanted, config = self.engine.envelope_at(policy_root, self.repo, head_tree)
        staged = bool(tree)
        if not staged:
            WorktreeStatus.start(self.repo)  # the key of a working tree run reads it
            tree = self.snapshots.head_tree_in_worktree(config)
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
        summary = self.evaluate(policy_root, config.ledger.file, head_tree, wanted)
        self.trees.put(summary)
        computed = f"HEAD (tree {short}, computed and stored)"
        return Baseline(own, computed, config, summary.obligations(), staged=staged)

    def evaluate(self, policy_root: Path, ledger_path: str, head_tree: str, envelope: Envelope) -> ScanSummary:
        """HEAD's tree under the run's policy, with HEAD's own ledger, in a runner of its own so its
        graph is freed before the run."""
        try:
            ledger = LedgerFile.parse(git(self.repo, "show", f"{head_tree}:{ledger_path}"))
        except subprocess.CalledProcessError:
            ledger = LedgerFile(source=ABSENT)
        runner = Runner(graph_source(self.engine.name, Path(tempfile.mkdtemp(prefix="baseline-", dir=self.work_dir))))
        run = runner.run(policy_root, self.repo, tree=head_tree, ledger=ledger)
        return summary_of(run, envelope, with_triples=False)

    def attribute(self, baseline: Baseline, summary: ScanSummary) -> Attributed:
        head = summary.obligations()
        reference = head if baseline.is_self else baseline.reference
        return Attributed(attribute(head, reference), baseline.against)


class RunMemo:
    """`check` of a git tree that a run already evaluated, as a lookup: the stored summary of the
    tree stands in for the run when no other input of the output differs.

    The key is the run's envelope (inputs, engine, policy, framework) and the source of its
    ledger. A staged run reads every input from its tree. A run of the working tree has HEAD's key
    only when the working tree holds HEAD's inputs (sources, declared side inputs, the ledger);
    the policy digest covers the policy files it reads from the working tree, and the ledger
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


class ReadTimeOwners:
    """A finding's owners, looked up when it is rendered, from the owners files under `root`.
    Summaries never store owners (D12), so an owners file edit needs no new summary."""

    def __init__(self, config: Config, modules: dict[str, str], root: Path) -> None:
        self.field = config.roles.owner
        self.labeler = Labeler(config, Graph(modules=dict(modules)), root)

    def of(self, finding: FindingRecord) -> tuple[str, ...]:
        if not self.field or not finding.owner:
            return ()
        value, _ = self.labeler.field_value(finding.owner, None, self.field)
        if value.state is State.KNOWN and isinstance(value.data, frozenset):
            return tuple(sorted(value.data))
        return ()


# ── commands ──────────────────────────────────────────────────────


class Commands:
    """`today` replaces the date in output that depends on it. With `memo` off, every check runs live."""

    def __init__(
        self,
        repo: Path,
        work_dir: Path,
        engine: str = ENGINE_PYTHON,
        today: datetime.date | None = None,
        memo: bool = True,
    ) -> None:
        self.repo = repo
        self.work_dir = work_dir
        self.source: GraphSource = graph_source(engine, work_dir)
        self.runner = Runner(self.source, today)
        self.snapshots = Snapshots(repo, work_dir)
        self.engine = LocalEngine(engine)
        self.summaries = SummaryStore()
        self.trees = TreeSummaries(self.snapshots, self.summaries)
        self.baselines = Baselines(self.snapshots, self.engine, self.summaries, self.trees)
        self.base_engines = BaseEngines(repo, work_dir, self.engine, ExtractorStore())
        self.memo = RunMemo(self.snapshots, self.summaries) if memo else None

    def check(self, index: bool, write_ledger: bool, changed: bool) -> int:
        """With `index`, the index is captured once as a git tree, and the run reads everything
        from that tree: code, policy, side inputs and the owners it prints."""
        tree = ""
        policy_root = files_root = self.repo
        if index:
            tree = self.snapshots.write_tree()
            if not tree:
                sys.stderr.write("the index holds merge conflicts, so there is no staged tree to check\n")
                return 2
            policy_root = self.snapshots.policy_of(tree)
            files_root = self.runner.tree_root(self.repo, tree)
        baseline = None if changed else self.baselines.prepare(policy_root, tree)
        if baseline is not None and self.memo is not None and not write_ledger:
            stored = self.memo.lookup(baseline, policy_root)
            if stored is not None:
                attributed = self.baselines.attribute(baseline, stored)
                return self.report(stored, baseline.config, policy_root, files_root, None, attributed)
        run = self.runner.run(
            policy_root, self.repo, staged=index, rewrite_ledger=write_ledger, tree=tree, files_root=files_root
        )
        if write_ledger:
            if index:
                sys.stderr.write("--write-ledger writes the working tree file, not the index\n")
            run.ledger.write(self.repo / run.config.ledger.file)
            sys.stdout.write(f"wrote {len(run.ledger.lines)} lines to {run.config.ledger.file}\n")
        self.write_outputs(run)
        if baseline is None:  # --changed: advisory, so it always exits 0
            touched = set(self.snapshots.changed_paths(None, tree))
            touched |= {m for m, p in run.graph.modules.items() if p in touched}
            summary = summary_of(run, Envelope(), with_triples=False, shown=TouchedDebt(touched).shown)
            self.report(summary, run.config, policy_root, files_root, touched, None)
            return 0
        summary = summary_of(run, baseline.envelope, with_triples=False)
        if not write_ledger:
            self.trees.put(summary, from_worktree=not index)
        attributed = self.baselines.attribute(baseline, summary)
        return self.report(summary, run.config, policy_root, files_root, None, attributed)

    def report(
        self,
        summary: ScanSummary,
        config: Config,
        policy_root: Path,
        files_root: Path,
        touched: set[str] | None,
        attributed: Attributed | None,
    ) -> int:
        """Prints the check report of a summary and returns its exit code. Owners are read from
        `files_root`, the run's own files."""
        owners = ReadTimeOwners(PolicyFiles(config).anchored(policy_root, self.repo), summary.modules, files_root)
        report = CheckReport(summary, owners, self.runner.today or datetime.date.today(), touched, attributed)
        sys.stdout.write(report.render())
        return report.exit_code()

    def write_outputs(self, run: Run) -> None:
        for output in run.config.outputs.values():
            path = run.root / output.path
            path.parent.mkdir(parents=True, exist_ok=True)
            if output.format == "tach-map":
                from hoglint.outputs.tach_map import TachMap  # noqa: PLC0415 — only this output needs it

                roots = as_tuple(run.config.discovery.get("source_roots", ["."]))
                TachMap(run.graph, run.graph.modules.values(), roots).write(path)
                continue
            edges = [
                {"kind": e.kind, "src": e.site.path, "surface": e.surface, "targets": [str(t) for t in e.targets]}
                for e in run.graph.edges
            ]
            path.write_text(json.dumps(edges))

    def explain(self, target: str) -> int:
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
        ctx = RuleContext(run.config, run.graph, run.result.labeler, run.root)
        edge = next((e for e in run.graph.edges if e.run_id(ctx.ordinal(e)) == run_id), None)
        if edge is None:
            sys.stderr.write(f"no edge with id {run_id}\n")
            return 2
        sys.stdout.write(
            f"edge     {edge.kind} {edge.site.path}:{edge.site.line} {edge.surface} -> {[str(t) for t in edge.targets]}\n"
        )
        sys.stdout.write(
            f"         resolution={edge.resolution.value} contexts={sorted(c.value for c in edge.contexts)}\n"
        )
        for spec in run.config.rules.values():
            primitive = primitive_of(spec)
            if not issubclass(primitive, EdgeRule):
                continue
            rule = primitive(ctx, spec)
            if not rule.sees(edge):
                continue
            for env in rule.envs_of(edge):
                parts = []
                for name in ("from_", "select", "deny", "allow", "require", "unless"):
                    selector: Selector | None = getattr(spec, name)
                    if selector is not None:
                        parts.append(f"{name.rstrip('_')}: {selector.source} -> {selector.evaluate(env.resolve).value}")
                sys.stdout.write(f"rule     {spec.id} (endpoint {spec.endpoint}, {env.dst.symbol or env.dst.module})\n")
                sys.stdout.write("".join(f"           {part}\n" for part in parts))
        for finding in run.result.findings():
            if edge in finding.witnesses:
                sys.stdout.write(f"finding  [{finding.status.value}] {finding.rule} {' '.join(finding.identity)}\n")
        return 0

    def query(self, by: str) -> int:
        summary, config = self.current_summary()
        owners = ReadTimeOwners(config, summary.modules, self.repo) if by == "owners" else None
        counts: dict[str, Counter[str]] = {}
        for finding in summary.findings:
            if finding.status is FindingStatus.ALLOWED:
                continue
            if owners is not None:
                keys = owners.of(finding) or ("(no owner)",)
            else:
                keys = (self.target_of(finding),)
            for key in keys:
                counts.setdefault(key, Counter())[f"{finding.status.value}:{finding.rule}"] += 1
        for key in sorted(counts, key=lambda k: (-sum(counts[k].values()), k)):
            total = sum(counts[key].values())
            detail = ", ".join(f"{name} {count}" for name, count in counts[key].most_common())
            sys.stdout.write(f"{key}  {total}  ({detail})\n")
        return 0

    def current_summary(self) -> tuple[ScanSummary, Config]:
        """The summary of the working tree: the stored summary of HEAD's tree when the working tree
        holds HEAD's inputs, else a run, which is stored when it has a tree."""
        key, config = self.engine.envelope_at(self.repo, self.repo, "")
        tree = self.snapshots.head_tree_in_worktree(config)
        key = dataclasses.replace(key, inputs=self.engine.inputs(self.repo, tree, config))
        stored = self.summaries.get(key, frozenset({"envelope", "findings"}))
        if stored is not None:
            return stored, config
        run = self.runner.run(self.repo)
        summary = summary_of(run, self.engine.envelope(run, tree), with_triples=False)
        self.trees.put(summary, from_worktree=True)
        return summary, run.config

    @staticmethod
    def target_of(finding: FindingRecord) -> str:
        for witness in finding.witnesses:
            if witness.targets:
                return witness.targets[0]
        return finding.identity[0] if finding.identity else "?"

    def explain_config(self) -> int:
        config = load_config(self.repo, self.repo / CONFIG_FILE)
        sys.stdout.write(ConfigExplainer(config).render())
        return 0

    # ── compare ──

    def compare(self, ref: str) -> int:
        WorktreeStatus.start(self.repo)  # the key of the head summaries reads it
        base_sha = self.snapshots.merge_base(ref)
        base_tree = self.snapshots.tree_of(base_sha)
        base_root = self.snapshots.commit(base_sha)
        head = self.runner.run(self.repo)
        renames = self.module_renames(base_sha, head.config)
        head_tree = self.snapshots.head_tree_in_worktree(head.config)
        head_key = self.engine.envelope(head, head_tree)
        scans = Scans(head=summary_of(head, head_key, renames.modules()))
        self.trees.put(scans.head, from_worktree=True)
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
        owners = ReadTimeOwners(head.config, scans.head.modules, self.repo)
        today = self.runner.today or datetime.date.today()
        sys.stdout.write(
            CheckReport(scans.head, owners, today, attributed=Attributed(report.labels, "the base")).render()
        )
        sys.stdout.write(report.render())
        return report.exit_code(head.result.failed(), bool(head.result.blocking_incomplete()))

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

        The base engine runs in this process when it is this engine, else in its own process. A
        stored summary with the exact key and the needed couplings replaces either scan; head code
        has a key only when the working tree holds HEAD's inputs, and is then read from HEAD's
        tree. When the engine and the policy did not change, the head scan already is head code
        under the base policy.
        """
        engine = self.base_engines.of(base_root)
        if engine is None:
            return None, None, ""
        scope, framework = head.scope, head.envelope.framework
        base_key = Envelope(self.engine.inputs(self.repo, base_tree, base_config), engine.id, base_policy, framework)
        found = self.summaries.get(base_key)
        base = found if found is not None and found.covers(scope, triples=True) else None
        source = f"the stored summary {base_key.key()}"
        if base is None:
            if found is not None:
                source = f"the stored summary {base_key.key()} lacks this rename's couplings, so the full base scan ran"
            else:
                missing = "no CI summary was found" if os.environ.get("CI") else "no stored summary was found"
                source = f"{missing} for {base_key.key()}, so the full base scan ran"
            if engine.binary is None:
                run = self.runner.run(base_root)
                base = summary_of(run, base_key, scope)
            else:
                base = self.base_engines.scan(engine, base_root, base_root, base_tree, scope)
            self.summaries.put(base.envelope, base)
        if same_policy and engine.binary is None:
            return base, head, source
        head_inputs = self.engine.inputs(self.repo, head_tree, base_config)
        head_base_key = Envelope(head_inputs, engine.id, base_policy, framework)
        head_base = self.stored(head_base_key, scope)
        if head_base is None:
            if engine.binary is None:
                run = self.runner.run(base_root, code_root=self.repo, tree=head_tree)
                head_base = summary_of(run, head_base_key, scope, with_triples=False)
            else:
                head_base = self.base_engines.scan(engine, base_root, self.repo, head_tree, scope)
            self.trees.put(head_base)
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
        engine_id = self.base_engines.id_of(root)
        if engine_id is None:
            raise EngineError(f"{ref} has no hoglint engine")
        key, _ = self.engine.envelope_at(root, self.repo, tree)
        return dataclasses.replace(key, engine=engine_id), root, tree

    def summarize(self, key: Envelope, root: Path, tree: str) -> ScanSummary:
        engine = self.base_engines.of(root)
        if engine is None:
            raise EngineError("the commit has no hoglint engine")
        if engine.binary is None:
            run = self.runner.run(root, tree=tree)
            summary = summary_of(run, key)
        else:
            summary = self.base_engines.scan(engine, root, root, tree, frozenset())
        self.summaries.put(summary.envelope, summary)
        return summary


def parser() -> argparse.ArgumentParser:
    """Each subcommand sets `handler`, a function of (Commands, parsed args) that returns the exit code."""
    main = argparse.ArgumentParser(
        prog="hoglint", description="Boundary linter: facts, edges, rules and a generated ledger."
    )
    main.add_argument("--repo", type=Path, default=Path.cwd(), help="repo root that holds hoglint.toml")
    main.add_argument("--engine", choices=ENGINES, default=ENGINE_PYTHON, help="python, or the Rust link (native)")
    sub = main.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="run every rule; exit 0 clean, 1 violations, 2 blocking incomplete")
    check.add_argument("--index", action="store_true", help="check the staged snapshot (git index), config included")
    check.add_argument("--write-ledger", action="store_true", help="rewrite the ledger from the current scan")
    check.add_argument(
        "--changed", action="store_true", help="advisory: only what the touched files show; always exits 0"
    )
    check.set_defaults(handler=lambda commands, args: commands.check(args.index, args.write_ledger, args.changed))
    explain = sub.add_parser("explain", help="labels of a path or module, or why an edge (edge:<id>) was selected")
    explain.add_argument("target")
    explain.set_defaults(handler=lambda commands, args: commands.explain(args.target))
    query = sub.add_parser("query", help="open findings and accepted debt, grouped")
    query.add_argument("--by", choices=("owners", "target"), required=True)
    query.set_defaults(handler=lambda commands, args: commands.query(args.by))
    compare = sub.add_parser("compare", help="PR mode against the merge base of a ref")
    compare.add_argument("--base", required=True)
    compare.set_defaults(handler=lambda commands, args: commands.compare(args.base))
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
    summary = sub.add_parser("summary", help="the run summary cache for CI: print a commit's key, export or import")
    summary.add_argument("action", choices=("key", "export", "import"))
    summary.add_argument("--ref", default="HEAD", help="the commit whose inputs the key names (key, export)")
    summary.add_argument("--file", type=Path, help="the summary file to write (export) or read (import)")
    summary.set_defaults(handler=lambda commands, args: commands.summary(args.action, args.ref, args.file))
    return main


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    repo = args.repo.resolve()
    batch_mode(args.engine)
    with tempfile.TemporaryDirectory(prefix="hoglint-") as work:
        commands = Commands(repo, Path(work), args.engine)
        try:
            return args.handler(commands, args)
        except ConfigError as error:
            sys.stderr.write("config error:\n" + "".join(f"  {problem}\n" for problem in error.problems))
            return 2
        except EngineError as error:
            sys.stderr.write(f"engine error: {error}\n")
            return 2
        finally:
            commands.snapshots.close()
