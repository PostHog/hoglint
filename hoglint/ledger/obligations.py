"""Who owns an incomplete result: the change under check, or the code it started from.

An obligation is (rule, key, reason); each item under it carries a line-free fingerprint. Against
a reference summary, per obligation:
- items above the reference count are introduced: the change added unknowns, its author owns them;
- an obligation the reference does not hold is introduced, also when its key exists there with
  another reason;
- an item whose fingerprint the reference lacks, within the reference count, is ambiguous: an old
  unknown was replaced or edited, and nothing tells which. It blocks like an introduced one;
- an item that matches the reference by fingerprint is inherited and does not block.

`compare` then tests each introduced or ambiguous item against base code read by the head's engine
and policy (`expose`). A match is exposed: the code is old, and the change to the engine or the
policy made the reading reach it. It blocks like an introduced one; the label only names the cause.
"""

from __future__ import annotations

from collections import Counter
from enum import StrEnum

from hoglint.ledger.scan import Obligation


class Attribution(StrEnum):
    INTRODUCED = "introduced"
    AMBIGUOUS = "ambiguous"
    INHERITED = "inherited"
    EXPOSED = "exposed"  # compare only: base code has it under the head's engine and policy
    UNATTRIBUTED = "unattributed"  # no reference summary, so nothing can be told apart


BLOCKING = frozenset({Attribution.INTRODUCED, Attribution.AMBIGUOUS, Attribution.EXPOSED})

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


def expose(labels: Labels, base_head: dict[Obligation, list[str]]) -> Labels:
    """`labels` with each blocking item that base code under the head's engine and policy also has,
    by obligation and fingerprint, relabelled exposed. Items that `labels` already inherits use up
    their matches first, so a second copy the change added stays introduced."""
    out: Labels = {}
    for (obligation, mark), items in labels.items():
        matches = base_head.get(obligation, []).count(mark) - items.count(Attribution.INHERITED)
        relabelled = []
        for label in items:
            if label in (Attribution.INTRODUCED, Attribution.AMBIGUOUS) and matches > 0:
                matches -= 1
                label = Attribution.EXPOSED
            relabelled.append(label)
        out[(obligation, mark)] = relabelled
    return out


def rows(labels: Labels) -> list[tuple[Attribution, Obligation]]:
    """One row per item, in obligation order."""
    return [(label, obligation) for (obligation, _), items in sorted(labels.items()) for label in items]
