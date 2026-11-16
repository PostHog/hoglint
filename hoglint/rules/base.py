"""What every primitive shares: rule metadata, its plan, and the words of what the native evaluator
judged (findings and incomplete items).

The judgment is the evaluator's (src/link/rules; `tests/rules/judgment.txt` holds it). Unknown
semantics per position:
- `from` / `select` false: the edge is not the rule's business.
- the destination did not resolve: `on_unresolved` decides (finding, block, incomplete).
- `from` / `select` unknown on a resolved edge: the rule is incomplete for that edge.
- a predicate (`deny`, `allow`, `require`) unknown: a finding. Unknown never escapes a deny.
- `unless` excuses only when it is true.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from hoglint.config.model import Config, RuleSpec
from hoglint.index import GraphIndex
from hoglint.labels import Labeler
from hoglint.ledger.errors import EngineError
from hoglint.model import (
    Context,
    Edge,
    Finding,
    FindingDetail,
    Graph,
    IncompleteCause,
    Owners,
    Remedy,
    Shown,
    Site,
    context_bits,
)

if TYPE_CHECKING:
    from hoglint.rules.entities import CoveragePrimitive, EdgeInventory, EntityRule
    from hoglint.rules.exports import ExportPrimitive
    from hoglint.rules.graphs import CyclePrimitive, CycleRow, Found, ReachPrimitive

from hoglint.rules.findings import FindingTable
from hoglint.rules.plan import identity_paths, ref_paths, selector_tree, tree
from hoglint.selectors import Ref, Resolver, Selector, State, conjuncts, render_value

PRIMITIVE_KINDS: dict[str, tuple[str, ...]] = {
    "use-shape": ("reference",),
    "signature": ("exposes",),
    "export": ("import", "exposes", "inherit", "decorate"),
}
DEFAULT_CONTEXTS: frozenset[str] = frozenset({Context.PROD})
# The selectors of a rule over edges that `explain edge:` shows, by `RuleSpec` key, in order.
EXPLAINED_SELECTORS = ("from_", "select", "deny", "allow", "require", "unless")


class RuleType:
    """What a rule type is, stated once on its class (D53). The schema's unread keys, the native
    plan's mode, the rules a ledger write runs whole again and the test generator's modes are
    all derived from these statements."""

    # The config's name of the primitive, and of this mode of it: an inventory has two modes,
    # one per class, and `MODE_KEY` is the key whose presence names the mode.
    PRIMITIVE: ClassVar[str]
    MODE: ClassVar[str]
    MODE_KEY: ClassVar[str] = ""
    # How the native evaluator runs the rule: "judge" (per edge), "entities", "reach", "cycle",
    # "export" or "coverage".
    NATIVE_MODE: ClassVar[str]
    # Whether one row of the rule reads many edges (a fold), so it runs again when any of them
    # moves. A type that does not say is taken to fold, which can only cost a rerun.
    FOLDS: ClassVar[bool] = True
    # Whether its selectors and identity read entities (a module, a symbol or a path) rather than
    # an edge's two ends.
    ENTITIES: ClassVar[bool] = False
    # The config keys the rule's code reads, besides the ones every rule has
    # (`config.schema.EVERY_RULE_READS`).
    READS: ClassVar[frozenset[str]]

    spec: RuleSpec

    def __init__(self, ctx: RuleContext, spec: RuleSpec) -> None:
        raise NotImplementedError

    def evaluate(self) -> RuleRun:
        raise NotImplementedError

    def plan(self) -> dict[str, Any]:
        """The rule as the native evaluator's plan holds it, from what the rule derived."""
        raise NotImplementedError

    @classmethod
    def mode_text(cls) -> str:
        """The mode as a config error names it."""
        return f"{cls.PRIMITIVE} rules with `{cls.MODE_KEY}`" if cls.MODE_KEY else f"{cls.PRIMITIVE} rules"


