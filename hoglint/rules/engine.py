"""Runs every rule over one graph, then exceptions, then the ledger, then levels.

Order: rules that read only edges first, coverage after them (it reads other rules' findings),
levels last (they read every result). Nothing here deletes an edge.
"""

from __future__ import annotations

import datetime
import dataclasses
from dataclasses import dataclass, field
from pathlib import Path

from hoglint.config.loader import ConfigError
from hoglint.config.model import Config, RuleSpec
from hoglint.labels import Labeler
from hoglint.ledger.equality import EqualityResult, check_equality
from hoglint.ledger.file import LedgerFile, LineKey
from hoglint.model import SCHEMA_VERSION, Finding, FindingStatus, Graph, IncompleteCause, Outcome, RuleResult
from hoglint.rules.base import IncompleteItem, RuleContext, RuleRun, rule_kinds
from hoglint.rules.edges import EdgePrimitive, EntrypointPrimitive, SignaturePrimitive, UseShapePrimitive
from hoglint.rules.entities import CoveragePrimitive, InventoryPrimitive, LayoutPrimitive
from hoglint.rules.exceptions import ExceptionReport, ExceptionsFile, apply_exceptions
from hoglint.rules.exports import ExportPrimitive
from hoglint.rules.graphs import CyclePrimitive, ReachPrimitive
from hoglint.rules.levels import LevelEvaluator, LevelResult

PRIMITIVES = {
    "edge": EdgePrimitive,
    "entrypoint": EntrypointPrimitive,
    "use-shape": UseShapePrimitive,
    "signature": SignaturePrimitive,
    "export": ExportPrimitive,
    "reach": ReachPrimitive,
    "cycle": CyclePrimitive,
    "layout": LayoutPrimitive,
    "coverage": CoveragePrimitive,
    "inventory": InventoryPrimitive,
}
LEVEL_PREFIX = "level:"


def ledger_key(finding: Finding) -> LineKey:
    return finding.rule, finding.identity


@dataclass(frozen=False, slots=True)
class RunResult:
    config: Config
    labeler: Labeler
    runs: dict[str, RuleRun] = field(default_factory=dict)
    levels: dict[str, LevelResult] = field(default_factory=dict)
    exceptions: ExceptionReport = field(default_factory=ExceptionReport)
    scan: dict[LineKey, int] = field(default_factory=dict)
    equality: EqualityResult | None = None

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
        self, config: Config, graph: Graph, repo_root: Path | None = None, today: datetime.date | None = None
    ) -> None:
        self.config = config
        self.graph = graph
        self.repo_root = repo_root
        self.today = today or datetime.date.today()
        self.labeler = Labeler(config, graph, repo_root)

    def failed_producers(self, spec: RuleSpec) -> list[str]:
        """A producer named in `requires`, or one that makes an edge kind the rule reads."""
        kinds = set(rule_kinds(spec))
        out = []
        for name, reason in sorted(self.graph.failed_producers.items()):
            stem = name.split("@", 1)[0]
            produced = stem.removeprefix("core-") if stem.startswith("core-") else stem
            if name in spec.requires or stem in spec.requires or produced in kinds:
                out.append(f"producer {name} failed: {reason}")
        return out

    def run_rules(self) -> dict[str, RuleRun]:
        conflicts = self.labeler.label_everything()
        if conflicts:
            raise ConfigError(sorted(set(conflicts)))
        ctx = RuleContext(self.config, self.graph, self.labeler, self.repo_root)
        ordered = sorted(self.config.rules.values(), key=lambda spec: spec.primitive == "coverage")
        for spec in ordered:
            failures = self.failed_producers(spec)
            if failures:
                run = RuleRun(spec)
                run.incomplete.extend(
                    IncompleteItem(spec.id, reason, reason, (), IncompleteCause.EXTRACTOR) for reason in failures
                )
                ctx.results[spec.id] = run
                continue
            ctx.results[spec.id] = PRIMITIVES[spec.primitive](ctx, spec).evaluate()
        for name in sorted(self.labeler.providers.failures):
            reason = f"provider {name} failed: {self.labeler.providers.failures[name]}"
            run = RuleRun(RuleSpec(f"provider:{name}", "layout", ledger=False))
            run.incomplete.append(IncompleteItem(run.spec.id, reason, reason, (), IncompleteCause.EXTRACTOR))
            ctx.results[run.spec.id] = run
        return ctx.results

    def evaluate(self, ledger: LedgerFile | None = None, exceptions: ExceptionsFile | None = None) -> RunResult:
        result = RunResult(self.config, self.labeler, self.run_rules())
        if exceptions is not None:
            result.exceptions = apply_exceptions(result.runs, exceptions, self.today)
        result.scan = self.scan_lines(result)
        self.mark_accepted(result, ledger)
        for name, spec in self.config.levels.items():
            level = LevelEvaluator(spec, self.labeler, result.runs).evaluate()
            result.levels[name] = level
            for group in level.passing:
                result.scan[(LEVEL_PREFIX + name, (group,))] = 1
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
                key = ledger_key(finding)
                lines[key] = lines.get(key, 0) + 1 if spec.counted else 1
        return lines

    def mark_accepted(self, result: RunResult, ledger: LedgerFile | None) -> None:
        if ledger is None:
            return
        for run in result.runs.values():
            updated = []
            for finding in run.findings:
                key = ledger_key(finding)
                accepted = (
                    finding.status is FindingStatus.NEW
                    and finding.ledgerable
                    and key in ledger.lines
                    and ledger.lines[key] >= result.scan.get(key, 0)
                )
                updated.append(dataclasses.replace(finding, status=FindingStatus.ACCEPTED) if accepted else finding)
            run.findings = updated
