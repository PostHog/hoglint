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
from hoglint.native.bridge import import_module
from hoglint.native.graph import CONTEXT_BITS, RESOLUTIONS, SYMBOL_KINDS, NativeGraph, NativeSymbols
from hoglint.native.plan import JUDGED, RulePlan
from hoglint.rules.audit import ReadAudit, audit_rule
from hoglint.rules.base import (
    EdgeEnv,
    EdgeRule,
    Endpoint,
    GroupedSurvivors,
    Judgment,
    RuleContext,
    RuleRun,
    column_ref,
    conjuncts,
    joined,
    read_endpoint,
)
from hoglint.rules.engine import primitive_of
from hoglint.rules.graphs import CyclePrimitive, CycleRow, EndpointsEnv, Found, Hop, ReachPrimitive
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


NO_CLASS = 0xFFFFFFFF


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


@dataclass(frozen=False, slots=True)
class Codes:
    """The values of one column, each named by a code: what the link groups keys by."""

    values: list[Any] = field(default_factory=list)
    index: dict[Any, int] = field(default_factory=dict)

    def code(self, value: Any) -> int:
        found = self.index.get(value)
        if found is None:
            found = self.index[value] = len(self.values)
            self.values.append(value)
        return found


@dataclass(frozen=True, slots=True)
class Classes:
    """The label classes of some keys over some columns: `class_of` holds each key slot's class
    as u32 bytes (NO_CLASS for a key not asked about), `rows` each class's values."""

    class_of: bytes
    rows: list[tuple[Any, ...]]


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


BASIS = "basis"
NO_GROUP = 0xFFFFFFFF


