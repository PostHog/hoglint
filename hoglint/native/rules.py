"""Rules over the native graph: lazy edge tables, and rule evaluation by the native evaluator.

The evaluator (src/link/rules) judges every edge of a rule on its own, per destination, from a
plan of the config (`hoglint.native.plan`). Edge and entrypoint rules come back as rows: a
violation becomes its finding here, and anything else goes to the rule's own code, which must
judge the same. Rules that judge in Python (use-shape, signature, inventory) take their
selection from it; cycle and reach rules take their pairs and their search.
"""

from __future__ import annotations

import json
from array import array
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from functools import cached_property
from pathlib import Path
from typing import Any, TypeVar

from hoglint.config.model import Config, RuleSpec
from hoglint.index import DECL_NAMES, MODULE_SCOPE, EdgeGroup, GraphIndex, Memo, SymbolFacts, has_endpoint_attrs
from hoglint.labels import Labeler
from hoglint.ledger.envelope import EngineError
from hoglint.model import Edge, EdgeRef, Finding, FindingDetail, Graph, Owners, Remedy, Shown, WitnessRow, owner_module
from hoglint.native.bridge import engine_errors, import_module
from hoglint.native.graph import SYMBOL_KINDS, NativeGraph, NativeSymbols
from hoglint.native.labels import NativeLabeler
from hoglint.native.plan import RulePlan
from hoglint.rules.base import (
    EdgeEnv,
    EdgeRule,
    IncompleteItem,
    Judgment,
    RuleContext,
    RuleRun,
    RuleType,
    project,
    subjects_of,
)
from hoglint.rules.engine import primitive_of
from hoglint.rules.entities import CoveragePrimitive, EdgeInventory, EntityRule
from hoglint.rules.exports import ExportPrimitive
from hoglint.rules.graphs import CyclePrimitive, CycleRow, Found, ReachPrimitive
from hoglint.rules.levels import LevelEvaluator, LevelResult
from hoglint.rules.plan import tree
from hoglint.selectors import Node, Truth

T = TypeVar("T")


class LazyKinds(Mapping[str, list[Edge]]):
    """kind -> every edge of that kind, made on first read."""

    def __init__(self, graph: NativeGraph, kinds: list[str]) -> None:
        self.graph = graph
        self.kinds = kinds
        self.made: dict[str, list[Edge]] = {}

    def __getitem__(self, kind: str) -> list[Edge]:
        if kind not in self.kinds:
            raise KeyError(kind)
        if kind not in self.made:
            self.made[kind] = self.graph.by_kind(kind)
        return self.made[kind]

    def __iter__(self) -> Iterator[str]:
        return iter(self.kinds)

    def __len__(self) -> int:
        return len(self.kinds)


class LazyModules(Mapping[str, Any]):
    """module -> a value made on first read from `make`."""

    def __init__(self, modules: list[str], make: Any) -> None:
        self.modules = modules
        self.known = frozenset(modules)
        self.make = make
        self.made: dict[str, Any] = {}

    def __getitem__(self, module: str) -> Any:
        if module not in self.known:
            raise KeyError(module)
        if module not in self.made:
            self.made[module] = self.make(module)
        return self.made[module]

    def __iter__(self) -> Iterator[str]:
        return iter(self.modules)

    def __len__(self) -> int:
        return len(self.modules)

    def __contains__(self, module: object) -> bool:
        """Membership with no value made: `Mapping.__contains__` would make it."""
        return module in self.known


class LazyGroups(Mapping[str, LazyModules]):
    """kind -> its edges per source module (`NativeGraphIndex.group`), the module list read
    from the link on first read."""

    def __init__(self, index: NativeGraphIndex, kinds: list[str]) -> None:
        self.index = index
        self.kinds = kinds
        self.made: dict[str, LazyModules] = {}

    def __getitem__(self, kind: str) -> LazyModules:
        if kind not in self.kinds:
            raise KeyError(kind)
        found = self.made.get(kind)
        if found is None:
            found = self.made[kind] = LazyModules(self.index.modules_of(kind), self.index.group_maker(kind))
        return found

    def __iter__(self) -> Iterator[str]:
        return iter(self.kinds)

    def __len__(self) -> int:
        return len(self.kinds)


