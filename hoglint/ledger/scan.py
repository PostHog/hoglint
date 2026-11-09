"""A scan summary: what one (code, policy, scanner) triple produced, in a form base and head compare.

`hoglint scan` writes it as JSON, so the base scanner can run in its own checkout and process.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hoglint.ledger.file import LineKey
from hoglint.model import Edge, FindingStatus, Graph
from hoglint.rules.engine import RunResult

Coupling = tuple[str, str, str]  # (src module, dst symbol or module, edge kind)
Triple = tuple[str, str, str, str]  # (src group, dst group, edge kind, context)


def coupling_of(edge: Edge) -> list[Coupling]:
    if not edge.targets:
        return [(edge.site.module, edge.surface, edge.kind)]
    return [(edge.site.module, str(target), edge.kind) for target in edge.targets]


def key_text(key: LineKey) -> str:
    return "\t".join((key[0], *key[1]))


def key_from_text(text: str) -> LineKey:
    parts = text.split("\t")
    return parts[0], tuple(parts[1:])


@dataclass(frozen=False, slots=True)
class ScanSummary:
    schema: int = 0
    policy: str = ""
    lines: dict[LineKey, int] = field(default_factory=dict)
    growth: dict[str, tuple[int, ...]] = field(default_factory=dict)
    findings: set[LineKey] = field(default_factory=set)
    incomplete: dict[tuple[str, str], str] = field(default_factory=dict)
    triples: set[Triple] = field(default_factory=set)
    couplings: dict[Coupling, int] = field(default_factory=dict)
    witnesses: dict[LineKey, list[Coupling]] = field(default_factory=dict)
    levels: dict[str, list[str]] = field(default_factory=dict)

    @staticmethod
    def from_run(result: RunResult, graph: Graph, schema: int, with_couplings: bool = False) -> ScanSummary:
        config = result.config
        summary = ScanSummary(schema=schema, policy=config.policy_hash, lines=dict(result.scan))
        for rule in config.rules.values():
            if rule.ledger and rule.identity:
                summary.growth[rule.id] = tuple(rule.identity.index(column) for column in rule.growth_key)
        for finding in result.findings():
            if finding.status is FindingStatus.ALLOWED:
                continue
            key = (finding.rule, finding.identity)
            summary.findings.add(key)
            if with_couplings and finding.ledgerable:
                for edge in finding.witnesses:
                    summary.witnesses.setdefault(key, []).extend(coupling_of(edge))
        for item in result.incomplete():
            summary.incomplete[(item.rule, item.key)] = item.reason
        summary.levels = {name: list(level.passing) for name, level in result.levels.items()}
        summary.triples = group_triples(result, graph)
        if with_couplings:
            for edge in graph.edges:
                for coupling in coupling_of(edge):
                    summary.couplings[coupling] = summary.couplings.get(coupling, 0) + 1
        return summary

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "policy": self.policy,
            "lines": {key_text(key): count for key, count in sorted(self.lines.items())},
            "growth": {rule: list(indexes) for rule, indexes in sorted(self.growth.items())},
            "findings": sorted(key_text(key) for key in self.findings),
            "incomplete": [[rule, key, reason] for (rule, key), reason in sorted(self.incomplete.items())],
            "triples": sorted(list(triple) for triple in self.triples),
            "couplings": [[*coupling, count] for coupling, count in sorted(self.couplings.items())],
            "witnesses": {
                key_text(key): [list(c) for c in couplings] for key, couplings in sorted(self.witnesses.items())
            },
            "levels": self.levels,
        }

    @staticmethod
    def from_json(data: dict[str, Any]) -> ScanSummary:
        return ScanSummary(
            schema=int(data.get("schema", 0)),
            policy=str(data.get("policy", "")),
            lines={key_from_text(text): int(count) for text, count in data.get("lines", {}).items()},
            growth={rule: tuple(indexes) for rule, indexes in data.get("growth", {}).items()},
            findings={key_from_text(text) for text in data.get("findings", [])},
            incomplete={(rule, key): reason for rule, key, reason in data.get("incomplete", [])},
            triples={(a, b, c, d) for a, b, c, d in data.get("triples", [])},
            couplings={(a, b, c): int(n) for a, b, c, n in data.get("couplings", [])},
            witnesses={
                key_from_text(text): [(a, b, c) for a, b, c in couplings]
                for text, couplings in data.get("witnesses", {}).items()
            },
            levels={name: list(groups) for name, groups in data.get("levels", {}).items()},
        )

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_json()))

    @staticmethod
    def read(path: Path) -> ScanSummary:
        return ScanSummary.from_json(json.loads(path.read_text()))


def group_triples(result: RunResult, graph: Graph) -> set[Triple]:
    """(src group, dst group, kind, context) for every edge between two known groups (`[roles] group`)."""
    group = result.config.roles.group
    if not group:
        return set()
    labeler = result.labeler
    out: set[Triple] = set()
    packages: dict[str, str | None] = {}

    def package_of(module: str | None) -> str | None:
        if module is None:
            return None
        if module not in packages:
            value, _ = labeler.field_value(module, None, group)
            packages[module] = str(value.data) if value.state.value == "known" else None
        return packages[module]

    for edge in graph.edges:
        src = package_of(edge.site.module)
        if src is None:
            continue
        context = "+".join(sorted(c.value for c in edge.contexts)) or "prod"
        for target in edge.targets:
            dst = package_of(labeler.module_of_symbol(str(target)))
            if dst is not None and dst != src:
                out.add((src, dst, edge.kind, context))
    return out
