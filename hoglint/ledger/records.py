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
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hoglint.ledger.equality import EqualityResult, LineDiff
from hoglint.ledger.file import LineKey, parse_key, render_key
from hoglint.model import Edge, Finding, FindingStatus, IncompleteCause, IncompleteKind, witness_row
from hoglint.rules.base import IncompleteItem
from hoglint.rules.exceptions import ExceptionReport, ExceptionsFile
from hoglint.rules.levels import Block


def touches(touched: set[str], paths: Iterable[str], subjects: Iterable[str]) -> bool:
    return not touched.isdisjoint(paths) or not touched.isdisjoint(subjects)


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
        return WitnessRecord(*witness_row(edge))

    def to_json(self) -> list[Any]:
        return [self.kind, self.path, self.line, self.column, self.surface, list(self.targets)]

    @staticmethod
    def from_json(data: list[Any]) -> WitnessRecord:
        kind, path, line, column, surface, targets = data
        return WitnessRecord(kind, path, int(line), int(column), surface, tuple(targets))


@dataclass(frozen=True, slots=True)
class ShownRecord:
    """What `check` prints of a finding besides its identity: the message and site."""

    message: str
    path: str  # empty when the finding has no site
    line: int

    @staticmethod
    def of(finding: Finding, portable: Portable) -> ShownRecord:
        site = finding.site
        return ShownRecord(
            portable.text(finding.message),
            site.path if site is not None else "",
            site.line if site is not None else 0,
        )

    def to_json(self) -> list[Any]:
        return [self.message, self.path, self.line]

    @staticmethod
    def from_json(data: list[Any]) -> ShownRecord:
        message, path, line = data
        return ShownRecord(message, path, int(line))


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

    def to_json(self) -> list[Any]:
        row: list[Any] = [None] * StoredFindings.FIELDS
        row[StoredFindings.RULE] = self.rule
        row[StoredFindings.IDENTITY] = list(self.identity)
        row[StoredFindings.STATUS] = self.status.value
        row[StoredFindings.SHOWN] = self.shown.to_json() if self.shown is not None else None
        row[StoredFindings.WITNESSES] = [witness.to_json() for witness in self.witnesses]
        row[StoredFindings.OWNER] = self.owner
        row[StoredFindings.SUBJECTS] = list(self.subjects)
        row[StoredFindings.LEDGERABLE] = self.ledgerable
        row[StoredFindings.UNKNOWN] = self.unknown
        return row

    @staticmethod
    def from_json(data: list[Any]) -> FindingRecord:
        shown = data[StoredFindings.SHOWN]
        return FindingRecord(
            data[StoredFindings.RULE],
            tuple(data[StoredFindings.IDENTITY]),
            FindingStatus(data[StoredFindings.STATUS]),
            ShownRecord.from_json(shown) if shown is not None else None,
            tuple(WitnessRecord.from_json(witness) for witness in data[StoredFindings.WITNESSES]),
            data[StoredFindings.OWNER],
            tuple(data[StoredFindings.SUBJECTS]),
            bool(data[StoredFindings.LEDGERABLE]),
            bool(data[StoredFindings.UNKNOWN]),
        )


# The lengths of the stored lists `ShownRecord` and `WitnessRecord` write.
SHOWN_FIELDS = 3
WITNESS_FIELDS = 6
STATUS_VALUES = frozenset(status.value for status in FindingStatus)


