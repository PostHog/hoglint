"""ORM and DRF meaning of a neutral use position (trailer chain + escape steps).

Core records where a name is used: the trailers above it (`.objects`, `.filter`, `()`, `[]`)
and the escape steps up to the statement. This module is the only place that turns such a
position into a Django shape. Precedence follows today's crossings scanner: `lock` and
`write` beat a scalar terminal, so `select_for_update().count()` is `lock`.
"""

from __future__ import annotations

from dataclasses import dataclass

from hoglint.core.producers import ESCAPE_WRAPPERS, chain_tokens
from hoglint.core.records import last_name

MANAGERS = frozenset(
    {
        "objects",
        "all_teams",
        "raw_objects",
        "objects_including_soft_deleted",
        "objects_with_deleted",
        "all_objects",
        "_default_manager",
        "_base_manager",
    }
)
SCALAR_TERMINALS = frozenset({"values", "values_list", "count", "exists", "aggregate"})
# After one of these a chain yields rows of values, never an instance, whatever follows.
VALUE_METHODS = frozenset({"values", "values_list"})
SINGLE_TERMINALS = frozenset({"get", "first", "last", "earliest", "latest"})
META_MANAGERS = frozenset({"default_manager", "base_manager"})
META_CLASS_ATTRS = frozenset({"model", "concrete_model", "proxy_for_model"})
WRITE_METHODS = frozenset(
    {
        "create",
        "update",
        "delete",
        "get_or_create",
        "update_or_create",
        "bulk_create",
        "bulk_update",
        "acreate",
        "aupdate",
        "adelete",
        "abulk_create",
    }
)
SUBQUERY_CALLS = frozenset({"Exists", "Subquery"})
# The keyword a signal's `connect`, `send` and `receiver` take the sending model by.
SENDER_KEYWORDS = frozenset({"sender"})

# AST class of the parent named by the first escape step, for `other(<class>)`.
STEP_PARENT = {
    "expr": "Expr",
    "assign": "Assign",
    "assign-target": "Assign",
    "annassign": "AnnAssign",
    "annotation": "AnnAssign",
    "annassign-target": "AnnAssign",
    "augassign": "AugAssign",
    "augassign-target": "AugAssign",
    "return": "Return",
    "for-iter": "For",
    "for-target": "For",
    "while-test": "While",
    "if-test": "If",
    "with": "withitem",
    "with-target": "withitem",
    "raise": "Raise",
    "raise-from": "Raise",
    "assert": "Assert",
    "assert-msg": "Assert",
    "del": "Delete",
    "match": "Match",
    "case-guard": "match_case",
    "except": "ExceptHandler",
    "kw": "keyword",
    "classkw": "keyword",
    "star": "Starred",
    "await": "Await",
    "ifexp": "IfExp",
    "bin": "BinOp",
    "unary": "UnaryOp",
    "bool": "BoolOp",
    "cmp": "Compare",
    "comp:key": "DictComp",
    "comp:val": "DictComp",
    "comp:iter": "comprehension",
    "comp:target": "comprehension",
    "comp:if": "comprehension",
    "lambda": "Lambda",
    "lambda:default": "arguments",
    "walrus": "NamedExpr",
    "walrus-target": "NamedExpr",
    "fstr": "FormattedValue",
    "tstr": "Interpolation",
    "yield": "Yield",
    "yieldfrom": "YieldFrom",
    "slice-part": "Slice",
    "param-default": "arguments",
    "param-ann": "arg",
    "typealias": "TypeAlias",
    "typealias-target": "TypeAlias",
    "typeparam": "TypeVar",
}
ANNOTATION_TERMINALS = ("param-ann:", "returns", "annotation", "typealias:")
# A union of classes assigned to a name (`Target = A | B`) is a type alias.
UNION_STEPS = ("bin:|",)
ALIAS_ASSIGN_STEPS = ("assign:", "annassign:")
LOCAL_ASSIGN_STEPS = ("assign:", "annassign:", "walrus:")

