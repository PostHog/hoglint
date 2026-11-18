"""What `check` prints and its exit code, read from a run summary.

The hook's view leads with a verdict and the new findings counted per rule, then the guard line,
then each problem that is not a finding with its fix, then the first finding of each rule in full
(its headline, its place, why the rule exists, the ways out and their safety), then a few more
headlines, and names the commands for the rest. A harness can cut hook output to a short preview,
so what a reader must see comes first and the whole stays under `CAP` characters. The full view
(`--all`) prints every row, and `--rule` the rows of one rule.
"""

from __future__ import annotations

import datetime
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from hoglint.config.model import Config, RuleSpec
from hoglint.labels import Labeler
from hoglint.ledger.equality import EqualityResult
from hoglint.ledger.file import LineChange, LineKey, change_row, is_level_line, render_key
from hoglint.ledger.obligations import BLOCKING, Attribution, Inherited, Labels
from hoglint.ledger.records import FindingRecord, IncompleteRecord, touches
from hoglint.ledger.scan import ScanSummary
from hoglint.model import FindingStatus, Graph, IncompleteCause, IncompleteKind
from hoglint.plugins import REGISTRY
from hoglint.rules.page import SAFETY_LABELS, way_lines
from hoglint.selectors import State

# The causes `RunResult.blocking_incomplete` reads. Keep the two equal, or check and compare exit differently.
BLOCKING_CAUSES = frozenset({IncompleteCause.INTRODUCED, IncompleteCause.EXTRACTOR})
# The causes `blocking_unknowns` gives when a baseline labels the unknowns; without one it gives
# `BLOCKING_CAUSES`.
BLOCKING_LABELS = frozenset(
    {Attribution.INTRODUCED.value, Attribution.AMBIGUOUS.value, IncompleteCause.EXTRACTOR.value}
)
# Why a blocking unknown blocks, by each cause `blocking_unknowns` can give. The unattributed
# `IncompleteCause.INTRODUCED` has the same value as `Attribution.INTRODUCED`.
CAUSE_WHY = {
    Attribution.INTRODUCED.value: "This commit adds code that hoglint cannot judge, so the commit cannot pass.",
    Attribution.AMBIGUOUS.value: "hoglint cannot tell if this commit added this unknown, so the commit cannot pass.",
    IncompleteCause.EXTRACTOR.value: (
        "A part of hoglint failed on this code, so hoglint cannot judge it. The fault is in hoglint, not in the code."
    ),
}
# The most the hook's view prints, under a harness's cap on hook output. Problems stop at
# `PROBLEMS_BUDGET`, so a first finding starts in a harness's short preview; findings stop where
# the closing lines still fit.
CAP = 8000
PROBLEMS_BUDGET = 1800
TAIL = 1000
FINDINGS_BUDGET = CAP - TAIL
FIRST_RULES = 6
MORE_HEADLINES = 4
ROWS_SHOWN = 5
GROUPS_SHOWN = 10
NOTES_SHOWN = 3
PAGE = "What the hook checks and what to do: hoglint help hook."
CARRIED = "Already {where} and not in step with the ledger: {parts}. {then}"
SHRINK = "run `hoglint ledger shrink`, then stage {ledger}. It removes stale lines and never adds one."
WRITE_MOVES = (
    "run `hoglint check --write-ledger`, then stage {ledger}. It records the new places and removes stale lines. "
    "It refuses new debt."
)