@dataclass(frozen=True, slots=True)
class IncompleteItem:
    """One reason a rule could not decide. `key` has no line numbers, so base and head compare."""

    rule: str
    key: str
    reason: str
    subjects: tuple[str, ...] = ()
    cause: IncompleteCause = IncompleteCause.INHERITED
    edge: Edge | None = field(default=None, compare=False)  # the edge that raised it; compare fingerprints it


@dataclass(frozen=False, slots=True)
class RuleRun:
    spec: RuleSpec
    findings: FindingTable = field(init=False)
    incomplete: list[IncompleteItem] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.findings = FindingTable(self.spec.ledger)


class Judgment(StrEnum):
    """What a rule decides for one edge, and nothing that names the edge: a group of edges shares
    it. What a finding says about its edge (identity, subjects, owner, message) is made per edge
    from the judgment (`EdgeRule.report`)."""

    CLEAN = "clean"
    UNRESOLVED = "unresolved"
    UNKNOWN_SELECTION = "unknown-selection"
    UNKNOWN_VIOLATION = "unknown-violation"
    VIOLATION = "violation"
    UNKNOWN_TYPE = "unknown-type"  # a signature slot with no usable type


@dataclass(frozen=True, slots=True)
class Endpoint:
    """One end of an edge, as a rule judges it."""

    module: str | None
    symbol: str | None
    path: str | None = None
    external: str | None = None
    unresolved: bool = False
    scope: str = ""


@dataclass(frozen=True, slots=True)
class Seen:
    """One destination of one edge as a judgment saw it: what its finding or incomplete item is
    worded from. The native evaluator gives every part but the edge (`Rules.judge`)."""

    edge: Edge
    src: Endpoint
    dst: Endpoint
    # The edge's label (`edge.label`).
    label: str
    # The refs the judgment read as unknown, as its message names them; empty for any other judgment.
    unknown: str
    # The rule's identity columns; empty for a judgment that `report` words as no finding.
    identity: tuple[str, ...]


