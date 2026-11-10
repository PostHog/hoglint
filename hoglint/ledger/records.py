"""Plain records of what one run found, so a stored summary can stand in for the run.

A record keeps what `check`, `query` and compare print or count, and nothing that needs the
graph: no Edge objects, no labels. Each record writes itself as a JSON list. Records are the same
on every machine and every day: text carries paths relative to the repo root (see `Portable`),
and exceptions keep their review dates, so "past review" is decided when a reader renders them.
Owners are never stored: a finding names its owner module, and a reader looks the owners up in
the snapshot's owners files when it renders them.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hoglint.ledger.equality import EqualityResult, LineDiff
from hoglint.ledger.file import LineKey, parse_key, render_key
from hoglint.model import Edge, Finding, FindingStatus, IncompleteCause
from hoglint.rules.base import IncompleteItem
from hoglint.rules.exceptions import ExceptionReport, ExceptionsFile
from hoglint.rules.levels import Block


@dataclass(frozen=True, slots=True)
class Portable:
    """Rewrites a run's absolute roots in free text to paths relative to the repo root."""

    prefixes: tuple[str, ...] = ()

    @staticmethod
    def of(*roots: Path) -> Portable:
        prefixes = {f"{form}/" for root in roots for form in (root, root.resolve())}
        return Portable(tuple(sorted(prefixes, key=len, reverse=True)))

    def text(self, value: str) -> str:
        if "/" not in value:  # every root is an absolute path; most text names modules, not files
            return value
        for prefix in self.prefixes:
            value = value.replace(prefix, "")
        return value


@dataclass(frozen=True, slots=True)
class WitnessRecord:
    kind: str
    path: str
    line: int
    column: int
    surface: str
    targets: tuple[str, ...]

    @staticmethod
    def of(edge: Edge) -> WitnessRecord:
        site = edge.site
        return WitnessRecord(edge.kind, site.path, site.line, site.column, edge.surface, tuple(map(str, edge.targets)))

    def to_json(self) -> list[Any]:
        return [self.kind, self.path, self.line, self.column, self.surface, list(self.targets)]

    @staticmethod
    def from_json(data: list[Any]) -> WitnessRecord:
        kind, path, line, column, surface, targets = data
        return WitnessRecord(kind, path, int(line), int(column), surface, tuple(targets))


@dataclass(frozen=True, slots=True)
class ShownRecord:
    """What `check` prints of a finding besides its identity: the message, site and remedies."""

    message: str
    path: str  # empty when the finding has no site
    line: int
    remedies: tuple[tuple[str, str], ...]

    @staticmethod
    def of(finding: Finding, portable: Portable) -> ShownRecord:
        site = finding.site
        return ShownRecord(
            portable.text(finding.message),
            site.path if site is not None else "",
            site.line if site is not None else 0,
            tuple((remedy.kind, remedy.detail) for remedy in finding.remedies),
        )

    def to_json(self) -> list[Any]:
        return [self.message, self.path, self.line, [list(remedy) for remedy in self.remedies]]

    @staticmethod
    def from_json(data: list[Any]) -> ShownRecord:
        message, path, line, remedies = data
        return ShownRecord(message, path, int(line), tuple((kind, detail) for kind, detail in remedies))


@dataclass(frozen=True, slots=True)
class FindingRecord:
    """One finding. `shown` is kept only for a finding `check` prints (new, not accepted by the
    run's ledger; with `--changed`, also accepted debt in touched files): making it can mean
    judging the edge again, and no reader of a stored summary prints the others. None means not
    stored, never empty; a reader that needs it runs the check."""

    rule: str
    identity: tuple[str, ...]
    status: FindingStatus
    shown: ShownRecord | None
    witnesses: tuple[WitnessRecord, ...]
    owner: str  # the module whose owners the finding takes; empty when it has none
    subjects: tuple[str, ...]
    ledgerable: bool
    unknown: bool

    @property
    def key(self) -> LineKey:
        return self.rule, self.identity

    @staticmethod
    def of(finding: Finding, portable: Portable, shown: bool) -> FindingRecord:
        return FindingRecord(
            finding.rule,
            finding.identity,
            finding.status,
            ShownRecord.of(finding, portable) if shown else None,
            tuple(WitnessRecord.of(edge) for edge in finding.witnesses),
            finding.owner.module if finding.owner is not None else "",
            finding.subjects,
            finding.ledgerable,
            finding.unknown,
        )

    def to_json(self) -> list[Any]:
        return [
            self.rule,
            list(self.identity),
            self.status.value,
            self.shown.to_json() if self.shown is not None else None,
            [witness.to_json() for witness in self.witnesses],
            self.owner,
            list(self.subjects),
            self.ledgerable,
            self.unknown,
        ]

    @staticmethod
    def from_json(data: list[Any]) -> FindingRecord:
        rule, identity, status, shown, witnesses, owner, subjects, ledgerable, unknown = data
        return FindingRecord(
            rule,
            tuple(identity),
            FindingStatus(status),
            ShownRecord.from_json(shown) if shown is not None else None,
            tuple(WitnessRecord.from_json(witness) for witness in witnesses),
            owner,
            tuple(subjects),
            bool(ledgerable),
            bool(unknown),
        )


