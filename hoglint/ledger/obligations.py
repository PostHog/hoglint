"""Who owns an incomplete result: the change under check, or the code it started from.

An obligation is (rule, key, reason); each item under it carries a line-free fingerprint. Against
a reference summary, per obligation:
- items above the reference count are introduced: the change added unknowns, its author owns them;
- an obligation the reference does not hold is introduced, also when its key exists there with
  another reason;
- an item whose fingerprint the reference lacks, within the reference count, is ambiguous: an old
  unknown was replaced or edited, and nothing tells which. It blocks like an introduced one;
- an item that matches the reference by fingerprint is inherited and does not block.

A staged run whose policy HEAD shares judges its findings and ledger lines against HEAD the same
way (`Inherited`): new findings of a ledger key that HEAD has as new at least as many times, and a
ledger line out of step with the scan exactly as at HEAD, are HEAD's debt, not the commit's. CI
judges them; the ledger catches up there.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from hoglint.ledger.equality import LineDiff
from hoglint.ledger.file import LineKey, is_level_line
from hoglint.ledger.scan import Obligation, ScanSummary


class Attribution(StrEnum):
    INTRODUCED = "introduced"
    AMBIGUOUS = "ambiguous"
    INHERITED = "inherited"
    UNATTRIBUTED = "unattributed"  # no reference summary, so nothing can be told apart


BLOCKING = frozenset({Attribution.INTRODUCED, Attribution.AMBIGUOUS})

Labels = dict[tuple[Obligation, str], list[Attribution]]  # (obligation, fingerprint) -> one label per item


def attribute(head: dict[Obligation, list[str]], reference: dict[Obligation, list[str]] | None) -> Labels:
    out: Labels = {}
    for obligation, marks in sorted(head.items()):
        if reference is None:
            for mark in marks:
                out.setdefault((obligation, mark), []).append(Attribution.UNATTRIBUTED)
            continue
        before = reference.get(obligation, [])
        left = Counter(before)
        unmatched: list[str] = []
        for mark in marks:
            if left[mark] > 0:
                left[mark] -= 1
                out.setdefault((obligation, mark), []).append(Attribution.INHERITED)
            else:
                unmatched.append(mark)
        extra = max(0, len(marks) - len(before))
        for index, mark in enumerate(unmatched):
            label = Attribution.INTRODUCED if index < extra else Attribution.AMBIGUOUS
            out.setdefault((obligation, mark), []).append(label)
    return out


def rows(labels: Labels) -> list[tuple[Attribution, Obligation]]:
    """One row per item, in obligation order."""
    return [(label, obligation) for (obligation, _), items in sorted(labels.items()) for label in items]


@dataclass(frozen=True, slots=True)
class Standing:
    """How a run stands against its ledger: per ledger key, the number of new findings, and the
    ledger lines out of step with the scan that no new finding stands behind: stale lines, and
    level lines the ledger lacks."""

    new_rows: Mapping[LineKey, int]
    lines: frozenset[LineDiff]

    @staticmethod
    def of(summary: ScanSummary) -> Standing:
        equality = summary.equality
        if equality is None:
            return Standing(summary.new_rows, frozenset())
        levels = (diff for diff in equality.new if is_level_line(diff.key))
        return Standing(summary.new_rows, frozenset([*equality.stale, *levels]))


@dataclass(frozen=True, slots=True)
class Inherited:
    """What a staged run has that HEAD has already: the keys of its new findings, and its ledger
    lines out of step with the scan."""

    keys: frozenset[LineKey]
    lines: frozenset[LineDiff]

    @staticmethod
    def of(run: Standing, head: Standing) -> Inherited:
        """A key is inherited when HEAD has at least as many new findings of it, so a count rise
        blocks all its findings, as without HEAD. A line is inherited when HEAD has it at the same
        recorded and scanned counts, so a line that the change moves blocks."""
        keys = frozenset(key for key, count in run.new_rows.items() if count <= head.new_rows.get(key, 0))
        return Inherited(keys, run.lines & head.lines)