class RuleContext:
    """Shared state of one evaluation: config, graph, labels and the runs so far. The native
    evaluator judges (`NativeRuleContext`); a rule words what it gives."""

    def __init__(self, config: Config, graph: Graph, labeler: Labeler, repo_root: Path | None = None) -> None:
        self.config = config
        self.graph = graph
        self.labeler = labeler
        self.repo_root = repo_root
        self.index = labeler.index
        self.results: dict[str, RuleRun] = {}

    def find_conflicts(self) -> None:
        """Records every label the config sets two ways on this graph, before any rule runs
        (`Labeler.find_conflicts`)."""
        self.labeler.find_conflicts()

    def met_conflicts(self) -> None:
        """Records the labels set two ways that the run met so far, outside the labeler. The
        labeler records them as it reads them, so there is nothing left to do here."""

    # What the native evaluator answers (`NativeRuleContext`): a rule asks its context, and words
    # what comes back.

    def reach_found(self, rule: ReachPrimitive) -> list[Found]:
        """What a reach rule's search finds."""
        raise NotImplementedError

    def inventory_judged(self, rule: EdgeInventory) -> None:
        """An inventory over edges judged into the rule's run."""
        raise NotImplementedError

    def entity_rows(self, rule: EntityRule) -> list[tuple[str, str, str | None, int, list[str]]]:
        """What a layout rule's or an entity inventory's entities report, as data the rule words
        (`EntityRule.report_rows`), in order."""
        raise NotImplementedError

    def layout_roots(self, within: str) -> list[str]:
        """The roots `within` names among the graph's module paths, sorted."""
        raise NotImplementedError

    def defined_in(self, paths: list[str]) -> list[list[str] | None]:
        """The declarations (`function`, `class`) of the symbols the module of each path defines,
        sorted; None for a path that no graph module has."""
        raise NotImplementedError

    def registry_modules(self, pattern: str) -> list[tuple[str, str]]:
        """The graph's (module, path) whose path `pattern` matches, by module."""
        raise NotImplementedError

    def coverage_required(self, rule: CoveragePrimitive) -> dict[str, tuple[str, str | None]]:
        """The paths a coverage rule needs covered, its items that cannot be placed already in the
        rule's run."""
        raise NotImplementedError

    def export_run(self, rule: ExportPrimitive) -> RuleRun:
        """An export rule's run."""
        raise NotImplementedError

    def cycle_rows(self, rule: CyclePrimitive) -> list[CycleRow]:
        """A cycle rule's rows."""
        raise NotImplementedError

    def cycle_identities(self, rule: CyclePrimitive, steps: list[tuple[int, int]]) -> list[tuple[str, ...]]:
        """The identity of each (edge, destination) of a cycle rule."""
        raise NotImplementedError

    def judged(self, rule: EdgeRule) -> None:
        """Every edge of an edge, entrypoint, signature or use-shape rule judged into its run."""
        raise NotImplementedError

    def finding(
        self,
        spec: RuleSpec,
        identity: tuple[str, ...],
        message: str,
        owner: str | None,
        subjects: tuple[str, ...] = (),
        witnesses: tuple[Edge, ...] = (),
        site: Site | None = None,
        ledgerable: bool = True,
        unknown: bool = False,
        remedies: tuple[Remedy, ...] = (),
    ) -> Finding:
        """A finding of `spec`; owners come from the `owner` module, the site from the first witness."""
        return Finding(
            rule=spec.id,
            identity=identity,
            detail=FindingDetail(
                message, site if site is not None or not witnesses else witnesses[0].site, witnesses, remedies
            ),
            owner=self.owner(owner),
            subjects=subjects,
            ledgerable=ledgerable,
            unknown=unknown,
        )

    def owner(self, module: str | None) -> Owners | None:
        """A finding's owners, looked up from `module` when something reads them."""
        return Owners(module, self.owners_of) if module is not None and self.config.roles.owner else None

    def owners_of(self, module: str | None) -> tuple[str, ...]:
        owner = self.config.roles.owner
        if module is None or not owner:
            return ()
        value, _ = self.labeler.field_value(module, None, owner)
        if value.state is State.KNOWN and isinstance(value.data, frozenset):
            return tuple(sorted(value.data))
        return ()


def subjects_of(src: str | None, dst: str | None) -> tuple[str, ...]:
    """A finding's subjects on an edge: its source module, and its destination module when that
    is another; a module with no name is none."""
    out = [src]
    if dst and dst != src:
        out.append(dst)
    return tuple(module for module in out if module)


def context_class(index: GraphIndex) -> type[RuleContext]:
    """The rule context for an index: the native graph's, whose evaluator judges every rule. A
    graph that is not linked natively has none: rules run in the native evaluator only."""
    found = getattr(index, "context_class", None)
    if found is None:
        raise EngineError("rules run in the native evaluator only: link the graph natively first")
    return found


def labeler_class(index: GraphIndex) -> type[Labeler]:
    """The labeler for an index: an index whose labels the native evaluator makes brings its own."""
    return getattr(index, "labeler_class", Labeler)


