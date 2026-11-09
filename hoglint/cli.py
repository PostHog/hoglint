"""hoglint command line: check, explain, query, compare, explain-config.

Exit codes: 0 clean, 1 violations (new findings, a ledger that is not equal to the scan, code
growth, a stale exception), 2 blocking incompleteness or a config error. An incomplete result
never hides violations: both are printed.
"""

from __future__ import annotations

import os
import sys
import json
import argparse
import tempfile
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hoglint.compare import Comparer, CompareReport, RawPolicy, Scans
from hoglint.config import CONFIG_FILE, Config, ConfigError, load_config
from hoglint.config.explain import ConfigExplainer
from hoglint.config.schema import as_tuple
from hoglint.index import GraphIndex
from hoglint.labels import CandidateCache
from hoglint.ledger.file import LedgerFile, render_key
from hoglint.ledger.growth import Renames
from hoglint.ledger.scan import ScanSummary
from hoglint.model import Finding, FindingStatus, Graph
from hoglint.rules.base import EdgeRule, RuleContext
from hoglint.rules.engine import Engine, RunResult, primitive_of
from hoglint.rules.exceptions import ExceptionsFile
from hoglint.selectors import Selector
from hoglint.source import GraphSource

PACKAGE_DIR = Path(__file__).resolve().parent


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def git_changed(repo: Path, ref: str, path: Path) -> bool:
    return subprocess.run(["git", "-C", str(repo), "diff", "--quiet", ref, "--", str(path)]).returncode != 0


# ── snapshots ─────────────────────────────────────────────────────


class Snapshots:
    """Materializes git snapshots (the index, a commit) into scratch directories."""

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


# ── one run ───────────────────────────────────────────────────────


@dataclass(frozen=False, slots=True)
class Run:
    root: Path
    config: Config
    graph: Graph
    result: RunResult
    ledger: LedgerFile
    exceptions: ExceptionsFile

    def summary(self, couplings: bool, triples: bool = True) -> ScanSummary:
        return ScanSummary.from_run(self.result, self.graph, with_couplings=couplings, with_triples=triples)


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

    def evaluate(self, config: Config, code_root: Path) -> tuple[Graph, RunResult]:
        """One policy over one code root, with no ledger and no exceptions."""
        graph = self.source.build(code_root, config)
        engine = Engine(config, graph, code_root, self.today, self.index_of(graph), self.candidates)
        return graph, engine.evaluate()

    def run(
        self, policy_root: Path, code_root: Path | None = None, staged: bool = False, rewrite_ledger: bool = False
    ) -> Run:
        """Policy (config, ledger, exceptions) from one root, code from another, or both from one.

        With `rewrite_ledger` the run is judged against the ledger it would write, not the file."""
        code_root = code_root or policy_root
        config = load_config(policy_root, policy_root / CONFIG_FILE)
        graph = self.source.build(code_root, config, staged)
        ledger = LedgerFile.read(policy_root / config.ledger.file)
        exceptions = ExceptionsFile.read(policy_root / config.ledger.exceptions, config)
        engine = Engine(config, graph, code_root, self.today, self.index_of(graph), self.candidates)
        result = engine.evaluate(ledger, exceptions, rewrite_ledger)
        return Run(code_root, config, graph, result, result.ledger or ledger, exceptions)


# ── rendering ─────────────────────────────────────────────────────


def where(finding: Finding) -> str:
    if finding.site is None:
        return ""
    return f"{finding.site.path}:{finding.site.line}"


def finding_line(finding: Finding) -> str:
    owners = f"  [{', '.join(finding.owners)}]" if finding.owners else ""
    return f"{finding.rule}  {' '.join(finding.identity)}\n    {finding.message}  {where(finding)}{owners}"


class CheckReport:
    def __init__(self, result: RunResult, touched: set[str] | None = None) -> None:
        self.result = result
        self.touched = touched

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
        incomplete = result.incomplete()
        out.append(f"incomplete ({len(incomplete)})")
        out.extend(f"  {item.rule} [{item.cause.value}]  {item.reason}" for item in incomplete)
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


# ── commands ──────────────────────────────────────────────────────


