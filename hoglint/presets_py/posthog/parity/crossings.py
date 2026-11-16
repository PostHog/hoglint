"""Comparison 4: the crossings baseline against hoglint's edges in the baseline's line format.

`legacy_ledger` renders hoglint's edges as the old crossings scanner writes its baseline, with the
scanner's own filters. A line only one side has is explained only by a check of the uses hoglint
reads at that module for that class:

- value rows: the old scanner names a chain by its last call before a subscript, or by its very
  last call, and calls it an instance use; hoglint reads `.values()` or `.values_list()` in the
  chain, so the rows are dicts or tuples and no instance crosses;
- a type alias: the old scanner reads a class inside a union or a generic alias as a collection
  or an operator; hoglint reads it as an annotation;
- a signal sender: the old scanner reads `sender=Model` as a keyword use; hoglint reads it as the
  sender shape, which model-crossings allows. The two lines pair up.

The baseline is checked against a fresh run of the old scanner first: a line the scanner no
longer writes is stale.
"""

from __future__ import annotations

import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from hoglint.core.analysis import AnalysisOptions
from hoglint.model import Graph, Resolution
from hoglint.native.analysis import NativeAnalysis
from hoglint.presets_py.posthog import legacy_ledger
from hoglint.presets_py.posthog.parity import old_tools
from hoglint.presets_py.posthog.parity.verdict import HOGLINT_ONLY, OLD_ONLY, Comparison, Difference, Effect, Reason

TOOL = "crossings"
VALUE_METHODS = (".values(", ".values_list(")
VALUE_ROWS = Reason(
    "value-rows",
    "The chain reads value rows: hoglint sees `.values()` or `.values_list()` in it, so dicts or "
    "tuples cross and no instance does. The old scanner names the chain by its last call, or by "
    "the last call before a subscript, and counts an instance use.",
    Effect.SAME,
)
TYPE_ALIAS = Reason(
    "type-alias",
    "The class sits inside a type alias (a union or a generic). hoglint reads it as an annotation, "
    "which holds no instance; the old scanner reads a collection or an operator.",
    Effect.SAME,
)
SENDER = Reason(
    "sender-keyword",
    "`sender=Model` names the model as a signal sender. hoglint reads the sender shape, which "
    "model-crossings allows; the old scanner reads a keyword use. The old line and hoglint's line "
    "name the same uses.",
    Effect.SAME,
)
STALE = Reason(
    "stale-baseline",
    "A fresh run of the old scanner no longer writes this line.",
    Effect.SAME,
)
TYPE_ALIAS_KINDS = ("in-collection", "other(BinOp)", "other(Subscript)")


@dataclass(frozen=True)
class Use:
    """One use hoglint reads of a class in a module: its line, shape and chain."""

    line: int
    shape: str
    detail: str = ""
    chain: str = ""


@dataclass(frozen=True)
class BaselineLine:
    target: str
    module: str
    kind: str
    count: int

    @classmethod
    def parse(cls, text: str) -> BaselineLine:
        target, module, kind, count = text.split(" ")
        return cls(target, module, kind, int(count))

    @property
    def symbol(self) -> str:
        return self.target.split(".", 1)[1]

    @property
    def detail(self) -> str:
        """The method in `instance-many(<method>)`, else empty."""
        return self.kind.partition("(")[2].removesuffix(")")


def value_rows(line: BaselineLine, uses: list[Use]) -> str | None:
    if not line.kind.startswith(("instance-many", "instance-single")):
        return None
    found = [
        use
        for use in uses
        if use.shape == "scalar-chain"
        and any(method in use.chain for method in VALUE_METHODS)
        and (not line.detail or f".{line.detail}(" in use.chain)
    ]
    if len(found) < line.count:
        return None
    return "; ".join(f"line {use.line}: {use.chain}" for use in found)


def type_alias(line: BaselineLine, uses: list[Use]) -> str | None:
    if line.kind not in TYPE_ALIAS_KINDS:
        return None
    found = [use for use in uses if use.shape == "annotation"]
    if len(found) < line.count:
        return None
    return "; ".join(f"line {use.line}: annotation" for use in found)


CHECKS = ((VALUE_ROWS, value_rows), (TYPE_ALIAS, type_alias))


def compare(
    baseline: list[str], fresh: list[str], derived: list[str], uses: dict[tuple[str, str], list[Use]]
) -> Comparison:
    """`uses` holds what hoglint reads per (consumer module, class name)."""
    comparison = Comparison(TOOL, old_items=len(baseline), hoglint_items=len(derived))
    old, ours, scanned = set(baseline), set(derived), set(fresh)
    comparison.unlisted_matches = len(old & ours)
    missing = sorted(old - ours)
    extra = sorted(ours - old)
    paired: set[str] = set()
    for text in missing:
        line = BaselineLine.parse(text)
        if text not in scanned:
            comparison.differences.append(Difference(text, OLD_ONLY, STALE))
            continue
        if line.kind == "other(keyword)":
            partner = f"{line.target} {line.module} sender {line.count}"
            if partner in extra:
                paired.add(partner)
                comparison.differences.append(Difference(text, OLD_ONLY, SENDER, f"hoglint: {partner}"))
                continue
        found = uses.get((line.module, line.symbol), [])
        for reason, check in CHECKS:
            evidence = check(line, found)
            if evidence is not None:
                comparison.differences.append(Difference(text, OLD_ONLY, reason, evidence))
                break
        else:
            shapes = ", ".join(f"line {use.line}: {use.shape}" for use in found) or "no use"
            comparison.differences.append(Difference(text, OLD_ONLY, None, f"hoglint reads {shapes}"))
    for text in extra:
        if text in paired:
            comparison.differences.append(
                Difference(text, HOGLINT_ONLY, SENDER, "pairs with the old other(keyword) line")
            )
            continue
        comparison.differences.append(Difference(text, HOGLINT_ONLY, None))
    stale_fresh = sorted(scanned - old)
    if stale_fresh:
        comparison.notes.append(f"the old scanner writes {len(stale_fresh)} lines the baseline lacks")
    return comparison


def reference_uses(graph: Graph) -> dict[tuple[str, str], list[Use]]:
    out: dict[tuple[str, str], list[Use]] = defaultdict(list)
    for edge in graph.by_kind("reference"):
        target = edge.target
        if edge.resolution != Resolution.RESOLVED or target is None or not edge.shape:
            continue
        use = Use(edge.site.line, edge.shape, edge.shape_detail or "", edge.attr("chain") or "")
        out[(edge.site.module, target.symbol)].append(use)
    return out


def run(root: Path, work: Path) -> Comparison:
    fresh = old_tools.driver(root, "crossings")["lines"]
    with tempfile.TemporaryDirectory(prefix="hoglint-parity-crossings-", dir=work) as scratch:
        options = AnalysisOptions(repo=root, config=legacy_ledger.PARITY_CONFIG, work_dir=Path(scratch))
        analysis = NativeAnalysis(options).run()
        parity = legacy_ledger.Parity(root, analysis.build)
        graph = analysis.graph
        parity.produce(graph)
        derived = parity.lines(graph)
        uses = reference_uses(graph)
        failed = sorted(graph.failed_producers)
    baseline = legacy_ledger.read_baseline((root / legacy_ledger.BASELINE_PATH).read_text())
    comparison = compare(baseline, fresh, derived, uses)
    if failed:
        comparison.error = f"producers failed: {', '.join(failed)}"
    return comparison
