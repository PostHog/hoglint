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
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from functools import cached_property
from pathlib import Path
from typing import Any

from hoglint.config.model import Config
from hoglint.index import DECL_NAMES, MODULE_SCOPE, EdgeGroup, GraphIndex, Memo, SymbolFacts, has_endpoint_attrs
from hoglint.labels import Labeler
from hoglint.ledger.envelope import EngineError
from hoglint.model import Edge, Finding, FindingDetail, Graph
from hoglint.native.bridge import import_module
from hoglint.native.graph import SYMBOL_KINDS, NativeGraph, NativeSymbols
from hoglint.native.labels import NativeLabeler
from hoglint.native.plan import JUDGED, RulePlan, tree
from hoglint.rules.base import EdgeEnv, EdgeRule, Judgment, RuleContext, RuleRun
from hoglint.rules.engine import primitive_of
from hoglint.rules.graphs import CyclePrimitive, CycleRow, Found, ReachPrimitive
from hoglint.selectors import Node, Truth


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
        for owner, _, names, _ in graph.link.class_facts("inherit", "class"):
            self.add_base(owner, names)
        for owner, surface, names, frozen in graph.link.class_facts("decorate", "decorated"):
            self.add_decorator(owner, surface, names)
            if frozen:
                self.frozen.add(owner)

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
        return bool(self.with_decl(decl))


class Rejudged:
    """The detail of a finding made from a judgment made outside the rule's own code: the rule
    judges the edge's destination again when something reads the message, site, witnesses or
    remedies. A judgment that differs is an engine error, never a silent difference."""

    __slots__ = ("edge", "judgment", "made", "rule", "target")

    def __init__(self, rule: EdgeRule, edge: Edge, judgment: Judgment, target: int = 0) -> None:
        self.rule = rule
        self.edge = edge
        self.judgment = judgment
        self.target = target
        self.made: FindingDetail | None = None

    def get(self) -> FindingDetail:
        if self.made is None:
            env = self.rule.envs_of(self.edge)[self.target]
            judged = self.rule.decide(env)
            if judged is not self.judgment:
                site = self.edge.site
                raise EngineError(
                    f"rule {self.rule.spec.id} judges {self.edge.kind} {site.path}:{site.line} {judged}, "
                    f"apart from the evaluator's {self.judgment}"
                )
            self.made = self.reported(env).findings[0].detail.get()
        return self.made

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


@contextmanager
def engine_errors() -> Iterator[None]:
    """An error the native evaluator returns is the engine's, never the config's: the config
    loaded, so a refusal here is a bug or a stale build (exit 2)."""
    try:
        yield
    except ValueError as error:
        raise EngineError(f"the native evaluator failed: {error}") from error


JUDGMENTS = list(Judgment)
# The evaluator's verdict codes of a denied target (`Rules.reach`).
VERDICT_TRUTHS = {1: Truth.TRUE, 2: Truth.UNKNOWN}