@dataclass(frozen=True, slots=True)
class Survivors:
    """A rule's edges that can make output: `ids`, and for each the group it was judged in
    (`group_of`, NO_GROUP for an edge judged alone). `judgments` holds each group's shared
    judgment, None when its edges are judged one by one."""

    ids: array[int]
    group_of: array[int]
    judgments: list[Judgment | None]
    reps: list[Edge]

    @staticmethod
    def alone(ids: array[int]) -> Survivors:
        return Survivors(ids, array("I", [NO_GROUP]) * len(ids), [], [])

    def judgment(self, at: int) -> Judgment | None:
        group = self.group_of[at]
        return None if group == NO_GROUP else self.judgments[group]


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
        self.codes: dict[tuple[str, str], array[int]] = {}
        self.values: dict[str, Codes] = {}
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
        spec = Column("dst.reads-symbol", self.reads_symbol, False)
        codes = self.column(BASIS, bases.distinct, spec)
        values = self.values[spec.name].values
        return bytes(code != NO_CLASS and values[code] is True for code in codes)

    def exports_of(self, endpoint: Endpoint) -> tuple[Any, ...]:
        """What `dst.exported` reads of the destination: whether it is external or outside the
        graph, and its membership of every export."""
        path = self.labeler.path_of(endpoint.module) if endpoint.module else None
        values: list[Any] = [endpoint.external is not None, path is None]
        if path is not None:
            values.extend(self.labeler.export_member((name, path, endpoint.symbol)) for name in self.exports)
        return tuple(values)

    def column(self, level: str, keys: array[int], spec: Column) -> array[int]:
        """The codes of column `spec` at `level`, one per key (NO_CLASS for a key not read yet),
        with every key in `keys` read. A module-level column at an entity level takes each key's
        value from its basis."""
        codes = self.codes.setdefault((level, spec.name), array("I"))
        size = len(self.endpoints[level])
        if len(codes) < size:
            codes.extend(array("I", [NO_CLASS]) * (size - len(codes)))
        missing = array("I", type(self.link).unread(keys.tobytes(), codes.tobytes()))
        if not missing:
            return codes
        table = self.values.setdefault(spec.name, Codes())
        if level == BASIS or spec.entity:
            endpoints, read = self.endpoints[level], spec.read
            for key in missing:
                codes[key] = table.code(read(endpoints[key]))
            return codes
        bases = self.entity_bases[level]
        basis_codes = self.column(BASIS, array("I", sorted({bases[key] for key in missing})), spec)
        for key in missing:
            codes[key] = basis_codes[bases[key]]
        return codes

    def of(self, keys: EndKeys, specs: list[Column]) -> Classes:
        """Classes of `keys` over the columns `specs`: the link groups the keys by their codes."""
        if keys.level == BASIS and any(spec.entity for spec in specs):
            raise RuntimeError(f"an entity column among {[spec.name for spec in specs]} at the basis level")
        columns = [self.column(keys.level, keys.distinct, spec).tobytes() for spec in specs]
        size = len(self.endpoints[keys.level])
        class_of, raw = type(self.link).key_classes(keys.distinct.tobytes(), size, columns)
        if not specs:
            return Classes(class_of, [()] if keys.distinct else [])
        codes = array("I", raw)
        tables = [self.values[spec.name].values for spec in specs]
        width = len(specs)
        rows = [
            tuple(table[codes[at + offset]] for offset, table in enumerate(tables))
            for at in range(0, len(codes), width)
        ]
        return Classes(class_of, rows)


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
        kept = type(self.link).keep_classes(ids.tobytes(), key_of, classes.class_of, bytes(keep))
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
            (src.key_of, src_classes.class_of),
            (dst.key_of, dst_classes.class_of),
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

    def decide(self, ids: array[int], reads: Reads, record: bool) -> Survivors:
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
        return Survivors(array("I", kept_ids), array("I", groups), judgments, rep_edges)

    def audit(self, edges: list[Edge]) -> None:
        """In audit mode, checks the decision of each representative for undeclared reads."""
        audit = self.ctx.audit
        if audit is not None:
            audit_rule(audit, self.rule, edges)

    def survivors(self) -> Survivors:
        """The edges, core and pack alike, that can make output for the rule. An irregular pack
        edge (several targets, or attrs that override its endpoints' labels) is never grouped:
        it survives, and the rule judges it alone."""
        rule = self.rule
        include = context_bits(rule.include)
        exclude = context_bits(rule.exclude)
        ids = array("I", self.link.candidates(list(rule.kinds()), include, exclude))
        reads = Reads.of(rule.read_refs(), self.ctx.config)
        if reads is None or self.role not in GROUPED_ROLES or not ids:
            return Survivors.alone(ids)
        regular, irregular = self.link.split_irregular(ids.tobytes())
        kept = self.grouped(array("I", regular), reads)
        alone = array("I", irregular)
        group_of = kept.group_of + array("I", [NO_GROUP]) * len(alone)
        return Survivors(kept.ids + alone, group_of, kept.judgments, kept.reps)

    def grouped(self, ids: array[int], reads: Reads) -> Survivors:
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
            return Survivors.alone(ids)
        return self.decide(ids, reads, type(rule).evaluate is EdgeRule.evaluate)

    def part_roots(self, node: Node) -> set[str]:
        reads = Reads.of(refs(node), self.ctx.config)
        return reads.roots() if reads is not None else {"edge", "src", "dst"}


JUDGMENTS = list(Judgment)


