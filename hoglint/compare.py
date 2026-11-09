"""`hoglint compare --base <ref>`: what a change does to findings, debt and policy.

It lists apart:
- findings resolved and findings new,
- ledger lines added and removed; an added and a removed line on one growth key with the same
  count is a zero-net swap and shows as one,
- existing debt in the modules the change touches (advisory),
- policy entries changed, each labelled tighten or relax by running both policies on both
  snapshots (anything hoglint cannot classify counts as relax),
- new (package pair, edge kind, context) triples,
- growth by attribution, and incomplete results split into introduced and inherited.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from hoglint.ledger.file import LedgerFile, LineKey, render_key
from hoglint.ledger.growth import AttributionCheck, GrowthKey, GrowthResult, Renames, growth_key
from hoglint.ledger.scan import ScanSummary, Triple

EXCEPTION_ENTRY = "exception"
RULE_PREFIX = "rules."
LEVEL_PREFIX = "levels."


@dataclass(frozen=True, slots=True)
class PolicyChange:
    entry: str
    change: str  # added | removed | changed
    label: str  # tighten | relax | neutral
    why: str


@dataclass(frozen=True, slots=True)
class Swap:
    key: GrowthKey
    removed: LineKey
    added: LineKey
    count: int


@dataclass(frozen=False, slots=True)
class Scans:
    """head: head code, head policy. base_head: base code, head policy (same scanner).
    base and head_base use the base scanner and base policy; None when the base has no hoglint."""

    head: ScanSummary
    base_head: ScanSummary | None = None
    base: ScanSummary | None = None
    head_base: ScanSummary | None = None


@dataclass(frozen=False, slots=True)
class CompareReport:
    resolved: list[LineKey] = field(default_factory=list)
    new: list[LineKey] = field(default_factory=list)
    ledger_added: list[tuple[LineKey, int]] = field(default_factory=list)
    ledger_removed: list[tuple[LineKey, int]] = field(default_factory=list)
    swaps: list[Swap] = field(default_factory=list)
    touched_debt: list[tuple[LineKey, int]] = field(default_factory=list)
    policy: list[PolicyChange] = field(default_factory=list)
    new_triples: list[Triple] = field(default_factory=list)
    growth: GrowthResult = field(default_factory=GrowthResult)
    introduced: list[tuple[str, str, str]] = field(default_factory=list)
    inherited: list[tuple[str, str, str]] = field(default_factory=list)

    def exit_code(self, head_failed: bool) -> int:
        if self.introduced:
            return 2
        return 1 if head_failed or self.growth.failed else 0

    def render(self) -> str:
        out: list[str] = []

        def section(title: str, rows: list[str]) -> None:
            out.append(f"{title} ({len(rows)})")
            out.extend(f"  {row}" for row in rows)

        section("findings resolved", [render_key(key) for key in self.resolved])
        section("findings new", [render_key(key) for key in self.new])
        section("ledger lines added", [f"{render_key(key)}  {count}" for key, count in self.ledger_added])
        section("ledger lines removed", [f"{render_key(key)}  {count}" for key, count in self.ledger_removed])
        section(
            "zero-net swaps",
            [
                f"{s.key[0]} {' '.join(s.key[1])}: {render_key(s.removed)} -> {render_key(s.added)} ({s.count})"
                for s in self.swaps
            ],
        )
        section(
            "existing debt in touched modules (advisory)",
            [f"{render_key(key)}  {count}" for key, count in self.touched_debt],
        )
        section("policy entries changed", [f"{c.label:8} {c.change:8} {c.entry}  ({c.why})" for c in self.policy])
        section("new package edges (pair, kind, context)", [" ".join(t) for t in self.new_triples])
        if self.growth.skipped:
            out.append(f"growth check skipped: {self.growth.skipped}")
        section(
            "code growth (fails)",
            [f"{g.rule} {' '.join(g.key)}: {g.before} -> {g.after}" for g in self.growth.code_growth],
        )
        section(
            "admitted moves (review)",
            [f"{g.rule} {' '.join(g.key)}: {g.before} -> {g.after}" for g in self.growth.admitted_moves],
        )
        section(
            "rule growth (passes)",
            [f"{g.rule} {' '.join(g.key)}: {g.before} -> {g.after}" for g in self.growth.rule_growth],
        )
        section("incomplete, introduced (blocks)", [f"{r}: {reason}" for r, _, reason in self.introduced])
        section("incomplete, inherited (reported)", [f"{r}: {reason}" for r, _, reason in self.inherited])
        return "\n".join(out) + "\n"


def canonical(value: Any) -> str:
    def clean(item: Any) -> Any:
        if isinstance(item, dict):
            return {k: clean(v) for k, v in sorted(item.items()) if k != "__source__"}
        if isinstance(item, list):
            return [clean(v) for v in item]
        return item

    return json.dumps(clean(value), sort_keys=True, default=str)


def policy_entries(raw: dict[str, Any], exceptions: list[dict[str, Any]]) -> dict[str, str]:
    """Each reviewable unit of policy: a named entry, a plain setting, an assignment, an exception."""
    out: dict[str, str] = {}
    for section, value in raw.items():
        if section == "assign":
            for entry in value:
                text = canonical(entry)
                out[f"assign {text}"] = text
        elif isinstance(value, dict):
            for name, entry in value.items():
                out[f"{section}.{name}"] = canonical(entry)
        else:
            out[section] = canonical(value)
    for entry in exceptions:
        text = canonical(entry)
        out[f"{EXCEPTION_ENTRY} {text}"] = text
    return out


@dataclass(frozen=False, slots=True)
class Evidence:
    """What changes when only the policy changes, on base code and on head code."""

    lost: dict[str, int] = field(default_factory=dict)
    gained: dict[str, int] = field(default_factory=dict)
    levels_gained: set[str] = field(default_factory=set)
    levels_lost: set[str] = field(default_factory=set)

    def add(self, old: ScanSummary, new: ScanSummary) -> None:
        for rule, _ in old.findings - new.findings:
            self.lost[rule] = self.lost.get(rule, 0) + 1
        for rule, _ in new.findings - old.findings:
            self.gained[rule] = self.gained.get(rule, 0) + 1
        for name in set(old.levels) | set(new.levels):
            before, after = set(old.levels.get(name, ())), set(new.levels.get(name, ()))
            if after - before:
                self.levels_gained.add(name)
            if before - after:
                self.levels_lost.add(name)


class PolicyClassifier:
    """Labels a changed entry by its effect, judged on both snapshots."""

    def __init__(self, evidence: Evidence | None) -> None:
        self.evidence = evidence

    def label(self, entry: str, change: str) -> tuple[str, str]:
        if entry.startswith(EXCEPTION_ENTRY):
            return (
                ("relax", "an exceptions addition allows a use")
                if change == "added"
                else ("tighten", "exception removed")
            )
        evidence = self.evidence
        if evidence is None:
            return "relax", "base scanner unavailable, so the effect cannot be classified"
        if entry.startswith(RULE_PREFIX):
            rule = entry.removeprefix(RULE_PREFIX)
            return self.judge(evidence.lost.get(rule, 0), evidence.gained.get(rule, 0), set(), set())
        if entry.startswith(LEVEL_PREFIX):
            name = entry.removeprefix(LEVEL_PREFIX)
            gained = {name} & evidence.levels_gained
            lost = {name} & evidence.levels_lost
            return self.judge(0, 0, gained, lost)
        return self.judge(
            sum(evidence.lost.values()), sum(evidence.gained.values()), evidence.levels_gained, evidence.levels_lost
        )

    @staticmethod
    def judge(lost: int, gained: int, levels_gained: set[str], levels_lost: set[str]) -> tuple[str, str]:
        if lost or levels_gained:
            parts = [f"{lost} findings disappear"] if lost else []
            parts += [f"more groups pass {name}" for name in sorted(levels_gained)]
            return "relax", ", ".join(parts)
        if gained or levels_lost:
            parts = [f"{gained} findings appear"] if gained else []
            parts += [f"fewer groups pass {name}" for name in sorted(levels_lost)]
            return "tighten", ", ".join(parts)
        return "neutral", "no effect on base or head code"


class Comparer:
    def __init__(
        self,
        scans: Scans,
        base_ledger: LedgerFile,
        head_ledger: LedgerFile,
        base_policy: tuple[dict[str, Any], list[dict[str, Any]]],
        head_policy: tuple[dict[str, Any], list[dict[str, Any]]],
        touched_modules: set[str],
        renames: Renames,
    ) -> None:
        self.scans = scans
        self.base_ledger = base_ledger
        self.head_ledger = head_ledger
        self.base_policy = base_policy
        self.head_policy = head_policy
        self.touched_modules = touched_modules
        self.renames = renames

    def report(self) -> CompareReport:
        report = CompareReport()
        self.findings(report)
        self.ledger_lines(report)
        self.touched(report)
        report.policy = self.policy_changes()
        self.triples(report)
        self.growth(report)
        self.incomplete(report)
        return report

    def reference(self) -> ScanSummary | None:
        """Base code under base policy when the base scanner ran, else under the head policy."""
        return self.scans.base or self.scans.base_head

    def findings(self, report: CompareReport) -> None:
        reference = self.reference()
        if reference is None:
            return
        head = self.scans.head.findings
        report.resolved = sorted(reference.findings - head)
        report.new = sorted(head - reference.findings)

    def ledger_lines(self, report: CompareReport) -> None:
        base, head = self.base_ledger.lines, self.head_ledger.lines
        added = [(key, head[key] - base.get(key, 0)) for key in sorted(head) if head[key] > base.get(key, 0)]
        removed = [(key, base[key] - head.get(key, 0)) for key in sorted(base) if base[key] > head.get(key, 0)]
        growth = self.scans.head.growth
        unmatched_removed = list(removed)
        for key, count in added:
            gkey = growth_key(key, growth)
            partner = next((r for r in unmatched_removed if growth_key(r[0], growth) == gkey and r[1] == count), None)
            if gkey is not None and partner is not None:
                unmatched_removed.remove(partner)
                report.swaps.append(Swap(gkey, partner[0], key, count))
                continue
            report.ledger_added.append((key, count))
        report.ledger_removed = unmatched_removed

    def touched(self, report: CompareReport) -> None:
        if not self.touched_modules:
            return
        for key, count in sorted(self.head_ledger.lines.items()):
            if any(column in self.touched_modules for column in key[1]):
                report.touched_debt.append((key, count))

    def policy_changes(self) -> list[PolicyChange]:
        before = policy_entries(*self.base_policy)
        after = policy_entries(*self.head_policy)
        evidence = None
        if self.scans.base is not None and self.scans.base_head is not None and self.scans.head_base is not None:
            evidence = Evidence()
            evidence.add(self.scans.base, self.scans.base_head)
            evidence.add(self.scans.head_base, self.scans.head)
        classifier = PolicyClassifier(evidence)
        out = []
        for entry in sorted(set(before) | set(after)):
            if before.get(entry) == after.get(entry):
                continue
            change = "added" if entry not in before else "removed" if entry not in after else "changed"
            label, why = classifier.label(entry, change)
            out.append(PolicyChange(entry, change, label, why))
        return out

    def triples(self, report: CompareReport) -> None:
        reference = self.scans.base or self.scans.base_head
        if reference is None:
            return
        report.new_triples = sorted(self.scans.head.triples - reference.triples)

    def growth(self, report: CompareReport) -> None:
        if self.scans.base is None or self.scans.head_base is None:
            report.growth = GrowthResult(skipped="the base commit has no hoglint scanner")
            return
        report.growth = AttributionCheck(self.scans.base, self.scans.head_base, self.scans.head, self.renames).run()

    def incomplete(self, report: CompareReport) -> None:
        reference = self.reference()
        before = set(reference.incomplete) if reference is not None else set()
        for (rule, key), reason in sorted(self.scans.head.incomplete.items()):
            row = (rule, key, reason)
            if (rule, key) in before:
                report.inherited.append(row)
            else:
                report.introduced.append(row)
