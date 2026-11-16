"""Comparison 3: the import-linter contracts against hoglint's findings.

A contract keeps its debt in `ignore_imports`, so as configured every contract is kept and there
is nothing to compare. The driver checks each contract again with that list taken out (in memory)
and says which entry hid each violation. Each violation is then matched to a finding of the rule
that stands in for the contract, or to a reason:

- the entry that hid it is a pattern (it holds a wildcard), which is part of the contract's own
  shape, such as "presentation may import any facade": the contract allows it too;
- the contract has no hoglint rule, so hoglint does not check it.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from functools import cache
from pathlib import Path

from hoglint.presets_py.posthog.parity import old_tools
from hoglint.presets_py.posthog.parity.hoglint_check import FindingRow, HoglintFacts
from hoglint.presets_py.posthog.parity.verdict import OLD_ONLY, Comparison, Difference, Effect, Match, Reason

TOOL = "import-linter"
# The rule that stands in for each contract, by the contract's name in pyproject.toml.
CONTRACT_RULES: dict[str, frozenset[str]] = {
    "packages/owners-yaml is a leaf": frozenset({"leaves-stay-leaves"}),
    "packages/personhog-proto is a leaf": frozenset({"leaves-stay-leaves"}),
    "sources vendors import shared code only from the sources SDK": frozenset({"sources-vendors-use-sdk"}),
    "sources vendors do not import each other": frozenset({"sources-vendors-independent"}),
    "presentation must use facade": frozenset({"presentation-uses-facade"}),
    "routes must only import presentation": frozenset({"presentation-uses-facade"}),
    "webhook consumers must only import facade": frozenset({"webhook-consumers-use-facade"}),
    "facade must not import presentation or DRF": frozenset({"facade-no-drf"}),
    "facade contracts and enums must not import Django (use posthog.enums for choices)": frozenset(
        {"contracts-no-django"}
    ),
}
ALLOWED_BY_PATTERN = Reason(
    "allowed-by-contract-pattern",
    "The ignore entry that hides it is a pattern (it holds a wildcard), part of the contract's own "
    "shape, such as presentation importing any product's facade. The contract allows the import, "
    "and so does hoglint.",
    Effect.SAME,
)
NO_RULE = Reason(
    "contract-without-rule",
    "No hoglint rule stands in for this contract, so hoglint does not check the import.",
    Effect.LESS,
)
# Contracts whose ignore list holds no debt: each entry is part of the contract's shape, which the
# stand-in rule writes as its scope and its `unless`.
SHAPE_ONLY = frozenset(
    {
        "sources vendors import shared code only from the sources SDK",
        "sources vendors do not import each other",
        "webhook consumers must only import facade",
    }
)
ALLOWED_BY_SHAPE = Reason(
    "allowed-by-contract-shape",
    "The ignore entry that hides it names one of the contract's own exceptions (the sources SDK may "
    "import core), and the stand-in rule holds the same exception: no finding of the rule names "
    "the import. The contract allows the import, and so does hoglint.",
    Effect.SAME,
)


def is_pattern(entry: str) -> bool:
    return "*" in entry


def chain_text(contract: str, chain: list[dict]) -> str:
    steps = " -> ".join([chain[0]["importer"], *(step["imported"] for step in chain)])
    lines = ",".join(str(n) for n in chain[0]["line_numbers"])
    return f"{contract}: {steps} (line {lines})"


def statement_lines(source: str, line: int) -> list[int]:
    """The lines of the import statement that starts at `line`: import-linter gives the statement's
    first line, hoglint the line of each name it imports."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import | ast.ImportFrom) and node.lineno == line:
            return list(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return [line]


def covering(
    chain: list[dict], rules: frozenset[str], facts: HoglintFacts, read_source: Callable[[str], str]
) -> list[FindingRow]:
    """Findings of the contract's rules on the chain's first import: a direct import at its
    statement, or, for a chain through other modules, a reach finding that starts at the same
    module."""
    first = chain[0]
    if not rules:
        return []
    if len(chain) > 1:
        return [
            row
            for rule in sorted(rules)
            for row in facts.findings
            if row.rule == rule and row.identity[:1] == (first["importer"],)
        ]
    path = facts.path_of_module(first["importer"])
    if path is None:
        return []
    source = read_source(path)
    found: list[FindingRow] = []
    for start in first["line_numbers"]:
        for line in statement_lines(source, start):
            found += [row for row in facts.naming(path, line, first["imported"], rules) if row not in found]
    return found


def judge(
    contract: str, violation: dict, rules: frozenset[str], facts: HoglintFacts, read_source: Callable[[str], str]
) -> Match | Difference:
    chain = violation["chain"]
    item = chain_text(contract, chain)
    rows = covering(chain, rules, facts, read_source)
    if rows:
        return Match(item, "; ".join(row.text() for row in rows))
    hidden = [entry for entry in violation["hidden_by"] if entry]
    if hidden and all(is_pattern(entry) for entry in hidden):
        return Difference(item, OLD_ONLY, ALLOWED_BY_PATTERN, f"hidden by '{hidden[0]}'")
    if hidden and contract in SHAPE_ONLY:
        return Difference(item, OLD_ONLY, ALLOWED_BY_SHAPE, f"hidden by '{hidden[0]}'; no finding of {sorted(rules)}")
    if not rules:
        return Difference(item, OLD_ONLY, NO_RULE, f"hidden by '{hidden[0]}'" if hidden else "")
    return Difference(item, OLD_ONLY, None, f"hidden by '{hidden[0]}'" if hidden else "not hidden")


def compare(contracts: list[dict], facts: HoglintFacts, read_source: Callable[[str], str]) -> Comparison:
    """`read_source` gives a file's text by its repo path; each file is read once."""
    read_source = cache(read_source)
    comparison = Comparison(TOOL)
    for contract in contracts:
        name = contract["name"]
        if name not in CONTRACT_RULES:
            comparison.not_compared.append(f"contract '{name}': the comparison does not know it")
            continue
        rules = CONTRACT_RULES[name]
        if not rules:
            comparison.not_compared.append(
                f"contract '{name}': no hoglint rule stands in for it; a new violation fails "
                "import-linter and passes hoglint"
            )
        for violation in contract["violations"]:
            comparison.old_items += 1
            outcome = judge(name, violation, rules, facts, read_source)
            if isinstance(outcome, Match):
                comparison.matches.append(outcome)
            else:
                comparison.differences.append(outcome)
        hiding = {entry for violation in contract["violations"] for entry in violation["hidden_by"] if entry}
        inert = [entry for entry in contract["ignore_imports"] if entry not in hiding]
        flagged = [entry for entry in inert if not is_pattern(entry) and flags_entry(entry, rules, facts)]
        state = "kept" if contract["kept_as_configured"] else "broken"
        comparison.notes.append(
            f"'{name}': {state} as configured; {len(contract['violations'])} violations with ignore_imports "
            f"taken out; {len(inert)} of {len(contract['ignore_imports'])} ignore entries hide none of them "
            f"(the contract does not check those imports), and hoglint has a finding for {len(flagged)} of them"
        )
    return comparison


def flags_entry(entry: str, rules: frozenset[str], facts: HoglintFacts) -> bool:
    """Whether a finding of the contract's rules names the import a plain ignore entry names."""
    importer, _, imported = entry.partition(" -> ")
    return any(
        row.rule in rules
        and row.identity[:1] == (importer,)
        and row.identity[1:2] != ()
        and (row.identity[1] == imported or row.identity[1].startswith(imported + "."))
        for row in facts.findings
    )


def run(root: Path, facts: HoglintFacts) -> Comparison:
    contracts = old_tools.driver(root, "import-linter")["contracts"]
    return compare(contracts, facts, lambda path: (root / path).read_text())