@dataclass(frozen=True, slots=True)
class BlockRecord:
    """One reason a group fails a level: the key (rule, identity) of a blocking finding, whose
    message the summary does not keep, or the text of a reason with no finding."""

    rule: str
    why: str
    identity: tuple[str, ...]
    reason: str

    @staticmethod
    def of(block: Block, portable: Portable) -> BlockRecord:
        if block.finding is None:
            return BlockRecord(block.rule, "", (), portable.text(block.text))
        return BlockRecord(block.rule, block.why, block.finding.identity, "")

    def to_json(self) -> list[Any]:
        return [self.rule, self.why, list(self.identity), self.reason]

    @staticmethod
    def from_json(data: list[Any]) -> BlockRecord:
        rule, why, identity, reason = data
        return BlockRecord(rule, why, tuple(identity), reason)


@dataclass(frozen=True, slots=True)
class IncompleteRecord:
    """One incomplete item; `mark` is its line-free fingerprint (see `scan.fingerprint`)."""

    rule: str
    key: str
    reason: str
    cause: IncompleteCause
    subjects: tuple[str, ...]
    mark: str

    @staticmethod
    def of(item: IncompleteItem, mark: str, portable: Portable) -> IncompleteRecord:
        return IncompleteRecord(item.rule, item.key, portable.text(item.reason), item.cause, item.subjects, mark)

    def to_json(self) -> list[Any]:
        return [self.rule, self.key, self.reason, self.cause.value, list(self.subjects), self.mark]

    @staticmethod
    def from_json(data: list[Any]) -> IncompleteRecord:
        rule, key, reason, cause, subjects, mark = data
        return IncompleteRecord(rule, key, reason, IncompleteCause(cause), tuple(subjects), mark)


def equality_to_json(equality: EqualityResult | None) -> dict[str, Any] | None:
    if equality is None:
        return None

    def diffs(items: list[LineDiff]) -> list[list[Any]]:
        return [[render_key(d.key), d.scanned, d.recorded] for d in items]

    return {"new": diffs(equality.new), "stale": diffs(equality.stale), "header": list(equality.header)}


def equality_from_json(data: dict[str, Any] | None) -> EqualityResult | None:
    if data is None:
        return None

    def diffs(items: list[list[Any]]) -> list[LineDiff]:
        return [LineDiff(parse_key(key), int(scanned), int(recorded)) for key, scanned, recorded in items]

    return EqualityResult(diffs(data["new"]), diffs(data["stale"]), list(data["header"]))


@dataclass(frozen=True, slots=True)
class ExceptionsRecord:
    """The exception entries that matched nothing, every entry's review date, and the file's
    problems. Whether an entry is past review depends on the day, so a reader decides it."""

    stale: tuple[str, ...] = ()
    reviews: tuple[tuple[str, str], ...] = ()  # (review_by as YYYY-MM-DD, label)
    problems: tuple[str, ...] = ()

    @staticmethod
    def of(report: ExceptionReport, exceptions: ExceptionsFile | None) -> ExceptionsRecord:
        entries = exceptions.entries if exceptions is not None else []
        return ExceptionsRecord(
            tuple(entry.label() for entry in report.stale),
            tuple((entry.review_by.isoformat(), entry.label()) for entry in entries if entry.review_by is not None),
            tuple(report.problems),
        )

    def expired(self, today: datetime.date) -> list[tuple[str, str]]:
        return [(review_by, label) for review_by, label in self.reviews if review_by < today.isoformat()]

    def to_json(self) -> dict[str, Any]:
        return {"stale": list(self.stale), "reviews": [list(e) for e in self.reviews], "problems": list(self.problems)}

    @staticmethod
    def from_json(data: dict[str, Any]) -> ExceptionsRecord:
        return ExceptionsRecord(
            tuple(data.get("stale", [])),
            tuple((review_by, label) for review_by, label in data.get("reviews", [])),
            tuple(data.get("problems", [])),
        )
