"""Primitives that judge one edge at a time: edge, entrypoint, use-shape, signature."""

from __future__ import annotations

from typing import Any

from hoglint.config.model import RuleSpec
from hoglint.model import Edge, Remedy
from hoglint.rules.base import EdgeEnv, EdgeRule, Endpoint, Judgment, RuleContext
from hoglint.selectors import Selector, Truth

EXPORTED = Selector.parse("dst.exported")
# Core's edge kinds of a name bound by an import, and of a later use of a bound name.
IMPORT = "import"
REFERENCE = "reference"


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
    FOLDS = False
    CONFIRM = "row"
    READS = EDGE_READS


class EntrypointPrimitive(EdgeRule):
    """Did a cross-package edge name a public surface? Judges the surface the caller wrote.

    With `uses_at_import = true` a coupling is judged once, at its import: a `reference` edge is
    not seen when its module has an `import` edge with the same surface that the rule reports (a
    finding, or an item it cannot tell about). A use whose import the rule judges clean or does not
    select is still seen, since the rule may tell the two apart (by `edge.kind`, `edge.context` or a
    field of the source symbol); so is a use with no such import (a name reached as an attribute of
    an imported module).
    """

    PRIMITIVE = MODE = "entrypoint"
    NATIVE_MODE = "judge"
    FOLDS = False
    CONFIRM = "row"
    READS = EDGE_READS | {"uses_at_import"}

    def __init__(self, ctx: RuleContext, spec: RuleSpec) -> None:
        super().__init__(ctx, spec)
        self.default_allow = spec.deny is None and spec.allow is None and spec.require is None
        self.uses_at_import = spec.uses_at_import
        self.imports: dict[tuple[str, str], list[Edge]] | None = None
        self.reported: dict[tuple[str, str], bool] = {}

    def imports_named(self, key: tuple[str, str]) -> list[Edge]:
        """The import edges of a (module, surface)."""
        if self.imports is None:
            self.imports = {}
            for edge in self.ctx.edges_of((IMPORT,)):
                self.imports.setdefault((edge.site.module, edge.surface), []).append(edge)
        return self.imports.get(key, [])

    def import_reported(self, key: tuple[str, str]) -> bool:
        """Whether the rule reports an import edge of a (module, surface): judged once per key,
        only for the keys a use asks about."""
        found = self.reported.get(key)
        if found is None:
            found = self.reported[key] = any(self.reports(edge) for edge in self.imports_named(key))
        return found

    def reports(self, edge: Edge) -> bool:
        """Whether the rule judges any destination of an edge it sees other than clean."""
        return any(self.decide(env) is not Judgment.CLEAN for env in self.edge_envs(edge))

    def sees(self, edge: Edge) -> bool:
        if not super().sees(edge):
            return False
        if not self.uses_at_import or edge.kind != REFERENCE:
            return True
        return not self.import_reported((edge.site.module, edge.surface))

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

    def plan(self) -> dict[str, Any]:
        return {**super().plan(), "uses_at_import": self.uses_at_import}


class UseShapePrimitive(EdgeRule):
    """Is this use of a foreign symbol in an allowed shape?

    With `flow = "def-use"` the pack's flow-refined shape (edge attr `flow_shape`, written as
    `kind` or `kind(detail)`) replaces the syntax-only shape. The environment carries the refined
    shape; the edge stays the graph's own, so its ordinal and its witness identity hold.
    """

    PRIMITIVE = MODE = "use-shape"
    NATIVE_MODE = "judge"
    FOLDS = False
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
    FOLDS = False
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

    @staticmethod
    def sees_callable(edge: Edge) -> bool:
        """Whether the rule sees a slot's callable: only a public one is part of the signature."""
        return not is_private(edge.attr("callable"))

    def sees(self, edge: Edge) -> bool:
        """A public callable's slot, but not the row of a type alias whose expansion core wrote:
        the rows of what it expands to stand for it."""
        return super().sees(edge) and self.sees_callable(edge) and edge.attr("alias") != "expanded"

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
