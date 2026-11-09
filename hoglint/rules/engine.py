"""Runs every rule over one graph, then exceptions, then the ledger, then levels.

Order: rules that read only edges first, coverage after them (it reads other rules' findings),
levels last (they read every result). Nothing here deletes an edge.
"""

from __future__ import annotations

import datetime
import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from hoglint.config.loader import ConfigError
from hoglint.config.model import Config, RuleSpec
from hoglint.index import GraphIndex
from hoglint.labels import CandidateCache, Labeler
from hoglint.ledger.equality import EqualityResult, check_equality
from hoglint.ledger.file import LedgerFile, LineKey, level_line
from hoglint.model import SCHEMA_VERSION, Finding, FindingStatus, Graph, IncompleteCause, Outcome, RuleResult
from hoglint.rules.base import IncompleteItem, RuleContext, RuleRun, context_class, rule_kinds
from hoglint.rules.edges import EdgePrimitive, EntrypointPrimitive, SignaturePrimitive, UseShapePrimitive
from hoglint.rules.entities import CoveragePrimitive, EdgeInventory, EntityInventory, LayoutPrimitive
from hoglint.rules.exceptions import ExceptionReport, ExceptionsFile, apply_exceptions
from hoglint.rules.exports import ExportPrimitive
from hoglint.rules.graphs import CyclePrimitive, ReachPrimitive
from hoglint.rules.levels import LevelEvaluator, LevelResult
from hoglint.selectors import Truth


class Primitive(Protocol):
    def __init__(self, ctx: RuleContext, spec: RuleSpec) -> None: ...

    def evaluate(self) -> RuleRun: ...


PRIMITIVES: dict[str, type[Primitive]] = {
    "edge": EdgePrimitive,
    "entrypoint": EntrypointPrimitive,
    "use-shape": UseShapePrimitive,
    "signature": SignaturePrimitive,
    "export": ExportPrimitive,
    "reach": ReachPrimitive,
    "cycle": CyclePrimitive,
    "layout": LayoutPrimitive,
    "coverage": CoveragePrimitive,
}


def primitive_of(spec: RuleSpec) -> type[Primitive]:
    """The class that runs a rule. An inventory over edges (`classify`) and one over entities
    (`exactly_one`) share a name in config but not a shape."""
    if spec.primitive == "inventory":
        return EdgeInventory if spec.classify else EntityInventory
    return PRIMITIVES[spec.primitive]


@dataclass(frozen=False, slots=True)
class RunResult:
    config: Config
    labeler: Labeler
    runs: dict[str, RuleRun] = field(default_factory=dict)
    levels: dict[str, LevelResult] = field(default_factory=dict)
    exceptions: ExceptionReport = field(default_factory=ExceptionReport)
    scan: dict[LineKey, int] = field(default_factory=dict)
    equality: EqualityResult | None = None
    ledger: LedgerFile | None = None

    def findings(self) -> list[Finding]:
        return [finding for run in self.runs.values() for finding in run.findings]

    def advisory(self, rule: str) -> bool:
        spec = self.config.rules.get(rule)
        return spec is not None and spec.report == "advisory"

    def violations(self) -> list[Finding]:
        return [
            finding
            for finding in self.findings()
            if finding.status is FindingStatus.NEW and not self.advisory(finding.rule)
        ]

    def incomplete(self) -> list[IncompleteItem]:
        return [item for run in self.runs.values() for item in run.incomplete if not self.advisory(item.rule)]

    def blocking_incomplete(self) -> list[IncompleteItem]:
        return [
            item for item in self.incomplete() if item.cause in (IncompleteCause.INTRODUCED, IncompleteCause.EXTRACTOR)
        ]

    def failed(self) -> bool:
        equality_failed = self.equality is not None and not self.equality.equal
        return bool(self.violations() or equality_failed or self.exceptions.stale or self.exceptions.problems)

    def exit_code(self) -> int:
        if self.blocking_incomplete():
            return 2
        return 1 if self.failed() else 0

    def rule_results(self) -> list[RuleResult]:
        out = []
        for rule_id, run in self.runs.items():
            new = [f for f in run.findings if f.status is FindingStatus.NEW]
            outcome = Outcome.INCOMPLETE if run.incomplete else Outcome.VIOLATIONS if new else Outcome.CLEAN
            out.append(
                RuleResult(rule_id, outcome, list(run.findings), [(item.cause, item.reason) for item in run.incomplete])
            )
        return out


