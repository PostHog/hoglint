"""Primitives that judge one edge at a time: edge, entrypoint, use-shape, signature."""

from __future__ import annotations

from hoglint.config.model import RuleSpec
from hoglint.model import Edge, Remedy
from hoglint.rules.base import EdgeEnv, EdgeRule, Endpoint, RuleContext
from hoglint.selectors import Ref, Selector, Truth

EXPORTED = Selector.parse("dst.exported")


class EdgePrimitive(EdgeRule):
    """Is this edge allowed?"""


class EntrypointPrimitive(EdgeRule):
    """Did a cross-package edge name a public surface? Judges the surface the caller wrote."""

    def __init__(self, ctx: RuleContext, spec: RuleSpec) -> None:
        super().__init__(ctx, spec)
        self.default_allow = spec.deny is None and spec.allow is None and spec.require is None

    def violation(self, env: EdgeEnv) -> Truth:
        if self.default_allow:
            return EXPORTED.evaluate(env.resolve).negate()
        return super().violation(env)

    def read_refs(self) -> list[Ref]:
        return super().read_refs() + (EXPORTED.refs() if self.default_allow else [])

    def remedies(self) -> tuple[Remedy, ...]:
        return super().remedies() or (Remedy("needs-public-api"),)

    def describe(self, env: EdgeEnv) -> str:
        return f"{env.src.module} names {env.edge.surface}, which is not exported to it"


class UseShapePrimitive(EdgeRule):
    """Is this use of a foreign symbol in an allowed shape?

    With `flow = "def-use"` the pack's flow-refined shape (edge attr `flow_shape`, written as
    `kind` or `kind(detail)`) replaces the syntax-only shape. The environment carries the refined
    shape; the edge stays the graph's own, so its ordinal and its witness identity hold.
    """

    def __init__(self, ctx: RuleContext, spec: RuleSpec) -> None:
        super().__init__(ctx, spec)
        self.flow = dict(spec.producer_options).get("flow") == "def-use"

    def env(self, edge: Edge, src: Endpoint, dst: Endpoint) -> EdgeEnv:
        refined = edge.attr("flow_shape") if self.flow else ""
        if not refined:
            return EdgeEnv(self.ctx, edge, src, dst)
        shape, _, detail = refined.partition("(")
        return EdgeEnv(self.ctx, edge, src, dst, shape, detail.removesuffix(")"))

    def read_refs(self) -> list[Ref]:
        return [*super().read_refs(), *(Ref(("edge", name)) for name in ("shape", "shape_detail", "use", "flow_shape"))]

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


def is_private(callable_name: str) -> bool:
    """A callable is private when any part of its qualname starts with `_` (dunders excepted)."""
    return any(part.startswith("_") and not part.endswith("__") for part in callable_name.split("."))


class SignaturePrimitive(EdgeRule):
    """Does a public signature mention a denied type? Untyped, Any and bare dict are unknown.

    Only public callables count: a private function or method is no part of the signature.
    """

    def sees(self, edge: Edge) -> bool:
        return super().sees(edge) and not is_private(edge.attr("callable"))

    def read_refs(self) -> list[Ref]:
        return [*super().read_refs(), Ref(("edge", "annotation")), Ref(("dst", "symbol"))]

    def droppable(self, env: EdgeEnv) -> bool:
        if self.selection(env) is Truth.FALSE:
            return True
        if not env.dst.unresolved and self.unknown_type(env):
            return False
        return super().droppable(env)

    def unknown_type(self, env: EdgeEnv) -> bool:
        if env.edge.attr("annotation") == "missing":
            return True
        return (env.dst.symbol or "") in self.spec.unknown_types

    def judge(self, env: EdgeEnv, describe: str) -> None:
        if self.selection(env) is Truth.FALSE:
            return
        if not env.dst.unresolved and self.unknown_type(env):
            message = f"{env.src.symbol}: {env.edge.attr('slot') or 'signature'} has no usable type ({env.edge.surface or 'missing'})"
            if self.spec.on_unknown == "incomplete":
                self.incomplete(env, message)
                return
            self.emit(env, message, ledgerable=self.spec.on_unknown == "finding", unknown=True)
            return
        super().judge(env, describe)

    def describe(self, env: EdgeEnv) -> str:
        slot = env.edge.attr("slot") or "signature"
        return f"{env.src.symbol} exposes {env.dst.symbol} in {slot}"
