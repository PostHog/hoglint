"""Couplings: the evidence move admission reads, cut to the modules a rename touches.

A coupling is (rule, src module, dst, edge kind, values). `values` are the rule's `edge.*`
identity columns for that edge, for example a use's shape label. They carry what the rule judges
besides the two ends, so a move that also changes how code uses a symbol finds no old coupling
to admit it. A rule whose identity names `edge.*` columns but whose primitive does not judge single
edges counts no coupling, so its growth is never admitted as a move.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from hoglint.ledger.file import LineKey
from hoglint.model import Edge, Graph
from hoglint.rules.base import EdgeRule, RuleContext, project, rule_kinds
from hoglint.rules.engine import RunResult, primitive_of

Coupling = tuple[str, str, str, str, tuple[str, ...]]  # (rule, src module, dst, edge kind, edge columns)
EDGE_ROOT = "edge."


def edge_columns(identity: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(column for column in identity if column.startswith(EDGE_ROOT))


def destinations(edge: Edge) -> list[str]:
    return [str(target) for target in edge.targets] or [edge.surface]


class CouplingScan:
    """Counts couplings and lists finding witnesses for the edges whose source module or target
    module is in `scope`. An empty scope counts nothing."""

    def __init__(self, result: RunResult, graph: Graph, scope: frozenset[str]) -> None:
        self.result = result
        self.graph = graph
        self.scope = scope

    def touches(self, edge: Edge) -> bool:
        return edge.site.module in self.scope or any(target.module in self.scope for target in edge.targets)

    def counts(self) -> dict[Coupling, int]:
        if not self.scope:
            return {}
        config = self.result.config
        edges = [edge for edge in self.graph.edges if self.touches(edge)]
        ctx = RuleContext(config, self.graph, self.result.labeler)
        out: Counter[Coupling] = Counter()
        for spec in config.rules.values():
            if not spec.ledger:
                continue
            columns = edge_columns(spec.identity)
            primitive = primitive_of(spec)
            if issubclass(primitive, EdgeRule):
                rule = primitive(ctx, spec)
                for edge in edges:
                    if rule.sees(edge):
                        values = {project(columns, env.resolve) for env in rule.envs_of(edge)}
                        out.update(self.couplings(spec.id, edge, values))
            elif not columns:
                kinds = set(rule_kinds(spec, config.views))
                for edge in edges:
                    if not kinds or edge.kind in kinds:
                        out.update(self.couplings(spec.id, edge, {()}))
        return dict(out)

    @staticmethod
    def couplings(rule: str, edge: Edge, values: Iterable[tuple[str, ...]]) -> list[Coupling]:
        return [(rule, edge.site.module, dst, edge.kind, value) for dst in destinations(edge) for value in values]

    def witnesses(self) -> dict[LineKey, list[Coupling]]:
        """The couplings behind each ledgerable finding that has a witness in scope."""
        if not self.scope:
            return {}
        rules = self.result.config.rules
        out: dict[LineKey, list[Coupling]] = {}
        for rule, run in self.result.runs.items():
            spec = rules.get(rule)
            if spec is None or not spec.ledger:
                continue
            table = run.findings
            positions = [i for i, column in enumerate(spec.identity) if column.startswith(EDGE_ROOT)]
            for row in range(len(table)):
                if not table.ledgerable[row] or table.allowed[row]:
                    continue
                witnesses = table.witnesses(row)
                if not any(self.touches(edge) for edge in witnesses):
                    continue
                identity = table.identity(row)
                values = tuple(identity[i] for i in positions if i < len(identity))
                for edge in witnesses:
                    out.setdefault((rule, identity), []).extend(self.couplings(rule, edge, (values,)))
        return out
