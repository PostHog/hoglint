"""Rules over the native graph: lazy edge tables and grouped evaluation.

The Python engine judges every edge. Here a rule's core edges are grouped first by what the rule
reads: the label values of both ends at the granularity the fields need (a module, a symbol),
and the edge fields it names. One representative per group is judged; a group that cannot make
a finding or an incomplete is dropped whole. The edges left go through the rule's own per-edge
code in the Python engine's order, so the results are the same.

Groups are built in stages, cheapest first: conjuncts of the selection that read only edge
fields, then only `src`, then only `dst`, then the rule's whole decision. A conjunct that is
false for a group makes the selection false for every edge in it.
"""

from __future__ import annotations

from array import array
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from functools import cached_property
from operator import attrgetter
from pathlib import Path
from typing import Any

from hoglint.config.model import Config
from hoglint.index import DECL_NAMES, MODULE_SCOPE, EdgeGroup, GraphIndex, Memo, SymbolFacts, has_endpoint_attrs
from hoglint.labels import Labeler
from hoglint.ledger.envelope import EngineError
from hoglint.model import Edge, Finding, FindingDetail, Graph
from hoglint.native.graph import CONTEXT_BITS, RESOLUTIONS, SYMBOL_KINDS, NativeGraph, NativeSymbols
from hoglint.rules.audit import ReadAudit, audit_rule
from hoglint.rules.base import (
    EdgeEnv,
    EdgeRule,
    Endpoint,
    Judgment,
    RuleContext,
    RuleRun,
    conjuncts,
    joined,
    read_endpoint,
)
from hoglint.rules.graphs import Hop
from hoglint.rules.reads import Reads, granularity
from hoglint.selectors import UNKNOWN, Node, Ref, Truth, Value, evaluate, refs

GROUPED_ROLES = ("origin", "surface")


def context_bits(names: frozenset[str]) -> int:
    return sum(1 << bit for bit, context in enumerate(CONTEXT_BITS) if context.value in names)


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


NO_CLASS = 0xFFFFFFFF


class Rejudged:
    """The detail of a finding made from its group's judgment: the rule judges the edge again
    when something reads the message, site, witnesses or remedies. An edge judged apart from its
    group is an engine error, never a silent difference."""

    __slots__ = ("edge", "judgment", "made", "rule")

    def __init__(self, rule: EdgeRule, edge: Edge, judgment: Judgment) -> None:
        self.rule = rule
        self.edge = edge
        self.judgment = judgment
        self.made: FindingDetail | None = None

    def get(self) -> FindingDetail:
        if self.made is None:
            envs = list(self.rule.edge_envs(self.edge))
            judged = [self.rule.decide(env) for env in envs]
            if judged != [self.judgment]:
                site = self.edge.site
                raise EngineError(
                    f"rule {self.rule.spec.id} judges {self.edge.kind} {site.path}:{site.line} {judged}, "
                    f"apart from its group's {self.judgment}"
                )
            self.made = self.reported(envs[0]).findings[0].detail.get()
        return self.made

    def reported(self, env: EdgeEnv) -> RuleRun:
        """What the group's judgment reports for `env`, message included, kept out of the rule's
        run: the judgment is checked already, so the rule does not decide again."""
        rule = self.rule
        saved, rule.run = rule.run, RuleRun(rule.spec)
        try:
            rule.report(env, self.judgment, rule.describe(env))
            return rule.run
        finally:
            rule.run = saved


@dataclass(frozen=False, slots=True)
class Classes:
    """The label class of the keys of one endpoint table that rules met, for one list of columns:
    `rows` holds each class's values, `class_of` each key's class (NO_CLASS until a rule meets
    the key)."""

    class_of: array[int] = field(default_factory=lambda: array("I"))
    rows: list[tuple[Any, ...]] = field(default_factory=list)
    index: dict[tuple[Any, ...], int] = field(default_factory=dict)

    def fill(self, keys: array[int], size: int, columns: list[list[Any]]) -> None:
        if len(self.class_of) < size:
            self.class_of.extend(array("I", [NO_CLASS]) * (size - len(self.class_of)))
        class_of = self.class_of
        for key in keys:
            if class_of[key] != NO_CLASS:
                continue
            values = tuple(column[key] for column in columns)
            found = self.index.get(values)
            if found is None:
                found = self.index[values] = len(self.rows)
                self.rows.append(values)
            class_of[key] = found


