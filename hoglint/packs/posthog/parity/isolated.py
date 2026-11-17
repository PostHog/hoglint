"""Comparison 5: who may skip the full test suite today, against hoglint's `isolated` level.

Today's answer comes from three places, read as the monorepo reads them: test selection's own
reader (`getIsolatedProducts` in `turbo-discover.js`: a `backend:contract-check` script with
narrowed inputs), the product lint's per-product rung (Isolated), and the not-isolated list in
`products/isolation_baseline.txt`. hoglint's answer is the level as the preset builds it, with a
ledger written from the run.

The owner chose the level's settings on Nov 16 (the "safety" setting, and the structure today's
lint asks for); they are not built. A product whose verdicts differ only because of them says
so:

- hoglint blocks it, and every block is a loose type or a computed import, which the safety
  setting does not block on;
- hoglint passes it, and it lacks the structure today's lint asks for (a sealed facade with
  contracts, a contract-check script with narrowed inputs), which the structure setting asks for.

A block of the wiring rule stays under the chosen setting: hoglint names wiring the product lint
does not watch, or cannot place.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hoglint.packs.posthog.parity import old_tools
from hoglint.packs.posthog.parity.hoglint_check import WIRING_RULE, HoglintFacts
from hoglint.packs.posthog.parity.verdict import Comparison, Difference, Effect, Match, Reason

TOOL = "isolated"
BOTH = "verdicts differ"
OWN_PRESENTATION = "presentation-uses-facade"
BASELINE_PATH = "products/isolation_baseline.txt"
PENDING = "the owner, Nov 16 (not built)"
# Blocks the safety setting does not block on: a loose or unresolved type in a rule other than
# the wiring rule, and a computed import that hoglint cannot follow.
LOOSE_BLOCKS = ("unknown",)
UNFOLLOWED_IMPORTS = "dynamic-imports"
PENDING_SAFETY = Reason(
    "pending-safety-setting",
    "hoglint's level as built blocks on every loose or unresolved type and every computed import. "
    "The safety setting chosen on Nov 16 blocks only on unwatched wiring, core reaching an "
    "unwatched file and its own presentation past the facade; this product has none of those.",
    Effect.MORE,
)
PENDING_STRUCTURE = Reason(
    "pending-structure-setting",
    "hoglint's level as built asks only for no blocking finding. Today's lint also asks for a "
    "sealed facade with contracts and a contract-check script with narrowed inputs, which this "
    "product lacks; the structure setting chosen on Nov 16 asks for the same.",
    Effect.LESS,
    decided=PENDING,
)
SAFETY_BLOCKS = Reason(
    "safety-setting-blocks",
    "hoglint's level as built blocks it on loose types only, but the safety setting chosen on Nov 16 "
    "blocks it too: core imports a file of the product at module level that its contract-check "
    "inputs do not watch, or its presentation reaches past its own facade.",
    Effect.MORE,
)
WIRING_STAYS = Reason(
    "wiring-blocks",
    "hoglint's wiring rule blocks it: a file a test outside the product drives, the request path "
    "reaches, a receiver or a hook callback, which its contract-check inputs do not watch, or "
    "which hoglint cannot place. This block stays under the setting chosen on Nov 16.",
    Effect.MORE,
)


@dataclass(frozen=True)
class Row:
    product: str
    skips_today: bool
    lint_rung: str
    baseline_isolated: bool | None
    withheld: str
    hoglint: bool
    blocks: tuple[str, ...]

    def cells(self) -> list[str]:
        def yes(value: bool | None) -> str:
            return "-" if value is None else "yes" if value else "no"

        return [
            self.product,
            yes(self.skips_today),
            self.lint_rung or "-",
            yes(self.baseline_isolated),
            "withheld" if self.withheld else "",
            "pass" if self.hoglint else "fail",
        ]


def block_rule(block: str) -> str:
    return block.split(":", 1)[0].split(" ", 1)[0]


def block_kind(block: str) -> str:
    """unknown, strict, new finding, incomplete, or did not run."""
    parts = block.split(": ")
    return parts[1] if len(parts) > 1 else "did not run"


def summary(blocks: tuple[str, ...]) -> str:
    counts: dict[str, int] = {}
    for block in blocks:
        key = f"{block_rule(block)} {block_kind(block).split(',')[0]}"
        counts[key] = counts.get(key, 0) + 1
    return ", ".join(f"{key} x{n}" for key, n in sorted(counts.items()))


def missing_structure(status: dict | None) -> list[str]:
    """The parts of the structure today's lint asks for that a product lacks."""
    if status is None:
        return ["no backend code"]
    parts = {
        "facade with contracts": status["has_facade_contracts"],
        "real facade api": status["has_real_facade"],
        "tach interface": status["has_tach_interface"],
        "no legacy interface leak": not status["has_legacy_leaks"],
        "no open presentation bypass": not status["bypass_entries"],
        "contract-check script": status["has_contract_check_script"],
        "narrowed contract-check inputs": status["has_narrowed_turbo"],
    }
    return [name for name, present in parts.items() if not present]


