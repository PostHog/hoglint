"""A rule's findings as a table (D58).

Every row has its identity (by place among the table's identities) and its flags. A row is either
a `Finding` the rule's own code made, or a row of the native evaluator's columns (`RowSource`),
from which a `Finding` is made only when a reader asks for that row. One check makes about 40,000
findings and prints a few, so the readers of every row (exceptions, scan lines, accepted marks,
levels, the stored summary) read the columns.

Status is kept as marks: the rows an exception allows, and the identities the ledger accepts. No
row is copied to change it.
"""

from __future__ import annotations

import dataclasses
from array import array
from collections.abc import Iterable, Iterator, Sequence
from typing import Protocol

from hoglint.model import Edge, Finding, FindingStatus, WitnessRow

NO_ROW = -1


class RowSource(Protocol):
    """The evaluator's rows of one rule: what a table reads of a row it did not get as a Finding."""

    identities: list[tuple[str, ...]]
    identity_at: array[int]
    ledgerable: bytes
    unknown: bytes

    def finding(self, at: int, identity: tuple[str, ...], status: FindingStatus) -> Finding: ...

    def edge_id(self, at: int) -> int: ...

    def edge(self, at: int) -> Edge: ...

    def witness_row(self, at: int) -> WitnessRow: ...

    def subjects(self, at: int) -> tuple[str, ...]: ...

    def owner_module(self, at: int) -> str | None: ...

    def parts(self, ats: Sequence[int]) -> tuple[list[tuple[WitnessRow, ...]], list[str | None], list[tuple[str, ...]]]:
        """The witness rows, owner module and subjects of rows `ats`, read in bulk."""
        ...