class FindingClasses:
    """The findings of a rule's grouped edges, made once per class of everything a finding
    names besides its detail: identity, owner, subjects and flags. A class holds the edges with
    the same source and destination keys at the level the identity reads, the same identity edge
    fields, and the same judgment; the link groups them. Each edge's finding is its class's, with
    the edge's own detail and witness."""

    def __init__(self, ctx: NativeRuleContext, grouper: Grouper, survivors: Survivors) -> None:
        self.ctx = ctx
        self.rule = grouper.rule
        self.made: dict[int, Finding] = {}
        self.class_of = self.classes(grouper, survivors)

    def edge_fields(self) -> list[str] | None:
        """The edge fields a finding's identity reads, with the ones the primitive's environment
        reads to refine the shape; None when one has no key."""
        names: set[str] = set()
        for column in self.rule.spec.identity:
            ref = column_ref(column)
            if len(ref.path) != 2:
                continue
            root, name = ref.path
            if name == "ordinal" or (root == "src" and name == "exported"):
                return None
            if root == "edge":
                names.add(name)
            elif name == "context":
                names.add("context")
        if names & {"label", "shape", "shape_detail"}:
            names.update({"label", "shape", "shape_detail"})
            names.update(ref.path[1] for ref in self.rule.own_reads() if len(ref.path) == 2 and ref.path[0] == "edge")
        return sorted(names)

    def classes(self, grouper: Grouper, survivors: Survivors) -> array[int] | None:
        ids = survivors.ids
        fields = self.edge_fields()
        if fields is None or all(judgment is None for judgment in survivors.judgments) or not ids:
            return None
        refs_read = [column_ref(column) for column in self.rule.spec.identity]
        reads = Reads.of(refs_read, self.ctx.config)
        if reads is None:
            return None
        labeler = self.ctx.labeler
        tables = self.ctx.endpoint_classes
        src = tables.bases(ids, "src")
        if not all(labeler.module_level(Ref(("src", name))) for name in reads.src):
            src = tables.entities(ids, "src", src, False)
        end = f"dst-{grouper.role}"
        dst = tables.bases(ids, end)
        symbol_level = not all(labeler.module_level(Ref(("dst", name))) for name in reads.dst if name != "unresolved")
        if symbol_level or reads.exported:
            dst = tables.entities(ids, end, dst, False)
        edge_key, edge_reps = self.ctx.native.native.link.edge_keys(ids.tobytes(), fields)
        codes = [JUDGMENTS.index(j) if j is not None else len(JUDGMENTS) for j in survivors.judgments]
        groups = array("I", (len(codes) if group == NO_GROUP else group for group in survivors.group_of))
        layers = [
            (src.key_of, array("I", range(len(tables.endpoints[src.level]))).tobytes()),
            (dst.key_of, array("I", range(len(tables.endpoints[dst.level]))).tobytes()),
            (edge_key, array("I", range(len(edge_reps) // 4)).tobytes()),
            (groups.tobytes(), array("I", [*codes, len(JUDGMENTS)]).tobytes()),
        ]
        class_of, _ = type(self.ctx.native.native.link).group(ids.tobytes(), layers)
        return array("I", class_of)

    def finding(self, at: int, edge: Edge, judgment: Judgment) -> Finding:
        """The finding of the `at`-th survivor, `edge`, whose group's judgment is `judgment`."""
        rule = self.rule
        detail = Rejudged(rule, edge, judgment)
        made = self.made.get(self.class_of[at]) if self.class_of is not None else None
        if made is None:
            env = rule.env(edge, self.ctx.src_endpoint(edge.site), self.ctx.dst_endpoints(edge, rule.spec.endpoint)[0])
            made = rule.finding_of(env, judgment, detail)
            if self.class_of is not None:
                self.made[self.class_of[at]] = made
            return made
        return Finding(
            rule=made.rule,
            identity=made.identity,
            detail=detail,
            owner=made.owner,
            subjects=made.subjects,
            ledgerable=made.ledgerable,
            unknown=made.unknown,
            witness=edge if made.witness is not None else None,
        )


VERDICT_CODES = {Truth.FALSE: 0, Truth.TRUE: 1, Truth.UNKNOWN: 2}
VERDICT_TRUTHS = {code: truth for truth, code in VERDICT_CODES.items()}
EXCUSED = 4


class NativeReach:
    """A reach rule's search in the link (D25, `Link.reach`). Python says what a hop means: the
    class of what `deny` and `unless` read of its destination, and the verdict of each start
    module's class against each class. The link walks the hops in the order the Python search
    takes them, so the findings, incompletes and witness paths are the same.

    Only for a rule whose verdicts read the endpoints alone, with no `select` (every edge it
    sees is followed) and no irregular edge; any other rule searches in Python."""

    def __init__(self, ctx: NativeRuleContext, rule: ReachPrimitive, ids: array[int]) -> None:
        self.ctx = ctx
        self.rule = rule
        self.ids = ids
        self.link = ctx.native.native.link
        self.grouper = Grouper(ctx, rule)
        self.modules = frozenset(self.link.columns(ids.tobytes(), ["module"])[0])
        refs_read = [
            ref for selector in (rule.spec.deny, rule.spec.unless) if selector is not None for ref in selector.refs()
        ]
        found = Reads.of(refs_read, ctx.config)
        assert found is not None
        self.reads = found

    @staticmethod
    def of(ctx: NativeRuleContext, rule: ReachPrimitive) -> NativeReach | None:
        if rule.spec.select is not None or rule.spec.endpoint not in GROUPED_ROLES or not rule.endpoint_verdicts:
            return None
        link = ctx.native.native.link
        include, exclude = context_bits(rule.include), context_bits(rule.exclude)
        ids = array("I")
        for kind in ctx.kinds_or_all(rule.kinds()):
            ids.extend(array("I", link.candidates([kind], include, exclude)))
        _, irregular = link.split_irregular(ids.tobytes())
        return NativeReach(ctx, rule, ids) if not irregular else None

    def has_hops(self, module: str) -> bool:
        return module in self.modules

    def hop_classes(self) -> tuple[bytes, list[Endpoint]]:
        """The verdict class of each hop, and a destination of each class."""
        keys = self.grouper.dst_keys(self.ids, self.reads)
        names = tuple(sorted(self.reads.dst - {"unresolved"}))
        classes = self.ctx.endpoint_classes.of(keys, self.grouper.dst_columns(names, False))
        class_of, reps = type(self.link).group(self.ids.tobytes(), [(keys.key_of, classes.class_of)])
        role = self.rule.spec.endpoint
        dsts = [self.ctx.dst_endpoints(edge, role)[0] for edge in self.ctx.native.native.core.get(array("I", reps))]
        return class_of, dsts

    def origin_classes(self, sources: list[str]) -> tuple[list[int], list[Endpoint]]:
        """The verdict class of each start module (what `deny` and `unless` read of it), and a
        start of each class."""
        names = sorted(self.reads.src)
        index: dict[tuple[Any, ...], int] = {}
        reps: list[Endpoint] = []
        out = []
        for source in sources:
            origin = self.ctx.module_endpoint(source)
            values = tuple(read_endpoint(self.ctx, origin, name, None, "src") for name in names)
            found = index.get(values)
            if found is None:
                found = index[values] = len(reps)
                reps.append(origin)
            out.append(found)
        return out, reps

    def verdicts(self, origins: list[Endpoint], dsts: list[Endpoint]) -> bytes:
        out = bytearray()
        for origin in origins:
            for dst in dsts:
                verdict, excused = self.rule.judge_hop(EndpointsEnv(self.ctx, origin, dst))
                out.append(VERDICT_CODES[verdict] | (EXCUSED if excused else 0))
        return bytes(out)

    def run(self, sources: list[str]) -> list[Found]:
        class_of, dsts = self.hop_classes()
        origin_of, origins = self.origin_classes(sources)
        role = self.rule.spec.endpoint
        found = self.link.reach(
            self.ids.tobytes(), class_of, role, list(zip(sources, origin_of)), self.verdicts(origins, dsts), len(dsts)
        )
        paths = [array("I", path) for _, _, path, _ in found]
        edges = iter(self.ctx.native.native.core.get(array("I", [edge for path in paths for edge in path])))
        out = []
        for (kind, source, _, verdict), path in zip(found, paths):
            walked = tuple(next(edges) for _ in path)
            dst = self.ctx.dst_endpoints(walked[-1], role)[0]
            out.append(Found(sources[source], walked, dst, VERDICT_TRUTHS[verdict] if kind == "denied" else None))
        return out


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
        self.reach_tables: dict[EdgeRule, dict[str, list[Hop]]] = {}

    def edge_of(self, edge: Edge | int) -> Edge:
        return edge if isinstance(edge, Edge) else self.native.native.core.get([edge])[0]

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
        return native.Rules(self.native.native.link, self.plan.text())

    def evaluated(self, rule: EdgeRule) -> Iterator[tuple[Edge, RuleRun]]:
        """The evaluator's rows of an edge or entrypoint rule, in the order the rule judges: a
        violation becomes its finding here; any other judgment goes to the rule's own code, which
        must judge the same."""
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

    def reach_search(self, rule: ReachPrimitive) -> NativeReach | None:
        return NativeReach.of(self, rule)

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

    def kinds_or_all(self, kinds: tuple[str, ...]) -> list[str]:
        return list(dict.fromkeys(kinds)) if kinds else self.native.kind_order()

    def edges_passing(self, kinds: tuple[str, ...], node: Node | None) -> list[Edge]:
        """`node`, which reads only an edge's kind and contexts, judged once per kind and context
        bits in the link; only the edges it does not make false become Edges."""
        if node is None or not kinds:
            return super().edges_passing(kinds, node)
        link = self.native.native.link
        core = self.native.native.core
        out: list[Edge] = []
        for kind in dict.fromkeys(kinds):
            ids = link.ids_of_kind(kind)
            if not ids:
                continue
            key_of, reps = link.edge_keys(ids, ["context"])
            keep = []
            for edge in core.get(array("I", reps)):
                src = self.src_endpoint(edge.site)
                keep.append(evaluate(node, EdgeEnv(self, edge, src, src).resolve) is not Truth.FALSE)
            kept, _ = type(link).keep_keys(ids, key_of, bytes(keep))
            out.extend(core.get(array("I", kept)))
        return out

    def source_modules(self, kinds: tuple[str, ...]) -> list[str]:
        return sorted({module for kind in self.kinds_or_all(kinds) for module in self.native.modules_of(kind)})

    def grouped_survivors(self, rule: EdgeRule) -> GroupedSurvivors:
        survivors = Grouper(self, rule).survivors()
        order = array("I", self.native.native.link.rule_order(survivors.ids.tobytes(), self.kinds_or_all(rule.kinds())))
        groups = [survivors.group_of[at] for at in order]
        core = self.native.native.core
        return GroupedSurvivors(
            [survivors.ids[at] for at in order],
            [None if group == NO_GROUP else group for group in groups],
            survivors.reps,
            lambda wanted: core.get(array("I", wanted)),
        )

    def judged_edges(self, rule: EdgeRule) -> Iterator[tuple[Edge, RuleRun | None]]:
        """The rule's edges in the order the rule judges them. An edge or entrypoint rule is
        judged by the evaluator, per edge (`evaluated`); for the others the grouped survivors
        come as handle arrays: a grouped edge with its finding, made from its class, any other
        alone, to be judged. Edges are made once, as the findings' witnesses."""
        if rule.spec.primitive in JUDGED:
            yield from self.evaluated(rule)
            return
        if self.plan.mode(rule.spec) == "select":
            selected = array("I", self.evaluator.select(self.plan.index[rule.spec.id]))
            for edge in self.native.native.core.get(selected):
                yield edge, None
            return
        grouper = Grouper(self, rule)
        survivors = grouper.survivors()
        link = self.native.native.link
        order = array("I", link.rule_order(survivors.ids.tobytes(), self.kinds_or_all(rule.kinds())))
        edges = self.native.native.core.get(array("I", (survivors.ids[at] for at in order)))
        findings = FindingClasses(self, grouper, survivors)
        sees = type(rule).sees is not EdgeRule.sees
        for at, edge in zip(order, edges):
            judgment = survivors.judgment(at)
            if judgment is None:
                if rule.sees(edge):
                    yield edge, None
                continue
            if sees and not rule.sees(edge):
                continue
            yield edge, RuleRun(rule.spec, [findings.finding(at, edge, judgment)])


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
