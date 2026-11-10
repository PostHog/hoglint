"""A rule's findings as a table (D58).

Every row has its identity (by place among the table's identities) and its flags, and comes from
one of the table's sources (`RowSource`): the `Finding` objects the rule's own code made
(`ObjectRows`), or the native evaluator's columns, from which a `Finding` is made only when a
reader asks for that row. One check makes about 40,000 findings and prints a few, so the readers
of every row (exceptions, scan lines, accepted marks, levels, the stored summary) read the
columns.

Status is kept as marks: the rows an exception allows, and the identities the ledger accepts. No
row is copied to change it.
"""

from __future__ import annotations

import dataclasses
from array import array
from collections.abc import Iterable, Iterator, Sequence
from typing import Protocol

from hoglint.model import Edge, Finding, FindingStatus, WitnessRow

# The place of a table's own `ObjectRows` among its sources.
OBJECTS = 0


class RowSource(Protocol):
    """Where some rows of a table come from: what the table reads of a row besides its identity
    and flags."""

    def finding(self, at: int, identity: tuple[str, ...]) -> Finding:
        """Row `at` as a Finding with status new."""
        ...

    def witnesses(self, at: int) -> tuple[Edge, ...]: ...

    def witness_ids(self, at: int) -> tuple[int, ...]: ...

    def subjects(self, at: int) -> tuple[str, ...]: ...

    def parts(self, ats: Sequence[int]) -> tuple[list[tuple[WitnessRow, ...]], list[str | None], list[tuple[str, ...]]]:
        """The witness rows, owner module and subjects of rows `ats`, read in bulk."""
        ...


class RowColumns(RowSource, Protocol):
    """A source that gives its rows' identities and flags as columns, for a table to copy."""

    identities: list[tuple[str, ...]]
    identity_at: array[int]
    ledgerable: bytes
    unknown: bytes


