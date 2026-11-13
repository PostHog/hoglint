"""How a rule writes itself into the native evaluator's plan (src/link/rules/plan.rs): selector
trees; context bits are `model.context_bits`. Each rule class builds its own part (`plan()`) from
what it derived."""

from __future__ import annotations

from typing import Any

from hoglint.selectors import And, Bare, ListLiteral, Node, Not, Operand, Or, Ref, Selector


def operand_tree(operand: Operand) -> dict[str, Any]:
    if isinstance(operand, Ref):
        return {"ref": list(operand.path)}
    if isinstance(operand, ListLiteral):
        return {"list": [str(item) for item in operand.items]}
    value = operand.value
    return {"lit": value if value is None or isinstance(value, bool) else str(value)}


def tree(node: Node) -> dict[str, Any]:
    """A selector's syntax tree as the plan writes it."""
    if isinstance(node, And):
        return {"and": [tree(part) for part in node.parts]}
    if isinstance(node, Or):
        return {"or": [tree(part) for part in node.parts]}
    if isinstance(node, Not):
        return {"not": tree(node.inner)}
    if isinstance(node, Bare):
        return {"bare": operand_tree(node.operand)}
    return {"cmp": [node.op, operand_tree(node.left), operand_tree(node.right)]}


def selector_tree(selector: Selector | None) -> dict[str, Any] | None:
    return tree(selector.node) if selector is not None else None


def identity_paths(identity: tuple[str, ...]) -> list[list[str]]:
    return [column.split(".") for column in identity]


def ref_paths(selector: Selector | None) -> list[list[str]]:
    """The paths of the refs a selector reads, each once, in its order."""
    if selector is None:
        return []
    return [list(ref.path) for ref in dict.fromkeys(selector.refs())]