class FindingTable:
    """One rule's findings, in the order the rule reports them. Iterating it, or indexing it,
    gives each row as a `Finding` with its status."""

    __slots__ = (
        "accepted",
        "allowed",
        "identities",
        "identity_at",
        "ledgerable",
        "made",
        "objects",
        "places",
        "source",
        "source_at",
        "unknown",
    )

    def __init__(self) -> None:
        self.identities: list[tuple[str, ...]] = []
        self.places: dict[tuple[str, ...], int] | None = None
        self.identity_at = array("I")
        self.ledgerable = bytearray()
        self.unknown = bytearray()
        self.allowed = bytearray()
        self.accepted: set[int] = set()
        self.objects: dict[int, Finding] = {}
        self.source: RowSource | None = None
        self.source_at = array("i")
        # Rows given as a Finding with their status, made when a reader asks; a mark empties it.
        self.made: dict[int, Finding] = {}

    def place(self, identity: tuple[str, ...]) -> int:
        if self.places is None:
            self.places = {}
            for at, known in enumerate(self.identities):
                self.places.setdefault(known, at)
        found = self.places.get(identity)
        if found is None:
            found = self.places[identity] = len(self.identities)
            self.identities.append(identity)
        return found

    def append(self, finding: Finding) -> None:
        """A row the rule's own code made."""
        row = len(self.identity_at)
        self.identity_at.append(self.place(finding.identity))
        self.ledgerable.append(finding.ledgerable)
        self.unknown.append(finding.unknown)
        self.allowed.append(0)
        self.source_at.append(NO_ROW)
        self.objects[row] = finding

    def extend(self, findings: Iterable[Finding]) -> None:
        for finding in findings:
            self.append(finding)

    def take(self, source: RowSource) -> None:
        """Makes `source` the table's evaluator rows; `add_rows` then adds them by place."""
        assert self.source is None, "a table takes the evaluator's rows of one call"
        self.source = source
        if self.identities:
            raise ValueError("the evaluator's rows come before any other row of their rule")
        self.identities.extend(source.identities)

    def add_rows(self, start: int, stop: int) -> None:
        """Rows `start` to `stop` of the evaluator's columns, in order."""
        source = self.source
        assert source is not None
        if start >= stop:
            return
        self.identity_at.extend(source.identity_at[start:stop])
        self.ledgerable.extend(source.ledgerable[start:stop])
        self.unknown.extend(source.unknown[start:stop])
        self.allowed.extend(bytes(stop - start))
        self.source_at.extend(range(start, stop))

    def __len__(self) -> int:
        return len(self.identity_at)

    def __iter__(self) -> Iterator[Finding]:
        for row in range(len(self.identity_at)):
            yield self.finding(row)

    def __getitem__(self, row: int) -> Finding:
        if row < 0:
            row += len(self.identity_at)
        if not 0 <= row < len(self.identity_at):
            raise IndexError(row)
        return self.finding(row)

    def identity(self, row: int) -> tuple[str, ...]:
        return self.identities[self.identity_at[row]]

    def status(self, row: int) -> FindingStatus:
        if self.allowed[row]:
            return FindingStatus.ALLOWED
        if self.ledgerable[row] and self.identity_at[row] in self.accepted:
            return FindingStatus.ACCEPTED
        return FindingStatus.NEW

    def finding(self, row: int) -> Finding:
        """The row as a Finding with its status."""
        found = self.made.get(row)
        if found is not None:
            return found
        status = self.status(row)
        made = self.objects.get(row)
        if made is None:
            assert self.source is not None
            made = self.source.finding(self.source_at[row], self.identity(row), status)
        elif made.status is not status:
            made = dataclasses.replace(made, status=status)
        self.made[row] = made
        return made

    def rows_with(self, status: FindingStatus) -> list[int]:
        """The rows with `status`, read from the marks and flags, with no row made."""
        at, ledgerable, allowed, accepted = self.identity_at, self.ledgerable, self.allowed, self.accepted
        rows = range(len(at))
        if status is FindingStatus.ALLOWED:
            return [row for row in rows if allowed[row]]
        if status is FindingStatus.ACCEPTED:
            return [row for row in rows if not allowed[row] and ledgerable[row] and at[row] in accepted]
        return [row for row in rows if not allowed[row] and not (ledgerable[row] and at[row] in accepted)]

    def allow(self, row: int) -> None:
        self.allowed[row] = 1
        self.made.clear()

    def accept(self, places: Iterable[int]) -> None:
        self.accepted.update(places)
        self.made.clear()

    def counted_places(self) -> list[int] | array[int]:
        """The identity place of each row a ledger counts (ledgerable, not allowed), in order."""
        if all(self.ledgerable) and not any(self.allowed):
            return self.identity_at
        at = self.identity_at
        return [at[row] for row in range(len(at)) if self.ledgerable[row] and not self.allowed[row]]

    def witnesses(self, row: int) -> tuple[Edge, ...]:
        made = self.objects.get(row)
        if made is not None:
            return made.witnesses
        assert self.source is not None
        return (self.source.edge(self.source_at[row]),)

    def witness_rows(self, row: int) -> tuple[WitnessRow, ...]:
        made = self.objects.get(row)
        if made is not None:
            return made.witness_rows
        assert self.source is not None
        return (self.source.witness_row(self.source_at[row]),)

    def witness_ids(self) -> array[int]:
        """The witnesses' edge ids, row by row."""
        out = array("I")
        for row in range(len(self.identity_at)):
            made = self.objects.get(row)
            if made is not None:
                out.extend(made.witness_ids)
                continue
            assert self.source is not None
            out.append(self.source.edge_id(self.source_at[row]))
        return out

    def subjects(self, row: int) -> tuple[str, ...]:
        made = self.objects.get(row)
        if made is not None:
            return made.subjects
        assert self.source is not None
        return self.source.subjects(self.source_at[row])

    def owner_module(self, row: int) -> str | None:
        made = self.objects.get(row)
        if made is not None:
            return made.owner.module if made.owner is not None else None
        assert self.source is not None
        return self.source.owner_module(self.source_at[row])

    def statuses(self) -> list[FindingStatus]:
        """Every row's status, in order."""
        at, ledgerable, allowed, accepted = self.identity_at, self.ledgerable, self.allowed, self.accepted
        new, done, kept = FindingStatus.NEW, FindingStatus.ACCEPTED, FindingStatus.ALLOWED
        return [
            kept if allowed[row] else done if ledgerable[row] and at[row] in accepted else new for row in range(len(at))
        ]

    def stored_parts(self) -> tuple[list[tuple[WitnessRow, ...]], list[str | None], list[tuple[str, ...]]]:
        """Every row's witness rows, owner module and subjects, in order: what a stored summary
        keeps of a row besides its identity, status and flags."""
        source, objects = self.source, self.objects
        if source is not None and not objects:
            return source.parts(self.source_at)
        count = len(self.identity_at)
        witnesses: list[tuple[WitnessRow, ...]] = [()] * count
        owners: list[str | None] = [None] * count
        subjects: list[tuple[str, ...]] = [()] * count
        for row, finding in objects.items():
            witnesses[row] = finding.witness_rows
            owners[row] = finding.owner.module if finding.owner is not None else None
            subjects[row] = finding.subjects
        if source is None:
            return witnesses, owners, subjects
        rows = [row for row in range(count) if row not in objects]
        found = source.parts([self.source_at[row] for row in rows])
        for row, witness, owner, subject in zip(rows, *found, strict=True):
            witnesses[row], owners[row], subjects[row] = witness, owner, subject
        return witnesses, owners, subjects
