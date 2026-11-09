"""Plain records of what one run found, so a stored summary can stand in for the run.

A record keeps what `check`, `query` and compare print or count, and nothing that needs the
graph: no Edge objects, no labels. Each record writes itself as a JSON list.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hoglint.ledger.equality import EqualityResult, LineDiff
from hoglint.ledger.file import LineKey, parse_key, render_key
from hoglint.model import Edge, Finding, FindingStatus, IncompleteCause
from hoglint.rules.base import IncompleteItem
from hoglint.rules.exceptions import ExceptionReport


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
class FindingRecord:
    rule: str
    identity: tuple[str, ...]
    status: FindingStatus
    message: str
    path: str  # empty when the finding has no site
    line: int
    witnesses: tuple[WitnessRecord, ...]
    owners: tuple[str, ...]
    subjects: tuple[str, ...]
    remedies: tuple[tuple[str, str], ...]
    ledgerable: bool
    unknown: bool

    @property
    def key(self) -> LineKey:
        return self.rule, self.identity

    @staticmethod
    def of(finding: Finding) -> FindingRecord:
        site = finding.site
        return FindingRecord(
            finding.rule,
            finding.identity,
            finding.status,
            finding.message,
            site.path if site is not None else "",
            site.line if site is not None else 0,
            tuple(WitnessRecord.of(edge) for edge in finding.witnesses),
            finding.owners,
            finding.subjects,
            tuple((remedy.kind, remedy.detail) for remedy in finding.remedies),
            finding.ledgerable,
            finding.unknown,
        )

    def to_json(self) -> list[Any]:
        return [
            self.rule,
            list(self.identity),
            self.status.value,
            self.message,
            self.path,
            self.line,
            [witness.to_json() for witness in self.witnesses],
            list(self.owners),
            list(self.subjects),
            [list(remedy) for remedy in self.remedies],
            self.ledgerable,
            self.unknown,
        ]

    @staticmethod
    def from_json(data: list[Any]) -> FindingRecord:
        rule, identity, status, message, path, line, witnesses, owners, subjects, remedies, ledgerable, unknown = data
        return FindingRecord(
            rule,
            tuple(identity),
            FindingStatus(status),
            message,
            path,
            int(line),
            tuple(WitnessRecord.from_json(witness) for witness in witnesses),
            tuple(owners),
            tuple(subjects),
            tuple((kind, detail) for kind, detail in remedies),
            bool(ledgerable),
            bool(unknown),
        )


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
    def of(item: IncompleteItem, mark: str) -> IncompleteRecord:
        return IncompleteRecord(item.rule, item.key, item.reason, item.cause, item.subjects, mark)

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
    """The exception entries that matched nothing, those past review, and the file's problems."""

    stale: tuple[str, ...] = ()
    expired: tuple[tuple[str, str], ...] = ()  # (review_by, label)
    problems: tuple[str, ...] = ()

    @staticmethod
    def of(report: ExceptionReport) -> ExceptionsRecord:
        return ExceptionsRecord(
            tuple(entry.label() for entry in report.stale),
            tuple((str(entry.review_by), entry.label()) for entry in report.expired),
            tuple(report.problems),
        )

    def to_json(self) -> dict[str, Any]:
        return {"stale": list(self.stale), "expired": [list(e) for e in self.expired], "problems": list(self.problems)}

    @staticmethod
    def from_json(data: dict[str, Any]) -> ExceptionsRecord:
        return ExceptionsRecord(
            tuple(data.get("stale", [])),
            tuple((review_by, label) for review_by, label in data.get("expired", [])),
            tuple(data.get("problems", [])),
        )
