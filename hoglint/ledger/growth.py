"""Growth by attribution.

Three scans decide where new debt came from:
- base: base code under the base engine and base policy,
- head_base: head code under the base engine and base policy,
- head: head code under the head engine and head policy.

Sums run per growth key, never one grand total. A growth key may name a label of the line's
source module that the identity does not hold (`src.package` beside `src.module`), so a coupling
removed in one product never pays for a new one in another. More debt in head_base than in base is code
growth and fails. More debt only in head than in head_base is rule growth: better detection found
old debt, so it passes and the change lists it. Less debt in head than in head_base is shrink:
the new engine, policy or environment no longer sees debt that the head code still has. It
passes, and the change lists it with the cause for the DevEx reviewer.

Growth an exception allows: code growth passes, listed for a person's review, when an exception
of the head policy allows every line behind it. The base policy cannot hold that exception, or the
lines would not have grown, so the change adds it; an exception without a reason or a review date
is a problem the check fails on, and owner review of the exceptions file is the person's gate.

Move admission: code growth passes as an admitted move when every coupling behind the new lines
already existed in base, under the renamed modules, with at least the same count. A coupling
carries the rule's `edge.*` identity columns (see `couplings.py`), so new uses bundled into a
move, and a use whose shape changed in the move, still fail.

`check` asks the same of a commit: HEAD's scan is the base, HEAD's ledger lines are its debt, and
the lines `moved` names need no person, so `--write-ledger` records them and the hook passes them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hoglint.ledger.couplings import Coupling
from hoglint.ledger.file import LineKey, is_level_line
from hoglint.ledger.scan import GrowthColumns, ModuleLabels, ScanSummary

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
    excepted: list[GrowthItem] = field(default_factory=list)
    shrink: list[GrowthItem] = field(default_factory=list)
    shrink_causes: list[str] = field(default_factory=list)
    skipped: str = ""

    @property
    def failed(self) -> bool:
        return bool(self.code_growth)


def growth_key(line: LineKey, growth: dict[str, GrowthColumns], labels: ModuleLabels) -> GrowthKey | None:
    """A line's growth key: its identity columns the rule names, and for a (field, index) column
    the field's label of the module at that identity column, as the summary recorded it."""
    rule, identity = line
    columns = growth.get(rule)
    if columns is None:
        if is_level_line(line):
            return None
        return rule, identity
    out: list[str] = []
    for column in columns:
        if isinstance(column, int):
            if column < len(identity):
                out.append(identity[column])
            continue
        name, index = column
        module = identity[index] if index < len(identity) else ""
        out.append(labels.get(name, {}).get(module, ""))
    return rule, tuple(out)


def sums(lines: dict[LineKey, int], growth: dict[str, GrowthColumns], labels: ModuleLabels) -> dict[GrowthKey, int]:
    out: dict[GrowthKey, int] = {}
    for line, count in lines.items():
        key = growth_key(line, growth, labels)
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
            key = growth_key(line, base.growth, head_base.labels)
            if key is not None and count > base.lines.get(line, 0):
                self.grown.setdefault(key, []).append(line)

    def run(self) -> GrowthResult:
        result = GrowthResult()
        base_sums = sums(self.base.lines, self.base.growth, self.base.labels)
        head_base_sums = sums(self.head_base.lines, self.base.growth, self.head_base.labels)
        allowed = self.head.findings.allowed_keys()
        for key, after in sorted(head_base_sums.items()):
            before = base_sums.get(key, 0)
            if after <= before:
                continue
            item = GrowthItem(key[0], key[1], before, after)
            if self.admitted(key):
                result.admitted_moves.append(item)
            elif self.excepted(key, allowed):
                result.excepted.append(item)
            else:
                result.code_growth.append(item)
        result.rule_growth, result.shrink = self.engine_and_policy_change()
        if result.shrink:
            result.shrink_causes = self.head_base.envelope.causes(self.head.envelope)
        return result

    def moved(self) -> frozenset[LineKey]:
        """The head lines above their base count that only changed place: their growth key did not
        grow, or it grew as an admitted move."""
        grew = {(item.rule, item.key) for item in self.run().code_growth}
        return frozenset(line for key, lines in self.grown.items() if key not in grew for line in lines)

    def admitted(self, key: GrowthKey) -> bool:
        lines = self.grown.get(key, [])
        if not lines:
            return False
        for line in lines:
            couplings = self.head_base.witnesses.get(line, [])
            if not couplings or not all(self.existed(coupling) for coupling in couplings):
                return False
        return True

    def excepted(self, key: GrowthKey, allowed: set[LineKey]) -> bool:
        """Whether an exception of the head policy allows every line that grew under `key`."""
        lines = self.grown.get(key, [])
        return bool(lines) and all(line in allowed for line in lines)

    def existed(self, coupling: Coupling) -> bool:
        rule, src, dst, kind, values = coupling
        original = (rule, self.renames.back(src), self.renames.back(dst), kind, values)
        if original == coupling:
            return False
        return self.base.couplings.get(original, 0) >= self.head_base.couplings.get(coupling, 0)

    def engine_and_policy_change(self) -> tuple[list[GrowthItem], list[GrowthItem]]:
        """Head code under both engines and policies, per growth key of the head policy: debt only
        the head sees (rule growth), and debt only the base engine and policy see (shrink)."""
        head_sums = sums(self.head.lines, self.head.growth, self.head.labels)
        before_sums = sums(self.head_base.lines, self.head.growth, self.head_base.labels)
        grown, shrunk = [], []
        for key in sorted(set(head_sums) | set(before_sums)):
            before, after = before_sums.get(key, 0), head_sums.get(key, 0)
            if after > before:
                grown.append(GrowthItem(key[0], key[1], before, after))
            elif after < before:
                shrunk.append(GrowthItem(key[0], key[1], before, after))
        return grown, shrunk