@dataclass(frozen=True, slots=True)
class Column:
    """One value an endpoint has for rules: `read` makes it. A module-level column is read once
    per basis; an entity-level one (`entity`) once per entity key."""

    name: str
    read: Callable[[Endpoint], Any]
    entity: bool


@dataclass(frozen=True, slots=True)
class EndKeys:
    """The stable keys of some ids at one end: the level the keys live at (`BASIS`, or an end's
    entity table), the key of each id, and the distinct keys."""

    level: str
    key_of: bytes
    distinct: array[int]


UNREAD = object()
BASIS = "basis"


class EndpointClasses:
    """Label classes of rule endpoints, shared by every rule of a run (D30).

    The link keys each end of an edge at two levels (`Link.end_bases`, `Link.end_entities`). A
    basis is what an endpoint's module-level values depend on (its module, whether it is
    external, a source's record); every end shares one basis table. An entity key adds the
    entity (a source's scope, a destination's symbol). Python reads each module-level column
    once per basis and each entity-level column once per entity key, each from one endpoint made
    from a representative edge; the link maps every edge to its key. A rule's class of a key is
    the tuple of the columns it reads. Core edges carry no endpoint attributes, so a class
    depends on the endpoint alone.
    """

    def __init__(self, ctx: NativeRuleContext) -> None:
        self.ctx = ctx
        self.link = ctx.native.native.link
        self.core = ctx.native.native.core
        self.endpoints: dict[str, list[Endpoint]] = {BASIS: []}
        self.entity_bases: dict[str, array[int]] = {}
        self.columns: dict[tuple[str, str], list[Any]] = {}
        self.classes: dict[tuple[str, tuple[str, ...]], Classes] = {}
        self.labeler = ctx.labeler
        self.exports = list(ctx.config.exports)
        self.by_class = any(spec.subclass_of for spec in ctx.config.exports.values())

    def add_endpoints(self, level: str, known: int, end: str, added: bytes) -> None:
        """An endpoint for each key the link added at `level`, made from its representative."""
        endpoints = self.endpoints.setdefault(level, [])
        if len(endpoints) != known:
            raise RuntimeError(f"endpoint table {level} has {len(endpoints)} keys, the link {known}")
        endpoints.extend(self.make_endpoints(end, added))

    def bases(self, ids: array[int], end: str) -> EndKeys:
        """The basis of each id at `end` ("src" or "dst-<role>")."""
        key_of, known, added, distinct = self.link.end_bases(ids.tobytes(), end)
        self.add_endpoints(BASIS, known, end, added)
        return EndKeys(BASIS, key_of, array("I", distinct))

    def entities(self, ids: array[int], end: str, bases: EndKeys, by_export: bool) -> EndKeys:
        """The entity key of each id at `end`. With `by_export`, only a basis that an export
        deciding by class covers keeps its entity."""
        level = f"{end}-entity"
        mask = self.export_mask(bases) if by_export else None
        (key_of, known, added, distinct), added_bases = self.link.end_entities(ids.tobytes(), end, bases.key_of, mask)
        self.add_endpoints(level, known, end, added)
        self.entity_bases.setdefault(level, array("I")).frombytes(added_bases)
        return EndKeys(level, key_of, array("I", distinct))

    def make_endpoints(self, end: str, reps: bytes) -> list[Endpoint]:
        if end == "src":
            return [self.ctx.src_endpoint_at(module, scope, path) for module, scope, path in self.link.src_sites(reps)]
        role = end.removeprefix("dst-")
        out = []
        for resolution, surface, module, symbol in self.link.dst_parts(reps):
            targets = (self.core.maker.entity(module, symbol),) if module is not None else ()
            out.append(self.ctx.dst_endpoints_of(role, RESOLUTIONS[resolution], surface, targets)[0])
        return out

    def reads_symbol(self, endpoint: Endpoint) -> bool:
        """Whether an export that decides by class covers the endpoint's module: only there does
        its export membership depend on its symbol."""
        path = self.labeler.path_of(endpoint.module) if endpoint.module else None
        return path is not None and self.labeler.export_reads_symbol(path)

    def export_mask(self, bases: EndKeys) -> bytes:
        """One byte per basis: 1 where the destination's entity decides its export membership."""
        column = self.column(BASIS, bases.distinct, Column("dst.reads-symbol", self.reads_symbol, False))
        return bytes(value is True for value in column)

    def exports_of(self, endpoint: Endpoint) -> tuple[Any, ...]:
        """What `dst.exported` reads of the destination: whether it is external or outside the
        graph, and its membership of every export."""
        path = self.labeler.path_of(endpoint.module) if endpoint.module else None
        values: list[Any] = [endpoint.external is not None, path is None]
        if path is not None:
            values.extend(self.labeler.export_member((name, path, endpoint.symbol)) for name in self.exports)
        return tuple(values)

    def column(self, level: str, keys: array[int], spec: Column) -> list[Any]:
        """Column `spec` at `level`, with every key in `keys` filled. A module-level column at an
        entity level takes each key's value from its basis."""
        column = self.columns.setdefault((level, spec.name), [])
        size = len(self.endpoints[level])
        if len(column) < size:
            column.extend([UNREAD] * (size - len(column)))
        missing = [key for key in keys if column[key] is UNREAD]
        if not missing:
            return column
        if level == BASIS or spec.entity:
            endpoints, read = self.endpoints[level], spec.read
            for key in missing:
                column[key] = read(endpoints[key])
            return column
        bases = self.entity_bases[level]
        values = self.column(BASIS, array("I", sorted({bases[key] for key in missing})), spec)
        for key in missing:
            column[key] = values[bases[key]]
        return column

    def of(self, keys: EndKeys, specs: list[Column]) -> Classes:
        """Classes of the keys at `keys.level` over the columns `specs`, with every key in
        `keys` filled."""
        if keys.level == BASIS and any(spec.entity for spec in specs):
            raise RuntimeError(f"an entity column among {[spec.name for spec in specs]} at the basis level")
        names = tuple(spec.name for spec in specs)
        classes = self.classes.get((keys.level, names))
        if classes is None:
            classes = self.classes[(keys.level, names)] = Classes()
        columns = [self.column(keys.level, keys.distinct, spec) for spec in specs]
        classes.fill(keys.distinct, len(self.endpoints[keys.level]), columns)
        return classes


