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
import argparse
import tempfile
import subprocess
import dataclasses
from collections import Counter
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

from hoglint.compare import Comparer, CompareReport, RawPolicy, Scans
from hoglint.config import CONFIG_FILE, Config, ConfigError, load_config
from hoglint.config.explain import ConfigExplainer
from hoglint.config.schema import as_tuple
from hoglint.facts import Extractor
from hoglint.index import GraphIndex
from hoglint.labels import CandidateCache
from hoglint.ledger.envelope import (
    ENGINE_PYTHON,
    BuildId,
    EngineError,
    EngineId,
    Envelope,
    Framework,
    PolicyFiles,
    policy_digest,
)
from hoglint.ledger.file import LedgerFile, render_key
from hoglint.ledger.growth import Renames
from hoglint.ledger.obligations import BLOCKING, Attribution, Labels, attribute
from hoglint.ledger.records import FindingRecord
from hoglint.ledger.scan import Obligation, ProtocolError, ScanSummary, obligation_of, obligations_of
from hoglint.ledger.store import ExtractorStore, SummaryStore
from hoglint.model import Finding, FindingStatus, Graph, IncompleteCause
from hoglint.rules.base import EdgeRule, IncompleteItem, RuleContext
from hoglint.rules.engine import Engine, RunResult, primitive_of
from hoglint.rules.exceptions import ExceptionsFile
from hoglint.selectors import Selector
from hoglint.source import GraphSource

PACKAGE_DIR = Path(__file__).resolve().parent


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


# ── snapshots ─────────────────────────────────────────────────────


class Snapshots:
    """Materializes git snapshots (the index, a commit) into scratch directories, and names their trees."""

    def __init__(self, repo: Path, work_dir: Path) -> None:
        self.repo = repo
        self.work_dir = work_dir
        self.worktrees: list[Path] = []

    def index(self) -> Path:
        target = Path(tempfile.mkdtemp(prefix="index-", dir=self.work_dir))
        git(self.repo, "checkout-index", "--all", f"--prefix={target}/")
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

    def changed_paths(self, ref: str | None, staged: bool) -> list[str]:
        if staged:
            return git(self.repo, "diff", "--cached", "--name-only").split()
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

    def index_tree(self) -> str:
        """The index as a tree id; empty while the index holds conflicts."""
        try:
            return git(self.repo, "write-tree").strip()
        except subprocess.CalledProcessError:
            return ""

    def clean(self) -> bool:
        return not git(self.repo, "status", "--porcelain", "-z", "--untracked-files=all").strip("\0")

    def head_tree_in_worktree(self, ledger_path: str) -> str:
        """HEAD's tree when the working tree holds HEAD's sources and ledger, the files a run reads
        as code; empty otherwise."""
        from hoglint.core.discovery import SOURCE_PATTERNS  # noqa: PLC0415 — the extractor stack loads only when needed

        tree = self.tree_of("HEAD")
        if not tree:
            return ""
        status = git(
            self.repo, "status", "--porcelain", "-z", "--untracked-files=all", "--", *SOURCE_PATTERNS, ledger_path
        )
        return "" if status.strip("\0") else tree


# ── one run ───────────────────────────────────────────────────────


@dataclass(frozen=False, slots=True)
class Run:
    root: Path
    config: Config
    graph: Graph
    result: RunResult
    ledger: LedgerFile
    exceptions: ExceptionsFile
    policy_root: Path | None = None


