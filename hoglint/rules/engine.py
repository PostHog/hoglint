"""Runs every rule over one graph, then exceptions, then the ledger, then levels.

Order: rules that read only edges first, coverage after them (it reads other rules' findings),
levels last (they read every result). Nothing here deletes an edge.
"""

from __future__ import annotations

import datetime
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from hoglint.config.loader import ConfigError
from hoglint.config.model import Config, RuleSpec
from hoglint.index import GraphIndex, index_of
from hoglint.labels import CandidateCache, Labeler
from hoglint.ledger.equality import EqualityResult, check_equality
from hoglint.ledger.file import LedgerFile, LineKey, level_line
from hoglint.model import SCHEMA_VERSION, Finding, FindingStatus, Graph, IncompleteCause, Outcome, RuleResult
from hoglint.rules.base import IncompleteItem, RuleContext, RuleRun, RuleType, context_class, labeler_class, rule_kinds
from hoglint.rules.edges import EdgePrimitive, EntrypointPrimitive, SignaturePrimitive, UseShapePrimitive
from hoglint.rules.entities import CoveragePrimitive, EdgeInventory, EntityInventory, LayoutPrimitive
from hoglint.rules.exceptions import ExceptionReport, ExceptionsFile, apply_exceptions
from hoglint.rules.exports import ExportPrimitive
from hoglint.rules.graphs import CyclePrimitive, ReachPrimitive
from hoglint.rules.levels import LevelEvaluator, LevelResult

# Every source the run could not read is an incomplete item of this rule, keyed on its path: the
# result holds none of the file's facts. The `unparsed` edge with a read error stands for the file.
UNREADABLE = "source:unreadable"
READ_ERROR = ("status", "read_error")

# Every literal path of an assignment that no file has, and every symbol an assignment or an export
# names that no symbol has, is an incomplete item of this rule: the labels it sets are missing.
STALE_NAMES = "config:stale-name"
GLOB_CHARACTERS = frozenset("*?[{")


def provider_rule(name: str) -> str:
    """The rule of a provider that failed: one incomplete item of it says why."""
    return f"provider:{name}"


def run_level_rules(config: Config) -> tuple[str, ...]:
    """The rules every run makes from the config and the whole tree, not from one file: their
    items belong to no file, so every run makes them again."""
    return (STALE_NAMES, UNREADABLE, *(provider_rule(name) for name in sorted(config.providers)))


class StaleNames:
    """The names the config writes out in full, checked against the tree: the config loads before
    the tree is read, so only a run can tell that one names nothing."""

    def __init__(self, config: Config, labeler: Labeler) -> None:
        self.config = config
        self.paths = labeler.path_to_module
        self.modules = labeler.graph.modules
        self.facts = labeler.facts

    def items(self) -> list[IncompleteItem]:
        out: list[IncompleteItem] = []
        for assignment in self.config.assigns:
            for path in (*assignment.match, *assignment.except_paths):
                if GLOB_CHARACTERS.isdisjoint(path) and path not in self.paths:
                    out.append(self.item(path, f"{path} is named by {assignment.label()} but no file has it"))
            if assignment.symbols is not None:
                for name in assignment.symbols.names:
                    if not self.known(name):
                        out.append(self.item(name, f"{name} is named by {assignment.label()} but no symbol has it"))
        for export in self.config.exports.values():
            for name in export.symbols:
                if not self.known(name):
                    out.append(self.item(name, f"{name} is named by {export.label()} but no symbol has it"))
        return out

    def known(self, name: str) -> bool:
        return self.facts.has(name) or name in self.modules

    @staticmethod
    def item(key: str, reason: str) -> IncompleteItem:
        return IncompleteItem(STALE_NAMES, key, reason)


# Every rule type, one per mode, in the order the test generator draws them.
RULE_TYPES: tuple[type[RuleType], ...] = (
    EdgePrimitive,
    EntrypointPrimitive,
    UseShapePrimitive,
    SignaturePrimitive,
    ExportPrimitive,
    ReachPrimitive,
    CyclePrimitive,
    LayoutPrimitive,
    CoveragePrimitive,
    EdgeInventory,
    EntityInventory,
)
PRIMITIVES: dict[str, type[RuleType]] = {cls.PRIMITIVE: cls for cls in RULE_TYPES if not cls.MODE_KEY}


def rule_type(primitive: str, classify: bool) -> type[RuleType] | None:
    """The class that runs a rule of `primitive`, None for no primitive. An inventory over edges
    (`classify`) and one over entities (`exactly_one`) share a name in config but not a shape."""
    if primitive == "inventory":
        return EdgeInventory if classify else EntityInventory
    return PRIMITIVES.get(primitive)


def primitive_of(spec: RuleSpec) -> type[RuleType]:
    """The class that runs a rule."""
    found = rule_type(spec.primitive, bool(spec.classify))
    assert found is not None, spec.primitive
    return found


