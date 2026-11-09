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
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

from hoglint.config.model import Config
from hoglint.index import MODULE_SCOPE, EdgeGroup, GraphIndex, Memo, SymbolFacts, has_endpoint_attrs
from hoglint.labels import Labeler
from hoglint.model import Edge, Graph
from hoglint.native.graph import CONTEXT_BITS, NativeGraph
from hoglint.rules.base import EDGE_DEPENDENT_FIELDS, EdgeEnv, EdgeRule, RuleContext, conjuncts, joined, read_endpoint
from hoglint.selectors import Node, Ref, Truth, evaluate, refs

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


class NativeSymbolFacts(SymbolFacts):
    """SymbolFacts from the link's inherit and decorate facts, then the packs' own edges."""

    def __init__(self, graph: NativeGraph, inherits: list[Edge], decorates: list[Edge]) -> None:
        super().__init__(graph, [], [])
        for owner, _, names in graph.link.class_facts("inherit", "class"):
            self.add_base(owner, names)
        for owner, surface, names in graph.link.class_facts("decorate", "decorated"):
            self.add_decorator(owner, surface, names)
        self.add_edges(inherits, decorates)


@dataclass(frozen=True, slots=True)
class Reads:
    """What a rule's decision reads, split by where the value comes from."""

    edge: frozenset[str]
    src: frozenset[str]
    dst: frozenset[str]
    exported: bool

    @staticmethod
    def of(ref_list: list[Ref], config: Config) -> Reads | None:
        """None when a read has no grouped form (a per-edge ordinal, a src export)."""
        edge: set[str] = set()
        src: set[str] = set()
        dst: set[str] = set()
        exported = False
        pending = list(ref_list)
        while pending:
            ref = pending.pop()
            if len(ref.path) != 2:
                continue
            root, name = ref.path
            if root == "edge":
                if name == "ordinal":
                    return None
                if name == "label":
                    dst.add("unresolved")
                edge.add("kind" if name == "label" else name)
            elif root in ("src", "dst") and name == "context":
                edge.add("context")
            elif root == "src" and name in EDGE_DEPENDENT_FIELDS:
                return None
            elif root == "dst" and name == "exported":
                if not exported:
                    exported = True
                    for spec in config.exports.values():
                        if spec.audience is not None:
                            pending.extend(refs(spec.audience.node))
            elif root == "src":
                src.add(name)
            elif root == "dst":
                dst.add(name)
        return Reads(frozenset(edge), frozenset(src), frozenset(dst), exported)

    def roots(self) -> set[str]:
        out = set()
        if self.edge:
            out.add("edge")
        if self.src:
            out.add("src")
        if self.dst or self.exported:
            out.add("dst")
        return out