class Runner:
    """Runs policy over code. One GraphIndex per graph serves every run that reads that graph, and
    one CandidateCache every run."""

    def __init__(self, source: GraphSource, today: Any = None) -> None:
        self.source = source
        self.today = today
        self.indexes: dict[int, tuple[Graph, GraphIndex]] = {}
        self.candidates = CandidateCache()

    def index_of(self, graph: Graph) -> GraphIndex:
        held = self.indexes.get(id(graph))
        if held is None or held[0] is not graph:
            held = self.indexes[id(graph)] = (graph, GraphIndex(graph))
        return held[1]

    def evaluate(self, config: Config, code_root: Path, policy_root: Path) -> tuple[Graph, RunResult]:
        """One policy over one code root, with no ledger and no exceptions."""
        judged = PolicyFiles(config).anchored(policy_root, code_root)
        graph = self.source.build(code_root, judged)
        engine = Engine(judged, graph, code_root, self.today, self.index_of(graph), self.candidates)
        return graph, engine.evaluate()

    def run(
        self,
        policy_root: Path,
        code_root: Path | None = None,
        staged: bool = False,
        rewrite_ledger: bool = False,
        tree: str = "",
        ledger: LedgerFile | None = None,
    ) -> Run:
        """Policy (config, ledger, exceptions) from one root, code from another, or both from one.

        The policy's data files are read from the policy root, whatever the code root. `tree`
        reads the code from that git tree by blob id; `ledger` replaces the policy root's ledger.
        With `rewrite_ledger` the run is judged against the ledger it would write, not the file."""
        code_root = code_root or policy_root
        config = load_config(policy_root, policy_root / CONFIG_FILE)
        judged = PolicyFiles(config).anchored(policy_root, code_root)
        graph = self.source.build(code_root, judged, staged, tree)
        if ledger is None:
            ledger = LedgerFile.read(policy_root / config.ledger.file)
        exceptions = ExceptionsFile.read(policy_root / config.ledger.exceptions, config)
        engine = Engine(judged, graph, code_root, self.today, self.index_of(graph), self.candidates)
        result = engine.evaluate(ledger, exceptions, rewrite_ledger)
        return Run(code_root, config, graph, result, result.ledger or ledger, exceptions, policy_root)


# ── rendering ─────────────────────────────────────────────────────


def where(finding: Finding) -> str:
    if finding.site is None:
        return ""
    return f"{finding.site.path}:{finding.site.line}"


def finding_line(finding: Finding) -> str:
    owners = f"  [{', '.join(finding.owners)}]" if finding.owners else ""
    return f"{finding.rule}  {' '.join(finding.identity)}\n    {finding.message}  {where(finding)}{owners}"


@dataclass(frozen=True, slots=True)
class Attributed:
    """Incomplete items labelled against a baseline summary; `against` names the baseline."""

    labels: Labels
    against: str

    @property
    def blocking(self) -> bool:
        return any(label in BLOCKING for items in self.labels.values() for label in items)

    def causes(self, items: list[IncompleteItem]) -> list[str]:
        """Each item's owner: a producer failure stays an extractor item, the rest take their label."""
        left = {key: list(labels) for key, labels in self.labels.items()}
        out = []
        for item in items:
            labels = left.get(obligation_of(item), [])
            if item.cause is not IncompleteCause.INHERITED or not labels:
                out.append(item.cause.value)
                continue
            out.append(labels.pop(0).value)
        return out


class CheckReport:
    def __init__(
        self, result: RunResult, touched: set[str] | None = None, attributed: Attributed | None = None
    ) -> None:
        self.result = result
        self.touched = touched
        self.attributed = attributed

    def incomplete_lines(self) -> list[str]:
        items = self.result.incomplete()
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

    def keep(self, finding: Finding) -> bool:
        if self.touched is None:
            return True
        paths = {edge.site.path for edge in finding.witnesses}
        return bool(paths & self.touched) or any(subject in self.touched for subject in finding.subjects)

    def render(self) -> str:
        result = self.result
        out: list[str] = []
        violations = [f for f in result.violations() if self.keep(f)]
        out.append(f"violations ({len(violations)})")
        out.extend(f"  {finding_line(f)}" for f in violations)
        advisory = [
            f for f in result.findings() if f.status is FindingStatus.NEW and result.advisory(f.rule) and self.keep(f)
        ]
        if advisory:
            out.append(f"advisory ({len(advisory)})")
            out.extend(f"  {finding_line(f)}" for f in advisory)
        if self.touched is not None:
            accepted = [f for f in result.findings() if f.status is FindingStatus.ACCEPTED and self.keep(f)]
            out.append(f"existing debt in touched files (advisory) ({len(accepted)})")
            out.extend(f"  {finding_line(f)}" for f in accepted)
        out.extend(self.incomplete_lines())
        if result.equality is not None and not result.equality.equal:
            out.append("ledger is not equal to the scan")
            out.extend(f"  {problem}" for problem in result.equality.header)
            out.extend(f"  new    {render_key(d.key)}  {d.recorded} -> {d.scanned}" for d in result.equality.new)
            out.extend(f"  stale  {render_key(d.key)}  {d.recorded} -> {d.scanned}" for d in result.equality.stale)
        for entry in result.exceptions.stale:
            out.append(f"stale exception: {entry.label()} matches nothing")
        for entry in result.exceptions.expired:
            out.append(f"exception past review_by {entry.review_by}: {entry.label()} (reported, not blocking)")
        out.extend(f"exceptions: {problem}" for problem in result.exceptions.problems)
        for name, level in result.levels.items():
            out.append(f"level {name}: {len(level.passing)} pass, {len(level.failing)} fail")
        accepted_count = sum(1 for f in result.findings() if f.status is FindingStatus.ACCEPTED)
        out.append(f"{accepted_count} findings accepted by the ledger")
        return "\n".join(out) + "\n"