@dataclass(frozen=False, slots=True)
class RunResult:
    config: Config
    labeler: Labeler
    runs: dict[str, RuleRun] = field(default_factory=dict)
    levels: dict[str, LevelResult] = field(default_factory=dict)
    exceptions: ExceptionReport = field(default_factory=ExceptionReport)
    scan: dict[LineKey, int] = field(default_factory=dict)
    equality: EqualityResult | None = None
    ledger: LedgerFile | None = None

    def findings(self) -> list[Finding]:
        return [finding for run in self.runs.values() for finding in run.findings]

    def advisory(self, rule: str) -> bool:
        spec = self.config.rules.get(rule)
        return spec is not None and spec.report == "advisory"

    def violations(self) -> list[Finding]:
        """New findings of rules that are not advisory: the few rows a check prints."""
        return [
            run.findings.finding(row)
            for rule, run in self.runs.items()
            if not self.advisory(rule)
            for row in run.findings.rows_with(FindingStatus.NEW)
        ]

    def incomplete(self) -> list[IncompleteItem]:
        return [item for run in self.runs.values() for item in run.incomplete if not self.advisory(item.rule)]

    def blocking_incomplete(self) -> list[IncompleteItem]:
        return [
            item for item in self.incomplete() if item.cause in (IncompleteCause.INTRODUCED, IncompleteCause.EXTRACTOR)
        ]

    def failed(self) -> bool:
        equality_failed = self.equality is not None and not self.equality.equal
        return bool(self.violations() or equality_failed or self.exceptions.stale or self.exceptions.problems)

    def exit_code(self) -> int:
        if self.blocking_incomplete():
            return 2
        return 1 if self.failed() else 0

    def rule_results(self) -> list[RuleResult]:
        out = []
        for rule_id, run in self.runs.items():
            new = [f for f in run.findings if f.status is FindingStatus.NEW]
            outcome = Outcome.INCOMPLETE if run.incomplete else Outcome.VIOLATIONS if new else Outcome.CLEAN
            out.append(
                RuleResult(rule_id, outcome, list(run.findings), [(item.cause, item.reason) for item in run.incomplete])
            )
        return out


