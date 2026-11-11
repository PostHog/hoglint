"""Primitives that judge one edge at a time: edge, entrypoint, use-shape, signature."""

from __future__ import annotations

from typing import Any

from hoglint.config.model import RuleSpec
from hoglint.model import Edge, Remedy
from hoglint.rules.base import EdgeEnv, EdgeRule, Endpoint, Judgment, RuleContext
from hoglint.selectors import Selector, Truth

EXPORTED = Selector.parse("dst.exported")


# What an edge rule reads; an entrypoint rule reads the same.
EDGE_READS = frozenset(
    {
        "kinds",
        "contexts",
        "view",
        "endpoint",
        "from",
        "select",
        "deny",
        "allow",
        "require",
        "unless",
        "remedy",
        "on_unresolved",
    }
)


class EdgePrimitive(EdgeRule):
    """Is this edge allowed?"""

    PRIMITIVE = MODE = "edge"
    NATIVE_MODE = "judge"
    CONFIRM = "row"
    READS = EDGE_READS


class EntrypointPrimitive(EdgeRule):
    """Did a cross-package edge name a public surface? Judges the surface the caller wrote."""

    PRIMITIVE = MODE = "entrypoint"
    NATIVE_MODE = "judge"
    CONFIRM = "row"
    READS = EDGE_READS

    def __init__(self, ctx: RuleContext, spec: RuleSpec) -> None:
        super().__init__(ctx, spec)
        self.default_allow = spec.deny is None and spec.allow is None and spec.require is None

    def violation(self, env: EdgeEnv) -> Truth:
        if self.default_allow:
            return EXPORTED.evaluate(env.resolve).negate()
        return super().violation(env)

    def remedies(self) -> tuple[Remedy, ...]:
        return super().remedies() or (Remedy("needs-public-api"),)

    def describe(self, env: EdgeEnv) -> str:
        return f"{env.src.module} names {env.edge.surface}, which is not exported to it"

    def verdict_plan(self) -> dict[str, Any]:
        return {"kind": "default-allow"} if self.default_allow else super().verdict_plan()


class UseShapePrimitive(EdgeRule):
    """Is this use of a foreign symbol in an allowed shape?

    With `flow = "def-use"` the pack's flow-refined shape (edge attr `flow_shape`, written as
    `kind` or `kind(detail)`) replaces the syntax-only shape. The environment carries the refined
    shape; the edge stays the graph's own, so its ordinal and its witness identity hold.
    """

    PRIMITIVE = MODE = "use-shape"
    NATIVE_MODE = "judge"
    CONFIRM = "row"
    READS = frozenset(
        {
            "kinds",
            "contexts",
            "endpoint",
            "from",
            "select",
            "unless",
            "allow_shapes",
            "deny_shapes",
            "flow",
            "remedy",
            "on_unresolved",
        }
    )

    def __init__(self, ctx: RuleContext, spec: RuleSpec) -> None:
        super().__init__(ctx, spec)
        self.flow = dict(spec.producer_options).get("flow") == "def-use"

    def env(self, edge: Edge, src: Endpoint, dst: Endpoint) -> EdgeEnv:
        refined = edge.attr("flow_shape") if self.flow else ""
        if not refined:
            return EdgeEnv(self.ctx, edge, src, dst)
        shape, _, detail = refined.partition("(")
        return EdgeEnv(self.ctx, edge, src, dst, shape, detail.removesuffix(")"))

    def shape_names(self, env: EdgeEnv) -> set[str]:
        shape = env.shape or env.edge.use or "other"
        names = {shape}
        if env.shape_detail:
            names.add(f"{shape}({env.shape_detail})")
        return names

    def violation(self, env: EdgeEnv) -> Truth:
        names = self.shape_names(env)
        if self.spec.allow_shapes and not names & set(self.spec.allow_shapes):
            return Truth.TRUE
        if self.spec.deny_shapes and names & set(self.spec.deny_shapes):
            return Truth.TRUE
        return Truth.FALSE

    def describe(self, env: EdgeEnv) -> str:
        return f"{env.src.module} uses {env.dst.symbol} as {env.label()}"

    def verdict_plan(self) -> dict[str, Any]:
        spec = self.spec
        return {
            "kind": "use-shape",
            "allow": list(spec.allow_shapes),
            "deny": list(spec.deny_shapes),
            "flow": self.flow,
        }


def is_private(callable_name: str) -> bool:
    """A callable is private when any part of its qualname starts with `_` (dunders excepted)."""
    return any(part.startswith("_") and not part.endswith("__") for part in callable_name.split("."))


class SignaturePrimitive(EdgeRule):
    """Does a public signature mention a denied type? Untyped, Any and bare dict are unknown.

    Only public callables count: a private function or method is no part of the signature.
    """

    PRIMITIVE = MODE = "signature"
    NATIVE_MODE = "judge"
    CONFIRM = "row"
    READS = frozenset(
        {
            "kinds",
            "contexts",
            "from",
            "select",
            "deny",
            "unless",
            "on_unknown",
            "unknown_types",
            "remedy",
            "on_unresolved",
        }
    )

    def sees(self, edge: Edge) -> bool:
        return super().sees(edge) and not is_private(edge.attr("callable"))

    @staticmethod
    def subject(env: EdgeEnv) -> str:
        """The callable a slot belongs to: the edge's `callable` in its module. A slot's edge sits
        in the scope around the callable, so the site names no symbol of its own."""
        callable_name = env.edge.attr("callable")
        if callable_name:
            return f"{env.src.module}.{callable_name}"
        return env.src.symbol or env.src.module or ""

    def decide(self, env: EdgeEnv) -> Judgment:
        if self.selection(env) is Truth.FALSE:
            return Judgment.CLEAN
        if not env.dst.unresolved and self.unknown_type(env):
            return Judgment.UNKNOWN_TYPE
        return super().decide(env)

    def unknown_type(self, env: EdgeEnv) -> bool:
        if env.edge.attr("annotation") == "missing":
            return True
        return (env.dst.symbol or "") in self.spec.unknown_types

    def report(self, env: EdgeEnv, judgment: Judgment, describe: str) -> None:
        if judgment is not Judgment.UNKNOWN_TYPE:
            super().report(env, judgment, describe)
            return
        message = f"{self.subject(env)}: {env.edge.attr('slot') or 'signature'} has no usable type ({env.edge.surface or 'missing'})"
        if self.spec.on_unknown == "incomplete":
            self.incomplete(env, message)
            return
        self.emit(env, judgment, message)

    def found(self) -> frozenset[Judgment]:
        """A slot of unknown type is a finding too, unless the rule reports it as incomplete."""
        if self.spec.on_unknown == "incomplete":
            return super().found()
        return frozenset({Judgment.VIOLATION, Judgment.UNKNOWN_TYPE})

    def flags(self, judgment: Judgment) -> tuple[bool, bool]:
        if judgment is Judgment.UNKNOWN_TYPE:
            return self.spec.on_unknown == "finding", True
        return super().flags(judgment)

    def describe(self, env: EdgeEnv) -> str:
        slot = env.edge.attr("slot") or "signature"
        return f"{self.subject(env)} exposes {env.dst.symbol} in {slot}"

    def plan(self) -> dict[str, Any]:
        return {**super().plan(), "signature": {"unknown_types": list(self.spec.unknown_types)}}