class NativeRuleContext(RuleContext):
    """RuleContext over a NativeGraphIndex: the evaluator judges, selects and searches; Python
    makes findings and messages."""

    def __init__(self, config: Config, graph: Graph, labeler: Labeler, repo_root: Path | None = None) -> None:
        super().__init__(config, graph, labeler, repo_root)
        index = labeler.index
        assert isinstance(index, NativeGraphIndex)
        self.native = index

    @cached_property
    def plan(self) -> RulePlan:
        rules = {spec.id: primitive_of(spec) for spec in self.config.rules.values()}
        edge_rules = {
            name: made
            for name, made in rules.items()
            if issubclass(made, EdgeRule) and RulePlan.mode(self.config.rules[name])
        }
        return RulePlan(self.config, self.native.native, self.labeler, edge_rules)

    @cached_property
    def evaluator(self) -> Any:
        """The native evaluator of this run: labels, and every edge rule compiled."""
        native: Any = import_module()
        with engine_errors():
            return native.Rules(self.native.native.link, self.plan.text())

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
        """The labels set two ways that the evaluator met while rules ran: what the Python engine
        meets lazily, where a rule reads them."""
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

    def evaluated(self, rule: EdgeRule) -> Iterator[tuple[Edge, RuleRun]]:
        """The evaluator's rows of an edge or entrypoint rule, in the order the rule judges: a
        violation becomes its finding here; any other judgment goes to the rule's own code, which
        must judge the same."""
        with engine_errors():
            rows = self.evaluator.judge(self.plan.index[rule.spec.id])
        ids = array("I", dict.fromkeys(row[0] for row in rows))
        edges = dict(zip(ids, self.native.native.core.get(ids)))
        spec = rule.spec
        ledgerable, unknown = rule.flags(Judgment.VIOLATION)
        for edge_id, target, code, identity, src, dst in rows:
            edge = edges[edge_id]
            judgment = JUDGMENTS[code]
            if judgment is not Judgment.VIOLATION:
                yield edge, self.judged_alone(rule, edge, target, judgment)
                continue
            finding = Finding(
                rule=spec.id,
                identity=tuple(identity),
                detail=Rejudged(rule, edge, judgment, target),
                owner=self.owner(src),
                subjects=(src, dst) if dst and dst != src else (src,),
                ledgerable=ledgerable and spec.ledger,
                unknown=unknown,
                witness=edge,
            )
            yield edge, RuleRun(spec, [finding])

    def cycle_rows(self, rule: CyclePrimitive) -> list[CycleRow]:
        with engine_errors():
            rows = self.evaluator.pairs(self.plan.index[rule.spec.id])
        return [CycleRow(edge, target, JUDGMENTS[code], pair) for edge, target, code, pair in rows]

    @staticmethod
    def judged_alone(rule: EdgeRule, edge: Edge, target: int, judgment: Judgment) -> RuleRun:
        """What the rule's own code reports for one destination the evaluator judged neither
        clean nor a plain violation."""
        env = rule.envs_of(edge)[target]
        judged = rule.decide(env)
        if judged is not judgment:
            site = edge.site
            raise EngineError(
                f"rule {rule.spec.id} judges {edge.kind} {site.path}:{site.line} {judged}, the evaluator {judgment}"
            )
        return rule.recorded([env])

    def reach_found(self, rule: ReachPrimitive) -> list[Found]:
        """The evaluator's search of a reach rule, its paths made into Edges."""
        with engine_errors():
            found = self.evaluator.reach(self.plan.index[rule.spec.id])
        ids = array("I", dict.fromkeys(edge for _, _, path, _ in found for edge, _ in path))
        edges = dict(zip(ids, self.native.native.core.get(ids)))
        out = []
        for what, source, path, code in found:
            walked = tuple(edges[edge] for edge, _ in path)
            dst = rule.envs_of(walked[-1])[path[-1][1]].dst if walked else None
            out.append(Found(what, source, walked, dst, VERDICT_TRUTHS.get(code)))
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

    def judged_edges(self, rule: EdgeRule) -> Iterator[tuple[Edge, RuleRun | None]]:
        """The rule's edges in the order the rule judges them: an edge or entrypoint rule's with
        what the evaluator judged (`evaluated`); a rule that judges in Python gets the edges its
        selection does not rule out, to judge."""
        if rule.spec.primitive in JUDGED:
            yield from self.evaluated(rule)
            return
        if self.plan.mode(rule.spec) != "select":
            yield from super().judged_edges(rule)
            return
        with engine_errors():
            selected = array("I", self.evaluator.select(self.plan.index[rule.spec.id]))
        for edge in self.native.native.core.get(selected):
            yield edge, None


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
        out: dict[int, int] = {}
        counters: dict[tuple[str, str, str, str], int] = {}
        for edge in self.graph.edges:
            key = (edge.site.module, edge.site.scope, edge.kind, edge.surface)
            out[edge.handle] = counters.get(key, 0)
            counters[key] = counters.get(key, 0) + 1
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