class Engine:
    def __init__(
        self,
        config: Config,
        graph: Graph,
        repo_root: Path | None = None,
        today: datetime.date | None = None,
        index: GraphIndex | None = None,
        candidates: CandidateCache | None = None,
    ) -> None:
        self.config = config
        self.graph = graph
        self.repo_root = repo_root
        self.today = today or datetime.date.today()
        index = index or index_of(graph)
        self.labeler = labeler_class(index)(config, graph, repo_root, index, candidates)

    def failed_producers(self, spec: RuleSpec) -> list[str]:
        """A producer named in `requires` (any version), or one that would have made an edge kind the rule reads."""
        kinds = set(rule_kinds(spec, self.config.views))
        out = []
        for name, failure in sorted(self.graph.failed_producers.items()):
            if name.partition("@")[0] in spec.requires or failure.kinds & kinds:
                out.append(f"producer {name} failed: {failure.reason}")
        return out

    def empty_expected_kinds(self) -> set[str]:
        """Kinds marked `expected` that no symbol matched: whatever produces them found nothing."""
        symbols = self.graph.symbols
        return {
            name
            for name, spec in self.config.kinds.items()
            if spec.expected and not any(symbol in symbols for symbol in self.labeler.kind_members(spec))
        }

    def kinds_read(self, spec: RuleSpec) -> set[str]:
        """Kind names a rule compares against, and for an export rule the kinds its shapes name."""
        out: set[str] = set()
        for selector in (spec.from_, spec.select, spec.deny, spec.allow, spec.require, spec.unless):
            if selector is not None:
                out |= selector.compared_values("kind")
        if spec.primitive == "export":
            for name in spec.exports or tuple(self.config.exports):
                policy = self.config.shapes[self.config.exports[name].shape]
                out |= {*policy.deny, *policy.data, *policy.allow}
        return out

    def missing_kinds(self, spec: RuleSpec, empty: set[str]) -> list[str]:
        return [
            f"kind {kind} matched no symbol, but [kinds] marks it expected"
            for kind in sorted(self.kinds_read(spec) & empty)
        ]

    def raise_conflicts(self, ctx: RuleContext) -> None:
        """Stops the run with every label set two ways that it met so far: what the labeler met,
        and what the rule context met outside it, worded by the labeler (D44). The Python engine
        meets a conflict where it reads the label, in a rule or a level, and stops after each."""
        ctx.met_conflicts()
        if self.labeler.conflicts:
            raise ConfigError(sorted(set(self.labeler.conflicts)))

    def rule_context(self) -> RuleContext:
        return context_class(self.labeler.index)(self.config, self.graph, self.labeler, self.repo_root)

    @staticmethod
    def add_incomplete_run(ctx: RuleContext, rule_id: str, items: list[IncompleteItem]) -> None:
        """A run of a rule no config declares that holds only incomplete items."""
        run = RuleRun(RuleSpec(rule_id, "layout", ledger=False))
        run.incomplete.extend(items)
        ctx.results[rule_id] = run

    def run_rules(self, ctx: RuleContext | None = None) -> dict[str, RuleRun]:
        """Every rule's run."""
        ctx = ctx or self.rule_context()
        ctx.find_conflicts()
        self.raise_conflicts(ctx)
        empty = self.empty_expected_kinds()
        ordered = sorted(self.config.rules.values(), key=lambda spec: spec.primitive == "coverage")
        for spec in ordered:
            failures = self.failed_producers(spec) + self.missing_kinds(spec, empty)
            if failures:
                run = RuleRun(spec)
                run.incomplete.extend(
                    IncompleteItem(spec.id, reason, reason, (), IncompleteCause.EXTRACTOR) for reason in failures
                )
                ctx.results[spec.id] = run
                continue
            ctx.results[spec.id] = primitive_of(spec)(ctx, spec).evaluate()
        self.raise_conflicts(ctx)
        for name in sorted(self.labeler.providers.failures):
            rule_id = provider_rule(name)
            reason = f"provider {name} failed: {self.labeler.providers.failures[name]}"
            self.add_incomplete_run(
                ctx, rule_id, [IncompleteItem(rule_id, reason, reason, (), IncompleteCause.EXTRACTOR)]
            )
        stale = StaleNames(self.config, self.labeler).items()
        if stale:
            self.add_incomplete_run(ctx, STALE_NAMES, stale)
        unread = sorted({edge.site.path for edge in self.graph.by_kind("unparsed") if READ_ERROR in edge.attrs})
        if unread:
            items: list[IncompleteItem] = []
            for path in unread:
                reason = f"{path} could not be read, so its facts are missing"
                items.append(IncompleteItem(UNREADABLE, path, reason, (), IncompleteCause.EXTRACTOR))
            self.add_incomplete_run(ctx, UNREADABLE, items)
        self.raise_conflicts(ctx)
        return ctx.results

    def evaluate(
        self,
        ledger: LedgerFile | None = None,
        exceptions: ExceptionsFile | None = None,
        rewrite_ledger: bool = False,
    ) -> RunResult:
        """One run. With `rewrite_ledger`, findings and levels are judged against the ledger this
        run writes (its own scan), so the next check over the same code finds that ledger equal."""
        ctx = self.rule_context()
        result = RunResult(self.config, self.labeler, self.run_rules(ctx))
        if exceptions is not None:
            result.exceptions = apply_exceptions(result.runs, exceptions, self.today)
        result.scan = self.scan_lines(result)
        if rewrite_ledger:
            ledger = LedgerFile(SCHEMA_VERSION, self.config.policy_hash, exists=True)
        if ledger is not None:
            self.mark_accepted(result, ledger.lines if not rewrite_ledger else result.scan)
        for name, spec in self.config.levels.items():
            level = LevelEvaluator(spec, self.labeler, result.runs).evaluate()
            result.levels[name] = level
            for group in level.passing:
                result.scan[level_line(name, group)] = 1
        self.raise_conflicts(ctx)
        if rewrite_ledger and ledger is not None:
            ledger.lines = dict(result.scan)
            result.ledger = ledger
        if ledger is not None:
            result.equality = check_equality(result.scan, ledger, SCHEMA_VERSION, self.config.policy_hash)
        return result

    def scan_lines(self, result: RunResult) -> dict[LineKey, int]:
        lines: dict[LineKey, int] = {}
        for rule_id, run in result.runs.items():
            spec = self.config.rules.get(rule_id)
            if spec is None or not spec.ledger:
                continue
            identities = run.findings.identities
            # Counted per identity place, in the order of each identity's first row.
            for place, count in Counter(run.findings.counted_places()).items():
                key = (rule_id, identities[place])
                lines[key] = lines.get(key, 0) + count if spec.counted else 1
        return lines

    def mark_accepted(self, result: RunResult, recorded: dict[LineKey, int]) -> None:
        """A new finding is accepted when the ledger records at least as many as the scan counts.
        That is a property of its identity: a row is accepted when its identity is, unless an
        exception allows it or the ledger may not hold it (`FindingTable.status`)."""
        for rule_id, run in result.runs.items():
            accepted = []
            for place, identity in enumerate(run.findings.identities):
                key = (rule_id, identity)
                if key in recorded and recorded[key] >= result.scan.get(key, 0):
                    accepted.append(place)
            run.findings.accept(accepted)
