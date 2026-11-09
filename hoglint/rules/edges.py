"""Primitives that judge one edge at a time: edge, entrypoint, use-shape, signature."""

from __future__ import annotations

from hoglint.config.model import RuleSpec
from hoglint.model import Remedy
from hoglint.rules.base import EdgeEnv, EdgeRule, RuleContext
from hoglint.selectors import Selector, Truth

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

    def remedies(self) -> tuple[Remedy, ...]:
        return super().remedies() or (Remedy("needs-public-api"),)

    def describe(self, env: EdgeEnv) -> str:
        return f"{env.src.module} names {env.edge.surface}, which is not exported to it"


class UseShapePrimitive(EdgeRule):
    """Is this use of a foreign symbol in an allowed shape?"""

    def shape_names(self, env: EdgeEnv) -> set[str]:
        shape = env.edge.shape or env.edge.use or "other"
        names = {shape}
        if env.edge.shape_detail:
            names.add(f"{shape}({env.edge.shape_detail})")
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


class SignaturePrimitive(EdgeRule):
    """Does a public signature mention a denied type? Untyped, Any and bare dict are unknown."""

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