class NativeSymbolFacts(SymbolFacts):
    """SymbolFacts from the link's inherit and decorate facts: one edge table, core and pack.
    Facts are read from the symbol columns (`NativeSymbols.fact`), with no dict per read."""

    def __init__(self, graph: NativeGraph) -> None:
        super().__init__(graph, [], [])
        symbols: Any = graph.symbols
        self.columns: NativeSymbols = symbols
        for owner, surface, names, _, called, resolved in graph.link.class_facts("inherit", "class"):
            self.add_base(owner, self.base_names(surface, names, called, resolved))
        for owner, surface, names, frozen, _, _ in graph.link.class_facts("decorate", "decorated"):
            self.add_decorator(owner, surface, names, frozen)

    def declared_class(self, symbol: str) -> bool:
        return self.columns.core_kind(symbol) == "class"

    def get(self, symbol: str, key: str) -> str:
        return self.columns.fact(symbol, key)

    def decl(self, symbol: str) -> str:
        raw = self.columns.fact(symbol, "decl") or self.columns.fact(symbol, "kind")
        return DECL_NAMES.get(raw, raw)

    def with_decl(self, decl: str) -> list[str]:
        """The core's symbols with the declaration from the kind column, merged in order with the
        symbols whose facts packs wrote."""
        kinds = [kind for kind in SYMBOL_KINDS if DECL_NAMES.get(kind, kind) == decl]
        core = set(self.columns.core_with_kinds(kinds))
        added = {name for name in self.columns.added if self.decl(name) == decl}
        return [name for name in self.columns if name in core or name in added]

    def find_decl(self, decl: str) -> bool:
        """Whether any symbol has the declaration, with no list of them made."""
        kinds = [kind for kind in SYMBOL_KINDS if DECL_NAMES.get(kind, kind) == decl]
        if self.columns.core_with_kinds(kinds):
            return True
        return any(self.decl(name) == decl for name in self.columns.added)


class RowEdges:
    """The edges of a rule's rows, by id, all made on the first read of any, and what a stored
    summary keeps of them, all read on the first read of any: a reader of one finding's witness
    reads most of them."""

    __slots__ = ("core", "ids", "made", "rows")

    def __init__(self, core: Any, ids: array[int]) -> None:
        self.core = core
        self.ids = ids
        self.made: dict[int, Edge] | None = None
        self.rows: dict[int, WitnessRow] | None = None

    def edge(self, edge_id: int) -> Edge:
        if self.made is None:
            self.made = dict(zip(self.ids, self.core.get(self.ids)))
        return self.made[edge_id]

    def witness_rows(self) -> dict[int, WitnessRow]:
        if self.rows is None:
            found = self.core.link.witness_rows(self.ids.tobytes())
            self.rows = {
                at: (kind, path, line, column, surface, tuple(targets))
                for at, (kind, path, line, column, surface, targets) in zip(self.ids, found)
            }
        return self.rows

    def row(self, edge_id: int) -> WitnessRow:
        return self.witness_rows()[edge_id]


class EdgeHandle:
    """A row's edge, by id (`model.EdgeRef`): made when something reads it."""

    __slots__ = ("edge_id", "edges")

    def __init__(self, edges: RowEdges, edge_id: int) -> None:
        self.edges = edges
        self.edge_id = edge_id

    def get(self) -> Edge:
        return self.edges.edge(self.edge_id)

    def row(self) -> WitnessRow:
        return self.edges.row(self.edge_id)


class RowDetail:
    """The detail of a finding whose message the evaluator's row holds: the site and witness
    are its edge's, made when something reads them."""

    __slots__ = ("handle", "made", "message", "remedies")

    def __init__(self, message: str, handle: EdgeHandle, remedies: tuple[Remedy, ...] = ()) -> None:
        self.message = message
        self.handle = handle
        self.remedies = remedies
        self.made: FindingDetail | None = None

    def get(self) -> FindingDetail:
        if self.made is None:
            edge = self.handle.get()
            self.made = FindingDetail(self.message, edge.site, (edge,), self.remedies)
        return self.made


def decided(rule: EdgeRule, edge: Edge, target: int, judgment: Judgment) -> EdgeEnv:
    """The environment of the edge's `target`-th destination, once the rule's own code judges it
    as the evaluator did: a judgment that differs is an engine error, never a silent difference."""
    env = rule.envs_of(edge)[target]
    judged = rule.decide(env)
    if judged is not judgment:
        site = edge.site
        raise EngineError(
            f"rule {rule.spec.id} judges {edge.kind} {site.path}:{site.line} {judged}, "
            f"apart from the evaluator's {judgment}"
        )
    return env