class Grouper:
    """Drops the groups of one rule's core edges that cannot make output; see the module doc."""

    def __init__(self, ctx: NativeRuleContext, rule: EdgeRule) -> None:
        self.ctx = ctx
        self.rule = rule
        self.labeler = ctx.labeler
        self.link = ctx.native.native.link
        self.core = ctx.native.native.core
        self.role = rule.spec.endpoint
        self.exports = list(ctx.config.exports.values())

    def env(self, edge: Edge) -> EdgeEnv:
        dst = self.ctx.dst_endpoints(edge, self.role)[0]
        return self.rule.env(edge, self.ctx.src_endpoint(edge.site), dst)

    def module_level(self, root: str, names: frozenset[str]) -> bool:
        return all(self.labeler.module_level(Ref((root, name))) for name in names if name != "unresolved")

    def dst_entity(self, reads: Reads) -> bool:
        if not self.module_level("dst", reads.dst):
            return True
        return reads.exported and any(spec.subclass_of for spec in self.exports)

    def keep(self, ids: array[int], group_of: array[int], keep: list[bool]) -> array[int]:
        return array("I", (i for i, group in zip(ids, group_of) if keep[group]))

    def stage(self, ids: array[int], node: Node, reads: Reads) -> array[int]:
        """Drops the edges whose group makes `node` false."""
        roots = reads.roots()
        if roots == {"edge"}:
            key_of, reps = self.link.edge_keys(ids.tobytes(), sorted(reads.edge))
        elif roots == {"src"}:
            key_of, reps, _, _ = self.link.endpoint_keys(
                ids.tobytes(), self.role, not self.module_level("src", reads.src), False
            )
        else:
            _, _, key_of, reps = self.link.endpoint_keys(ids.tobytes(), self.role, False, self.dst_entity(reads))
        rep_edges = self.core.get(array("I", reps))
        keep = [evaluate(node, self.env(edge).resolve) is not Truth.FALSE for edge in rep_edges]
        return self.keep(ids, array("I", key_of), keep)

    def src_class(self, edge: Edge, names: list[str]) -> tuple[Any, ...]:
        endpoint = self.ctx.src_endpoint(edge.site)
        return tuple(read_endpoint(self.ctx, endpoint, name, edge, "src") for name in names)

    def dst_class(self, edge: Edge, names: list[str], exported: bool) -> tuple[Any, ...]:
        endpoint = self.ctx.dst_endpoints(edge, self.role)[0]
        values: list[Any] = [endpoint.unresolved]
        values.extend(read_endpoint(self.ctx, endpoint, name, edge, "dst") for name in names)
        if exported:
            path = self.labeler.path_of(endpoint.module) if endpoint.module else None
            values.append(endpoint.external is not None)
            values.append(path is None)
            if path is not None:
                values.extend(self.labeler.export_member((spec.name, path, endpoint.symbol)) for spec in self.exports)
        return tuple(values)

    @staticmethod
    def classes(rows: list[tuple[Any, ...]]) -> bytes:
        seen: dict[tuple[Any, ...], int] = {}
        return array("I", (seen.setdefault(row, len(seen)) for row in rows)).tobytes()

    def decide(self, ids: array[int], reads: Reads) -> array[int]:
        """Drops the edges whose group the rule's whole decision drops."""
        raw = ids.tobytes()
        src_scope = not self.module_level("src", reads.src)
        src_key, src_reps, dst_key, dst_reps = self.link.endpoint_keys(
            raw, self.role, src_scope, self.dst_entity(reads)
        )
        edge_key, edge_reps = self.link.edge_keys(raw, sorted(reads.edge))
        src_names = sorted(reads.src)
        dst_names = sorted(reads.dst - {"unresolved"})
        src_rows = [self.src_class(edge, src_names) for edge in self.core.get(array("I", src_reps))]
        dst_rows = [self.dst_class(edge, dst_names, reads.exported) for edge in self.core.get(array("I", dst_reps))]
        edge_classes = array("I", range(len(array("I", edge_reps)))).tobytes()
        layers = [
            (src_key, self.classes(src_rows)),
            (dst_key, self.classes(dst_rows)),
            (edge_key, edge_classes),
        ]
        group_of, reps = type(self.link).group(raw, layers)
        keep = [not self.rule.droppable(self.env(edge)) for edge in self.core.get(array("I", reps))]
        return self.keep(ids, array("I", group_of), keep)

    def survivors(self) -> array[int]:
        rule = self.rule
        include = context_bits(rule.include)
        exclude = context_bits(rule.exclude)
        ids = array("I", self.link.candidates(list(rule.kinds()), include, exclude))
        reads = Reads.of(rule.read_refs(), self.ctx.config)
        if reads is None or self.role not in GROUPED_ROLES or not ids:
            return ids
        parts = conjuncts(rule.selectors())
        for root in ("edge", "src", "dst"):
            mine = [part for part in parts if self.part_roots(part) == {root}]
            node = joined(mine)
            if node is not None and ids:
                part_reads = Reads.of(refs(node), self.ctx.config)
                if part_reads is not None:
                    ids = self.stage(ids, node, part_reads)
        return self.decide(ids, reads) if ids else ids

    def part_roots(self, node: Node) -> set[str]:
        reads = Reads.of(refs(node), self.ctx.config)
        return reads.roots() if reads is not None else {"edge", "src", "dst"}


