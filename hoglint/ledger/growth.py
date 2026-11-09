"""Growth by attribution.

Three scans decide where new debt came from:
- base: base code under the base engine and base policy,
- head_base: head code under the base engine and base policy,
- head: head code under the head engine and head policy.

Sums run per growth key, never one grand total. More debt in head_base than in base is code
growth and fails. More debt only in head than in head_base is rule growth: better detection found
old debt, so it passes and the change lists it. Less debt in head than in head_base is shrink:
the new engine, policy or environment no longer sees debt that the head code still has. It
passes, and the change lists it with the cause for the DevEx reviewer.

Move admission: code growth passes as an admitted move when every coupling behind the new lines
already existed in base, under the renamed modules, with at least the same count. A coupling
carries the rule's `edge.*` identity columns (see `couplings.py`), so new uses bundled into a
move, and a use whose shape changed in the move, still fail.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hoglint.ledger.couplings import Coupling
from hoglint.ledger.file import LineKey, is_level_line
from hoglint.ledger.scan import ScanSummary

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
    shrink: list[GrowthItem] = field(default_factory=list)
    shrink_causes: list[str] = field(default_factory=list)
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

    def modules(self) -> frozenset[str]:
        """Old and new names: the modules whose couplings move admission reads."""
        return frozenset({*self.head_to_base, *self.head_to_base.values()})


class AttributionCheck:
    def __init__(self, base: ScanSummary, head_base: ScanSummary, head: ScanSummary, renames: Renames) -> None:
        self.base = base
        self.head_base = head_base
        self.head = head
        self.renames = renames
        self.grown: dict[GrowthKey, list[LineKey]] = {}
        for line, count in head_base.lines.items():
            key = growth_key(line, base.growth)
            if key is not None and count > base.lines.get(line, 0):
                self.grown.setdefault(key, []).append(line)

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
        result.rule_growth, result.shrink = self.engine_and_policy_change()
        if result.shrink:
            result.shrink_causes = self.head_base.envelope.causes(self.head.envelope)
        return result

    def admitted(self, key: GrowthKey) -> bool:
        lines = self.grown.get(key, [])
        if not lines:
            return False
        for line in lines:
            couplings = self.head_base.witnesses.get(line, [])
            if not couplings or not all(self.existed(coupling) for coupling in couplings):
                return False
        return True

    def existed(self, coupling: Coupling) -> bool:
        rule, src, dst, kind, values = coupling
        original = (rule, self.renames.back(src), self.renames.back(dst), kind, values)
        if original == coupling:
            return False
        return self.base.couplings.get(original, 0) >= self.head_base.couplings.get(coupling, 0)

    def engine_and_policy_change(self) -> tuple[list[GrowthItem], list[GrowthItem]]:
        """Head code under both engines and policies, per growth key of the head policy: debt only
        the head sees (rule growth), and debt only the base engine and policy see (shrink)."""
        head_sums = sums(self.head.lines, self.head.growth)
        before_sums = sums(self.head_base.lines, self.head.growth)
        grown, shrunk = [], []
        for key in sorted(set(head_sums) | set(before_sums)):
            before, after = before_sums.get(key, 0), head_sums.get(key, 0)
            if after > before:
                grown.append(GrowthItem(key[0], key[1], before, after))
            elif after < before:
                shrunk.append(GrowthItem(key[0], key[1], before, after))
        return grown, shrunk