class Engine:
    def __init__(
        self,
        config: Config,
        graph: Graph,
        repo_root: Path | None = None,
        today: datetime.date | None = None,
        index: GraphIndex | None = None,
        candidates: CandidateCache | None = None,
    ) -> None:
        self.config = config
        self.graph = graph
        self.repo_root = repo_root
        self.today = today or datetime.date.today()
        self.labeler = Labeler(config, graph, repo_root, index, candidates)

    def failed_producers(self, spec: RuleSpec) -> list[str]:
        """A producer named in `requires` (any version), or one that would have made an edge kind the rule reads."""
        kinds = set(rule_kinds(spec, self.config.views))
        out = []
        for name, failure in sorted(self.graph.failed_producers.items()):
            if name.partition("@")[0] in spec.requires or failure.kinds & kinds:
                out.append(f"producer {name} failed: {failure.reason}")
        return out

    def empty_expected_kinds(self) -> set[str]:
        """Kinds marked `expected` that no symbol matched: whatever produces them found nothing."""
        labeler = self.labeler
        return {
            name
            for name, spec in self.config.kinds.items()
            if spec.expected
            and not any(labeler.kind_matches(spec, symbol) is Truth.TRUE for symbol in self.graph.symbols)
        }

    def kinds_read(self, spec: RuleSpec) -> set[str]:
        """Kind names a rule compares against, and for an export rule the kinds its shapes name."""
        out: set[str] = set()
        for selector in (spec.from_, spec.select, spec.deny, spec.allow, spec.require, spec.unless):
            if selector is not None:
                out |= selector.compared_values("kind")
        if spec.primitive == "export":
            for name in spec.exports or tuple(self.config.exports):
                policy = self.config.shapes[self.config.exports[name].shape]
                out |= {*policy.deny, *policy.data, *policy.allow}
        return out

    def missing_kinds(self, spec: RuleSpec, empty: set[str]) -> list[str]:
        return [
            f"kind {kind} matched no symbol, but [kinds] marks it expected"
            for kind in sorted(self.kinds_read(spec) & empty)
        ]

    def raise_conflicts(self) -> None:
        if self.labeler.conflicts:
            raise ConfigError(sorted(set(self.labeler.conflicts)))

    def run_rules(self) -> dict[str, RuleRun]:
        self.labeler.find_conflicts()
        self.raise_conflicts()
        ctx = context_class(self.labeler.index)(self.config, self.graph, self.labeler, self.repo_root)
        empty = self.empty_expected_kinds()
        ordered = sorted(self.config.rules.values(), key=lambda spec: spec.primitive == "coverage")
        for spec in ordered:
            failures = self.failed_producers(spec) + self.missing_kinds(spec, empty)
            if failures:
                run = RuleRun(spec)
                run.incomplete.extend(
                    IncompleteItem(spec.id, reason, reason, (), IncompleteCause.EXTRACTOR) for reason in failures
                )
                ctx.results[spec.id] = run
                continue
            ctx.results[spec.id] = primitive_of(spec)(ctx, spec).evaluate()
        for name in sorted(self.labeler.providers.failures):
            reason = f"provider {name} failed: {self.labeler.providers.failures[name]}"
            run = RuleRun(RuleSpec(f"provider:{name}", "layout", ledger=False))
            run.incomplete.append(IncompleteItem(run.spec.id, reason, reason, (), IncompleteCause.EXTRACTOR))
            ctx.results[run.spec.id] = run
        self.raise_conflicts()
        return ctx.results

    def evaluate(
        self,
        ledger: LedgerFile | None = None,
        exceptions: ExceptionsFile | None = None,
        rewrite_ledger: bool = False,
    ) -> RunResult:
        """One run. With `rewrite_ledger`, findings and levels are judged against the ledger this
        run writes (its own scan), so the next check over the same code finds that ledger equal."""
        result = RunResult(self.config, self.labeler, self.run_rules())
        if exceptions is not None:
            result.exceptions = apply_exceptions(result.runs, exceptions, self.today)
        result.scan = self.scan_lines(result)
        if rewrite_ledger:
            ledger = LedgerFile(SCHEMA_VERSION, self.config.policy_hash, exists=True)
        if ledger is not None:
            self.mark_accepted(result, ledger.lines if not rewrite_ledger else result.scan)
        for name, spec in self.config.levels.items():
            level = LevelEvaluator(spec, self.labeler, result.runs).evaluate()
            result.levels[name] = level
            for group in level.passing:
                result.scan[level_line(name, group)] = 1
        self.raise_conflicts()
        if rewrite_ledger and ledger is not None:
            ledger.lines = dict(result.scan)
            result.ledger = ledger
        if ledger is not None:
            result.equality = check_equality(result.scan, ledger, SCHEMA_VERSION, self.config.policy_hash)
        return result

    def scan_lines(self, result: RunResult) -> dict[LineKey, int]:
        lines: dict[LineKey, int] = {}
        for rule_id, run in result.runs.items():
            spec = self.config.rules.get(rule_id)
            if spec is None or not spec.ledger:
                continue
            for finding in run.findings:
                if finding.status is FindingStatus.ALLOWED or not finding.ledgerable:
                    continue
                key = finding.ledger_key
                lines[key] = lines.get(key, 0) + 1 if spec.counted else 1
        return lines

    def mark_accepted(self, result: RunResult, recorded: dict[LineKey, int]) -> None:
        """A new finding is accepted when the ledger records at least as many as the scan counts."""
        for run in result.runs.values():
            updated = []
            for finding in run.findings:
                key = finding.ledger_key
                accepted = (
                    finding.status is FindingStatus.NEW
                    and finding.ledgerable
                    and key in recorded
                    and recorded[key] >= result.scan.get(key, 0)
                )
                updated.append(dataclasses.replace(finding, status=FindingStatus.ACCEPTED) if accepted else finding)
            run.findings = updated