# What a reader of a blocking unknown does, by its kind: (title, ways).
KIND_WORDS: dict[IncompleteKind, tuple[str, list[tuple[str, str]]]] = {
    IncompleteKind.UNRESOLVED: (
        "names something that hoglint cannot find",
        [
            ("safe", "If the name is wrong, correct it."),
            ("person", "If the name is right, stop and tell the person. The config declares external packages."),
        ],
    ),
    IncompleteKind.NOT_MODELLED: (
        "goes through a value that hoglint does not follow (a computed name, a variable or a call)",
        [
            ("safe", "If the set of names is fixed, write each name out."),
            (
                "person",
                "If the code must compute the name, stop and tell the person. The config declares such patterns.",
            ),
        ],
    ),
    IncompleteKind.UNKNOWN_LABEL: (
        "reads a value that the config does not set",
        [
            ("care", "Put the code where the config gives it its labels, as the code next to it."),
            ("person", "If the code belongs where it is, stop and tell the person. The config must set the value."),
        ],
    ),
    IncompleteKind.PRODUCER_FAILED: (
        "has no facts, because a part of hoglint failed",
        [("person", "Stop and tell the person. CI runs the full check.")],
    ),
    IncompleteKind.UNREADABLE: (
        "has no facts, because hoglint cannot read the file",
        [("safe", "Make the file valid Python in UTF-8.")],
    ),
    IncompleteKind.STALE_NAME: (
        "is named by the config, and the tree no longer has it",
        [("person", "Stop and tell the person. The config must change with the code that moved or removed the name.")],
    ),
}