class Grouper:
    """Drops the groups of one rule's core edges that cannot make output; see the module doc."""

    def __init__(self, ctx: NativeRuleContext, rule: EdgeRule) -> None:
        self.ctx = ctx
        self.rule = rule
        self.labeler = ctx.labeler
        self.link = ctx.native.native.link
        self.core = ctx.native.native.core
        self.tables = ctx.endpoint_classes
        self.role = rule.spec.endpoint
        self.judgments: dict[int, Judgment] = {}

    def env(self, edge: Edge) -> EdgeEnv:
        dst = self.ctx.dst_endpoints(edge, self.role)[0]
        return self.rule.env(edge, self.ctx.src_endpoint(edge.site), dst)

    def src_keys(self, ids: array[int], reads: Reads) -> EndKeys:
        """The source key of each id at the level `reads` need: its basis, or its scope too."""
        bases = self.tables.bases(ids, "src")
        if not granularity(reads, self.labeler).src_scope:
            return bases
        return self.tables.entities(ids, "src", bases, False)

    def dst_keys(self, ids: array[int], reads: Reads) -> EndKeys:
        """The destination key of each id at the level `reads` need: its basis, its entity, or
        its entity only where an export that decides by class covers it."""
        end = f"dst-{self.role}"
        bases = self.tables.bases(ids, end)
        level = granularity(reads, self.labeler)
        if not level.dst_entity and not level.dst_by_export:
            return bases
        return self.tables.entities(ids, end, bases, level.dst_by_export)

    def keep(self, ids: array[int], key_of: bytes, keep: list[bool]) -> array[int]:
        kept, _ = type(self.link).keep_keys(ids.tobytes(), key_of, bytes(keep))
        return array("I", kept)

    def keep_classes(self, ids: array[int], key_of: bytes, classes: Classes, keep: list[bool]) -> array[int]:
        kept = type(self.link).keep_classes(ids.tobytes(), key_of, classes.class_of.tobytes(), bytes(keep))
        return array("I", kept)

    def field_column(self, root: str, name: str) -> Column:
        """`root.name` of an endpoint: a module-level value is read once per basis."""
        return Column(
            f"{root}.{name}",
            lambda endpoint: read_endpoint(self.ctx, endpoint, name, None, root),
            not self.labeler.module_level(Ref((root, name))),
        )

    def field_columns(self, root: str, names: tuple[str, ...]) -> list[Column]:
        return [self.field_column(root, name) for name in names]

    def dst_columns(self, names: tuple[str, ...], exported: bool) -> list[Column]:
        """The columns of a destination's class: whether it resolved, the fields `names`, and with
        `exported` what export membership reads."""
        out = [Column("dst.unresolved", attrgetter("unresolved"), False), *self.field_columns("dst", names)]
        if exported:
            out.append(Column("dst.exports", self.tables.exports_of, self.tables.by_class))
        return out

    @staticmethod
    def class_keep(node: Node, root: str, names: tuple[str, ...], rows: list[tuple[Any, ...]]) -> list[bool]:
        """Whether `node`, which reads only `root`'s fields `names`, is not false for each class."""
        out = []
        for row in rows:
            values = dict(zip(names, row))

            def resolve(ref: Ref, values: dict[str, Any] = values) -> Value:
                if len(ref.path) != 2 or ref.path[0] != root:
                    return UNKNOWN
                return values[ref.path[1]]

            out.append(evaluate(node, resolve) is not Truth.FALSE)
        return out

    def stage(self, ids: array[int], node: Node, reads: Reads) -> array[int]:
        """Drops the edges whose group makes `node` false. An endpoint's part is judged once per
        label class: the values of the fields it reads."""
        roots = reads.roots()
        if roots == {"edge"} or reads.exported:
            if roots == {"edge"}:
                key_of, reps = self.link.edge_keys(ids.tobytes(), sorted(reads.edge))
            else:
                level = granularity(reads, self.labeler)
                entity = level.dst_entity or level.dst_by_export
                _, _, key_of, reps = self.link.endpoint_keys(ids.tobytes(), self.role, False, entity)
            rep_edges = self.core.get(array("I", reps))
            return self.keep(
                ids, key_of, [evaluate(node, self.env(edge).resolve) is not Truth.FALSE for edge in rep_edges]
            )
        root = "src" if roots == {"src"} else "dst"
        keys = self.src_keys(ids, reads) if root == "src" else self.dst_keys(ids, reads)
        names = tuple(sorted(reads.src if root == "src" else reads.dst))
        classes = self.tables.of(keys, self.field_columns(root, names))
        return self.keep_classes(ids, keys.key_of, classes, self.class_keep(node, root, names, classes.rows))

    def layers(self, ids: array[int], reads: Reads) -> tuple[bytes, bytes]:
        """Groups `ids` by the classes of what `reads` names: (group of each id, representatives)."""
        raw = ids.tobytes()
        src_names = tuple(sorted(reads.src))
        dst_names = tuple(sorted(reads.dst - {"unresolved"}))
        src = self.src_keys(ids, reads)
        dst = self.dst_keys(ids, reads)
        src_classes = self.tables.of(src, self.field_columns("src", src_names))
        dst_classes = self.tables.of(dst, self.dst_columns(dst_names, reads.exported))
        edge_key, edge_reps = self.link.edge_keys(raw, sorted(reads.edge))
        edge_classes = array("I", range(len(array("I", edge_reps)))).tobytes()
        layers = [
            (src.key_of, src_classes.class_of.tobytes()),
            (dst.key_of, dst_classes.class_of.tobytes()),
            (edge_key, edge_classes),
        ]
        return type(self.link).group(raw, layers)

    def shared(self, env: EdgeEnv) -> tuple[bool, Judgment | None]:
        """Whether the group of `env`'s edge stays, and the judgment its edges share: one whose
        report is one finding, which each edge then makes from its own env."""
        judgment = self.rule.decide(env)
        if judgment is Judgment.CLEAN:
            return False, None
        run = self.rule.reported(env, judgment)
        return True, judgment if len(run.findings) == 1 and not run.incomplete else None

    def decide(self, ids: array[int], reads: Reads, record: bool) -> array[int]:
        """Drops the edges whose group the rule's whole decision drops. With `record`, a group
        whose judgment makes one finding gives that judgment to every edge left in it."""
        group_of, reps = self.layers(ids, reads)
        rep_edges = self.core.get(array("I", reps))
        self.audit(rep_edges)
        keep: list[bool] = []
        judgments: list[Judgment | None] = []
        for edge in rep_edges:
            env = self.env(edge)
            kept, judgment = self.shared(env) if record else (not self.rule.droppable(env), None)
            keep.append(kept)
            judgments.append(judgment)
        kept_ids, groups = type(self.link).keep_keys(ids.tobytes(), group_of, bytes(keep))
        for edge_id, group in zip(array("I", kept_ids), array("I", groups)):
            judgment = judgments[group]
            if judgment is not None:
                self.judgments[edge_id] = judgment
        return array("I", kept_ids)

    def audit(self, edges: list[Edge]) -> None:
        """In audit mode, checks the decision of each representative for undeclared reads."""
        audit = self.ctx.audit
        if audit is not None:
            audit_rule(audit, self.rule, edges)

    def survivors(self) -> array[int]:
        """The edges, core and pack alike, that can make output for the rule. An irregular pack
        edge (several targets, or attrs that override its endpoints' labels) is never grouped:
        it survives, and the rule judges it alone."""
        rule = self.rule
        include = context_bits(rule.include)
        exclude = context_bits(rule.exclude)
        ids = array("I", self.link.candidates(list(rule.kinds()), include, exclude))
        reads = Reads.of(rule.read_refs(), self.ctx.config)
        if reads is None or self.role not in GROUPED_ROLES or not ids:
            return ids
        regular, irregular = self.link.split_irregular(ids.tobytes())
        kept = self.grouped(array("I", regular), reads)
        return array("I", sorted([*kept, *array("I", irregular)]))

    def grouped(self, ids: array[int], reads: Reads) -> array[int]:
        """The edges of `ids` whose group can make output; see the module doc."""
        rule = self.rule
        parts = conjuncts(rule.selectors())
        for root in ("edge", "src", "dst"):
            mine = [part for part in parts if self.part_roots(part) == {root}]
            node = joined(mine)
            if node is not None and ids:
                part_reads = Reads.of(refs(node), self.ctx.config)
                if part_reads is not None:
                    ids = self.stage(ids, node, part_reads)
        if not ids:
            return ids
        return self.decide(ids, reads, type(rule).evaluate is EdgeRule.evaluate)

    def part_roots(self, node: Node) -> set[str]:
        reads = Reads.of(refs(node), self.ctx.config)
        return reads.roots() if reads is not None else {"edge", "src", "dst"}


