"""Rules over the native graph: rule evaluation by the native evaluator, and its rows worded.

The evaluator (src/link/rules) judges every edge of a rule on its own, per destination, from a
plan of the config (`hoglint.native.plan`). Edge, entrypoint, use-shape, signature and inventory
rules come back as rows that hold what a message names: the rule's Python class words each row
(`EdgeRule.report`). Cycle and reach rules take their pairs and their search.
"""

from __future__ import annotations

from array import array
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from functools import cached_property
from pathlib import Path
from typing import Any, TypeVar

from hoglint.config.model import Config, CoverSpec
from hoglint.index import DECL_NAMES, MODULE_SCOPE, GraphIndex, Memo, SymbolFacts
from hoglint.labels import Labeler
from hoglint.ledger.envelope import EngineError
from hoglint.model import Edge, Finding, FindingDetail, Graph, Owners, Shown, WitnessRow, owner_module
from hoglint.native.bridge import engine_errors, import_module
from hoglint.native.graph import SYMBOL_KINDS, NativeGraph, NativeSymbols
from hoglint.native.labels import NativeLabeler
from hoglint.native.plan import RulePlan
from hoglint.rules.base import (
    EdgeRule,
    Endpoint,
    Judgment,
    RuleContext,
    RuleRun,
    RuleType,
    Seen,
    named_seen,
    subjects_of,
)
from hoglint.rules.engine import primitive_of
from hoglint.rules.entities import CoveragePrimitive, EdgeInventory, EntityRule
from hoglint.rules.exports import ExportPrimitive
from hoglint.rules.graphs import CyclePrimitive, CycleRow, Found, ReachPrimitive
from hoglint.selectors import Truth

T = TypeVar("T")


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


class NativeSymbolFacts(SymbolFacts):
    """SymbolFacts from the link's inherit and decorate facts: one edge table, core and pack.
    Facts are read from the symbol columns (`NativeSymbols.fact`), with no dict per read."""

    def __init__(self, graph: NativeGraph) -> None:
        super().__init__(graph, [], [])
        symbols: Any = graph.symbols
        self.columns: NativeSymbols = symbols
        for owner, surface, names, _, called, resolved, classes in graph.link.class_facts("inherit", "class"):
            self.add_base(owner, self.base_names(surface, names, called, resolved, classes))
        for owner, surface, names, frozen, _, _, _ in graph.link.class_facts("decorate", "decorated"):
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

    __slots__ = ("handle", "made", "message")

    def __init__(self, message: str, handle: EdgeHandle) -> None:
        self.message = message
        self.handle = handle
        self.made: FindingDetail | None = None

    def get(self) -> FindingDetail:
        if self.made is None:
            edge = self.handle.get()
            self.made = FindingDetail(self.message, edge.site, (edge,))
        return self.made


class Worded:
    """The detail of a finding the evaluator found: the rule words it from the row when something
    reads the message, site or witnesses."""

    __slots__ = ("at", "made", "rows")

    def __init__(self, rows: JudgedRows, at: int) -> None:
        self.rows = rows
        self.at = at
        self.made: FindingDetail | None = None

    def get(self) -> FindingDetail:
        if self.made is None:
            rows, at = self.rows, self.at
            seen = rows.seen(at, rows.handle(at).get())
            self.made = rows.rule.worded(seen, rows.judgment(at)).findings[0].detail.get()
        return self.made


def distinct_ids(ids: Iterable[int]) -> array[int]:
    """Edge ids, each once, in the order first seen, as the link reads them."""
    return array("I", dict.fromkeys(ids))


def u32s(raw: bytes) -> array[int]:
    out = array("I")
    out.frombytes(raw)
    return out