# ── engines and baselines ─────────────────────────────────────────


class LocalEngine:
    """The engine this process runs: its id, its extractor, and the envelope of one of its runs."""

    def __init__(self, name: str = ENGINE_PYTHON) -> None:
        self.name = name
        self.frameworks: dict[tuple[str, ...], str] = {}

    @cached_property
    def binary(self) -> Path:
        try:
            return Extractor.find_binary()
        except FileNotFoundError as error:
            raise EngineError(str(error)) from error

    @cached_property
    def build_id(self) -> str:
        return BuildId.of_binary(self.binary)

    @cached_property
    def id(self) -> str:
        root = EngineId.root_of(PACKAGE_DIR)
        if (root / "Cargo.toml").is_file():
            return EngineId.of(root, self.name)
        return EngineId.of(root, self.name, self.build_id)

    def framework(self, config: Config) -> str:
        packages = as_tuple(config.discovery.get("external"))
        if packages not in self.frameworks:
            self.frameworks[packages] = Framework.digest(packages)
        return self.frameworks[packages]

    def envelope(self, run: Run, tree: str) -> Envelope:
        root = run.policy_root or run.root
        policy = policy_digest(run.config, run.exceptions.raw, run.exceptions.problems, root)
        return Envelope(tree, self.id, policy, self.framework(run.config))

    def envelope_at(self, policy_root: Path, tree: str) -> tuple[Envelope, Config]:
        """The envelope a run of this engine under the policy at `policy_root` would have, before the run."""
        config = load_config(policy_root, policy_root / CONFIG_FILE)
        exceptions = ExceptionsFile.read(policy_root / config.ledger.exceptions, config)
        policy = policy_digest(config, exceptions.raw, exceptions.problems, policy_root)
        return Envelope(tree, self.id, policy, self.framework(config)), config


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
        return EngineId.of(EngineId.root_of(base_root / package), self.local.name)

    def of(self, base_root: Path) -> BaseEngine | None:
        """None when the base predates the in-repo engine."""
        engine_id = self.id_of(base_root)
        package = self.package()
        if engine_id is None or package is None:
            return None if engine_id is None else BaseEngine(engine_id)
        crate = package.parent
        self.check_local(crate)
        build_id = BuildId.of_sources(base_root / crate)
        if build_id is None:
            raise EngineError(f"the base engine has no extractor sources in {crate}")
        if engine_id == self.local.id:
            return BaseEngine(engine_id)
        return BaseEngine(engine_id, self.binary(build_id, crate), base_root / package.parent)

    def check_local(self, crate: Path) -> None:
        """This checkout's extractor must be the build of its own sources, and it is kept for later bases."""
        expected = BuildId.of_sources(self.repo / crate)
        if expected is None:
            return
        if expected != self.local.build_id:
            raise EngineError(
                f"{self.local.binary} is extractor build {self.local.build_id}, but the sources in {crate} "
                f"are build {expected}; rebuild it with {crate}/bin/build"
            )
        self.extractors.keep(self.local.binary, expected)

    def binary(self, build_id: str, crate: Path) -> Path:
        override = os.environ.get("HOGLINT_BASE_FACTS_BIN")
        if override:
            found = BuildId.of_binary(Path(override))
            if found != build_id:
                raise EngineError(f"HOGLINT_BASE_FACTS_BIN is extractor build {found}; the base needs {build_id}")
            return Path(override)
        if build_id == self.local.build_id:
            return self.local.binary
        stored = self.extractors.find(build_id)
        if stored is not None:
            return stored
        raise EngineError(
            f"no hoglint-facts binary of build {build_id} for the base engine: build {crate}/bin/build in a "
            f"checkout of the base commit and copy the binary it prints to {self.extractors.path(build_id)}, "
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


@dataclass(frozen=True, slots=True)
class Baseline:
    """What one check run attributes its incomplete results against.

    `envelope` is the run's own; its tree is set when the run's code is a git tree (the index, or a
    clean HEAD), so the run's summary is stored for the next commit. `reference` is None when the
    run itself is the baseline (`is_self`) or when there is no commit to compare with."""

    envelope: Envelope
    against: str
    reference: dict[Obligation, list[str]] | None = None
    is_self: bool = False


class Baselines:
    """Introduced versus inherited outside compare. The baseline is HEAD's tree under the same
    engine and policy as the run, so a policy change never counts as introduced by code. It comes
    from the per-machine summary cache; else the run itself is HEAD; else HEAD's tree is evaluated
    once by blob id, with no checkout, and stored. A staged run stores its own summary under the
    index tree, so the next commit finds its baseline in the cache."""

    def __init__(self, snapshots: Snapshots, engine: LocalEngine, store: SummaryStore) -> None:
        self.snapshots = snapshots
        self.repo = snapshots.repo
        self.work_dir = snapshots.work_dir
        self.engine = engine
        self.store = store

    def prepare(self, policy_root: Path, staged: bool) -> Baseline:
        head_tree = self.snapshots.tree_of("HEAD")
        if not head_tree:
            return Baseline(Envelope(), "nothing (no commit yet)")
        wanted, config = self.engine.envelope_at(policy_root, head_tree)
        own_tree = self.snapshots.index_tree() if staged else ""
        envelope = dataclasses.replace(wanted, tree=own_tree)
        short = head_tree[:12]
        stored = self.store.get(wanted)
        if stored is not None:
            return Baseline(envelope, f"HEAD (tree {short}, stored summary)", stored.obligations())
        if own_tree == head_tree or (not staged and self.snapshots.head_tree_in_worktree(config.ledger.file)):
            return Baseline(wanted, f"HEAD (tree {short}, this run)", is_self=True)
        summary = self.evaluate(policy_root, config.ledger.file, wanted)
        self.store.put(wanted, summary)
        return Baseline(envelope, f"HEAD (tree {short}, computed and stored)", summary.obligations())

    def evaluate(self, policy_root: Path, ledger_path: str, envelope: Envelope) -> ScanSummary:
        """HEAD's tree under the run's policy, with HEAD's own ledger, in a runner of its own so its
        graph is freed before the run."""
        try:
            ledger = LedgerFile.parse(git(self.repo, "show", f"{envelope.tree}:{ledger_path}"))
        except subprocess.CalledProcessError:
            ledger = LedgerFile()
        runner = Runner(GraphSource(Path(tempfile.mkdtemp(prefix="baseline-", dir=self.work_dir))))
        run = runner.run(policy_root, self.repo, tree=envelope.tree, ledger=ledger)
        return ScanSummary.from_run(run.result, run.graph, envelope, with_triples=False)

    def attribute(self, baseline: Baseline, run: Run, store: bool) -> Attributed:
        """`store` keeps the run's summary under its tree; a run that rewrote the ledger has none."""
        head = obligations_of(run.result.incomplete())
        if store and baseline.envelope.tree:
            summary = ScanSummary.from_run(run.result, run.graph, baseline.envelope, with_triples=False)
            self.store.put(baseline.envelope, summary)
        reference = head if baseline.is_self else baseline.reference
        return Attributed(attribute(head, reference), baseline.against)


# ── commands ──────────────────────────────────────────────────────


class Commands:
    def __init__(self, repo: Path, work_dir: Path) -> None:
        self.repo = repo
        self.work_dir = work_dir
        self.source = GraphSource(work_dir)
        self.runner = Runner(self.source)
        self.snapshots = Snapshots(repo, work_dir)
        self.engine = LocalEngine()
        self.summaries = SummaryStore()
        self.baselines = Baselines(self.snapshots, self.engine, self.summaries)
        self.base_engines = BaseEngines(repo, work_dir, self.engine, ExtractorStore())

    def check(self, index: bool, write_ledger: bool, changed: bool) -> int:
        policy_root = self.snapshots.index() if index else self.repo
        baseline = None if changed else self.baselines.prepare(policy_root, index)
        run = self.runner.run(policy_root, self.repo, staged=index, rewrite_ledger=write_ledger)
        if write_ledger:
            if index:
                sys.stderr.write("--write-ledger writes the working tree file, not the index\n")
            run.ledger.write(self.repo / run.config.ledger.file)
            sys.stdout.write(f"wrote {len(run.ledger.lines)} lines to {run.config.ledger.file}\n")
        self.write_outputs(run)
        touched = None
        if changed:
            touched = set(self.snapshots.changed_paths(None, index))
            touched |= {m for m, p in run.graph.modules.items() if p in touched}
        attributed = None if baseline is None else self.baselines.attribute(baseline, run, store=not write_ledger)
        sys.stdout.write(CheckReport(run.result, touched, attributed).render())
        if changed:
            return 0
        if attributed is not None and attributed.blocking:
            return 2
        return run.result.exit_code()

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
        counts: dict[str, Counter[str]] = {}
        for finding in self.current_findings():
            if finding.status is FindingStatus.ALLOWED:
                continue
            if by == "owners":
                keys = finding.owners or ("(no owner)",)
            else:
                keys = (self.target_of(finding),)
            for key in keys:
                counts.setdefault(key, Counter())[f"{finding.status.value}:{finding.rule}"] += 1
        for key in sorted(counts, key=lambda k: (-sum(counts[k].values()), k)):
            total = sum(counts[key].values())
            detail = ", ".join(f"{name} {count}" for name, count in counts[key].most_common())
            sys.stdout.write(f"{key}  {total}  ({detail})\n")
        return 0

    def current_findings(self) -> list[FindingRecord]:
        """The findings of the working tree: from the stored summary of HEAD's tree when the working
        tree holds HEAD's sources, else from a run, which is stored when it has a tree."""
        key, config = self.engine.envelope_at(self.repo, "")
        key = dataclasses.replace(key, tree=self.snapshots.head_tree_in_worktree(config.ledger.file))
        stored = self.summaries.get(key)
        if stored is not None:
            return stored.findings
        run = self.runner.run(self.repo)
        summary = ScanSummary.from_run(run.result, run.graph, self.engine.envelope(run, key.tree), with_triples=False)
        self.summaries.put(summary.envelope, summary)
        return summary.findings

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
        base_sha = self.snapshots.merge_base(ref)
        base_tree = self.snapshots.tree_of(base_sha)
        base_root = self.snapshots.commit(base_sha)
        head = self.runner.run(self.repo)
        renames = self.module_renames(base_sha, head.config)
        head_tree = self.snapshots.head_tree_in_worktree(head.config.ledger.file)
        head_key = self.engine.envelope(head, head_tree)
        scans = Scans(head=ScanSummary.from_run(head.result, head.graph, head_key, renames.modules()))
        self.summaries.put(head_key, scans.head)
        base_config = load_config(base_root, base_root / CONFIG_FILE) if (base_root / CONFIG_FILE).is_file() else None
        base_exceptions = ExceptionsFile()
        same_policy = False
        if base_config is not None:
            base_exceptions = ExceptionsFile.read(base_root / base_config.ledger.exceptions, base_config)
            base_policy = policy_digest(base_config, base_exceptions.raw, base_exceptions.problems, base_root)
            same_policy = base_policy == scans.head.envelope.policy
            try:
                scans.base, scans.head_base, scans.base_source = self.base_scans(
                    base_tree, base_root, base_policy, scans.head, same_policy
                )
            except EngineError as error:
                scans.base_failure = str(error)
        if scans.base is None or not same_policy:
            # Head policy on base code: the reference when no base scan exists, and the evidence
            # that labels each policy change.
            base_graph, base_head = self.runner.evaluate(head.config, base_root, self.repo)
            envelope = Envelope("", self.engine.id, scans.head.envelope.policy, scans.head.envelope.framework)
            scans.base_head = ScanSummary.from_run(base_head, base_graph, envelope)
        touched_paths = set(self.snapshots.changed_paths(base_sha, staged=False))
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
        sys.stdout.write(CheckReport(head.result, attributed=Attributed(report.labels, "the base")).render())
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
        self, base_tree: str, base_root: Path, base_policy: str, head: ScanSummary, same_policy: bool
    ) -> tuple[ScanSummary | None, ScanSummary | None, str]:
        """Base code and head code, both under the base engine and the base policy; (None, None)
        when the base predates hoglint, and where the base summary came from. Raises EngineError
        when the base engine cannot run.

        The base engine runs in this process when it is this engine, else in its own process. A
        stored summary with the exact key and the needed couplings replaces either scan; head code
        has a key only when the working tree holds HEAD's sources. When the engine and the policy
        did not change, the head scan already is head code under the base policy.
        """
        engine = self.base_engines.of(base_root)
        if engine is None:
            return None, None, ""
        scope, framework = head.scope, head.envelope.framework
        base_key = Envelope(base_tree, engine.id, base_policy, framework)
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
                base = ScanSummary.from_run(run.result, run.graph, base_key, scope)
            else:
                base = self.base_engines.scan(engine, base_root, base_root, base_tree, scope)
            self.summaries.put(base.envelope, base)
        if same_policy and engine.binary is None:
            return base, head, source
        head_base_key = Envelope(head.envelope.tree, engine.id, base_policy, framework)
        head_base = self.stored(head_base_key, scope)
        if head_base is None:
            if engine.binary is None:
                run = self.runner.run(base_root, code_root=self.repo)
                head_base = ScanSummary.from_run(run.result, run.graph, head_base_key, scope, with_triples=False)
            else:
                head_base = self.base_engines.scan(engine, base_root, self.repo, head.envelope.tree, scope)
            self.summaries.put(head_base.envelope, head_base)
        return base, head_base, source

    def scan(self, code_root: Path, out: Path, tree: str, scope_file: Path | None) -> int:
        """One summary of code under this repo's policy and this engine: what a newer engine asks
        of a base engine (see `BaseEngines.scan`). `tree` is the code's git tree id, if it is one."""
        scope = frozenset(json.loads(scope_file.read_text())) if scope_file is not None else frozenset()
        run = self.runner.run(self.repo, code_root=code_root)
        ScanSummary.from_run(run.result, run.graph, self.engine.envelope(run, tree), scope).write(out)
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
        key, root = self.commit_key(ref)
        if action == "export":
            if file is None:
                raise EngineError("summary export needs --file")
            summary = self.summaries.get(key) or self.summarize(key, root)
            summary.write(file)
        sys.stdout.write(f"{key.key()}\n")
        return 0

    def commit_key(self, ref: str) -> tuple[Envelope, Path]:
        """The key of a commit's summary, and a root that holds the commit's files: the repo when
        it is a clean checkout of that commit, else a checkout of it."""
        tree = self.snapshots.tree_of(ref)
        if not tree:
            raise EngineError(f"{ref} names no commit")
        root = self.repo if tree == self.snapshots.tree_of("HEAD") and self.snapshots.clean() else None
        root = root or self.snapshots.commit(ref)
        engine_id = self.base_engines.id_of(root)
        if engine_id is None:
            raise EngineError(f"{ref} has no hoglint engine")
        key, _ = self.engine.envelope_at(root, tree)
        return dataclasses.replace(key, engine=engine_id), root

    def summarize(self, key: Envelope, root: Path) -> ScanSummary:
        engine = self.base_engines.of(root)
        if engine is None:
            raise EngineError("the commit has no hoglint engine")
        if engine.binary is None:
            run = self.runner.run(root)
            summary = ScanSummary.from_run(run.result, run.graph, key)
        else:
            summary = self.base_engines.scan(engine, root, root, key.tree, frozenset())
        self.summaries.put(summary.envelope, summary)
        return summary


