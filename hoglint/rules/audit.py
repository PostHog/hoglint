"""Read audit: a rule's decision reads only what the rule declares.

Grouped evaluation judges one edge per group and gives the others the same judgment, so a
decision that reads a field its rule does not declare would hand out a wrong answer. A decision
is `droppable` and `decide`; what a finding says about its own edge (identity, subjects, owner,
message) is made per edge from the judgment, so it is not audited. In audit mode every read a
decision makes is recorded:
- label and endpoint fields, where `read_endpoint` reads them;
- edge fields, where `EdgeEnv.edge_value` reads them;
- the Edge and Endpoint attributes a primitive's own Python code reads.

A read outside what the rule's reads grant (`Reads.granted`) is a failure. Building the
environment, hashing and comparing are not reads. Tests run the audit over small graphs; the
parity tool runs it over every group a native run judges.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from hoglint.model import Edge
from hoglint.rules import base
from hoglint.rules.base import EdgeEnv, EdgeRule, Endpoint, RuleContext, StagedSelection
from hoglint.rules.reads import decision_reads, granularity

EDGE_FIELDS = {
    "kind": "kind",
    "surface": "surface",
    "use": "use",
    "shape": "shape",
    "shape_detail": "shape_detail",
    "resolution": "resolution",
    "contexts": "context",
    "producer": "producer",
    "via": "via",
    "targets": "targets",
    "site": "site",
    "attrs": "attrs",
}
ENDPOINT_FIELDS = {"module", "symbol", "path", "external", "unresolved", "scope", "label_symbol"}
ENV_FIELDS = {"shape": "shape", "shape_detail": "shape_detail"}


@dataclass(frozen=False)
class Failure:
    rule: str
    reads: tuple[tuple[str, str], ...]
    edge: Edge

    def render(self) -> str:
        names = ", ".join(f"{root}.{name}" for root, name in self.reads)
        site = self.edge.site
        return f"{self.rule}: reads {names}, which it does not declare ({site.path}:{site.line} {self.edge.kind})"


@dataclass(frozen=False)
class ReadAudit:
    """Records the reads of one decision at a time; `installed` patches the read points."""

    reads: set[tuple[str, str]] = field(default_factory=set)
    failures: list[Failure] = field(default_factory=list)
    checked: int = 0
    depth: int = 0
    active: bool = False
    src: Endpoint | None = None
    dst: Endpoint | None = None

    def log(self, root: str, name: str) -> None:
        if self.active:
            self.reads.add((root, name))

    def quietly(self, call: Callable[..., Any]) -> Callable[..., Any]:
        """`call` with attribute reads inside it not recorded."""
        audit = self

        def inner(*args: Any, **kwargs: Any) -> Any:
            audit.depth += 1
            try:
                return call(*args, **kwargs)
            finally:
                audit.depth -= 1

        return inner

    @contextmanager
    def installed(self) -> Iterator[ReadAudit]:
        audit = self
        saved: dict[tuple[Any, str], Any] = {}

        def patch(owner: Any, name: str, value: Any) -> None:
            saved[(owner, name)] = owner.__dict__[name] if name in owner.__dict__ else None
            setattr(owner, name, value)

        read_endpoint = base.read_endpoint
        edge_value = EdgeEnv.edge_value
        endpoint_value = EdgeEnv.endpoint_value
        edge_attr = Edge.attr

        def audited_read_endpoint(ctx: RuleContext, endpoint: Endpoint, name: str, edge: Edge | None, root: str) -> Any:
            audit.log(root, name)
            return audit.quietly(read_endpoint)(ctx, endpoint, name, edge, root)

        def audited_edge_value(env: EdgeEnv, name: str) -> Any:
            audit.log("edge", name)
            return audit.quietly(edge_value)(env, name)

        def audited_endpoint_value(env: EdgeEnv, endpoint: Endpoint, name: str, root: str) -> Any:
            if name in ("context", "exported"):
                audit.log(root, name)
            return audit.quietly(endpoint_value)(env, endpoint, name, root)

        def audited_attr(edge: Edge, name: str, default: str = "") -> str:
            if audit.depth == 0:
                audit.log("edge", name)
            return audit.quietly(edge_attr)(edge, name, default)

        def edge_getattribute(edge: Edge, name: str) -> Any:
            if audit.depth == 0 and name in EDGE_FIELDS:
                audit.log("edge", EDGE_FIELDS[name])
            return object.__getattribute__(edge, name)

        def endpoint_getattribute(endpoint: Endpoint, name: str) -> Any:
            if audit.depth == 0 and name in ENDPOINT_FIELDS:
                root = "src" if endpoint is audit.src else "dst" if endpoint is audit.dst else "endpoint"
                audit.log(root, "symbol" if name == "label_symbol" else name)
            return object.__getattribute__(endpoint, name)

        def env_getattribute(env: EdgeEnv, name: str) -> Any:
            if audit.depth == 0 and name in ENV_FIELDS:
                audit.log("edge", ENV_FIELDS[name])
            return object.__getattribute__(env, name)

        patch(base, "read_endpoint", audited_read_endpoint)
        patch(EdgeEnv, "edge_value", audited_edge_value)
        patch(EdgeEnv, "endpoint_value", audited_endpoint_value)
        patch(EdgeEnv, "__init__", self.quietly(EdgeEnv.__init__))
        patch(StagedSelection, "rest_key", staticmethod(self.quietly(StagedSelection.rest_key)))
        patch(EdgeEnv, "__getattribute__", env_getattribute)
        patch(Edge, "attr", audited_attr)
        patch(Edge, "__getattribute__", edge_getattribute)
        patch(Endpoint, "__getattribute__", endpoint_getattribute)
        for owner in (Edge, Endpoint):
            patch(owner, "__hash__", self.quietly(owner.__hash__))
            patch(owner, "__eq__", self.quietly(owner.__eq__))
        try:
            yield self
        finally:
            for (owner, name), value in saved.items():
                if value is None:
                    delattr(owner, name)
                else:
                    setattr(owner, name, value)

    def check(
        self, rule: EdgeRule, edge: Edge, src: Endpoint, dst: Endpoint, granted: frozenset[tuple[str, str]]
    ) -> None:
        """Runs the rule's decision for one edge (`droppable` and `decide`, the judgment a group
        shares) and records a failure for each read outside `granted`. What a finding says about
        its own edge is made per edge from the judgment, so it is not audited."""
        self.reads = set()
        self.src, self.dst = src, dst
        self.checked += 1
        self.active = True
        try:
            env = rule.env(edge, src, dst)
            rule.droppable(env)
            rule.decide(env)
        finally:
            self.active = False
        extra = tuple(sorted(self.reads - granted))
        if extra:
            self.failures.append(Failure(rule.spec.id, extra, edge))


def audit_rule(audit: ReadAudit, rule: EdgeRule, edges: Iterable[Edge]) -> None:
    """Audits `rule`'s decision for every environment of `edges`; failures go to `audit`. An edge
    the rule does not see is audited too: a group's representative can be one."""
    reads = decision_reads(rule)
    if reads is None:
        return
    src_scope, dst_entity = granularity(reads, rule.ctx.labeler)
    granted = reads.granted(src_scope, dst_entity)
    for edge in edges:
        src = rule.ctx.src_endpoint(edge.site)
        for dst in rule.ctx.dst_endpoints(edge, rule.spec.endpoint):
            audit.check(rule, edge, src, dst, granted)