class JudgedRows:
    """A judged rule's rows as the evaluator gives them (`Rules.judge`): columns, with each
    judgment, identity, module and text once. The rows whose judgment the rule takes as a finding
    (`EdgeRule.found`) are a `RowColumns` source of the rule's table: a `Finding` is made for one of them
    only when a reader asks for it."""

    __slots__ = (
        "alone",
        "alone_edges",
        "alone_set",
        "ctx",
        "dst_at",
        "dst_symbol_at",
        "edges",
        "found",
        "identities",
        "identity_at",
        "judgment_at",
        "judgments",
        "label_at",
        "ledgerable",
        "modules",
        "no_place",
        "row_edges",
        "rule",
        "src_at",
        "src_symbol_at",
        "targets",
        "texts",
        "unknown",
        "unknown_at",
    )

    def __init__(self, ctx: NativeRuleContext, rule: EdgeRule, raw: tuple[Any, ...]) -> None:
        edges, targets, names, judgment_at, identities, identity_at, modules, src_at, dst_at, words = raw
        texts, src_symbol_at, dst_symbol_at, label_at, unknown_at = words
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
        self.texts: list[str] = texts
        self.src_symbol_at = u32s(src_symbol_at)
        self.dst_symbol_at = u32s(dst_symbol_at)
        self.label_at = u32s(label_at)
        self.unknown_at = u32s(unknown_at)
        self.no_place: int = import_module().NO_PLACE
        self.found = rule.found()
        flags = [rule.flags(judgment) if judgment in self.found else (False, False) for judgment in self.judgments]
        self.ledgerable = bytes([flags[at][0] for at in self.judgment_at])
        self.unknown = bytes([flags[at][1] for at in self.judgment_at])
        self.row_edges: RowEdges | None = None
        # The rows whose judgment the rule words by its own `report`, in order: few or none.
        places = {at for at, judgment in enumerate(self.judgments) if judgment not in self.found}
        self.alone = [row for row, at in enumerate(self.judgment_at) if at in places] if places else []
        self.alone_set = frozenset(self.alone)
        # The edges of the rows the rule reports by its own `report`, by id.
        ids = distinct_ids(self.edges[at] for at in self.alone)
        self.alone_edges: dict[int, Edge] = dict(zip(ids, ctx.native.native.core.get(ids)))

    def judgment(self, at: int) -> Judgment:
        return self.judgments[self.judgment_at[at]]

    def text(self, place: int) -> str | None:
        return None if place == self.no_place else self.texts[place]

    def seen(self, at: int, edge: Edge) -> Seen:
        """What row `at` names, `edge` being its edge: what the rule words its finding or
        incomplete item from."""
        src = Endpoint(self.src(at), self.text(self.src_symbol_at[at]))
        unresolved = self.judgment(at) is Judgment.UNRESOLVED
        dst = Endpoint(self.dst(at), self.text(self.dst_symbol_at[at]), unresolved=unresolved)
        return Seen(
            edge,
            src,
            dst,
            self.texts[self.label_at[at]],
            self.texts[self.unknown_at[at]],
            tuple(self.identities[self.identity_at[at]]),
        )

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

    def finding(self, at: int, identity: tuple[str, ...]) -> Finding:
        handle = self.handle(at)
        return Finding(
            rule=self.rule.spec.id,
            identity=identity,
            detail=Worded(self, at),
            owner=self.ctx.owner(self.src(at)),
            subjects=self.subjects(at),
            ledgerable=bool(self.ledgerable[at]),
            unknown=bool(self.unknown[at]),
            witness=handle,
        )