# The names the link's shape code reads (src/link/packs/django/shapes.rs `Vocabulary`), handed
# over with each run of the producer: one list for both engines.
VOCABULARY: dict[str, list[str]] = {
    "managers": sorted(MANAGERS),
    "scalar_terminals": sorted(SCALAR_TERMINALS),
    "single_terminals": sorted(SINGLE_TERMINALS),
    "meta_managers": sorted(META_MANAGERS),
    "meta_class_attrs": sorted(META_CLASS_ATTRS),
    "write_methods": sorted(WRITE_METHODS),
    "subquery_calls": sorted(SUBQUERY_CALLS),
    "value_methods": sorted(VALUE_METHODS),
    "sender_keywords": sorted(SENDER_KEYWORDS),
    "annotation_terminals": list(ANNOTATION_TERMINALS),
    "local_assign_steps": list(LOCAL_ASSIGN_STEPS),
    "union_steps": list(UNION_STEPS),
    "alias_assign_steps": list(ALIAS_ASSIGN_STEPS),
}


@dataclass(frozen=True)
class Shape:
    kind: str
    detail: str = ""

    def text(self) -> str:
        return f"{self.kind}({self.detail})" if self.detail else self.kind


def step_parent(step: str) -> str:
    if step.startswith("?"):
        return step[1:].split(".", 1)[0]
    if step in STEP_PARENT:
        return STEP_PARENT[step]
    head = step.split(":", 1)[0]
    if step.startswith("comp:"):
        return {"comp:elt": "ListComp"}.get(step, STEP_PARENT.get(step, "comprehension"))
    return STEP_PARENT.get(head, head)


@dataclass(frozen=True)
class UsePosition:
    """A neutral use position: trailers directly above the used node, then the escape steps."""

    trailers: tuple[str, ...]
    up: tuple[str, ...]

    @classmethod
    def of(cls, chain: str, up: str) -> UsePosition:
        return cls(tuple(chain_tokens(chain)), tuple(up.split()))

    def methods(self) -> tuple[list[str], tuple[str, ...]]:
        """Attribute names before the first subscript, and the trailers from that subscript on."""
        methods: list[str] = []
        for i, t in enumerate(self.trailers):
            if t == "[]":
                return methods, self.trailers[i:]
            if t != "()":
                methods.append(t[1:])
        return methods, ()

    def local_target(self) -> str | None:
        """The plain local name a use is assigned to (`qs = M.objects.filter()`), else None."""
        steps = list(self.up)
        while steps and steps[0] in ESCAPE_WRAPPERS:
            steps.pop(0)
        if not steps or not steps[0].startswith(LOCAL_ASSIGN_STEPS):
            return None
        target = steps[0].split(":", 1)[1]
        return target if target.isidentifier() else None


