"""Equality: the scan of the code and the ledger file match line by line, with counts.

A new line fails: fix the code, or record a reviewed exception. A stale line fails too, so fixed
debt leaves the file in the same change.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hoglint.ledger.file import LedgerFile, LineKey


@dataclass(frozen=True, slots=True)
class LineDiff:
    key: LineKey
    scanned: int
    recorded: int


@dataclass(frozen=False, slots=True)
class EqualityResult:
    new: list[LineDiff] = field(default_factory=list)
    stale: list[LineDiff] = field(default_factory=list)
    header: list[str] = field(default_factory=list)

    @property
    def equal(self) -> bool:
        return not (self.new or self.stale or self.header)


def check_equality(scan: dict[LineKey, int], ledger: LedgerFile, schema: int, policy: str) -> EqualityResult:
    result = EqualityResult()
    if ledger.exists and ledger.schema != schema:
        result.header.append(
            f"ledger schema {ledger.schema} differs from {schema}: the change that moves to the new"
            " schema rewrites the ledger, and its review reads every line"
        )
    if ledger.exists and ledger.policy != policy:
        result.header.append(
            f"ledger was written under policy {ledger.policy or '?'}, current is {policy}: a policy change"
            " is a change of its own, reviewed with the ledger lines it moves"
        )
    result.header.extend(ledger.problems)
    for key in sorted(set(scan) | set(ledger.lines)):
        scanned = scan.get(key, 0)
        recorded = ledger.lines.get(key, 0)
        if scanned > recorded:
            result.new.append(LineDiff(key, scanned, recorded))
        elif recorded > scanned:
            result.stale.append(LineDiff(key, scanned, recorded))
    return result