class NativeRuleContext(RuleContext):
    """RuleContext over a NativeGraphIndex: the evaluator judges, selects and searches; Python
    makes findings and messages."""

    def __init__(self, config: Config, graph: Graph, labeler: Labeler, repo_root: Path | None = None) -> None:
        super().__init__(config, graph, labeler, repo_root)
        index = labeler.index
        assert isinstance(index, NativeGraphIndex)
        self.native = index
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
        """The labels set two ways that the evaluator met so far, in rules and levels: those met
        only where something reads them."""
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
    def alone_reports(rule: EdgeRule, rows: JudgedRows) -> Iterator[tuple[int, RuleRun]]:
        """Each row whose judgment the rule does not take as a finding, with what the rule's
        `report` words of it."""
        for at in rows.alone:
            seen = rows.seen(at, rows.alone_edges[rows.edges[at]])
            yield at, rule.worded(seen, rows.judgment(at))

    def judged(self, rule: EdgeRule) -> None:
        """The evaluator's rows of an edge, entrypoint, signature or use-shape rule, into the
        rule's findings table in the order the rule judges: a violation (or a slot of unknown
        type the rule reports as a finding) is a row of the evaluator's columns, and a row the
        rule's `report` words is a row it made."""
        rows = JudgedRows(self, rule, self.run_rule(self.evaluator.judge, rule))
        table = rule.run.findings
        number = table.take(rows)
        start = 0
        for at, reported in self.alone_reports(rule, rows):
            table.add_rows(number, start, at)
            table.extend(reported.findings)
            rule.run.incomplete.extend(reported.incomplete)
            start = at + 1
        table.add_rows(number, start, len(rows.edges))

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

    def inventory_judged(self, rule: EdgeInventory) -> None:
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
                detail=RowDetail(rule.unclassified(identity), handle),
                owner=self.owner(src),
                subjects=subjects_of(src, dst),
                witness=handle,
            )
            rule.run.findings.append(finding)
        for identity in sorted(classified - set(seen)):
            rule.run.findings.append(self.finding(spec, identity, rule.stale(identity), None, ledgerable=False))

    def entity_rows(self, rule: EntityRule) -> list[tuple[str, str, str | None, int, list[str]]]:
        return self.run_rule(self.evaluator.entities, rule)

    def layout_roots(self, within: str) -> list[str]:
        with engine_errors():
            return self.evaluator.layout_roots(within)

    def defined_in(self, paths: list[str]) -> list[list[str] | None]:
        with engine_errors():
            return self.evaluator.defined_in(paths)

    def registry_modules(self, pattern: str) -> list[tuple[str, str]]:
        with engine_errors():
            return self.evaluator.registry_modules(pattern)

    def coverage_required(self, rule: CoveragePrimitive) -> dict[str, tuple[CoverSpec, str | None]]:
        """The evaluator's paths of a coverage rule, from the witness ids of the findings its rule
        covers name, each with the first cover that needs it; what it cannot tell goes to the
        rule's run. The rule's class words both."""
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
            elif what == "unknown-selection":
                rule.unknown_selection(module, covers[at])
            elif what == "unread-items":
                rule.unread_items(module, surface)
            elif what == "unread-core-items":
                rule.unread_core_items(surface)
            elif what == "computed-load":
                rule.computed_load(module, surface)
            else:
                rule.unplaced(module, surface)
        return {path: (covers[at], module) for path, at, module in paths}

    def owner(self, module: str | None) -> Owners | None:
        """A finding's owners, one lookup per module: the evaluator's rows name few modules many
        times."""
        if module is None:
            return None
        found = self.owners.get(module)
        if found is None:
            found = self.owners[module] = super().owner(module)
        return found

    def cycle_rows(self, rule: CyclePrimitive) -> list[CycleRow]:
        rows = self.run_rule(self.evaluator.pairs, rule)
        with engine_errors():
            return [CycleRow(edge, target, Judgment(name), *rest) for edge, target, name, *rest in rows]

    def cycle_identities(self, rule: CyclePrimitive, steps: list[tuple[int, int]]) -> list[tuple[str, ...]]:
        found = self.run_rule(self.evaluator.cycle_identities, rule, steps)
        return [tuple(identity) for identity in found]

    def reach_found(self, rule: ReachPrimitive) -> list[Found]:
        """The evaluator's search of a reach rule, its paths made into Edges."""
        found = self.run_rule(self.evaluator.reach, rule)
        with engine_errors():
            verdicts = [Truth(verdict) if verdict is not None else None for _, _, _, verdict, _ in found]
        ids = distinct_ids(edge for _, _, path, _, _ in found for edge, _ in path)
        edges = dict(zip(ids, self.native.native.core.get(ids)))
        out = []
        for (what, source, path, _, named), verdict in zip(found, verdicts, strict=True):
            walked = tuple(edges[edge] for edge, _ in path)
            seen = named_seen(walked[-1], source, named, what == "unresolved") if walked else None
            out.append(Found(what, source, walked, seen, verdict))
        return out


class NativeGraphIndex(GraphIndex):
    """GraphIndex over a NativeGraph: nothing reads every edge unless a caller asks for it."""

    def __init__(self, graph: NativeGraph) -> None:
        self.graph = graph
        self.native = graph
        self.core_count: int = graph.link.core_count()
        self.module_order: dict[str, list[str]] = {}
        self.facts = NativeSymbolFacts(graph)
        self.path_to_module = {path: module for module, path in graph.modules.items()}
        self.module_of_symbol = Memo(self.find_module)

    context_class: type[RuleContext] = NativeRuleContext
    labeler_class: type[Labeler] = NativeLabeler

    def modules_of(self, kind: str) -> list[str]:
        """Source modules of `kind` in the order their first edge appears."""
        found = self.module_order.get(kind)
        if found is None:
            found = self.module_order[kind] = self.native.link.source_modules(kind)
        return found

    def module_edges(self, kind: str, module: str) -> list[Edge]:
        """One module's edges of one kind, core then pack, with no irregular edges sorted out."""
        return self.native.core.get(array("I", self.native.link.ids_from(kind, module)))

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
    def module_rebinds(self) -> Mapping[str, list[Edge]]:  # type: ignore[override]
        """`rebind` edges per module: the names an export module hands out by assigning them again."""
        return LazyModules(self.modules_of("rebind"), lambda module: self.module_edges("rebind", module))

    def edges_at(self, handles: list[int]) -> list[Edge]:
        return self.native.core.get(array("I", handles))
