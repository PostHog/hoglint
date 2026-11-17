"""Primitives that judge one edge at a time: edge, entrypoint, use-shape, signature."""

from __future__ import annotations

from typing import Any

from hoglint.config.model import RuleSpec
from hoglint.model import Remedy
from hoglint.rules.base import EdgeRule, Judgment, RuleContext, Seen

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
    READS = EDGE_READS


class EntrypointPrimitive(EdgeRule):
    """Did a cross-package edge name a public surface? Judges the surface the caller wrote.

    With `uses_at_import = true` a coupling is judged once, at its import: a `reference` edge is
    not seen when its module has an `import` edge that the rule reports (a finding, or an item it
    cannot tell about) with the same surface or a dotted prefix of it, so a name reached as an
    attribute of a reported module import (`module.Name`) is judged there too. A use whose import
    the rule judges clean or does not select is still seen, since the rule may tell the two apart
    (by `edge.kind`, `edge.context` or a field of the source symbol); so is a use with no such
    import.
    """

    PRIMITIVE = MODE = "entrypoint"
    NATIVE_MODE = "judge"
    FOLDS = False
    READS = EDGE_READS | {"uses_at_import"}

    def __init__(self, ctx: RuleContext, spec: RuleSpec) -> None:
        super().__init__(ctx, spec)
        self.default_allow = spec.deny is None and spec.allow is None and spec.require is None
        self.uses_at_import = spec.uses_at_import
        self.reported: dict[tuple[str, str], bool] = {}

    def remedies(self) -> tuple[Remedy, ...]:
        return super().remedies() or (Remedy("needs-public-api"),)

    def describe(self, seen: Seen) -> str:
        return f"{seen.src.module} names {seen.edge.surface}, which is not exported to it"

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

    def describe(self, seen: Seen) -> str:
        return f"{seen.src.module} uses {seen.dst.symbol} in the shape {seen.label}, which the rule does not allow"

    def verdict_plan(self) -> dict[str, Any]:
        spec = self.spec
        return {
            "kind": "use-shape",
            "allow": list(spec.allow_shapes),
            "deny": list(spec.deny_shapes),
            "flow": self.flow,
        }


class SignaturePrimitive(EdgeRule):
    """Does a public signature mention a denied type? Untyped, Any and bare dict are unknown.

    Only public callables count: a private function or method is no part of the signature.
    """

    PRIMITIVE = MODE = "signature"
    NATIVE_MODE = "judge"
    FOLDS = False
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
    def subject(seen: Seen) -> str:
        """The callable a slot belongs to: the edge's `callable` in its module. A slot's edge sits
        in the scope around the callable, so the site names no symbol of its own."""
        callable_name = seen.edge.attr("callable")
        if callable_name:
            return f"{seen.src.module}.{callable_name}"
        return seen.src.symbol or seen.src.module or ""

    def report(self, seen: Seen, judgment: Judgment, describe: str) -> None:
        if judgment is not Judgment.UNKNOWN_TYPE:
            super().report(seen, judgment, describe)
            return
        message = f"{self.subject(seen)}: {seen.edge.attr('slot') or 'signature'} has no usable type ({seen.edge.surface or 'missing'})"
        if self.spec.on_unknown == "incomplete":
            self.incomplete(seen, message)
            return
        self.emit(seen, judgment, message)

    def identified(self) -> frozenset[Judgment]:
        if self.spec.on_unknown == "incomplete":
            return super().identified()
        return super().identified() | {Judgment.UNKNOWN_TYPE}

    def found(self) -> frozenset[Judgment]:
        """A slot of unknown type is a finding too, unless the rule reports it as incomplete."""
        if self.spec.on_unknown == "incomplete":
            return super().found()
        return frozenset({Judgment.VIOLATION, Judgment.UNKNOWN_TYPE})

    def flags(self, judgment: Judgment) -> tuple[bool, bool]:
        if judgment is Judgment.UNKNOWN_TYPE:
            return self.spec.on_unknown == "finding", True
        return super().flags(judgment)

    def describe(self, seen: Seen) -> str:
        slot, type_name = seen.edge.attr("slot") or "signature", seen.dst.symbol
        if slot.startswith("param:"):
            return f"{self.subject(seen)} takes {type_name} in the parameter {slot.removeprefix('param:')}"
        if slot == "return":
            return f"{self.subject(seen)} returns {type_name}"
        return f"{self.subject(seen)} has {type_name} in its {slot}"

    def plan(self) -> dict[str, Any]:
        return {**super().plan(), "signature": {"unknown_types": list(self.spec.unknown_types)}}
