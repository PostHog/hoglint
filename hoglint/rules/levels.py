"""The level primitive: which groups pass an ordered level such as Isolated.

A level is specified apart from lint acceptance:
- A group passes when every rule in `requires` ran for it and none of its findings is new.
- Ledgered debt may coexist with the level, except where it is safety-relevant: a finding that
  exists because something did not resolve, an incomplete result, or any finding of a rule in
  `strict` forbids the level even when the ledger accepts it.
- Exceptions make a use allowed, so an allowed finding does not count against the group. They
  never remove impact edges; test selection reads edges, not this result.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hoglint.config.model import LevelSpec
from hoglint.labels import Labeler
from hoglint.model import FindingStatus
from hoglint.rules.base import RuleRun
from hoglint.selectors import State, Truth


@dataclass(frozen=False, slots=True)
class LevelResult:
    name: str
    passing: list[str] = field(default_factory=list)
    failing: dict[str, list[str]] = field(default_factory=dict)


class LevelEvaluator:
    def __init__(self, spec: LevelSpec, labeler: Labeler, runs: dict[str, RuleRun]) -> None:
        self.spec = spec
        self.labeler = labeler
        self.runs = runs
        self.group_cache: dict[str, str | None] = {}

    def group_of(self, subject: str) -> str | None:
        if subject in self.group_cache:
            return self.group_cache[subject]
        module = self.labeler.module_of_symbol(subject) or subject
        value, _ = self.labeler.field_value(module, None, self.spec.group_by)
        group = str(value.data) if value.state is State.KNOWN else None
        self.group_cache[subject] = group
        return group

    def groups(self) -> list[str]:
        out: set[str] = set()
        for module in self.labeler.graph.modules:
            group = self.group_of(module)
            if group is None:
                continue
            if self.spec.select is not None:
                resolve = self.labeler.entity_resolver(module, None)
                if self.spec.select.evaluate(resolve) is not Truth.TRUE:
                    continue
            out.add(group)
        return sorted(out)

    def evaluate(self) -> LevelResult:
        result = LevelResult(self.spec.name)
        reasons: dict[str, list[str]] = {group: [] for group in self.groups()}
        for rule_id in self.spec.requires:
            run = self.runs.get(rule_id)
            if run is None:
                for group in reasons:
                    reasons[group].append(f"{rule_id} did not run")
                continue
            self.collect(rule_id, run, reasons)
        for group, why in sorted(reasons.items()):
            if why:
                result.failing[group] = why
            else:
                result.passing.append(group)
        return result

    def collect(self, rule_id: str, run: RuleRun, reasons: dict[str, list[str]]) -> None:
        strict = rule_id in self.spec.strict
        for finding in run.findings:
            if finding.status is FindingStatus.ALLOWED:
                continue
            blocking = finding.unknown or strict or finding.status is FindingStatus.NEW
            if not blocking:
                continue
            why = (
                "unknown"
                if finding.unknown
                else "strict, so the ledger does not excuse it"
                if strict
                else "new finding"
            )
            for group in {self.group_of(subject) for subject in finding.subjects}:
                if group in reasons:
                    reasons[group].append(f"{rule_id}: {why}: {finding.message}")
        for item in run.incomplete:
            groups = {self.group_of(subject) for subject in item.subjects} if item.subjects else set(reasons)
            for group in groups:
                if group in reasons:
                    reasons[group].append(f"{rule_id}: incomplete: {item.reason}")