class StoredFindings:
    """The findings section as it is stored: one row per finding in `FindingRecord.to_json`'s
    form, a list or a tuple. A row becomes a `FindingRecord` when a reader asks for it, so a
    check that prints a few findings decodes a few."""

    # The place of each field in a stored row: every writer and reader of a row uses these.
    RULE = 0
    IDENTITY = 1
    STATUS = 2
    SHOWN = 3
    WITNESSES = 4
    OWNER = 5
    SUBJECTS = 6
    LEDGERABLE = 7
    UNKNOWN = 8
    FIELDS = 9
    # The places a reader reads in a stored witness (`WitnessRecord.to_json`).
    WITNESS_PATH = 1
    WITNESS_TARGETS = 5

    def __init__(self, rows: list[Any] | None = None) -> None:
        self.rows: list[Any] = rows if rows is not None else []

    @staticmethod
    def of(records: list[FindingRecord]) -> StoredFindings:
        return StoredFindings([record.to_json() for record in records])

    @staticmethod
    def loaded(rows: Any) -> StoredFindings:
        """Rows read from a stored summary, each checked for the shape `FindingRecord.from_json`
        reads, with no record made: a malformed row fails here, where readers of a stored summary
        turn an error into a miss (D28), not later in a reader."""
        if not isinstance(rows, list):
            raise ValueError("the findings section holds no list of rows")
        for row in rows:
            StoredFindings.check(row)
        return StoredFindings(rows)

    @staticmethod
    def check(row: Any) -> None:
        if not isinstance(row, list) or len(row) != StoredFindings.FIELDS:
            raise ValueError(f"a findings row has {StoredFindings.FIELDS} fields: {row!r}")
        status = row[StoredFindings.STATUS]
        if status not in STATUS_VALUES:
            raise ValueError(f"a findings row has an unknown status {status!r}")
        if not isinstance(row[StoredFindings.RULE], str) or not isinstance(row[StoredFindings.OWNER], str):
            raise ValueError(f"a findings row names its rule and owner as text: {row!r}")
        if not isinstance(row[StoredFindings.IDENTITY], list) or not isinstance(row[StoredFindings.SUBJECTS], list):
            raise ValueError(f"a findings row holds its identity and subjects as lists: {row!r}")
        shown = row[StoredFindings.SHOWN]
        if shown is not None and (not isinstance(shown, list) or len(shown) != SHOWN_FIELDS):
            raise ValueError(f"a findings row's message has {SHOWN_FIELDS} fields: {shown!r}")
        witnesses = row[StoredFindings.WITNESSES]
        if not isinstance(witnesses, list) or any(
            not isinstance(witness, list)
            or len(witness) != WITNESS_FIELDS
            or not isinstance(witness[StoredFindings.WITNESS_TARGETS], list)
            for witness in witnesses
        ):
            raise ValueError(
                f"a findings row's witnesses have {WITNESS_FIELDS} fields each, the targets a list: {witnesses!r}"
            )

    def __iter__(self) -> Iterator[FindingRecord]:
        for row in self.rows:
            yield FindingRecord.from_json(row)

    def __len__(self) -> int:
        return len(self.rows)

    def with_status(self, status: FindingStatus) -> list[FindingRecord]:
        value = status.value
        return [FindingRecord.from_json(row) for row in self.rows if row[self.STATUS] == value]

    def count(self, status: FindingStatus) -> int:
        value = status.value
        return sum(1 for row in self.rows if row[self.STATUS] == value)

    def keys(self) -> set[LineKey]:
        """The ledger key (rule, identity) of every row no exception allows."""
        allowed = FindingStatus.ALLOWED.value
        return {(row[self.RULE], tuple(row[self.IDENTITY])) for row in self.rows if row[self.STATUS] != allowed}

    def new_rows(self) -> dict[LineKey, int]:
        """Per ledger key (rule, identity), the number of new rows: findings that the run's ledger
        does not accept and no exception allows."""
        new = FindingStatus.NEW.value
        keys = ((row[self.RULE], tuple(row[self.IDENTITY])) for row in self.rows if row[self.STATUS] == new)
        return dict(Counter(keys))

    def allowed_keys(self) -> set[LineKey]:
        """The ledger key (rule, identity) of every row an exception allows."""
        allowed = FindingStatus.ALLOWED.value
        return {(row[self.RULE], tuple(row[self.IDENTITY])) for row in self.rows if row[self.STATUS] == allowed}

    def source_module(self, row: Any, source_columns: dict[str, int]) -> str:
        """The module a row's line comes from: its identity's `src.module` column when its rule
        has one (`source_columns`: rule -> that column), else its owner module; empty for none."""
        column = source_columns.get(row[self.RULE])
        identity = row[self.IDENTITY]
        if column is not None and column < len(identity):
            return identity[column]
        return row[self.OWNER]

    def open_rows(self, source_columns: dict[str, int]) -> Iterator[tuple[FindingStatus, str, str, str, str]]:
        """(status, rule, owner module, target, source module) of every row no exception allows:
        what `query` groups by. The target is the first witness's first target, else the
        identity's first column, else "?"."""
        allowed = FindingStatus.ALLOWED.value
        targets = self.WITNESS_TARGETS
        for row in self.rows:
            status = row[self.STATUS]
            if status == allowed:
                continue
            identity = row[self.IDENTITY]
            first = identity[0] if identity else "?"
            target = next((witness[targets][0] for witness in row[self.WITNESSES] if witness[targets]), first)
            source = self.source_module(row, source_columns)
            yield FindingStatus(status), row[self.RULE], row[self.OWNER], target, source

    def to_json(self) -> list[Any]:
        return self.rows


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
        if block.table is None:
            return BlockRecord(block.rule, "", (), portable.text(block.text))
        return BlockRecord(block.rule, block.why, block.identity, "")

    def to_json(self) -> list[Any]:
        return [self.rule, self.why, list(self.identity), self.reason]

    @staticmethod
    def from_json(data: list[Any]) -> BlockRecord:
        rule, why, identity, reason = data
        return BlockRecord(rule, why, tuple(identity), reason)


@dataclass(frozen=True, slots=True)
class IncompleteRecord:
    """One incomplete item; `mark` is its line-free fingerprint (see `scan.fingerprint`). `path`
    and `line` place it for a reader, from the edge that raised it; empty and 0 for none."""

    rule: str
    key: str
    reason: str
    cause: IncompleteCause
    subjects: tuple[str, ...]
    mark: str
    kind: IncompleteKind
    path: str
    line: int

    @staticmethod
    def of(item: IncompleteItem, mark: str, portable: Portable) -> IncompleteRecord:
        site = item.edge.site if item.edge is not None else None
        path, line = (site.path, site.line) if site is not None else ("", 0)
        reason = portable.text(item.reason)
        return IncompleteRecord(item.rule, item.key, reason, item.cause, item.subjects, mark, item.kind, path, line)

    def place(self) -> str:
        """Where a reader finds the item: its file and line, else its module, else nothing."""
        if self.path:
            return f"{self.path}:{self.line}" if self.line else self.path
        return self.subjects[0] if self.subjects else ""

    def to_json(self) -> list[Any]:
        fields = [self.rule, self.key, self.reason, self.cause.value, list(self.subjects), self.mark]
        return [*fields, self.kind.value, self.path, self.line]

    @staticmethod
    def from_json(data: list[Any]) -> IncompleteRecord:
        rule, key, reason, cause, subjects, mark, kind, path, line = data
        return IncompleteRecord(
            rule, key, reason, IncompleteCause(cause), tuple(subjects), mark, IncompleteKind(kind), path, int(line)
        )


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
            tuple((entry.review_by.isoformat(), entry.label()) for entry in entries),
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