def safety_blocks(product: str, facts: HoglintFacts) -> list[str]:
    """What the safety setting blocks a product on besides unwatched wiring: core reaching a file
    its inputs do not watch, and its own presentation reaching past its facade."""
    found = [f"core reaches unwatched {path}" for path in facts.core_reach_unwatched.get(product, [])]
    for row in facts.findings:
        if row.rule != OWN_PRESENTATION or len(row.identity) < 2:
            continue
        if facts.package_of_name(row.identity[0]) == product == facts.package_of_name(row.identity[1]):
            found.append(f"{OWN_PRESENTATION} {row.identity[0]} -> {row.identity[1]}")
    return found


def judge(row: Row, status: dict | None, facts: HoglintFacts) -> Match | Difference:
    item = " | ".join(row.cells())
    if row.skips_today == row.hoglint:
        return Match(item, summary(row.blocks) if row.blocks else "no block")
    if row.skips_today:
        if any(block_rule(b) == WIRING_RULE for b in row.blocks):
            return Difference(item, BOTH, WIRING_STAYS, summary(row.blocks))
        if all(block_kind(b) in LOOSE_BLOCKS or block_rule(b) == UNFOLLOWED_IMPORTS for b in row.blocks):
            stays = safety_blocks(row.product, facts)
            if stays:
                return Difference(item, BOTH, SAFETY_BLOCKS, "; ".join(stays))
            return Difference(item, BOTH, PENDING_SAFETY, summary(row.blocks))
        return Difference(item, BOTH, None, summary(row.blocks))
    missing = missing_structure(status)
    if missing:
        return Difference(item, BOTH, PENDING_STRUCTURE, "lacks: " + ", ".join(missing))
    return Difference(item, BOTH, None, f"withheld: {row.withheld}" if row.withheld else "")


def not_isolated(text: str) -> set[str]:
    return {line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")}


def compare(skips: list[str], statuses: dict[str, dict], baseline: set[str], facts: HoglintFacts) -> Comparison:
    comparison = Comparison(TOOL)
    groups = set(facts.level_passing) | set(facts.level_failing)
    rows = []
    for product in sorted(set(statuses) | groups | set(skips)):
        status = statuses.get(product)
        row = Row(
            product=product,
            skips_today=product in skips,
            lint_rung=status["rung"] if status else "",
            baseline_isolated=(product not in baseline) if status else None,
            withheld=(status or {}).get("skip_withheld_reason") or "",
            hoglint=product in facts.level_passing,
            blocks=tuple(facts.level_failing.get(product, ())),
        )
        rows.append(row)
        comparison.old_items += 1
        outcome = judge(row, status, facts)
        if isinstance(outcome, Match):
            comparison.matches.append(outcome)
        else:
            comparison.differences.append(outcome)
    lint_isolated = {name for name, status in statuses.items() if status["isolation_enabled"]}
    if lint_isolated != set(skips):
        comparison.notes.append(
            f"the product lint's Isolated rung and test selection disagree: lint only {sorted(lint_isolated - set(skips))}, "
            f"test selection only {sorted(set(skips) - lint_isolated)}"
        )
    recorded = {name for name in statuses if name not in baseline}
    if recorded != lint_isolated:
        comparison.notes.append(
            f"{BASELINE_PATH} and the lint's rung disagree: baseline only {sorted(recorded - lint_isolated)}, "
            f"lint only {sorted(lint_isolated - recorded)}"
        )
    reached = ", ".join(f"{product} ({len(paths)})" for product, paths in facts.core_reach_unwatched.items())
    comparison.notes.append(f"core imports unwatched files of: {reached or 'no product'}")
    comparison.hoglint_items = len(facts.level_passing)
    comparison.notes.append(
        f"skips today {len(skips)}, hoglint passes {len(facts.level_passing)} of {len(groups)} product groups; "
        "columns: product | skips today | lint rung | baseline isolated | withheld | hoglint"
    )
    return comparison


def run(root: Path, facts: HoglintFacts) -> Comparison:
    skips = old_tools.skip_list(root)
    statuses = old_tools.driver(root, "product-lint")["products"]
    baseline = not_isolated((root / BASELINE_PATH).read_text())
    return compare(skips, statuses, baseline, facts)