def plural(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


@dataclass(frozen=True, slots=True)
class Attributed:
    """Incomplete items labelled against a baseline summary; `against` names the baseline.
    `inherited` names the new findings and ledger lines that HEAD has already, for a staged run
    judged against HEAD (see `Inherited`); None for a run judged against its ledger alone. `where`
    names the commits they come from (HEAD, the parents of a merge, compare's base), and `then`
    who judges them instead."""

    labels: Labels
    against: str
    inherited: Inherited | None = None
    where: str = "at HEAD"
    then: str = "CI judges them; the ledger catches up there."

    @property
    def inherited_keys(self) -> frozenset[LineKey]:
        return self.inherited.keys if self.inherited is not None else frozenset()

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


@dataclass(frozen=True, slots=True)
class Problem:
    """Something other than a finding that blocks: a title that stands alone, and the lines under it."""

    title: str
    lines: tuple[str, ...]

    def render(self) -> list[str]:
        return [self.title, *(f"  {line}" for line in self.lines)]


@dataclass(frozen=True, slots=True)
class View:
    """Which rows a report prints: the hook's view, every row (`all`), or the rows of one `rule`.
    `rerun` is the command that printed it, which the output names for more rows."""

    rerun: str = "hoglint check"
    all: bool = False
    rule: str = ""


# The view of a report no command line asked for: the hook's, as `check` names itself.
HOOK_VIEW = View()


@dataclass(frozen=True, slots=True)
class Policy:
    """What the report reads of the config: each rule's words, and the files the guard line names."""

    rules: dict[str, RuleSpec]
    ledger: str
    exceptions: str
    config: str
    # The levels whose claim is an input list a person keeps, so a file it leaves out is added there.
    claims_in_lists: frozenset[str] = frozenset()

    @staticmethod
    def of(config: Config, config_file: str) -> Policy:
        in_lists = frozenset(
            name
            for name, level in config.levels.items()
            if getattr(REGISTRY.coverage_adapters.get(str(level.claim_option("adapter"))), "adds_paths", False)
        )
        return Policy(dict(config.rules), config.ledger.file, config.ledger.exceptions, config_file, in_lists)


class ReadTimeOwners:
    """A finding's owners, looked up when it is rendered, from the owners files under `root`.
    Summaries never store owners (D12), so an owners file edit needs no new summary."""

    def __init__(self, config: Config, modules: dict[str, str], root: Path) -> None:
        self.field = config.roles.owner
        self.labeler = Labeler(config, Graph(modules=dict(modules)), root)

    def of_module(self, owner: str) -> tuple[str, ...]:
        """The owners of a finding whose owner module is `owner` (empty for none)."""
        if not self.field or not owner:
            return ()
        value, _ = self.labeler.field_value(owner, None, self.field)
        if value.state is State.KNOWN and isinstance(value.data, frozenset):
            return tuple(sorted(value.data))
        return ()


class CheckReport:
    """What `check` prints and its exit code, read from a run summary: the one a live run makes,
    or the stored one that stands in for the run. Owners and the day are read when it renders."""

    def __init__(
        self,
        summary: ScanSummary,
        owners: ReadTimeOwners,
        today: datetime.date,
        policy: Policy,
        view: View,
        touched: set[str] | None = None,
        attributed: Attributed | None = None,
        grown: list[LineChange] | None = None,
        moved: frozenset[LineKey] = frozenset(),
    ) -> None:
        """`moved` names the scan's lines that only changed place against HEAD's ledger: a new
        finding on one of them asks for the ledger to be written, not for a fix."""
        self.summary = summary
        self.owners = owners
        self.today = today
        self.policy = policy
        self.rerun = view.rerun
        self.touched = touched
        self.attributed = attributed
        self.inherited = attributed.inherited if attributed is not None else None
        self.inherited_keys = attributed.inherited_keys if attributed is not None else frozenset()
        self.grown = grown or []
        self.moved = moved
        self.show_all = view.all or bool(view.rule) or touched is not None
        self.rule = view.rule

    # ── rows ──

    def keep(self, finding: FindingRecord) -> bool:
        if self.touched is None:
            return True
        return touches(self.touched, (witness.path for witness in finding.witnesses), finding.subjects)

    def new(self) -> list[FindingRecord]:
        return [f for f in self.summary.findings.with_status(FindingStatus.NEW) if self.keep(f)]

    def blocking(self) -> list[FindingRecord]:
        """The new findings of rules that are not advisory, without those HEAD has already."""
        return [f for f in self.new() if f.rule not in self.summary.advisory and f.key not in self.inherited_keys]

    def violations(self) -> list[FindingRecord]:
        return [f for f in self.blocking() if f.key not in self.moved]

    def moved_findings(self) -> list[FindingRecord]:
        return [f for f in self.blocking() if f.key in self.moved]

    def inherited_findings(self) -> list[FindingRecord]:
        return [f for f in self.new() if f.rule not in self.summary.advisory and f.key in self.inherited_keys]

    def equality(self) -> EqualityResult | None:
        """The ledger's equality with the scan, without what HEAD has already: the lines of new
        findings HEAD has, and the lines out of step at HEAD the same way."""
        equality = self.summary.equality
        inherited = self.inherited
        if equality is None or inherited is None:
            return equality
        new = [d for d in equality.new if d.key not in inherited.keys and d not in inherited.lines]
        stale = [d for d in equality.stale if d not in inherited.lines]
        return EqualityResult(new, stale, equality.header)

    def advisory(self) -> list[FindingRecord]:
        return [f for f in self.new() if f.rule in self.summary.advisory]

    def causes(self) -> list[str]:
        items = self.summary.incomplete
        if self.attributed is None:
            return [item.cause.value for item in items]
        return self.attributed.causes(items)

    def unknowns(self) -> list[tuple[IncompleteRecord, str]]:
        """Each incomplete item with its cause."""
        return list(zip(self.summary.incomplete, self.causes(), strict=True))

    def blocking_unknowns(self) -> list[tuple[IncompleteRecord, str]]:
        blocking = set(BLOCKING_LABELS)
        if self.attributed is None:
            blocking = {cause.value for cause in BLOCKING_CAUSES}
        return [(item, cause) for item, cause in self.unknowns() if cause in blocking]

    # ── exit code ──

    def exit_code(self) -> int:
        """`check` without `--changed`: 2 for a blocking incomplete result, 1 when the run failed."""
        if self.attributed is not None and self.attributed.blocking:
            return 2
        if any(item.cause in BLOCKING_CAUSES for item in self.summary.incomplete):
            return 2
        return 1 if self.failed() else 0

    def failed(self) -> bool:
        """A new finding, a ledger out of step with the scan, an exception that fails, a failed
        claim or ledger growth, without what the baseline has already."""
        summary = self.summary
        equality = self.equality()
        unequal = equality is not None and not equality.equal
        exceptions_failed = summary.exceptions.stale or summary.exceptions.problems
        claims_failed = summary.failed_claims() or summary.claim_errors
        return bool(self.violations() or unequal or exceptions_failed or claims_failed or self.grown)

    # ── words ──

    def headline(self, finding: FindingRecord) -> str:
        shown = finding.shown
        if shown is None:
            raise ValueError(f"the summary keeps no message for {finding.rule} {' '.join(finding.identity)}")
        return f"{finding.rule}: {shown.message}"

    def place(self, finding: FindingRecord) -> list[str]:
        shown = finding.shown
        site = f"{shown.path}:{shown.line}" if shown is not None and shown.path else ""
        owners = self.owners.of_module(finding.owner)
        if not site and not owners:
            return []
        listed = f"  [{', '.join(owners)}]" if owners else ""
        return [f"  at {site or '(no line)'}{listed}"]

    def finding_block(self, finding: FindingRecord, full: bool) -> list[str]:
        """A finding's headline and place; with `full`, its rule's why and ways out too."""
        out = [self.headline(finding), *self.place(finding)]
        if not full:
            return out
        if finding.unknown:
            out.append("  note: hoglint cannot tell part of this case. If a name in it is wrong, correct the name.")
        spec = self.policy.rules.get(finding.rule)
        if spec is not None:
            out.extend(f"  {line}" for line in way_lines(spec))
        out.append(f"  see: hoglint explain {finding.rule}")
        return out

    def unknown_line(self, item: IncompleteRecord, cause: str) -> str:
        place = item.place()
        at = f" {place}:" if place else ""
        return f"  {item.rule} [{cause}, {item.kind.value}]{at} {item.reason}"

    def guard(self) -> str:
        policy = self.policy
        return (
            "Fix each finding, or stop and tell the person. Do not edit "
            f"{policy.ledger}, {policy.exceptions}, {policy.config} or a preset to make this check pass."
        )

    # ── problems that are not findings ──

    def cut(self, rows: list[str], shown: int) -> list[str]:
        """The first `shown` rows in the hook's view and a line that counts the rest; every row in the full view."""
        rows = [row.replace("\t", " ") for row in rows]
        if self.show_all or len(rows) <= shown:
            return rows
        return [*rows[:shown], f"and {len(rows) - shown} more: {self.rerun} --all"]

    def unknown_problems(self) -> list[Problem]:
        """One problem per rule, kind and cause of the blocking unknowns, a row per place: the ways
        out are the same for each place of a kind."""
        groups: dict[tuple[str, IncompleteKind, str], list[IncompleteRecord]] = {}
        for item, cause in self.blocking_unknowns():
            groups.setdefault((item.rule, item.kind, cause), []).append(item)
        out = []
        for (rule, kind, cause), items in groups.items():
            title, ways = KIND_WORDS[kind]
            subject = "this place" if len(items) == 1 else f"each of these {len(items)} places"
            rows = [f"at {item.place() or item.key}: {item.reason}" for item in items]
            lines = [
                *self.cut(rows, ROWS_SHOWN),
                f"why: {CAUSE_WHY[cause]}",
                *(f"{SAFETY_LABELS[safety]}: {text}" for safety, text in ways),
            ]
            if rule in self.policy.rules:
                lines.append(f"see: hoglint explain {rule}")
            out.append(Problem(f"{rule}: {subject} {title}.", tuple(lines)))
        return out

    def ledger_problems(self) -> list[Problem]:
        ledger = self.policy.ledger
        equality = self.equality()
        out = []
        moved = self.moved_findings()
        if moved:
            rows = self.cut([f"moved  {render_key(finding.key)}" for finding in moved], ROWS_SHOWN)
            why = "why: a ledger line names the place of a finding. A move to a new place adds no debt."
            fix = f"{SAFETY_LABELS['safe']}: {WRITE_MOVES.format(ledger=ledger)}"
            title = f"{plural(len(moved), 'finding')} moved, and {ledger} records the old place."
            out.append(Problem(title, (*rows, why, fix)))
        if equality is not None and not equality.equal:
            out.extend(
                Problem(f"{ledger}: {header}.", ("needs a person: stop and tell the person.",))
                for header in equality.header
            )
            if equality.stale:
                stale = [f"stale  {render_key(d.key)}  {d.recorded} -> {d.scanned}" for d in equality.stale]
                rows = self.cut(stale, ROWS_SHOWN)
                why = "why: the ledger must equal the scan, so a fixed finding leaves the ledger."
                fix = f"{SAFETY_LABELS['safe']}: {SHRINK.format(ledger=ledger)}"
                out.append(
                    Problem(
                        f"{ledger} records {plural(len(equality.stale), 'finding')} that the code no longer has.",
                        (*rows, why, fix),
                    )
                )
            levels = [d for d in equality.new if is_level_line(d.key)]
            if levels:
                rows = [f"new  {render_key(d.key)}".replace("\t", " ") for d in levels]
                fix = (
                    f"{SAFETY_LABELS['safe']}: run `hoglint check --write-ledger`, then stage {ledger}. "
                    "A level line records a group that passes a level, and it is no debt."
                )
                out.append(
                    Problem(
                        f"{ledger} does not record {plural(len(levels), 'group')} that now pass a level.", (*rows, fix)
                    )
                )
        if self.grown:
            rows = self.cut([change_row(change) for change in self.grown], ROWS_SHOWN)
            fix = f"{SAFETY_LABELS['person']}: new debt needs a person. Remove the lines and fix the findings, or stop and tell the person."
            out.append(
                Problem(
                    f"This change adds {plural(len(self.grown), 'line')} to {ledger}, with no policy change.",
                    (*rows, fix),
                )
            )
        return out

    def exception_problems(self) -> list[Problem]:
        exceptions = self.policy.exceptions
        summary = self.summary
        out = [
            Problem(
                f"{exceptions}: {label} matches no finding.",
                (f"{SAFETY_LABELS['safe']}: remove the exception. The finding that it allowed is gone.",),
            )
            for label in summary.exceptions.stale
        ]
        person = (
            f"{SAFETY_LABELS['person']}: a person writes and reviews exceptions. "
            "Do not write or complete one to make this check pass."
        )
        out.extend(Problem(f"{problem}.", (person,)) for problem in summary.exceptions.problems)
        return out

    def claim_ways(self, name: str, where: str) -> list[str]:
        """Dropping the claim needs a person: it takes the product out of isolation, which its owners
        decide. A claim that narrows an input list a person keeps can also watch the file a reason
        names; a claim read from a key has no list, since hoglint derives the watched files."""
        rows = f"`{self.rerun} --rule <rule>` lists the rows of a rule."
        if name not in self.policy.claims_in_lists:
            return [
                f"{SAFETY_LABELS['care']}: Fix each reason in the code. {rows}",
                f"{SAFETY_LABELS['person']}: To remove the claim in {where}, stop and tell the person. The group "
                "then claims no level, its full test suite runs on every change, and the product is no longer isolated.",
                "No ledger line or exception accepts a failed claim.",
            ]
        return [
            f"{SAFETY_LABELS['safe']}: If a reason names a file that is not watched, add the file to the "
            f"watched inputs in {where}. This can only run more tests, never fewer.",
            f"{SAFETY_LABELS['care']}: Fix each other reason in the code. {rows}",
            f"{SAFETY_LABELS['person']}: To drop the narrowing in {where}, stop and tell the person. The group then "
            "claims no level, its full test suite runs on every change, and the product is no longer isolated.",
            "No ledger line or exception accepts a failed claim.",
        ]

    def claim_problems(self) -> list[Problem]:
        """In the full view, one problem per failed claim with each reason; in the hook's view, one
        problem for all of them, a row per group with its reasons counted by rule."""
        failed = self.summary.failed_claims()
        out = []
        if self.show_all:
            for name, group, where, reasons in failed:
                title = f"{group} claims the level {name} in {where}, and fails it ({plural(len(reasons), 'reason')})."
                out.append(Problem(title, (*reasons, *self.claim_ways(name, where))))
        elif failed:
            rows = []
            for name, group, where, reasons in failed:
                counts = Counter(reason.split(":", 1)[0] for reason in reasons)
                by_rule = ", ".join(f"{count} x {rule}" for rule, count in counts.most_common())
                rows.append(f"{group} claims {name} in {where}: {by_rule}")
            verb = "claims a level and fails it" if len(failed) == 1 else "claim a level and fail it"
            title = f"{plural(len(failed), 'group')} {verb}."
            where = failed[0][2] if len(failed) == 1 else "the file that the row of the group names"
            out.append(Problem(title, (*self.cut(rows, GROUPS_SHOWN), *self.claim_ways(failed[0][0], where))))
        for name, error in sorted(self.summary.claim_errors.items()):
            person = f"{SAFETY_LABELS['person']}: stop and tell the person."
            out.append(Problem(f"hoglint cannot read the claims of the level {name}: {error}.", (person,)))
        return out

    def problems(self) -> list[Problem]:
        return [*self.unknown_problems(), *self.ledger_problems(), *self.exception_problems(), *self.claim_problems()]

    def notes(self) -> list[str]:
        """What a reader should know and what blocks nothing: exceptions past their review date."""
        exceptions = self.policy.exceptions
        notes = [
            f"note: {exceptions}: {label} is past its review date {review_by}. A person reviews it; this does not block."
            for review_by, label in self.summary.exceptions.expired(self.today)
        ]
        return self.cut(notes, NOTES_SHOWN)

    def carried(self) -> list[str]:
        """The line that names what HEAD has already and this commit is not blocked on; none when
        there is nothing."""
        findings = len(self.inherited_findings())
        lines = len(self.inherited.lines) if self.inherited is not None else 0
        parts = [plural(findings, "finding")] if findings else []
        parts += [plural(lines, "ledger line")] if lines else []
        if self.attributed is None or not parts:
            return []
        return [CARRIED.format(where=self.attributed.where, parts=", ".join(parts), then=self.attributed.then)]

    # ── views ──

    def counts(self) -> tuple[int, int, int]:
        """(advisory rows, old unknowns, old findings in the ledger)."""
        old_unknowns = len(self.summary.incomplete) - len(self.blocking_unknowns())
        return len(self.advisory()), old_unknowns, self.summary.findings.count(FindingStatus.ACCEPTED)

    def verdict(self, violations: list[FindingRecord], problems: list[Problem]) -> str:
        parts = [plural(len(violations), "new finding")] if violations else []
        parts += [plural(len(problems), "other problem")] if problems else []
        return f"hoglint: this commit is blocked: {', '.join(parts)}."

    def by_rule(self, violations: list[FindingRecord]) -> list[str]:
        out = ["New findings by rule:"]
        out.extend(f"  {count:4d}  {rule}" for rule, count in Counter(f.rule for f in violations).most_common())
        return out

    def render(self) -> str:
        if self.show_all:
            return self.render_all()
        violations = self.violations()
        problems = self.problems()
        advisory, old_unknowns, accepted = self.counts()
        notes = self.notes()
        if not violations and not problems:
            line = f"hoglint: no new problems ({advisory} advisory rows, {old_unknowns} old unknowns, {accepted} old findings in the ledger)."
            return "\n".join([line, *self.carried(), *notes]) + "\n"
        out = [self.verdict(violations, problems)]
        if violations:
            out.extend(self.by_rule(violations))
        out.extend([self.guard(), ""])
        self.first_problems(problems, out)
        shown = self.first_findings(violations, out)
        rest = len(violations) - shown
        hidden = f"{rest} more findings, " if rest else ""
        out.append(
            f"Not shown: {hidden}{advisory} advisory rows, {old_unknowns} old unknowns, {accepted} old findings in the ledger. "
            f"Show every row: {self.rerun} --all. Show one rule: {self.rerun} --rule <rule>."
        )
        out.extend(self.carried())
        out.extend(notes)
        out.append(PAGE)
        return "\n".join(out) + "\n"

    def first_problems(self, problems: list[Problem], out: list[str]) -> None:
        """Appends the problems in full within their budget, at least one, then the titles of a few
        more, and a line that counts the rest."""
        size = len("\n".join(out))
        printed = 0
        for problem in problems:
            block = problem.render()
            if printed and size + len("\n".join(block)) > PROBLEMS_BUDGET:
                break
            out.extend([*block, ""])
            size += len("\n".join(block)) + 2
            printed += 1
        titles = [problem.title for problem in problems[printed:]]
        if not titles:
            return
        out.extend(["More problems:", *self.cut(titles, MORE_HEADLINES), ""])

    def first_findings(self, violations: list[FindingRecord], out: list[str]) -> int:
        """Appends the first finding of each rule in full, then a few more headlines, within the
        budget; returns how many findings it printed."""
        firsts: dict[str, FindingRecord] = {}
        for finding in violations:
            firsts.setdefault(finding.rule, finding)
        printed: list[FindingRecord] = []
        size = len("\n".join(out))
        if firsts:
            out.append(
                f"The first finding of each rule ({min(len(firsts), FIRST_RULES)} of {len(violations)} findings):"
            )
        for finding in list(firsts.values())[:FIRST_RULES]:
            block = self.finding_block(finding, full=True)
            if printed and size + len("\n".join(block)) > FINDINGS_BUDGET:
                break
            out.extend([*block, ""])
            size += len("\n".join(block)) + 2
            printed.append(finding)
        more: list[str] = []
        added = 0
        for finding in [f for f in violations if not any(f is p for p in printed)][:MORE_HEADLINES]:
            block = self.finding_block(finding, full=False)
            size += len("\n".join(block)) + 1
            if size > FINDINGS_BUDGET:
                break
            more.extend(block)
            added += 1
        if more:
            out.extend(["More findings:", *more, ""])
        return len(printed) + added

    def render_all(self) -> str:
        """Every row, or the rows of `rule`: the findings with their rule's words once, the
        advisory rows, the unknowns, the problems and the counts."""
        violations = [f for f in self.violations() if not self.rule or f.rule == self.rule]
        problems = self.problems()
        out = [self.verdict(violations, problems) if violations or problems else "hoglint: no new problems."]
        if violations:
            out.extend(self.by_rule(violations))
            out.append(self.guard())
        for problem in problems:
            out.extend(problem.render())
        out.append(f"new findings ({len(violations)})")
        seen: set[str] = set()
        for finding in violations:
            full = finding.rule not in seen
            seen.add(finding.rule)
            out.extend(f"  {line}" for line in self.finding_block(finding, full=full))
        advisory = [f for f in self.advisory() if not self.rule or f.rule == self.rule]
        out.append(f"advisory rows ({len(advisory)})")
        for finding in advisory:
            out.extend(f"  {line}" for line in self.finding_block(finding, full=False))
        if self.touched is not None:
            accepted = [f for f in self.summary.findings.with_status(FindingStatus.ACCEPTED) if self.keep(f)]
            out.append(f"existing debt in touched files (advisory) ({len(accepted)})")
            for finding in accepted:
                out.extend(f"  {line}" for line in self.finding_block(finding, full=False))
        unknowns = [(item, cause) for item, cause in self.unknowns() if not self.rule or item.rule == self.rule]
        out.append(f"unknowns ({len(unknowns)})")
        out.extend(self.unknown_line(item, cause) for item, cause in unknowns)
        if self.attributed is not None:
            counts = Counter(self.causes())
            tally = ", ".join(
                f"{counts[label.value]} {label.value}"
                for label in Attribution
                if counts[label.value] or label in BLOCKING
            )
            out.append(f"unknowns attributed against {self.attributed.against}: {tally}")
        summary = self.summary
        for name, passing in summary.levels.items():
            out.append(f"level {name}: {len(passing)} pass, {len(summary.level_failures.get(name, {}))} fail")
        out.append(f"{summary.findings.count(FindingStatus.ACCEPTED)} findings accepted by the ledger")
        out.extend(self.carried())
        out.extend(self.notes())
        if violations or problems:
            out.append(PAGE)
        return "\n".join(out) + "\n"