def rule_kinds(spec: RuleSpec, views: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    """Edge kinds a rule reads: its own list, else the primitive's or the view's default."""
    if spec.kinds:
        return spec.kinds
    if spec.primitive in PRIMITIVE_KINDS:
        return PRIMITIVE_KINDS[spec.primitive]
    return views.get(spec.view, ())


def contexts_of(edge: Edge) -> frozenset[str]:
    """The edge's contexts; `Context` is a `StrEnum`, so the members compare as their names."""
    return edge.contexts or DEFAULT_CONTEXTS


def named_seen(edge: Edge, src: str, named: tuple[Any, ...], unresolved: bool) -> Seen:
    """What the evaluator names of a reach or cycle row (`Named`): (identity, destination module,
    source symbol, destination symbol, label), its edge being `edge` and its source `src`."""
    identity, dst, src_symbol, dst_symbol, label = named
    return Seen(
        edge, Endpoint(src, src_symbol), Endpoint(dst, dst_symbol, unresolved=unresolved), label, "", tuple(identity)
    )


@cache
def column_ref(column: str) -> Ref:
    return Ref(tuple(column.split(".")))


def project(columns: tuple[str, ...], resolve: Resolver) -> tuple[str, ...]:
    """A finding's identity: one string per column, never a line number."""
    return tuple(render_value(resolve(column_ref(column))) for column in columns)


# ── staged selection ─────────────────────────────────────────────


class EdgeRule(RuleType):
    """What every rule over edges words and plans: the evaluator judges each edge (`Rules.judge`),
    the rule words each row it gives."""

    def __init__(self, ctx: RuleContext, spec: RuleSpec) -> None:
        self.ctx = ctx
        self.spec = spec
        self.run = RuleRun(spec)
        self.include = frozenset(spec.contexts_include)
        self.exclude = frozenset(spec.contexts_exclude or ())

    def kinds(self) -> tuple[str, ...]:
        return rule_kinds(self.spec, self.ctx.config.views)

    def selectors(self) -> tuple[Selector | None, ...]:
        """The selectors that pick edges, in the order they apply."""
        return self.spec.from_, self.spec.select

    def identified(self) -> frozenset[Judgment]:
        """The judgments whose rows `report` words as findings: only their rows carry the rule's
        identity, so an identity column is read where a finding needs it."""
        out = {Judgment.VIOLATION, Judgment.UNKNOWN_VIOLATION}
        if self.spec.on_unresolved != "incomplete":
            out.add(Judgment.UNRESOLVED)
        return frozenset(out)

    def incomplete(self, seen: Seen, reason: str) -> None:
        key = f"{seen.src.module}|{seen.edge.kind}|{seen.edge.surface}"
        self.run.incomplete.append(IncompleteItem(self.spec.id, key, reason, self.subjects(seen), edge=seen.edge))

    def subjects(self, seen: Seen) -> tuple[str, ...]:
        return subjects_of(seen.src.module, seen.dst.module)

    # Whether `report` names the edge as a finding's only witness. Then a finding whose detail is
    # made when read still has its witness and site without making it.
    witness_is_edge = True

    def found(self) -> frozenset[Judgment]:
        """The judgments whose evaluator rows become findings as they are; a row of any other
        judgment goes to the rule's own `report`."""
        return frozenset({Judgment.VIOLATION})

    def flags(self, judgment: Judgment) -> tuple[bool, bool]:
        """(ledgerable, unknown) of the finding `judgment` makes. They come from the rule and the
        judgment alone, never from the edge, so every edge of a group shares them."""
        if judgment is Judgment.UNRESOLVED:
            return self.spec.on_unresolved == "finding", True
        return True, judgment is Judgment.UNKNOWN_VIOLATION

    def finding_of(self, seen: Seen, judgment: Judgment, detail: Shown) -> Finding:
        """The finding `judgment` makes for one destination. Its identity, owner and subjects name
        the entity, so they come from what was seen; its flags from the judgment."""
        ledgerable, unknown = self.flags(judgment)
        later = self.witness_is_edge and not isinstance(detail, FindingDetail)
        return Finding(
            rule=self.spec.id,
            identity=seen.identity,
            detail=detail,
            owner=self.ctx.owner(seen.src.module),
            subjects=self.subjects(seen),
            ledgerable=ledgerable,
            unknown=unknown,
            witness=seen.edge if later else None,
        )

    def emit(
        self,
        seen: Seen,
        judgment: Judgment,
        message: str,
        remedies: tuple[Remedy, ...] = (),
        witnesses: tuple[Edge, ...] = (),
    ) -> Finding:
        shown = witnesses or (seen.edge,)
        detail = FindingDetail(message, shown[0].site, shown, remedies or self.remedies())
        finding = self.finding_of(seen, judgment, detail)
        self.run.findings.append(finding)
        return finding

    def remedies(self) -> tuple[Remedy, ...]:
        if self.spec.remedy:
            return (Remedy("documented-replacement", self.spec.remedy),)
        return ()

    def on_unresolved(self, seen: Seen) -> None:
        what = f"{seen.edge.kind} {seen.edge.surface!r} did not resolve"
        if self.spec.on_unresolved == "incomplete":
            self.incomplete(seen, what)
            return
        self.emit(seen, Judgment.UNRESOLVED, what)

    def report(self, seen: Seen, judgment: Judgment, describe: str) -> None:
        """The finding or incomplete `judgment` makes for one destination, all of it from what was
        seen of it; `describe` is the finding message."""
        if judgment is Judgment.UNRESOLVED:
            self.on_unresolved(seen)
        elif judgment is Judgment.UNKNOWN_SELECTION:
            self.incomplete(seen, f"selection reads unknown {seen.unknown or 'values'}")
        elif judgment is Judgment.UNKNOWN_VIOLATION:
            self.emit(seen, judgment, f"{describe}; cannot tell, unassigned: {seen.unknown or 'a value'}")
        elif judgment is Judgment.VIOLATION:
            self.emit(seen, judgment, describe)

    def evaluate(self) -> RuleRun:
        self.ctx.judged(self)
        return self.run

    def worded(self, seen: Seen, judgment: Judgment) -> RuleRun:
        """What `judgment` reports for `seen`, message included, kept out of this rule's run."""
        saved, self.run = self.run, RuleRun(self.spec)
        try:
            self.report(seen, judgment, self.describe(seen))
            return self.run
        finally:
            self.run = saved

    def describe(self, seen: Seen) -> str:
        return f"{seen.src.module} -> {seen.dst.symbol or seen.dst.module} ({seen.edge.kind})"

    def edges_plan(self) -> dict[str, Any]:
        """Which edges the rule sees, and its selection in the order it applies."""
        return {
            "kinds": list(dict.fromkeys(self.kinds())),
            "include": context_bits(self.include),
            "exclude": context_bits(self.exclude),
            "endpoint": self.spec.endpoint,
            "selection": [tree(part) for part in conjuncts(self.selectors())],
        }

    def verdict_plan(self) -> dict[str, Any]:
        """How the rule decides a selected destination: its predicates."""
        spec = self.spec
        return {
            "kind": "predicates",
            "deny": selector_tree(spec.deny),
            "allow": selector_tree(spec.allow),
            "require": selector_tree(spec.require),
        }

    def selectors_plan(self) -> list[tuple[str, dict[str, Any]]]:
        """Each selector of the rule by its `RuleSpec` key, as `explain edge:` reads them."""
        spec = self.spec
        out = []
        for name in EXPLAINED_SELECTORS:
            selector: Selector | None = getattr(spec, name)
            if selector is not None:
                out.append((name, tree(selector.node)))
        return out

    def plan(self) -> dict[str, Any]:
        """A rule judged per edge. A signature rule adds its part, an entrypoint rule its
        `uses_at_import`."""
        spec = self.spec
        return {
            "id": spec.id,
            "mode": self.NATIVE_MODE,
            "selectors": self.selectors_plan(),
            "edges": self.edges_plan(),
            "identity": identity_paths(spec.identity),
            "identified": sorted(self.identified()),
            "unknown_refs": {
                "selection": [ref_paths(spec.from_), ref_paths(spec.select)],
                "verdict": [ref_paths(spec.deny), ref_paths(spec.allow), ref_paths(spec.require)],
            },
            "unless": selector_tree(spec.unless),
            "signature": None,
            "verdict": self.verdict_plan(),
            "uses_at_import": False,
        }
