"""`hoglint compare --base <ref>`: what a change does to findings, debt and policy.

It lists apart:
- findings resolved and findings new,
- ledger lines added and removed; an added and a removed line on one growth key with the same
  count is a zero-net swap and shows as one,
- existing debt in the modules the change touches (advisory),
- policy entries changed, each labelled tighten or relax by running both policies on both
  snapshots (anything hoglint cannot classify counts as relax),
- new (package pair, edge kind, context) triples,
- growth by attribution, debt the new engine, policy or environment no longer sees, and
  incomplete results split into introduced, ambiguous and inherited.

A base that has hoglint but whose engine cannot run is incomplete (exit 2), never a skip. Only a
base that predates hoglint skips the growth check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hoglint.config.schema import canonical
from hoglint.ledger.file import LedgerFile, LineKey, render_key
from hoglint.ledger.growth import AttributionCheck, GrowthItem, GrowthKey, GrowthResult, Renames, growth_key
from hoglint.ledger.obligations import BLOCKING, Attribution, Labels, attribute, rows
from hoglint.ledger.scan import Obligation, ScanSummary, Triple

EXCEPTION_ENTRY = "exception"
FILE_ENTRY = "file"
RULE_SECTION = "rules."
# The rows each section of compare's short view shows.
SHOWN_ROWS = 10
LEVEL_SECTION = "levels."


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


@dataclass(frozen=True, slots=True)
class RawPolicy:
    """One side's policy as written: the expanded config, the exception entries, and the content
    digest of each policy data file."""

    config: dict[str, Any] = field(default_factory=dict)
    exceptions: list[dict[str, Any]] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=False, slots=True)
class Scans:
    """head: head code, head policy. base_head: base code, head policy (same engine).
    base and head_base use the base engine and base policy; None when the base has no hoglint or
    its engine could not run (`base_failure` says which)."""

    head: ScanSummary
    base_head: ScanSummary | None = None
    base: ScanSummary | None = None
    head_base: ScanSummary | None = None
    base_failure: str = ""
    base_source: str = ""


def growth_row(item: GrowthItem) -> str:
    return f"{item.rule} {' '.join(item.key)}: {item.before} -> {item.after}"


UNWATCHED_TITLE = "files that stop being watched (advisory: a change to one no longer runs the full suite)"


@dataclass(frozen=False, slots=True)
class CompareReport:
    reference: str = ""
    base_failure: str = ""
    base_source: str = ""
    resolved: list[LineKey] = field(default_factory=list)
    new: list[LineKey] = field(default_factory=list)
    ledger_added: list[tuple[LineKey, int]] = field(default_factory=list)
    ledger_removed: list[tuple[LineKey, int]] = field(default_factory=list)
    swaps: list[Swap] = field(default_factory=list)
    touched_debt: list[tuple[LineKey, int]] = field(default_factory=list)
    policy: list[PolicyChange] = field(default_factory=list)
    new_triples: list[Triple] = field(default_factory=list)
    growth: GrowthResult = field(default_factory=GrowthResult)
    labels: Labels = field(default_factory=dict)
    # Files a group watches at the base and no longer at the head, under the head's policy: the
    # test selection then skips more for them. Advisory (hoglint/watched.py `stopped_watching`).
    unwatched: list[str] = field(default_factory=list)

    def incomplete(self, label: Attribution) -> list[Obligation]:
        return [obligation for attribution, obligation in rows(self.labels) if attribution is label]

    @property
    def blocking(self) -> bool:
        return bool(self.base_failure) or any(label in BLOCKING for items in self.labels.values() for label in items)

    def exit_code(self, head_failed: bool, head_blocking: bool = False) -> int:
        if self.blocking or head_blocking:
            return 2
        return 1 if head_failed or self.growth.failed else 0

    def verdict(self, code: int) -> str:
        """What the comparison decides, and what fails in it."""
        fails = []
        if self.base_failure:
            fails.append("the base scan failed")
        if self.growth.code_growth:
            fails.append(f"{len(self.growth.code_growth)} growth keys grew from code")
        unknowns = len(self.incomplete(Attribution.INTRODUCED)) + len(self.incomplete(Attribution.AMBIGUOUS))
        if unknowns:
            fails.append(f"{unknowns} new unknowns")
        if code and not fails:
            fails.append("the head check below fails")
        verdict = {0: "passes", 1: "fails", 2: "cannot be decided"}[code]
        return f"hoglint compare: the change {verdict}" + (f": {'; '.join(fails)}." if fails else ".")

    def render(self, code: int, rerun: str) -> str:
        """The comparison as CI's reader needs it: the verdict, what fails, what a person reviews,
        and one line of counts. Each section shows its first rows; `render_all` shows them all."""
        out: list[str] = [self.verdict(code)]
        if self.base_failure:
            out.append(f"base scan failed (blocks): {self.base_failure}")
        if self.base_source:
            out.append(f"base summary: {self.base_source}")
        if self.growth.skipped:
            out.append(f"growth check skipped: {self.growth.skipped}")

        def section(title: str, rows: list[str]) -> None:
            if not rows:
                return
            out.append(f"{title} ({len(rows)})")
            out.extend(f"  {row}" for row in rows[:SHOWN_ROWS])
            if len(rows) > SHOWN_ROWS:
                out.append(f"  and {len(rows) - SHOWN_ROWS} more: {rerun} --all")

        section("code growth (fails)", [growth_row(g) for g in self.growth.code_growth])
        for label, title in (
            (Attribution.INTRODUCED, "incomplete, introduced (blocks)"),
            (Attribution.AMBIGUOUS, "incomplete, ambiguous replacement (blocks)"),
        ):
            section(title, [f"{rule}: {reason}" for rule, _, reason in self.incomplete(label)])
        review = [
            ("admitted moves (review)", [growth_row(g) for g in self.growth.admitted_moves]),
            (
                "code growth a new exception allows (needs a person's review)",
                [growth_row(g) for g in self.growth.excepted],
            ),
            ("policy entries changed", [f"{c.label:8} {c.change:8} {c.entry}  ({c.why})" for c in self.policy]),
            (
                "debt the new scanner, policy or environment no longer sees (review)",
                [f"{growth_row(g)}  [{'+'.join(self.growth.shrink_causes)}]" for g in self.growth.shrink],
            ),
        ]
        if any(rows for _, rows in review):
            out.append("Needs a person's review (it passes here; the owners of the files hold the gate):")
            for title, rows in review:
                section(title, rows)
        section(UNWATCHED_TITLE, self.unwatched)
        out.append(
            f"counts: findings new {len(self.new)}, resolved {len(self.resolved)}; ledger lines added "
            f"{len(self.ledger_added)}, removed {len(self.ledger_removed)}; zero-net swaps {len(self.swaps)}; "
            f"rule growth {len(self.growth.rule_growth)}; new group edges {len(self.new_triples)}; old unknowns "
            f"{len(self.incomplete(Attribution.INHERITED))}. Show every section: {rerun} --all."
        )
        return "\n".join(out) + "\n\n"

    def render_all(self) -> str:
        out: list[str] = []

        def section(title: str, rows: list[str]) -> None:
            out.append(f"{title} ({len(rows)})")
            out.extend(f"  {row}" for row in rows)

        if self.base_failure:
            out.append(f"base scan failed (blocks): {self.base_failure}")
        if self.base_source:
            out.append(f"base summary: {self.base_source}")
        if self.reference:
            out.append(f"findings and new pairs are against {self.reference}")
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
        section("new group edges (pair, kind, context)", [" ".join(t) for t in self.new_triples])
        section(UNWATCHED_TITLE, self.unwatched)
        if self.growth.skipped:
            out.append(f"growth check skipped: {self.growth.skipped}")
        section("code growth (fails)", [growth_row(g) for g in self.growth.code_growth])
        section("admitted moves (review)", [growth_row(g) for g in self.growth.admitted_moves])
        section(
            "code growth a new exception allows (needs a person's review)",
            [growth_row(g) for g in self.growth.excepted],
        )
        section("rule growth (passes)", [growth_row(g) for g in self.growth.rule_growth])
        causes = "+".join(self.growth.shrink_causes)
        section(
            "debt the new scanner, policy or environment no longer sees (review)",
            [f"{growth_row(g)}  [{causes}]" for g in self.growth.shrink],
        )
        for label, title in (
            (Attribution.INTRODUCED, "incomplete, introduced (blocks)"),
            (Attribution.AMBIGUOUS, "incomplete, ambiguous replacement (blocks)"),
            (Attribution.INHERITED, "incomplete, inherited (reported)"),
        ):
            section(title, [f"{rule}: {reason}" for rule, _, reason in self.incomplete(label)])
        return "\n".join(out) + "\n"


def policy_entries(policy: RawPolicy) -> dict[str, str]:
    """Each reviewable unit of policy: a named entry, a plain setting, an assignment, an exception,
    a data file."""
    out: dict[str, str] = {}
    for section, value in policy.config.items():
        if section == "assign":
            for entry in value:
                text = canonical(entry)
                out[f"assign {text}"] = text
        elif isinstance(value, dict):
            for name, entry in value.items():
                out[f"{section}.{name}"] = canonical(entry)
        else:
            out[section] = canonical(value)
    for entry in policy.exceptions:
        text = canonical(entry)
        out[f"{EXCEPTION_ENTRY} {text}"] = text
    for path, digest in policy.files.items():
        out[f"{FILE_ENTRY} {path}"] = digest
    return out


@dataclass(frozen=False, slots=True)
class Evidence:
    """What changes when only the policy changes, on base code and on head code. Findings are kept
    as ledger keys, so a finding that changes on both snapshots counts once."""

    lost: set[LineKey] = field(default_factory=set)
    gained: set[LineKey] = field(default_factory=set)
    levels_gained: set[str] = field(default_factory=set)
    levels_lost: set[str] = field(default_factory=set)

    def add(self, old: ScanSummary, new: ScanSummary) -> None:
        old_keys, new_keys = old.finding_keys(), new.finding_keys()
        self.lost |= old_keys - new_keys
        self.gained |= new_keys - old_keys
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
        if entry.startswith(RULE_SECTION):
            rule = entry.removeprefix(RULE_SECTION)
            lost = sum(1 for key in evidence.lost if key[0] == rule)
            gained = sum(1 for key in evidence.gained if key[0] == rule)
            return self.judge(lost, gained, set(), set())
        if entry.startswith(LEVEL_SECTION):
            name = entry.removeprefix(LEVEL_SECTION)
            gained = {name} & evidence.levels_gained
            lost = {name} & evidence.levels_lost
            return self.judge(0, 0, gained, lost)
        return self.judge(len(evidence.lost), len(evidence.gained), evidence.levels_gained, evidence.levels_lost)

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
        base_policy: RawPolicy,
        head_policy: RawPolicy,
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
        report = CompareReport(
            reference=self.reference_name(), base_failure=self.scans.base_failure, base_source=self.scans.base_source
        )
        self.findings(report)
        self.ledger_lines(report)
        self.touched(report)
        report.policy = self.policy_changes()
        self.triples(report)
        self.growth(report)
        self.incomplete(report)
        return report

    def reference(self) -> ScanSummary | None:
        """Base code under base policy when the base engine ran, else under the head policy."""
        return self.scans.base or self.scans.base_head

    def reference_name(self) -> str:
        if self.scans.base is not None:
            return "base code under the base policy"
        if self.scans.base_head is not None:
            return "base code under the head policy (the base has no hoglint scan)"
        return ""

    def findings(self, report: CompareReport) -> None:
        reference = self.reference()
        if reference is None:
            return
        head, before = self.scans.head.finding_keys(), reference.finding_keys()
        report.resolved = sorted(before - head)
        report.new = sorted(head - before)

    def ledger_lines(self, report: CompareReport) -> None:
        base, head = self.base_ledger.lines, self.head_ledger.lines
        added = [(key, head[key] - base.get(key, 0)) for key in sorted(head) if head[key] > base.get(key, 0)]
        removed = [(key, base[key] - head.get(key, 0)) for key in sorted(base) if base[key] > head.get(key, 0)]
        growth = self.scans.head.growth
        # A removed line's module may be gone from the head, so the base scan knows its labels.
        base_labels = self.scans.base.labels if self.scans.base is not None else {}
        labels = {name: {**base_labels.get(name, {}), **values} for name, values in self.scans.head.labels.items()}
        unmatched_removed = list(removed)
        for key, count in added:
            gkey = growth_key(key, growth, labels)
            partner = next(
                (r for r in unmatched_removed if growth_key(r[0], growth, labels) == gkey and r[1] == count), None
            )
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
        before = policy_entries(self.base_policy)
        after = policy_entries(self.head_policy)
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
        reference = self.reference()
        if reference is None:
            return
        report.new_triples = sorted(self.scans.head.triples - reference.triples)

    def growth(self, report: CompareReport) -> None:
        if self.scans.base_failure:
            report.growth = GrowthResult(skipped="the base engine could not run, so growth is unknown")
            return
        if self.scans.base is None or self.scans.head_base is None:
            report.growth = GrowthResult(skipped="the base commit predates hoglint")
            return
        report.growth = AttributionCheck(self.scans.base, self.scans.head_base, self.scans.head, self.renames).run()

    def incomplete(self, report: CompareReport) -> None:
        reference = self.reference()
        report.labels = attribute(self.scans.head.obligations(), reference.obligations() if reference else {})
