"""Growth by attribution.

Three scans decide where new debt came from:
- base: base code under the base scanner and base policy,
- head_base: head code under the base scanner and base policy,
- head: head code under the head scanner and head policy.

Sums run per growth key, never one grand total. More debt in head_base than in base is code
growth and fails. More debt only in head than in head_base is rule growth: better detection found
old debt, so it passes and the change lists it.

Move admission: code growth passes as an admitted move when every coupling behind the new lines
already existed in base, under the renamed modules, with at least the same count. New uses
bundled into a move still fail.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from hoglint.ledger.file import LineKey, is_level_line
from hoglint.ledger.scan import Coupling, ScanSummary

GrowthKey = tuple[str, tuple[str, ...]]  # (rule, growth columns)


@dataclass(frozen=True, slots=True)
class GrowthItem:
    rule: str
    key: tuple[str, ...]
    before: int
    after: int


@dataclass(frozen=False, slots=True)
class GrowthResult:
    code_growth: list[GrowthItem] = field(default_factory=list)
    rule_growth: list[GrowthItem] = field(default_factory=list)
    admitted_moves: list[GrowthItem] = field(default_factory=list)
    skipped: str = ""

    @property
    def failed(self) -> bool:
        return bool(self.code_growth)


def growth_key(line: LineKey, growth: dict[str, tuple[int, ...]]) -> GrowthKey | None:
    rule, identity = line
    indexes = growth.get(rule)
    if indexes is None:
        if is_level_line(line):
            return None
        return rule, identity
    return rule, tuple(identity[i] for i in indexes if i < len(identity))


def sums(lines: dict[LineKey, int], growth: dict[str, tuple[int, ...]]) -> dict[GrowthKey, int]:
    out: dict[GrowthKey, int] = {}
    for line, count in lines.items():
        key = growth_key(line, growth)
        if key is not None:
            out[key] = out.get(key, 0) + count
    return out


class Renames:
    """Module renames between base and head, from git's rename detection."""

    def __init__(self, head_to_base: dict[str, str]) -> None:
        self.head_to_base = head_to_base

    def back(self, name: str) -> str:
        """The base name of a head module or symbol (longest renamed module prefix)."""
        head = name
        while head:
            if head in self.head_to_base:
                return self.head_to_base[head] + name[len(head) :]
            head = head.rpartition(".")[0]
        return name


class AttributionCheck:
    def __init__(self, base: ScanSummary, head_base: ScanSummary, head: ScanSummary, renames: Renames) -> None:
        self.base = base
        self.head_base = head_base
        self.head = head
        self.renames = renames

    def run(self) -> GrowthResult:
        result = GrowthResult()
        base_sums = sums(self.base.lines, self.base.growth)
        head_base_sums = sums(self.head_base.lines, self.base.growth)
        for key, after in sorted(head_base_sums.items()):
            before = base_sums.get(key, 0)
            if after <= before:
                continue
            item = GrowthItem(key[0], key[1], before, after)
            if self.admitted(key):
                result.admitted_moves.append(item)
            else:
                result.code_growth.append(item)
        result.rule_growth = self.rule_growth()
        return result

    def grown_lines(self, key: GrowthKey) -> Iterable[LineKey]:
        for line, count in self.head_base.lines.items():
            if growth_key(line, self.base.growth) == key and count > self.base.lines.get(line, 0):
                yield line

    def admitted(self, key: GrowthKey) -> bool:
        lines = list(self.grown_lines(key))
        if not lines:
            return False
        for line in lines:
            couplings = self.head_base.witnesses.get(line, [])
            if not couplings or not all(self.existed(coupling) for coupling in couplings):
                return False
        return True

    def existed(self, coupling: Coupling) -> bool:
        src, dst, kind = coupling
        original = (self.renames.back(src), self.renames.back(dst), kind)
        if original == coupling:
            return False
        return self.base.couplings.get(original, 0) >= self.head_base.couplings.get(coupling, 0)

    def rule_growth(self) -> list[GrowthItem]:
        """Debt only the head scanner or policy sees, per rule and growth key of the head policy."""
        head_sums = sums(self.head.lines, self.head.growth)
        before_sums = sums(self.head_base.lines, self.head.growth)
        out = []
        for key, after in sorted(head_sums.items()):
            before = before_sums.get(key, 0)
            if after > before:
                out.append(GrowthItem(key[0], key[1], before, after))
        return out