class ObjectRows:
    """The rows a rule's own code made, as `Finding` objects."""

    __slots__ = ("objects",)

    def __init__(self) -> None:
        self.objects: list[Finding] = []

    def add(self, finding: Finding) -> int:
        self.objects.append(finding)
        return len(self.objects) - 1

    def finding(self, at: int, identity: tuple[str, ...]) -> Finding:
        return self.objects[at]

    def witnesses(self, at: int) -> tuple[Edge, ...]:
        return self.objects[at].witnesses

    def witness_ids(self, at: int) -> tuple[int, ...]:
        return self.objects[at].witness_ids

    def subjects(self, at: int) -> tuple[str, ...]:
        return self.objects[at].subjects

    def parts(self, ats: Sequence[int]) -> tuple[list[tuple[WitnessRow, ...]], list[str | None], list[tuple[str, ...]]]:
        found = [self.objects[at] for at in ats]
        owners = [finding.owner.module if finding.owner is not None else None for finding in found]
        return [finding.witness_rows for finding in found], owners, [finding.subjects for finding in found]


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
        "source_at",
        "source_of",
        "sources",
        "taken",
        "unknown",
    )

    def __init__(self) -> None:
        self.identities: list[tuple[str, ...]] = []
        self.places: dict[tuple[str, ...], int] = {}
        self.identity_at = array("I")
        self.ledgerable = bytearray()
        self.unknown = bytearray()
        self.allowed = bytearray()
        self.accepted: set[int] = set()
        self.objects = ObjectRows()
        self.sources: list[RowSource] = [self.objects]
        # Each source `take` added, by its place among `sources`, with the table's place of each
        # of its identities.
        self.taken: dict[int, tuple[RowColumns, list[int]]] = {}
        # Per row: its source's place among `sources`, and its place in that source.
        self.source_of = array("B")
        self.source_at = array("I")
        # Rows made as a Finding when a reader asks, with status new: a mark changes no made row.
        self.made: dict[int, Finding] = {}

    def place(self, identity: tuple[str, ...]) -> int:
        found = self.places.get(identity)
        if found is None:
            found = self.places[identity] = len(self.identities)
            self.identities.append(identity)
        return found

    def append(self, finding: Finding) -> None:
        """A row the rule's own code made."""
        self.identity_at.append(self.place(finding.identity))
        self.ledgerable.append(finding.ledgerable)
        self.unknown.append(finding.unknown)
        self.allowed.append(0)
        self.source_of.append(OBJECTS)
        self.source_at.append(self.objects.add(finding))

    def extend(self, findings: Iterable[Finding]) -> None:
        for finding in findings:
            self.append(finding)

    def take(self, source: RowColumns) -> int:
        """Makes `source` one of the table's sources; returns its place, which `add_rows` takes."""
        number = len(self.sources)
        self.sources.append(source)
        self.taken[number] = (source, [self.place(identity) for identity in source.identities])
        return number

    def add_rows(self, number: int, start: int, stop: int) -> None:
        """Rows `start` to `stop` of the columns of source `number`, in order."""
        source, places = self.taken[number]
        self.identity_at.extend([places[at] for at in source.identity_at[start:stop]])
        self.ledgerable.extend(source.ledgerable[start:stop])
        self.unknown.extend(source.unknown[start:stop])
        self.allowed.extend(bytes(stop - start))
        self.source_of.extend(bytes([number]) * (stop - start))
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
        """A row's status, from the marks and its own flags: the one place that decides it."""
        if self.allowed[row]:
            return FindingStatus.ALLOWED
        if self.ledgerable[row] and self.identity_at[row] in self.accepted:
            return FindingStatus.ACCEPTED
        return FindingStatus.NEW

    def finding(self, row: int) -> Finding:
        """The row as a Finding with its status. A copy shares the made row's detail, so a
        message made once is not made again."""
        made = self.made.get(row)
        if made is None:
            made = self.made[row] = self.sources[self.source_of[row]].finding(self.source_at[row], self.identity(row))
        status = self.status(row)
        return made if made.status is status else dataclasses.replace(made, status=status)

    def rows_with(self, status: FindingStatus) -> list[int]:
        """The rows with `status`, read from the marks and flags, with no row made."""
        return [row for row in range(len(self.identity_at)) if self.status(row) is status]

    def allow(self, row: int) -> None:
        self.allowed[row] = 1

    def accept(self, places: Iterable[int]) -> None:
        self.accepted.update(places)

    def counted_places(self) -> list[int] | array[int]:
        """The identity place of each row a ledger counts (ledgerable, not allowed), in order."""
        if all(self.ledgerable) and not any(self.allowed):
            return self.identity_at
        at = self.identity_at
        return [at[row] for row in range(len(at)) if self.ledgerable[row] and not self.allowed[row]]

    def witnesses(self, row: int) -> tuple[Edge, ...]:
        return self.sources[self.source_of[row]].witnesses(self.source_at[row])

    def witness_ids(self) -> array[int]:
        """The witnesses' edge ids, row by row."""
        out = array("I")
        sources, source_of, source_at = self.sources, self.source_of, self.source_at
        for row in range(len(self.identity_at)):
            out.extend(sources[source_of[row]].witness_ids(source_at[row]))
        return out

    def subjects(self, row: int) -> tuple[str, ...]:
        return self.sources[self.source_of[row]].subjects(self.source_at[row])

    def statuses(self) -> list[FindingStatus]:
        """Every row's status, in order."""
        return [self.status(row) for row in range(len(self.identity_at))]

    def stored_parts(self) -> tuple[list[tuple[WitnessRow, ...]], list[str | None], list[tuple[str, ...]]]:
        """Every row's witness rows, owner module and subjects, in order: what a stored summary
        keeps of a row besides its identity, status and flags. Each source reads its rows in bulk."""
        count = len(self.identity_at)
        witnesses: list[tuple[WitnessRow, ...]] = [()] * count
        owners: list[str | None] = [None] * count
        subjects: list[tuple[str, ...]] = [()] * count
        for number, source in enumerate(self.sources):
            rows = [row for row in range(count) if self.source_of[row] == number]
            found = source.parts([self.source_at[row] for row in rows])
            for row, witness, owner, subject in zip(rows, *found, strict=True):
                witnesses[row], owners[row], subjects[row] = witness, owner, subject
        return witnesses, owners, subjects
