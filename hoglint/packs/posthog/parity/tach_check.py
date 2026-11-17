"""Comparison 2: tach check, as `hogli lint:tach` runs it, against hoglint's findings.

tach's own debt sits in its config, so the passes run twice: as configured, and with the debt
taken out of a copy of tach.toml (see `drivers.tach_check`). Each failing line is matched to a
hoglint finding, or to a reason:

- A dependency line (module A cannot depend on B) is covered by a finding of a rule that replaced
  `depends_on` on the same package pair. Otherwise hoglint does not check it, by the owner's
  decision that dropped `depends_on`.
- An interface line (a path that is not part of B's public interface) is covered by a finding at
  that line that uses the path. Otherwise the reason must be checked for the line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from hoglint.packs.posthog.parity import old_tools
from hoglint.packs.posthog.parity.hoglint_check import HoglintFacts
from hoglint.packs.posthog.parity.verdict import OLD_ONLY, Comparison, Difference, Effect, Match, Reason

TOOL = "tach-check"
REPLACED_DEPENDS_ON = frozenset({"no-new-package-cycles", "foundation-direction", "infrastructure-direction"})
DEPENDENCY_RUNS = ("ci-dependencies", "exposed-dependencies")
INTERFACE_RUNS = ("ci-interfaces", "exposed-interfaces")
LOCATION_RULE = "product-code-at-home"
DEPENDS_ON_DROPPED = Reason(
    "depends-on-dropped",
    "tach fails any dependency `depends_on` does not list. hoglint checks only the rules that replaced "
    "it: no-new-package-cycles, foundation-direction and infrastructure-direction, and none has a "
    "finding on this package pair.",
    Effect.LESS,
    decided="the owner, Nov 5 and 9",
)
INSIDE_ONE_PACKAGE = Reason(
    "inside-one-package",
    "hoglint places the importing file in the package it imports from: product code that still "
    "lives outside products/ keeps its product id (B12). An import inside one package is no "
    "crossing; product-code-at-home reports the file's place instead.",
    Effect.SAME,
)
QUOTED = re.compile(r"'([^']+)'")


@dataclass(frozen=True)
class TachLine:
    run: str
    path: str
    line: int
    message: str

    @property
    def imported(self) -> str:
        """The dotted path tach names first: the import it fails."""
        found = QUOTED.search(self.message)
        return found.group(1) if found else ""

    def text(self) -> str:
        return f"{self.run} {self.path}:{self.line} {self.message}"


def dependency_difference(line: TachLine, facts: HoglintFacts) -> Match | Difference:
    pair = (facts.package_of_path(line.path) or "?", facts.package_of_name(line.imported) or "?")
    for rule in sorted(REPLACED_DEPENDS_ON):
        rows = facts.with_identity(rule, pair)
        if rows:
            return Match(line.text(), rows[0].text())
    return Difference(line.text(), OLD_ONLY, DEPENDS_ON_DROPPED, f"package pair {pair[0]} -> {pair[1]}")


def interface_difference(line: TachLine, facts: HoglintFacts) -> Match | Difference:
    rows = facts.naming(line.path, line.line, line.imported)
    if rows:
        return Match(line.text(), "; ".join(row.text() for row in rows))
    importer, target = facts.package_of_path(line.path), facts.package_of_name(line.imported)
    module = facts.module_of_path.get(line.path, "")
    placed = [row for row in facts.findings if row.rule == LOCATION_RULE and module in row.subjects]
    if importer is not None and importer == target and placed:
        return Difference(line.text(), OLD_ONLY, INSIDE_ONE_PACKAGE, f"package {importer}; {placed[0].text()}")
    return Difference(line.text(), OLD_ONLY, None, f"importer package {importer}, target package {target}")


def compare(runs: dict[str, list[dict]], facts: HoglintFacts) -> Comparison:
    comparison = Comparison(TOOL)
    for run in (*DEPENDENCY_RUNS, *INTERFACE_RUNS):
        judge = dependency_difference if run in DEPENDENCY_RUNS else interface_difference
        for raw in runs.get(run, []):
            line = TachLine(run, raw["path"], raw["line"], raw["message"])
            comparison.old_items += 1
            outcome = judge(line, facts)
            if isinstance(outcome, Match):
                comparison.matches.append(outcome)
            else:
                comparison.differences.append(outcome)
    comparison.hoglint_items = len(comparison.matches)
    counts = ", ".join(f"{run} {len(runs.get(run, []))}" for run in (*DEPENDENCY_RUNS, *INTERFACE_RUNS))
    comparison.notes.append(f"failing lines per run: {counts}")
    return comparison


def run(root: Path, facts: HoglintFacts) -> Comparison:
    found = old_tools.driver(root, "tach-check")
    comparison = compare(found["runs"], facts)
    comparison.notes.append(
        f"exposed runs: depends_on emptied on {found['emptied_depends_on']} modules; "
        f"{len(found['removed_patterns'])} legacy expose patterns removed from product interfaces"
    )
    comparison.not_compared.append(
        "`# tach-ignore` comments: tach has no switch to turn them off, so what they hide is not in the exposed runs"
    )
    return comparison