class Commands:
    def __init__(self, repo: Path, work_dir: Path) -> None:
        self.repo = repo
        self.work_dir = work_dir
        self.source = GraphSource(work_dir)
        self.runner = Runner(self.source)
        self.snapshots = Snapshots(repo, work_dir)

    def check(self, index: bool, write_ledger: bool, changed: bool) -> int:
        policy_root = self.snapshots.index() if index else self.repo
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
        sys.stdout.write(CheckReport(run.result, touched).render())
        if changed:
            return 0
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
        run = self.runner.run(self.repo)
        counts: dict[str, Counter[str]] = {}
        for finding in run.result.findings():
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

    @staticmethod
    def target_of(finding: Finding) -> str:
        for edge in finding.witnesses:
            if edge.targets:
                return str(edge.targets[0])
        return finding.identity[0] if finding.identity else "?"

    def explain_config(self) -> int:
        config = load_config(self.repo, self.repo / CONFIG_FILE)
        sys.stdout.write(ConfigExplainer(config).render())
        return 0

    # ── compare ──

    def compare(self, ref: str) -> int:
        base_sha = self.snapshots.merge_base(ref)
        base_root = self.snapshots.commit(base_sha)
        head = self.runner.run(self.repo)
        scans = Scans(head=head.summary(couplings=False))
        base_config = load_config(base_root, base_root / CONFIG_FILE) if (base_root / CONFIG_FILE).is_file() else None
        base_exceptions = ExceptionsFile()
        same_policy = False
        if base_config is not None:
            base_exceptions = ExceptionsFile.read(base_root / base_config.ledger.exceptions, base_config)
            same_policy = (
                base_config.policy_hash == head.config.policy_hash and base_exceptions.raw == head.exceptions.raw
            )
            scans.base, scans.head_base = self.base_scans(base_sha, base_root, head if same_policy else None)
        if scans.base is None or not same_policy:
            # Head policy on base code: the reference when no base scan exists, and the evidence
            # that labels each policy change.
            base_graph, base_head = self.runner.evaluate(head.config, base_root)
            scans.base_head = ScanSummary.from_run(base_head, base_graph)
        touched_paths = set(self.snapshots.changed_paths(base_sha, staged=False))
        touched = {module for module, path in head.graph.modules.items() if path in touched_paths}
        report: CompareReport = Comparer(
            scans,
            LedgerFile.read(base_root / head.config.ledger.file),
            head.ledger,
            RawPolicy(base_config.expanded if base_config is not None else {}, base_exceptions.raw),
            RawPolicy(head.config.expanded, head.exceptions.raw),
            touched,
            self.module_renames(base_sha, head.config),
        ).report()
        sys.stdout.write(CheckReport(head.result).render())
        sys.stdout.write(report.render())
        return report.exit_code(head.result.failed())

    def module_renames(self, base_sha: str, config: Config) -> Renames:
        from hoglint.core.modules import ModuleNamer  # noqa: PLC0415 — the extractor stack loads only when needed

        namer = ModuleNamer(as_tuple(config.discovery.get("source_roots", ["."])))
        mapping = {}
        for old, new in self.snapshots.renames(base_sha):
            if old.endswith(".py") and new.endswith(".py"):
                mapping[namer.module_of(new)[0]] = namer.module_of(old)[0]
        return Renames(mapping)

    def base_scans(
        self, base_sha: str, base_root: Path, head_under_same_policy: Run | None
    ) -> tuple[ScanSummary | None, ScanSummary | None]:
        """Base code and head code, both under the base scanner and the base policy.

        The scanner is this package when it lives outside the repo or did not change since the
        base; otherwise the base checkout's own package runs in a subprocess. When the scanner and
        the policy did not change, the head run already is head code under the base policy.
        """
        repo = self.repo.resolve()
        package = PACKAGE_DIR.relative_to(repo) if PACKAGE_DIR.is_relative_to(repo) else None
        if package is not None and not (base_root / package).is_dir():
            return None, None
        if package is not None and git_changed(self.repo, base_sha, package):
            return self.scan_external(base_root, package, base_root), self.scan_external(base_root, package, self.repo)
        base_run = self.runner.run(base_root)
        head_under_base = head_under_same_policy or self.runner.run(base_root, code_root=self.repo)
        return base_run.summary(couplings=True), head_under_base.summary(couplings=True, triples=False)

    def scan_external(self, base_root: Path, package: Path, code_root: Path) -> ScanSummary | None:
        out = Path(tempfile.mkdtemp(dir=self.work_dir)) / "scan.json"
        command = [
            sys.executable,
            "-m",
            "hoglint",
            "--repo",
            str(base_root),
            "scan",
            "--code-root",
            str(code_root),
            "--out",
            str(out),
        ]
        env = {"PYTHONPATH": str(base_root / package.parent)}
        completed = subprocess.run(command, env={**os.environ, **env}, capture_output=True, text=True)
        if completed.returncode != 0 or not out.is_file():
            sys.stderr.write(f"base scanner failed:\n{completed.stderr[-2000:]}\n")
            return None
        return ScanSummary.read(out)

    def scan(self, code_root: Path, out: Path) -> int:
        self.runner.run(self.repo, code_root=code_root).summary(couplings=True).write(out)
        return 0


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
    scan = sub.add_parser("scan")
    scan.add_argument("--code-root", type=Path, required=True)
    scan.add_argument("--out", type=Path, required=True)
    scan.set_defaults(handler=lambda commands, args: commands.scan(args.code_root.resolve(), args.out))
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
        finally:
            commands.snapshots.close()
