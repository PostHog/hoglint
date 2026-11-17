"""What one parity comparison found, and how it is printed and written.

A comparison runs an old tool and hoglint on the same tree. Each item the two disagree on is a
difference. A difference is explained only by a reason that code checked for that item; an item
no reason fits stays unexplained, and the run fails.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class Effect(StrEnum):
    """What a difference means for "hoglint checks at least what the old tool checks"."""

    SAME = "same check"
    MORE = "hoglint checks more"
    LESS = "hoglint checks less"


@dataclass(frozen=True)
class Reason:
    """Why an item differs. `decided` names who decided a difference that makes hoglint check less."""

    name: str
    text: str
    effect: Effect
    decided: str = ""


OLD_ONLY = "old tool only"
HOGLINT_ONLY = "hoglint only"


@dataclass(frozen=True)
class Difference:
    """An item one side has and the other has not. `evidence` says what the reason's check found."""

    item: str
    side: str
    reason: Reason | None = None
    evidence: str = ""


@dataclass(frozen=True)
class Match:
    """An old tool's item and the hoglint result that covers it."""

    item: str
    evidence: str


class Verdict(StrEnum):
    EQUAL = "equal"
    EXPLAINED = "explained"
    UNEXPLAINED = "unexplained"
    FAILED = "failed"


@dataclass(frozen=False)
class Comparison:
    tool: str
    old_items: int = 0
    hoglint_items: int = 0
    matches: list[Match] = field(default_factory=list)
    # Matched items too many to list (the map's pairs): counted, not written.
    unlisted_matches: int = 0
    differences: list[Difference] = field(default_factory=list)
    not_compared: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    seconds: float = 0.0
    error: str = ""

    @property
    def matched(self) -> int:
        return len(self.matches) + self.unlisted_matches

    @property
    def unexplained(self) -> list[Difference]:
        return [d for d in self.differences if d.reason is None]

    @property
    def less(self) -> list[Difference]:
        """Differences where hoglint checks less and no decision names it."""
        return [
            d
            for d in self.differences
            if d.reason is not None and d.reason.effect is Effect.LESS and not d.reason.decided
        ]

    @property
    def verdict(self) -> Verdict:
        if self.error:
            return Verdict.FAILED
        if self.unexplained:
            return Verdict.UNEXPLAINED
        if self.differences:
            return Verdict.EXPLAINED
        return Verdict.EQUAL

    def verdict_text(self) -> str:
        if self.verdict is Verdict.UNEXPLAINED:
            return f"unexplained ({len(self.unexplained)})"
        return self.verdict.value

    def render(self) -> str:
        """The per-tool file: every difference grouped by reason, then the unexplained ones, then
        the old tool's items hoglint covers."""
        lines = [
            f"{self.tool}: {self.verdict_text()}",
            f"old tool items {self.old_items}, hoglint items {self.hoglint_items}, matched {self.matched}, "
            f"differences {len(self.differences)}, unexplained {len(self.unexplained)}, "
            f"hoglint checks less with no decision {len(self.less)}, {self.seconds:.1f} s",
        ]
        if self.error:
            lines += ["", "failed:", self.error]
        for note in self.notes:
            lines.append(f"note: {note}")
        for item in self.not_compared:
            lines.append(f"not compared: {item}")
        groups: dict[Reason, list[Difference]] = defaultdict(list)
        for difference in self.differences:
            if difference.reason is not None:
                groups[difference.reason].append(difference)
        for reason, differences in sorted(groups.items(), key=lambda pair: (pair[0].effect, pair[0].name)):
            decided = f", decided by {reason.decided}" if reason.decided else ""
            lines += ["", f"## {reason.name} ({reason.effect}{decided}): {len(differences)}", reason.text]
            lines += [render_difference(d) for d in differences]
        if self.unexplained:
            lines += ["", f"## unexplained: {len(self.unexplained)}"]
            lines += [render_difference(d) for d in self.unexplained]
        if self.matches:
            lines += ["", f"## covered by hoglint: {len(self.matches)}"]
            lines += [f"{m.item}\n    <- {m.evidence}" for m in self.matches]
        return "\n".join(lines) + "\n"


def render_difference(difference: Difference) -> str:
    line = f"[{difference.side}] {difference.item}"
    return f"{line}\n    {difference.evidence}" if difference.evidence else line


def verdict_table(comparisons: list[Comparison]) -> str:
    header = ("tool", "verdict", "old", "hoglint", "matched", "differ", "unexplained", "less", "seconds")
    rows = [header]
    for c in comparisons:
        rows.append(
            (
                c.tool,
                c.verdict_text(),
                str(c.old_items),
                str(c.hoglint_items),
                str(c.matched),
                str(len(c.differences)),
                str(len(c.unexplained)),
                str(len(c.less)),
                f"{c.seconds:.1f}",
            )
        )
    widths = [max(len(row[i]) for row in rows) for i in range(len(header))]
    return "\n".join("  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip() for row in rows) + "\n"


def write_files(comparisons: list[Comparison], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for comparison in comparisons:
        (out / f"{comparison.tool}.txt").write_text(comparison.render())
    (out / "verdicts.txt").write_text(verdict_table(comparisons))