class NativeRuleContext(RuleContext):
    """RuleContext over a NativeGraphIndex: groups come from the link, and a rule's groups hold
    only the core edges that can make output for it."""

    # Set by the parity tool's audit: every grouped decision is checked for undeclared reads.
    audit: ReadAudit | None = None

    def __init__(self, config: Config, graph: Graph, labeler: Labeler, repo_root: Path | None = None) -> None:
        super().__init__(config, graph, labeler, repo_root)
        index = labeler.index
        assert isinstance(index, NativeGraphIndex)
        self.native = index
        self.endpoint_classes = EndpointClasses(self)
        self.judgments: dict[EdgeRule, dict[int, Judgment]] = {}
        self.reach_tables: dict[EdgeRule, dict[str, list[Hop]]] = {}

    def edge_of(self, edge: Edge | int) -> Edge:
        return edge if isinstance(edge, Edge) else self.native.native.core.get([edge])[0]

    def reach_hops(self, rule: EdgeRule, module: str) -> list[Any] | None:
        """Core hops as edge ids with endpoints from the link, then pack edges as Edges, judged
        one by one as before they lived in the link. Only for a rule with no `select`: every core
        edge it sees is followed, so no core edge is judged here. Every module's hops are made at
        once, the first time the rule asks for one."""
        if rule.spec.select is not None or rule.spec.endpoint not in GROUPED_ROLES:
            return None
        found = self.reach_tables.get(rule)
        if found is None:
            found = self.reach_tables[rule] = self.all_reach_hops(rule)
        return list(found.get(module, ()))

    def all_reach_hops(self, rule: EdgeRule) -> dict[str, list[Hop]]:
        """`reach_hops` of every module: per kind, each module's core hops, then its pack hops."""
        link = self.native.native.link
        core_count = self.native.core_count
        tables = self.endpoint_classes
        end = f"dst-{rule.spec.endpoint}"
        include, exclude = context_bits(rule.include), context_bits(rule.exclude)
        out: dict[str, list[Hop]] = {}
        for kind in self.kinds_or_all(rule.kinds()):
            by_module: dict[str, list[Hop]] = {}
            core = array("I", (i for i in array("I", link.candidates([kind], include, exclude)) if i < core_count))
            if core:
                keys = tables.entities(core, end, tables.bases(core, end), False)
                endpoints = tables.endpoints[keys.level]
                modules = link.columns(core.tobytes(), ["module"])[0]
                surfaces = [surface for _, surface, _, _ in link.dst_parts(core.tobytes())]
                for edge_id, key, module, surface in zip(core, array("I", keys.key_of), modules, surfaces):
                    by_module.setdefault(module, []).append(Hop(edge_id, endpoints[key], Truth.TRUE, surface))
            pack: dict[str, list[Edge]] = {}
            pack_ids = array("I", (i for i in array("I", link.ids_of_kind(kind)) if i >= core_count))
            for edge in self.native.native.core.get(pack_ids):
                pack.setdefault(edge.site.module, []).append(edge)
            for module, edges in pack.items():
                group = EdgeGroup(edges, [edge for edge in edges if self.native.is_irregular(edge)])
                for env in rule.group_envs(module, group):
                    selected = rule.selection(env)
                    if selected is not Truth.FALSE:
                        by_module.setdefault(module, []).append(Hop(env.edge, env.dst, selected, env.edge.surface))
            for module, hops in by_module.items():
                out.setdefault(module, []).extend(hops)
        return out

    def replica(self, rule: EdgeRule, edge: Edge) -> Finding | None:
        """The finding of a grouped edge: its group's judgment, with the identity, owner and
        subjects of the edge's own env. The detail (message, site, witnesses, remedies) is made
        when something reads it."""
        judgment = self.judgments.get(rule, {}).get(edge.handle)
        if judgment is None:
            return None
        env = rule.env(edge, self.src_endpoint(edge.site), self.dst_endpoints(edge, rule.spec.endpoint)[0])
        return rule.finding_of(env, judgment, Rejudged(rule, edge, judgment))

    def kinds_or_all(self, kinds: tuple[str, ...]) -> list[str]:
        return list(dict.fromkeys(kinds)) if kinds else self.native.kind_order()

    def source_modules(self, kinds: tuple[str, ...]) -> list[str]:
        return sorted({module for kind in self.kinds_or_all(kinds) for module in self.native.modules_of(kind)})

    def rule_groups(self, rule: EdgeRule) -> Iterator[tuple[str, EdgeGroup]]:
        grouper = Grouper(self, rule)
        ids = grouper.survivors()
        survivors = self.native.native.core.get(ids)
        self.judgments[rule] = grouper.judgments
        by_place: dict[tuple[str, str], list[Edge]] = {}
        for edge in survivors:
            by_place.setdefault((edge.kind, edge.site.module), []).append(edge)
        for kind in self.kinds_or_all(rule.kinds()):
            for module in self.native.modules_of(kind):
                edges = by_place.get((kind, module))
                if edges:
                    yield module, self.native.group(kind, module, edges)


class NativeGraphIndex(GraphIndex):
    """GraphIndex over a NativeGraph: nothing reads every edge unless a caller asks for it."""

    def __init__(self, graph: NativeGraph) -> None:
        self.graph = graph
        self.native = graph
        self.core_count: int = graph.link.core_count()
        self.module_order: dict[str, list[str]] = {}
        self.by_kind = LazyKinds(graph, self.kind_order())
        self.groups = {kind: LazyModules(self.modules_of(kind), self.group_maker(kind)) for kind in self.kind_order()}
        self.facts = NativeSymbolFacts(graph)
        self.path_to_module = {path: module for module, path in graph.modules.items()}
        self.module_of_symbol = Memo(self.find_module)

    context_class: type[RuleContext] = NativeRuleContext

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

    def group(self, kind: str, module: str, edges: list[Edge] | None = None) -> EdgeGroup:
        """One module's edges of one kind, core then pack: all of them, or the ones given."""
        if edges is None:
            edges = self.native.core.get(array("I", self.native.link.ids_from(kind, module)))
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

    def module_edges(self, kind: str, module: str) -> list[Edge]:
        if module not in self.groups.get(kind, {}):
            return []
        return self.groups[kind][module].edges

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