class Rejudged:
    """The detail of a finding made from a judgment made outside the rule's own code: the rule
    judges the edge's destination again when something reads the message, site, witnesses or
    remedies. A judgment or an identity that differs is an engine error, never a silent
    difference."""

    __slots__ = ("handle", "identity", "judgment", "made", "rule", "target")

    def __init__(
        self, rule: EdgeRule, handle: EdgeRef, judgment: Judgment, identity: tuple[str, ...], target: int = 0
    ) -> None:
        self.rule = rule
        self.handle = handle
        self.judgment = judgment
        self.identity = identity
        self.target = target
        self.made: FindingDetail | None = None

    def confirmed(self, rule: EdgeRule | None = None) -> EdgeEnv:
        """The destination's environment, once the rule's own code judges it as the evaluator did
        and its own `project` gives the evaluator's identity: the ledger keys a finding by it.
        `rule` is the rule over the labels it judges with: the run's own by default, one over a
        plain Python labeler on a ledger write (D55)."""
        rule = rule or self.rule
        edge = self.handle.get()
        env = decided(rule, edge, self.target, self.judgment)
        identity = project(rule.spec.identity, env.resolve)
        if identity != self.identity:
            site = edge.site
            raise EngineError(
                f"rule {rule.spec.id} gives {edge.kind} {site.path}:{site.line} the identity {identity}, "
                f"apart from the evaluator's {self.identity}"
            )
        return env

    def reported(self, env: EdgeEnv) -> RuleRun:
        """What the judgment reports for `env`, message included, kept out of the rule's run: the
        judgment is checked already, so the rule does not decide again."""
        rule = self.rule
        saved, rule.run = rule.run, RuleRun(rule.spec)
        try:
            rule.report(env, self.judgment, rule.describe(env))
            return rule.run
        finally:
            rule.run = saved

    def get(self) -> FindingDetail:
        if self.made is None:
            self.made = self.reported(self.confirmed()).findings[0].detail.get()
        return self.made


def distinct_ids(ids: Iterable[int]) -> array[int]:
    """Edge ids, each once, in the order first seen, as the link reads them."""
    return array("I", dict.fromkeys(ids))


def confirmed_row(finding: Finding) -> tuple[object, ...]:
    """What a ledger write holds the evaluator to of a finding: its identity, subjects and flags."""
    return finding.identity, finding.subjects, finding.ledgerable, finding.unknown


def confirmed_rows(run: RuleRun) -> list[tuple[object, ...] | IncompleteItem]:
    """What a ledger write holds the evaluator to of a run: each finding (`confirmed_row`) and each
    incomplete item, in order."""
    return [*(confirmed_row(finding) for finding in run.findings), *run.incomplete]


# The detail of a finding a ledger write makes only to compare it (`confirmed_row`).
NO_DETAIL = FindingDetail("", None)


def u32s(raw: bytes) -> array[int]:
    out = array("I")
    out.frombytes(raw)
    return out