def parser() -> argparse.ArgumentParser:
    """Each subcommand sets `handler`, a function of (Commands, parsed args) that returns the exit code."""
    main = argparse.ArgumentParser(
        prog="hoglint", description="Boundary linter: facts, edges, rules and a generated ledger."
    )
    main.add_argument("--repo", type=Path, default=Path.cwd(), help="repo root that holds hoglint.toml")
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
    scan = sub.add_parser("scan", help="write a scan summary (protocol 1) of code under this policy and engine")
    scan.add_argument("--code-root", type=Path, required=True)
    scan.add_argument("--out", type=Path, required=True)
    scan.add_argument("--tree", default="", help="the git tree id of the code, when it is one")
    scan.add_argument("--scope", type=Path, help="JSON list of modules whose couplings move admission reads")
    scan.set_defaults(
        handler=lambda commands, args: commands.scan(args.code_root.resolve(), args.out, args.tree, args.scope)
    )
    summary = sub.add_parser("summary", help="the run summary cache for CI: print a commit's key, export or import")
    summary.add_argument("action", choices=("key", "export", "import"))
    summary.add_argument("--ref", default="HEAD", help="the commit whose tree the key names (key, export)")
    summary.add_argument("--file", type=Path, help="the summary file to write (export) or read (import)")
    summary.set_defaults(handler=lambda commands, args: commands.summary(args.action, args.ref, args.file))
    return main


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    repo = args.repo.resolve()
    with tempfile.TemporaryDirectory(prefix="hoglint-") as work:
        commands = Commands(repo, Path(work))
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