class OrmShapes:
    """Classifies one use position into today's crossings vocabulary."""

    def enclosing_call(self, position: UsePosition, rest: tuple[str, ...]) -> str | None:
        """First Call above the top of the attribute/call chain with a Name or Attribute func."""
        previous = None
        for t in rest:
            if t == "()" and previous is not None and previous.startswith("."):
                return previous[1:]
            previous = t
        previous = rest[-1] if rest else None
        for step in position.up:
            if step.startswith(("arg:", "kw:")):
                name = last_name(step.split(":", 2)[2])
                if name:
                    return name
            elif step == "()" and previous is not None and previous.startswith("."):
                return previous[1:]
            elif step.startswith("classkw:"):
                return None
            previous = step
        return None

    @staticmethod
    def in_annotation(position: UsePosition) -> bool:
        if any(t == "()" for t in position.trailers):
            return False
        for step in position.up:
            if step.startswith(ANNOTATION_TERMINALS):
                return True
            if step in ("slice", "elt:tuple", "[]") or step.startswith(("bin:", ".")):
                continue
            return False
        return False

    @staticmethod
    def in_type_alias(position: UsePosition) -> bool:
        """An operand of a union of classes that is assigned to a name: `Target = A | B`."""
        steps = list(position.up)
        unions = 0
        while steps and steps[0] in UNION_STEPS:
            steps.pop(0)
            unions += 1
        return unions > 0 and bool(steps) and steps[0].startswith(ALIAS_ASSIGN_STEPS)

    def manager_chain(self, methods: list[str], enclosing: str | None) -> Shape:
        terminal = methods[-1] if methods else "manager"
        if "select_for_update" in methods:
            return Shape("lock")
        writes = [m for m in methods if m in WRITE_METHODS]
        if writes:
            return Shape("write", ",".join(writes))
        if enclosing == "Prefetch":
            return Shape("prefetch")
        if enclosing in SUBQUERY_CALLS:
            return Shape("subquery", enclosing or "")
        if terminal in SCALAR_TERMINALS:
            return Shape("scalar-chain", terminal)
        values = [m for m in methods if m in VALUE_METHODS]
        if values:
            return Shape("scalar-chain", values[0])
        if enclosing is not None and enclosing.endswith("Field"):
            return Shape("drf-field-queryset")
        if terminal in SINGLE_TERMINALS:
            return Shape("instance-single")
        return Shape("instance-many", terminal)

    def attribute_chain(self, methods: list[str], enclosing: str | None) -> Shape:
        head = methods[0]
        if head == "DoesNotExist":
            return Shape("exception")
        if head == "_meta":
            if len(methods) == 1:
                return Shape("_meta")
            if methods[1] in META_MANAGERS:
                return self.manager_chain(methods[2:], enclosing)
            if methods[1] in META_CLASS_ATTRS:
                return self.attribute_chain(methods[2:], enclosing) if len(methods) > 2 else Shape("_meta")
            return Shape("_meta") if methods[1] != "managers" else Shape("other", "Attribute:_meta.managers")
        if head in MANAGERS:
            return self.manager_chain(methods[1:], enclosing)
        if head[:1].isupper():
            return Shape("nested-class-attr", head)
        return Shape("other", f"Attribute:{head}")

    def classify(self, position: UsePosition) -> Shape:
        if self.in_annotation(position):
            return Shape("annotation")
        if not position.trailers and self.in_type_alias(position):
            return Shape("annotation")
        methods, rest = position.methods()
        if methods:
            return self.attribute_chain(methods, self.enclosing_call(position, rest))
        if position.trailers:
            # The parent is the Call that calls the node, or the Subscript that indexes it.
            return Shape("construct") if position.trailers[0] == "()" else Shape("in-collection")
        step = position.up[0] if position.up else "?"
        if step.startswith("arg:"):
            callee = step.split(":", 2)[2]
            name = "?" if callee.startswith("?") else callee.rsplit(".", 1)[-1]
            return Shape("isinstance") if name == "isinstance" else Shape("passed-as-class", name)
        if step.startswith("kw:") and step.split(":")[1] in SENDER_KEYWORDS:
            return Shape("sender")
        if step.startswith("assign:") and "model" in step[len("assign:") :].split(","):
            return Shape("drf-model-serializer")
        if step.startswith(("kw:model:", "classkw:model")) and step.split(":")[1] == "model":
            return Shape("drf-model-serializer")
        if step.startswith(("elt:", "dict:")) or step == "slice":
            return Shape("in-collection")
        return Shape("other", step_parent(step))

    def refine(self, source: UsePosition, later: list[UsePosition]) -> Shape | None:
        """The shape of a manager chain held in a local and used again (`qs = ...; qs.update()`).

        Returns the refined shape only when a later use makes it stronger (lock or write);
        a syntax-only read would miss it.
        """
        methods, _ = source.methods()
        if not methods or methods[0] not in MANAGERS:
            return None
        combined = list(methods[1:])
        for position in later:
            more, _ = position.methods()
            combined.extend(more)
        refined = self.manager_chain(combined, None)
        if refined.kind not in ("lock", "write"):
            return None
        return refined