class JudgedRows:
    """A judged rule's rows as the evaluator gives them (`Rules.judge`): columns, with each
    judgment, identity and module once. The rows whose judgment the rule takes as a finding
    (`EdgeRule.found`) are a `RowColumns` source of the rule's table: a `Finding` is made for one of them
    only when a reader asks for it."""

    __slots__ = (
        "alone",
        "alone_edges",
        "alone_set",
        "ctx",
        "dst_at",
        "edges",
        "found",
        "identities",
        "identity_at",
        "judgment_at",
        "judgments",
        "ledgerable",
        "modules",
        "no_place",
        "reported",
        "row_edges",
        "rule",
        "src_at",
        "targets",
        "unknown",
    )

    def __init__(self, ctx: NativeRuleContext, rule: EdgeRule, raw: tuple[Any, ...]) -> None:
        edges, targets, names, judgment_at, identities, identity_at, modules, src_at, dst_at = raw
        self.ctx = ctx
        self.rule = rule
        self.edges = u32s(edges)
        self.targets = u32s(targets)
        with engine_errors():
            self.judgments = [Judgment(name) for name in names]
        self.judgment_at = u32s(judgment_at)
        self.identities: list[tuple[str, ...]] = [tuple(identity) for identity in identities]
        self.identity_at = u32s(identity_at)
        self.modules: list[str] = modules
        self.src_at = u32s(src_at)
        self.dst_at = u32s(dst_at)
        self.no_place: int = import_module().NO_PLACE
        self.found = rule.found()
        flags = [rule.flags(judgment) if judgment in self.found else (False, False) for judgment in self.judgments]
        self.ledgerable = bytes([flags[at][0] for at in self.judgment_at])
        self.unknown = bytes([flags[at][1] for at in self.judgment_at])
        self.row_edges: RowEdges | None = None
        # What the rule's own code reported for each row it judged, kept for a ledger write.
        self.reported: dict[int, RuleRun] = {}
        # The rows whose judgment goes to the rule's own code, in order: few or none.
        places = {at for at, judgment in enumerate(self.judgments) if judgment not in self.found}
        self.alone = [row for row, at in enumerate(self.judgment_at) if at in places] if places else []
        self.alone_set = frozenset(self.alone)
        # Rust gives only some judgments an identity (`Judgment::has_identity`); Python decides
        # which judgments are findings, and a finding needs one.
        if self.no_place in self.identity_at:
            for at, place in enumerate(self.identity_at):
                if place == self.no_place and at not in self.alone_set:
                    raise EngineError(
                        f"rule {rule.spec.id}: the evaluator gives a {self.judgment(at)} row no identity, "
                        "but the rule takes it as a finding"
                    )
        # The edges of the rows the rule's own code reports, by id.
        ids = distinct_ids(self.edges[at] for at in self.alone)
        self.alone_edges: dict[int, Edge] = dict(zip(ids, ctx.native.native.core.get(ids)))

    def judgment(self, at: int) -> Judgment:
        return self.judgments[self.judgment_at[at]]

    def edge_rows(self) -> RowEdges:
        """The edges of the rows the rule takes as findings."""
        if self.row_edges is None:
            alone = self.alone_set
            ids = distinct_ids(edge for row, edge in enumerate(self.edges) if row not in alone)
            self.row_edges = RowEdges(self.ctx.native.native.core, ids)
        return self.row_edges

    def handle(self, at: int) -> EdgeHandle:
        return EdgeHandle(self.edge_rows(), self.edges[at])

    def witnesses(self, at: int) -> tuple[Edge, ...]:
        return (self.handle(at).get(),)

    def witness_ids(self, at: int) -> tuple[int, ...]:
        return (self.edges[at],)

    def src(self, at: int) -> str:
        return self.modules[self.src_at[at]]

    def dst(self, at: int) -> str | None:
        place = self.dst_at[at]
        return None if place == self.no_place else self.modules[place]

    def subjects(self, at: int) -> tuple[str, ...]:
        return subjects_of(self.src(at), self.dst(at))

    def parts(self, ats: Sequence[int]) -> tuple[list[tuple[WitnessRow, ...]], list[str], list[tuple[str, ...]]]:
        """The witness rows, owner module and subjects of rows `ats`: the witness rows from the
        link in one call, owners and subjects once per module and pair of modules."""
        if not ats:
            return [], [], []
        by_edge, edges = self.edge_rows().witness_rows(), self.edges
        witnesses: list[tuple[WitnessRow, ...]] = [(by_edge[edges[at]],) for at in ats]
        owners = [owner_module(self.ctx.owner(module)) for module in self.modules]
        src_at, dst_at = self.src_at, self.dst_at
        subjects_by_pair: dict[tuple[int, int], tuple[str, ...]] = {}
        subjects = []
        for at in ats:
            pair = (src_at[at], dst_at[at])
            found = subjects_by_pair.get(pair)
            if found is None:
                found = subjects_by_pair[pair] = self.subjects(at)
            subjects.append(found)
        return witnesses, [owners[src_at[at]] for at in ats], subjects

    def rejudged(self, at: int, identity: tuple[str, ...]) -> Rejudged:
        return Rejudged(self.rule, self.handle(at), self.judgment(at), identity, self.targets[at])

    def finding(self, at: int, identity: tuple[str, ...]) -> Finding:
        handle = self.handle(at)
        return Finding(
            rule=self.rule.spec.id,
            identity=identity,
            detail=self.rejudged(at, identity),
            owner=self.ctx.owner(self.src(at)),
            subjects=self.subjects(at),
            ledgerable=bool(self.ledgerable[at]),
            unknown=bool(self.unknown[at]),
            witness=handle,
        )

    def confirm(self, judge: EdgeRule) -> None:
        """Every row judged again by `judge` (D50, D55) over its own labels, and held to what a
        ledger write keeps of it (`confirmed_row`): a row the rule takes as a finding must give
        its judgment and identity, and then the finding the rule's own code makes of it; a row
        that code reported must give what it reported over the evaluator's labels."""
        for at in range(len(self.edges)):
            if at in self.alone_set:
                edge = self.alone_edges[self.edges[at]]
                ours = confirmed_rows(self.reported[at])
                theirs = confirmed_rows(judge.recorded([decided(judge, edge, self.targets[at], self.judgment(at))]))
            else:
                identity = self.identities[self.identity_at[at]]
                env = self.rejudged(at, identity).confirmed(judge)
                edge = env.edge
                ours = [confirmed_row(self.finding(at, identity))]
                theirs = [confirmed_row(judge.finding_of(env, self.judgment(at), NO_DETAIL))]
            if ours != theirs:
                site = edge.site
                raise EngineError(
                    f"rule {judge.spec.id} reports {edge.kind} {site.path}:{site.line} as {theirs} "
                    f"with its own labels, apart from {ours} with the evaluator's"
                )