class NativeRuleContext(RuleContext):
    """RuleContext over a NativeGraphIndex: groups come from the link, and a rule's groups hold
    only the core edges that can make output for it."""

    def __init__(self, config: Config, graph: Graph, labeler: Labeler, repo_root: Path | None = None) -> None:
        super().__init__(config, graph, labeler, repo_root)
        index = labeler.index
        assert isinstance(index, NativeGraphIndex)
        self.native = index

    def kinds_or_all(self, kinds: tuple[str, ...]) -> list[str]:
        return list(dict.fromkeys(kinds)) if kinds else self.native.kind_order()

    def source_modules(self, kinds: tuple[str, ...]) -> list[str]:
        return sorted({module for kind in self.kinds_or_all(kinds) for module in self.native.modules_of(kind)})

    def rule_groups(self, rule: EdgeRule) -> Iterator[tuple[str, EdgeGroup]]:
        survivors = self.native.native.core.get(Grouper(self, rule).survivors())
        by_place: dict[tuple[str, str], list[Edge]] = {}
        for edge in survivors:
            by_place.setdefault((edge.kind, edge.site.module), []).append(edge)
        for kind in self.kinds_or_all(rule.kinds()):
            extra = self.native.extra_by.get(kind, {})
            for module in self.native.modules_of(kind):
                core = by_place.get((kind, module), [])
                if core or module in extra:
                    yield module, self.native.group(kind, module, core)


class NativeGraphIndex(GraphIndex):
    """GraphIndex over a NativeGraph: nothing reads every edge unless a caller asks for it."""

    def __init__(self, graph: NativeGraph) -> None:
        self.graph = graph
        self.native = graph
        self.core_modules: dict[str, list[str]] = {}
        extra = graph.edge_list.extra
        self.extra_by: dict[str, dict[str, list[Edge]]] = {}
        for edge in extra:
            self.extra_by.setdefault(edge.kind, {}).setdefault(edge.site.module, []).append(edge)
        self.endpoint_attr_edges = {id(edge) for edge in extra if edge.attrs and has_endpoint_attrs(edge)}
        self.by_kind = LazyKinds(graph, self.kind_order())
        self.groups = {kind: LazyModules(self.modules_of(kind), self.group_maker(kind)) for kind in self.kind_order()}
        pack_inherits = [edge for edge in extra if edge.kind == "inherit"]
        pack_decorates = [edge for edge in extra if edge.kind == "decorate"]
        self.facts = NativeSymbolFacts(graph, pack_inherits, pack_decorates)
        self.path_to_module = {path: module for module, path in graph.modules.items()}
        self.module_of_symbol = Memo(self.find_module)

    context_class: type[RuleContext] = NativeRuleContext

    def kind_order(self) -> list[str]:
        """Kinds in the order their first edge appears: core kinds, then pack kinds."""
        out = list(self.native.link.kind_order())
        out.extend(kind for kind in self.extra_by if kind not in out)
        return out

    def modules_of(self, kind: str) -> list[str]:
        """Source modules of `kind` in the order their first edge appears."""
        found = self.core_modules.get(kind)
        if found is None:
            found = self.core_modules[kind] = self.native.link.source_modules(kind)
        known = set(found)
        return found + [module for module in self.extra_by.get(kind, {}) if module not in known]

    def is_irregular(self, edge: Edge) -> bool:
        site = edge.site
        if id(edge) in self.endpoint_attr_edges:
            return True
        return bool(site.path) and site.path != self.graph.modules.get(site.module)

    def group(self, kind: str, module: str, core: list[Edge] | None = None) -> EdgeGroup:
        """One module's edges of one kind: its core edges (all, or the ones given), then its
        pack edges."""
        if core is None:
            core = self.native.core.get(array("I", self.native.link.ids_from(kind, module)))
        edges = core + self.extra_by.get(kind, {}).get(module, [])
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
            out[id(edge)] = counters.get(key, 0)
            counters[key] = counters.get(key, 0) + 1
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
