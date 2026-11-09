"""Shared records between hoglint's layers: entities, edges, findings and results.

Producers (core resolver, language and framework packs) emit Edges. Rules read Edges and
labels and emit Findings. Nothing downstream deletes an Edge: filtering is a view, acceptance
is a ledger or exception match.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum

# Bump when the meaning or shape of any fact, edge or identity changes. The ledger header
# stores it; a changed value makes the growth check compare under both schemas.
SCHEMA_VERSION = 1
LANGUAGE_PYTHON = "python"


class Resolution(StrEnum):
    RESOLVED = "resolved"
    CANDIDATES = "candidates"
    UNRESOLVED = "unresolved"
    EXTERNAL = "external"
    NOT_APPLICABLE = "not-applicable"


class Context(StrEnum):
    PROD = "prod"
    TEST = "test"
    MIGRATION = "migration"
    TYPE_ONLY = "type_only"
    FUNCTION_LOCAL = "function_local"
    CONDITIONAL = "conditional"


@dataclass(frozen=True, slots=True)
class EntityId:
    """Language-qualified id: the same path and symbol in another language is another entity."""

    language: str
    module: str
    symbol: str = ""

    def __str__(self) -> str:
        return f"{self.module}.{self.symbol}" if self.symbol else self.module


@dataclass(frozen=True, slots=True)
class Site:
    """Where a coupling is written. `scope` is the stable part; `line` moves with every edit."""

    path: str
    module: str
    scope: str
    line: int
    column: int = 0


@dataclass(frozen=True, slots=True)
class Edge:
    """One coupling between a site and the code it names.

    `surface` is what the code wrote and is never replaced. `targets` hold the origin after
    re-exports, `via` the chain in between. `kind` names the edge family; `use` is a neutral
    use position from core (call, attribute, subscript, argument, return, ...); framework
    packs add their own meaning in `shape`. `producer` names the pass that made the edge, so
    a failed producer marks the rules that need it incomplete.
    """

    kind: str
    site: Site
    surface: str
    targets: tuple[EntityId, ...]
    resolution: Resolution
    contexts: frozenset[Context]
    producer: str
    via: tuple[EntityId, ...] = ()
    use: str = ""
    shape: str = ""
    shape_detail: str = ""
    attrs: tuple[tuple[str, str], ...] = ()

    @property
    def target(self) -> EntityId | None:
        return self.targets[0] if len(self.targets) == 1 else None

    def attr(self, name: str, default: str = "") -> str:
        for key, value in self.attrs:
            if key == name:
                return value
        return default

    def run_id(self, ordinal: int) -> str:
        """Run-local id for witnesses and explain output. Never a ledger or exception key."""
        raw = f"{self.kind}|{self.site.module}|{self.site.scope}|{self.surface}|{ordinal}"
        return hashlib.blake2s(raw.encode(), digest_size=6).hexdigest()


class Outcome(StrEnum):
    CLEAN = "clean"
    VIOLATIONS = "violations"
    INCOMPLETE = "incomplete"


class FindingStatus(StrEnum):
    NEW = "new"
    ACCEPTED = "accepted"  # matched a ledger line
    ALLOWED = "allowed"  # matched an exception; reported for explain only


class IncompleteCause(StrEnum):
    """Who owns an incomplete result (agreed Nov 9)."""

    INTRODUCED = "introduced"  # the change under check added the unknown: its author, blocks
    EXTRACTOR = "extractor"  # a producer failed: DevEx
    INHERITED = "inherited"  # existed before the change: product owners, never blocks


@dataclass(frozen=True, slots=True)
class Remedy:
    kind: str  # permitted-destination | documented-replacement | safe-rewrite | needs-public-api | seal-relation | admit-debt
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Finding:
    rule: str
    identity: tuple[str, ...]  # the rule's identity projection; no line numbers
    message: str
    site: Site | None
    witnesses: tuple[Edge, ...] = ()
    remedies: tuple[Remedy, ...] = ()
    owners: tuple[str, ...] = ()
    status: FindingStatus = FindingStatus.NEW


@dataclass(frozen=False, slots=True)
class RuleResult:
    rule: str
    outcome: Outcome
    findings: list[Finding] = field(default_factory=list)
    incomplete: list[tuple[IncompleteCause, str]] = field(default_factory=list)


@dataclass(frozen=False, slots=True)
class Graph:
    """Everything producers made from one snapshot. Rules and views read only this."""

    edges: list[Edge] = field(default_factory=list)
    modules: dict[str, str] = field(default_factory=dict)  # module -> path
    failed_producers: dict[str, str] = field(default_factory=dict)  # producer -> reason
    symbols: dict[str, dict[str, str]] = field(default_factory=dict)  # "module.qualname" -> facts (kind, bases, ...)

    def by_kind(self, *kinds: str) -> list[Edge]:
        wanted = set(kinds)
        return [edge for edge in self.edges if edge.kind in wanted]