class NativeRuleContext(RuleContext):
    """RuleContext over a NativeGraphIndex: the evaluator judges, selects and searches; Python
    makes findings and messages."""

    def __init__(self, config: Config, graph: Graph, labeler: Labeler, repo_root: Path | None = None) -> None:
        super().__init__(config, graph, labeler, repo_root)
        index = labeler.index
        assert isinstance(index, NativeGraphIndex)
        self.native = index
        self.judged_rows: list[JudgedRows] = []
        self.owners: dict[str, Owners | None] = {}

    @cached_property
    def plan(self) -> RulePlan:
        rules = [primitive_of(spec)(self, spec) for spec in self.config.rules.values()]
        return RulePlan(self.config, self.native.native, self.labeler, rules)

    @cached_property
    def evaluator(self) -> Any:
        """The native evaluator of this run: labels, and every edge rule compiled."""
        native: Any = import_module()
        link = self.native.native.link
        with engine_errors():
            return native.Rules(link, self.plan.text(lambda plan: native.Rules.reads(link, plan)))

    def run_rule(self, call: Callable[..., T], rule: RuleType, *args: Any) -> T:
        """`call`, one of the evaluator's calls on one rule, on `rule`'s place in the plan."""
        with engine_errors():
            return call(self.plan.index[rule.spec.id], *args)

    def find_conflicts(self) -> None:
        """The evaluator labels every module before any rule runs and finds each label set two
        ways; the labeler words each one with its sources (D44)."""
        with engine_errors():
            conflicts = self.evaluator.conflicts()
        self.word(conflicts)
        if conflicts and not self.labeler.conflicts:
            raise EngineError(f"the evaluator finds labels set two ways that the labeler does not: {conflicts[:3]}")
        if isinstance(self.labeler, NativeLabeler):
            self.labeler.read_from(self.evaluator)

    def met_conflicts(self) -> None:
        """The labels set two ways that the evaluator met so far, in rules and levels: what the
        Python engine meets lazily, where it reads them."""
        with engine_errors():
            met = self.evaluator.met()
        self.word(met)

    def word(self, conflicts: list[tuple[str, str, str]]) -> None:
        """Has the labeler record each conflict, with its sources, as reading its entity does."""
        labeler = self.labeler
        for what, entity, _ in conflicts:
            if what in ("module", "zones"):
                labeler.module_labels(entity)
            elif what == "kind":
                labeler.kind_conflict(entity)
            elif what == "symbol":
                labeler.symbol_labels(entity)
            else:
                labeler.provider_labels(entity)

    @staticmethod
    def judged_alone(rule: EdgeRule, edge: Edge, target: int, judgment: Judgment) -> RuleRun:
        """What the rule's own code reports for one destination the evaluator judged neither
        clean nor a finding of its own."""
        return rule.recorded([decided(rule, edge, target, judgment)])

    def alone_reports(self, rule: EdgeRule, rows: JudgedRows) -> Iterator[tuple[int, RuleRun]]:
        """Each row whose judgment the rule does not take as a finding, with what the rule's own
        code reports for it; that code must judge as the evaluator did."""
        for at in rows.alone:
            reported = rows.reported[at] = self.judged_alone(
                rule, rows.alone_edges[rows.edges[at]], rows.targets[at], rows.judgment(at)
            )
            yield at, reported

    def judged(self, rule: EdgeRule) -> bool:
        """The evaluator's rows of an edge, entrypoint, signature or use-shape rule, into the
        rule's findings table in the order the rule judges: a violation (or a slot of unknown
        type the rule reports as a finding) is a row of the evaluator's columns, and a row the
        rule's own code reports is a row it made."""
        if rule.CONFIRM != "row":
            return False
        rows = JudgedRows(self, rule, self.run_rule(self.evaluator.judge, rule))
        table = rule.run.findings
        number = table.take(rows)
        self.judged_rows.append(rows)
        start = 0
        for at, reported in self.alone_reports(rule, rows):
            table.add_rows(number, start, at)
            table.extend(reported.findings)
            rule.run.incomplete.extend(reported.incomplete)
            start = at + 1
        table.add_rows(number, start, len(rows.edges))
        return True

    def export_run(self, rule: ExportPrimitive) -> RuleRun:
        """The evaluator's check of an export rule, its rows made into findings and incomplete
        items in order; a witness edge is kept as a handle."""
        rows = self.run_rule(self.evaluator.export, rule)
        core = self.native.native.core
        edges = RowEdges(core, distinct_ids(row[5] for row in rows if row[5] is not None))
        spec = rule.spec
        run = rule.run
        for what, name, module, label, trail, edge_id, ledgerable, unknown, identity, subject in rows:
            if what == "unknown":
                run.incomplete.append(rule.unknown_item(spec.id, module, name, trail, label, subject))
                continue
            if what == "unresolved":
                run.incomplete.append(rule.unresolved_item(spec.id, module, name))
                continue
            message = rule.message(module, trail, label)
            handle = EdgeHandle(edges, edge_id) if edge_id is not None else None
            detail: Shown = RowDetail(message, handle) if handle is not None else FindingDetail(message, None)
            finding = Finding(
                rule=spec.id,
                identity=tuple(identity),
                detail=detail,
                owner=self.owner(module),
                subjects=(module, subject),
                ledgerable=ledgerable,
                unknown=unknown,
                witness=handle,
            )
            run.findings.append(finding)
        return run

    def inventory_judged(self, rule: EdgeInventory) -> bool:
        """The evaluator's rows of an inventory over edges: the first destination with each
        identity, kept as a handle, and the ones it cannot place, which the rule reports itself;
        then the classification file's entries, as `EdgeInventory.evaluate` compares them."""
        rows = JudgedRows(self, rule, self.run_rule(self.evaluator.judge, rule))
        for _, reported in self.alone_reports(rule, rows):
            rule.run.findings.extend(reported.findings)
            rule.run.incomplete.extend(reported.incomplete)
        alone = rows.alone_set
        seen: dict[tuple[str, ...], tuple[EdgeHandle, str, str | None]] = {}
        for at in range(len(rows.edges)):
            if at not in alone:
                seen[rows.identities[rows.identity_at[at]]] = (rows.handle(at), rows.src(at), rows.dst(at))
        spec = rule.spec
        classified = rule.classifications()
        for identity in sorted(seen):
            if identity in classified:
                continue
            handle, src, dst = seen[identity]
            finding = Finding(
                rule=spec.id,
                identity=identity,
                detail=RowDetail(rule.unclassified(identity), handle, rule.remedies()),
                owner=self.owner(src),
                subjects=subjects_of(src, dst),
                witness=handle,
            )
            rule.run.findings.append(finding)
        for identity in sorted(classified - set(seen)):
            rule.run.findings.append(self.finding(spec, identity, rule.stale(identity), None, ledgerable=False))
        return True

    def entity_rows(self, rule: EntityRule) -> list[tuple[str, str, str | None, int, list[str]]] | None:
        if rule.NATIVE_MODE != "entities":
            return None
        return self.run_rule(self.evaluator.entities, rule)

    def layout_roots(self, within: str) -> list[str]:
        with engine_errors():
            return self.evaluator.layout_roots(within)

    def registry_modules(self, pattern: str) -> list[tuple[str, str]]:
        with engine_errors():
            return self.evaluator.registry_modules(pattern)

    def coverage_required(self, rule: CoveragePrimitive) -> dict[str, tuple[str, str | None]]:
        """The evaluator's paths of a coverage rule, from the witness ids of the findings its rule
        covers name, each with why its first cover needs it; what it cannot tell goes to the rule's
        run. The rule's class words both."""
        witnesses: list[bytes | None] = []
        for cover in rule.spec.covers:
            run = self.results.get(cover.rule) if cover.rule else None
            if run is None:
                witnesses.append(None)
                continue
            witnesses.append(run.findings.witness_ids().tobytes())
        paths, incomplete = self.run_rule(self.evaluator.coverage, rule, witnesses)
        covers = rule.spec.covers
        for what, at, module, surface in incomplete:
            if what == "not-run":
                rule.not_run(covers[at])
            else:
                rule.unplaced(module, surface)
        return {path: (rule.why(covers[at]), module) for path, at, module in paths}

    def owner(self, module: str | None) -> Owners | None:
        """A finding's owners, one lookup per module: the evaluator's rows name few modules many
        times."""
        if module is None:
            return None
        found = self.owners.get(module)
        if found is None:
            found = self.owners[module] = super().owner(module)
        return found

    @cached_property
    def plain(self) -> RuleContext:
        """A rule context over a plain Python labeler, which reads no label the evaluator
        computed: what a ledger write confirms the evaluator's rows with (D55)."""
        labeler = Labeler(self.config, self.graph, self.repo_root, self.index)
        return RuleContext(self.config, self.graph, labeler, self.repo_root)

    def confirm_findings(self, ran: list[RuleSpec]) -> None:
        """Every finding the evaluator made is judged again by its rule's own code, with labels
        the evaluator did not compute: a run that writes a ledger reads no message, so a wrong
        row would become accepted debt (D50, D55).

        A rule whose class confirms per row (`RuleType.CONFIRM`) has each row judged again by its
        rule over the plain context. Every other rule that ran runs whole again in its Python
        primitive over the plain context, the runs before it at hand for a coverage rule, and
        must give the evaluator's findings and incomplete items, in order."""
        plain = self.plain
        for rows in self.judged_rows:
            judge = primitive_of(rows.rule.spec)(plain, rows.rule.spec)
            assert isinstance(judge, EdgeRule)
            rows.confirm(judge)
        plain.results.update(self.results)
        for spec in ran:
            if primitive_of(spec).CONFIRM != "whole":
                continue
            ours, theirs = (
                confirmed_rows(self.results[spec.id]),
                confirmed_rows(primitive_of(spec)(plain, spec).evaluate()),
            )
            if ours == theirs:
                continue
            at = next(
                (at for at, (mine, other) in enumerate(zip(ours, theirs)) if mine != other), min(len(ours), len(theirs))
            )
            mine = ours[at] if at < len(ours) else "nothing"
            other = theirs[at] if at < len(theirs) else "nothing"
            raise EngineError(f"rule {spec.id}: the evaluator gives {mine} as row {at}, the rule's own code {other}")

    def confirm_levels(self, levels: dict[str, LevelResult], runs: dict[str, RuleRun]) -> None:
        """Every level, as the run judged it, must pass and fail the groups it passes and fails
        over a plain Python labeler (D55): a ledger writes a line per passing group, and a group's
        label decides which findings block it."""
        for name, spec in self.config.levels.items():
            ours = levels[name]
            theirs = LevelEvaluator(spec, self.plain.labeler, runs).evaluate()
            if ours.passing == theirs.passing and set(ours.blocks) == set(theirs.blocks):
                continue
            wrong = sorted(set(ours.passing) ^ set(theirs.passing)) or sorted(set(ours.blocks) ^ set(theirs.blocks))
            raise EngineError(f"level {name}: the evaluator and the Python labeler pass groups apart on {wrong[:3]}")

    def cycle_rows(self, rule: CyclePrimitive) -> list[CycleRow]:
        rows = self.run_rule(self.evaluator.pairs, rule)
        with engine_errors():
            return [CycleRow(edge, target, Judgment(name), pair) for edge, target, name, pair in rows]

    def reach_found(self, rule: ReachPrimitive) -> list[Found]:
        """The evaluator's search of a reach rule, its paths made into Edges."""
        found = self.run_rule(self.evaluator.reach, rule)
        with engine_errors():
            verdicts = [Truth(verdict) if verdict is not None else None for _, _, _, verdict in found]
        ids = distinct_ids(edge for _, _, path, _ in found for edge, _ in path)
        edges = dict(zip(ids, self.native.native.core.get(ids)))
        out = []
        for (what, source, path, _), verdict in zip(found, verdicts, strict=True):
            walked = tuple(edges[edge] for edge, _ in path)
            dst = rule.envs_of(walked[-1])[path[-1][1]].dst if walked else None
            out.append(Found(what, source, walked, dst, verdict))
        return out

    def kinds_or_all(self, kinds: tuple[str, ...]) -> list[str]:
        return list(dict.fromkeys(kinds)) if kinds else self.native.kind_order()

    def edges_passing(self, kinds: tuple[str, ...], node: Node | None) -> list[Edge]:
        """`node`, which reads only an edge's kind and contexts, judged by the evaluator; only the
        edges it does not make false become Edges."""
        if node is None or not kinds:
            return super().edges_passing(kinds, node)
        with engine_errors():
            passing = array("I", self.evaluator.passing(list(kinds), json.dumps(tree(node))))
        return self.native.native.core.get(passing)

    def source_modules(self, kinds: tuple[str, ...]) -> list[str]:
        return sorted({module for kind in self.kinds_or_all(kinds) for module in self.native.modules_of(kind)})


class NativeGraphIndex(GraphIndex):
    """GraphIndex over a NativeGraph: nothing reads every edge unless a caller asks for it."""

    def __init__(self, graph: NativeGraph) -> None:
        self.graph = graph
        self.native = graph
        self.core_count: int = graph.link.core_count()
        self.module_order: dict[str, list[str]] = {}
        self.by_kind = LazyKinds(graph, self.kind_order())
        self.groups = LazyGroups(self, self.kind_order())
        self.facts = NativeSymbolFacts(graph)
        self.path_to_module = {path: module for module, path in graph.modules.items()}
        self.module_of_symbol = Memo(self.find_module)

    context_class: type[RuleContext] = NativeRuleContext
    labeler_class: type[Labeler] = NativeLabeler

    def kind_order(self) -> list[str]:
        """Kinds in the order their first edge appears: core kinds, then pack kinds."""
        return list(self.native.link.kind_order())

    def modules_of(self, kind: str) -> list[str]:
        """Source modules of `kind` in the order their first edge appears."""
        found = self.module_order.get(kind)
        if found is None:
            found = self.module_order[kind] = self.native.link.source_modules(kind)
        return found

    def pack_edges(self, kind: str, module: str) -> list[Edge]:
        """The pack edges of `kind` from one module."""
        ids = array("I", self.native.link.ids_from(kind, module))
        return self.native.core.get(array("I", (i for i in ids if i >= self.core_count)))

    def is_irregular(self, edge: Edge) -> bool:
        site = edge.site
        if has_endpoint_attrs(edge):
            return True
        return bool(site.path) and site.path != self.graph.modules.get(site.module)

    def module_edges(self, kind: str, module: str) -> list[Edge]:
        """One module's edges of one kind, core then pack, with no irregular edges sorted out."""
        return self.native.core.get(array("I", self.native.link.ids_from(kind, module)))

    def group(self, kind: str, module: str) -> EdgeGroup:
        """One module's edges of one kind, core then pack."""
        edges = self.module_edges(kind, module)
        return EdgeGroup(edges, [edge for edge in edges if self.is_irregular(edge)])

    def group_maker(self, kind: str) -> Any:
        return lambda module: self.group(kind, module)

    @cached_property
    def ordinals(self) -> dict[int, int]:  # type: ignore[override]
        """Each edge's place among edges with the same module, scope, kind and surface. Reads
        every edge: only explain asks for it."""
        out = self.count_ordinals()
        for row, last in self.native.repeats.items():
            out[row] = out[last]
        return out

    @cached_property
    def module_imports(self) -> Mapping[str, list[Edge]]:  # type: ignore[override]
        """Module-scope import edges per module."""

        def imports(module: str) -> list[Edge]:
            return [e for e in self.module_edges("import", module) if e.site.scope in (MODULE_SCOPE, "")]

        return LazyModules(self.modules_of("import"), imports)

    @cached_property
    def exposes(self) -> Mapping[str, list[tuple[str, Edge]]]:  # type: ignore[override]
        """(callable qualname, exposes edge) per module."""

        def exposes(module: str) -> list[tuple[str, Edge]]:
            return [(e.attr("callable") or e.site.scope, e) for e in self.module_edges("exposes", module)]

        return LazyModules(self.modules_of("exposes"), exposes)

    @cached_property
    def exposed_handles(self) -> Mapping[str, list[tuple[str, int]]]:  # type: ignore[override]
        """(callable qualname, handle of the exposes edge) per module, read off the link's
        columns."""

        def exposed(module: str) -> list[tuple[str, int]]:
            ids = self.native.link.ids_from("exposes", module)
            callables, scopes = self.native.link.columns(ids, ["attr:callable", "scope"])
            return [
                (callable_name or scope, handle)
                for callable_name, scope, handle in zip(callables, scopes, array("I", ids))
            ]

        return LazyModules(self.modules_of("exposes"), exposed)

    def edges_at(self, handles: list[int]) -> list[Edge]:
        return self.native.core.get(array("I", handles))
