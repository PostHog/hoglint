"""The plan the native evaluator runs (src/link/rules/plan.rs): the config of one run as data.

Python loads and validates the config; the plan hands Rust its fields and domains, assignments,
zones, registries, exports with their audiences, kinds, and per rule what `EdgeRule.decide` reads.
Nothing in it is computed per module or per symbol, except the label column of a field a Python
provider sets, and only when something the plan holds reads that field.
"""

from __future__ import annotations

import json
import hashlib
from collections.abc import Iterable
from typing import Any

from hoglint.config.model import Config, FieldSpec, RuleSpec
from hoglint.labels import Labeler
from hoglint.native.graph import CONTEXT_BITS, NativeGraph
from hoglint.rules.base import RuleType, conjuncts, rule_kinds
from hoglint.rules.entities import CoveragePrimitive, EdgeInventory
from hoglint.rules.exports import DEFAULT_EXCLUDED
from hoglint.rules.graphs import CyclePrimitive
from hoglint.selectors import (
    And,
    Bare,
    FieldType,
    ListLiteral,
    Node,
    Not,
    Operand,
    Or,
    Ref,
    Selector,
    State,
    Value,
    refs,
)


def context_bits(names: frozenset[str]) -> int:
    return sum(1 << bit for bit, context in enumerate(CONTEXT_BITS) if context.value in names)


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


def set_value(spec: FieldSpec | None, value: Any) -> dict[str, Any]:
    """A value an assignment sets: a set field's items (strings, as the schema checks), a string
    (maybe a template), a bool, or the `str()` and `repr()` of anything else."""
    if spec is not None and spec.type is FieldType.SET and isinstance(value, list):
        return {"items": list(value)}
    if isinstance(value, bool):
        return {"bool": value}
    if isinstance(value, str):
        return {"text": value}
    return {"other": [str(value), repr(value)]}


def label_value(value: Value) -> Any:
    """A label as `Labeler.label_only` gives it, for a provider column."""
    if value.state is not State.KNOWN:
        return "none"
    if value.chain:
        return {"zone": str(value.data)}
    if isinstance(value.data, frozenset):
        return {"set": sorted(str(item) for item in value.data)}
    if isinstance(value.data, bool):
        return {"bool": value.data}
    return {"text": str(value.data)}


class RulePlan:
    """The plan of one run over a native graph, and each rule's place in it."""

    def __init__(self, config: Config, graph: NativeGraph, labeler: Labeler, rules: list[RuleType]) -> None:
        self.config = config
        self.graph = graph
        self.labeler = labeler
        self.rules = rules
        self.specs = [rule.spec for rule in rules]
        self.index = {spec.id: at for at, spec in enumerate(self.specs)}
        self.export_at = {name: at for at, name in enumerate(config.exports)}
        self.shape_at = {name: at for at, name in enumerate(config.shapes)}

    @staticmethod
    def covers(spec: RuleSpec) -> list[dict[str, Any]]:
        """A coverage rule's covers, split as `CoveragePrimitive.from_edges` splits a selection."""
        out = []
        for cover in spec.covers if spec.primitive == "coverage" else ():
            selector = cover.selector
            kinds = selector.restricts(("edge", "kind")) if selector is not None else None
            parts = conjuncts((selector,))
            by_edge = [part for part in parts if CoveragePrimitive.reads_kind_and_context(part)]
            column = cover.column if "." in cover.column else f"edge.{cover.column}"
            out.append(
                {
                    "rule": cover.rule or None,
                    "column": column.split("."),
                    "kinds": sorted(kinds) if kinds else None,
                    "by_edge": tree(by_edge[0] if len(by_edge) == 1 else And(tuple(by_edge))) if by_edge else None,
                    "rest": [tree(part) for part in parts if not CoveragePrimitive.reads_kind_and_context(part)],
                    "endpoint": cover.endpoint,
                }
            )
        return out

    @staticmethod
    def entities(spec: RuleSpec) -> dict[str, Any]:
        """What a rule over entities adds (`EntityRule`)."""
        wanted = spec.select.restricts(("kind",)) if spec.select is not None and spec.on == "symbol" else None
        return {
            "on_symbol": spec.on == "symbol",
            "wanted": sorted(wanted) if wanted is not None else None,
            "exactly_one": spec.exactly_one or None,
        }

    def export_rule(self, spec: RuleSpec) -> dict[str, Any] | None:
        """What an export rule adds: the exports it checks, their default contexts, and how it
        reports what it cannot tell (`ExportPrimitive`)."""
        if spec.primitive != "export":
            return None
        excluded = DEFAULT_EXCLUDED if spec.contexts_exclude is None else spec.contexts_exclude
        return {
            "exports": [self.export_at[name] for name in spec.exports or tuple(self.config.exports)],
            "exclude": context_bits(frozenset(excluded)),
            "unknown_types": list(spec.unknown_types),
            "on_unknown": spec.on_unknown,
            "on_unresolved": spec.on_unresolved,
        }

    def rule(self, rule: RuleType) -> dict[str, Any]:
        spec = rule.spec
        mode = rule.NATIVE_MODE
        entities = mode == "entities"
        # A reach rule's `from` is its start; a rule over entities reads its `select` alone and
        # never an edge's kinds or contexts.
        alone = spec.primitive == "reach" or entities
        selectors = (spec.select,) if alone else (spec.from_, spec.select)
        return {
            "id": spec.id,
            "mode": mode,
            "kinds": [] if entities else list(dict.fromkeys(rule_kinds(spec, self.config.views))),
            "include": 0 if entities else context_bits(frozenset(spec.contexts_include)),
            "exclude": 0 if entities else context_bits(frozenset(spec.contexts_exclude or ())),
            "endpoint": spec.endpoint,
            "selection": [tree(part) for part in conjuncts(selectors)],
            "deny": selector_tree(spec.deny),
            "allow": selector_tree(spec.allow),
            "require": selector_tree(spec.require),
            "unless": selector_tree(spec.unless),
            "default_allow": spec.primitive == "entrypoint"
            and spec.deny is None
            and spec.allow is None
            and spec.require is None,
            "identity": [column.split(".") for column in spec.identity],
            "group": CyclePrimitive.group_of(self.config, spec) if spec.primitive == "cycle" else None,
            "start": selector_tree(spec.from_) if spec.primitive == "reach" else None,
            "export": self.export_rule(spec),
            "covers": self.covers(spec),
            "entities": self.entities(spec) if entities else None,
            "inventory": isinstance(rule, EdgeInventory),
            "use_shape": {
                "allow": list(spec.allow_shapes),
                "deny": list(spec.deny_shapes),
                "flow": dict(spec.producer_options).get("flow") == "def-use",
            }
            if spec.primitive == "use-shape"
            else None,
            "signature": spec.primitive == "signature",
            "unknown_types": list(spec.unknown_types) if spec.primitive == "signature" else [],
        }

    def selectors(self) -> Iterable[Selector]:
        """Every selector the plan holds."""
        for spec in self.specs:
            for selector in (spec.from_, spec.select, spec.deny, spec.allow, spec.require, spec.unless):
                if selector is not None:
                    yield selector
            for cover in spec.covers if spec.primitive == "coverage" else ():
                if cover.selector is not None:
                    yield cover.selector
        for export in self.config.exports.values():
            if export.audience is not None:
                yield export.audience
        for field in self.config.fields.values():
            if field.domain is not None and field.domain.selector is not None:
                yield field.domain.selector
        for policy in self.config.shapes.values():
            if policy.behavior is not None and policy.behavior.where is not None:
                yield policy.behavior.where
        for level in self.config.levels.values():
            if level.select is not None:
                yield level.select

    def names_read(self) -> set[str]:
        """The last name of every field the plan reads: selectors, identity columns and a cycle
        rule's group. It only spares columns: a provider field read without one stops the run
        in `Rules.new`."""
        out = {ref.path[-1] for selector in self.selectors() for ref in refs(selector.node)}
        out.update(column.rsplit(".", 1)[-1] for spec in self.specs for column in spec.identity)
        out.update(CyclePrimitive.group_of(self.config, spec) for spec in self.specs if spec.primitive == "cycle")
        out.update(spec.exactly_one for spec in self.specs if spec.exactly_one)
        out.update(level.group_by for level in self.config.levels.values())
        out.update(
            cover.column.rsplit(".", 1)[-1]
            for spec in self.specs
            if spec.primitive == "coverage"
            for cover in spec.covers
            if cover.rule
        )
        return out

    def modules(self) -> list[str]:
        """The graph's modules in the order of every per-module column of the plan."""
        return list(self.graph.modules)

    def provided(self, modules: list[str]) -> tuple[list[dict[str, Any]], list[int]]:
        """The label column of each provider-set module field the plan reads, module by module,
        and the places of the modules whose provider labels two sources set apart."""
        labeler = self.labeler
        fields = self.config.fields
        names = [
            name
            for name in sorted(labeler.provider_fields & self.names_read())
            if name in fields and fields[name].on == "module"
        ]
        if not names:
            return [], []
        columns: dict[str, list[Any]] = {name: [] for name in names}
        conflicted = []
        for at, module in enumerate(modules):
            labels, conflict = labeler.planned_provided(module)
            if conflict:
                conflicted.append(at)
            for name in names:
                label = labels.get(name)
                columns[name].append("none" if label is None else label_value(labeler.to_value(fields[name], label)))
        return [{"field": name, "labels": columns[name]} for name in names], conflicted

    def fields(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "type": spec.type.value,
                "on_symbol": spec.on == "symbol",
                "provided": spec.name in self.labeler.provider_fields,
                "domain": None
                if spec.domain is None
                else {
                    "everywhere": spec.domain.everywhere,
                    "globs": list(spec.domain.globs),
                    "selector": selector_tree(spec.domain.selector),
                },
            }
            for spec in self.config.fields.values()
        ]

    def assigns(self) -> list[dict[str, Any]]:
        fields = self.config.fields
        return [
            {
                "match": list(assign.match),
                "except_paths": list(assign.except_paths),
                "except_values": [[name, list(values)] for name, values in assign.except_values],
                "only_values": [[name, list(values)] for name, values in assign.only_values],
                "sets": [[name, set_value(fields.get(name), value)] for name, value in assign.sets],
                "override": assign.override,
                "symbols": None
                if assign.symbols is None
                else {
                    "decorated_by": list(assign.symbols.decorated_by),
                    "subclass_of": list(assign.symbols.subclass_of),
                },
            }
            for assign in self.config.assigns
        ]

    def data(self) -> dict[str, Any]:
        config = self.config
        zones = config.zones
        modules = self.modules()
        provided, provider_conflicts = self.provided(modules)
        return {
            "module_count": len(modules),
            "modules": hashlib.sha256("\n".join(modules).encode()).hexdigest(),
            "fields": self.fields(),
            "assigns": self.assigns(),
            "zones": {
                "within": list(zones.within),
                "default": zones.default,
                "zones": [
                    {"name": zone.name, "parent": zone.parent, "paths": list(zone.paths), "override": zone.override}
                    for zone in zones.zones
                ],
            },
            "registries": [
                {"name": spec.name, "module": spec.module, "loader": spec.loader, "lifecycle": spec.lifecycle}
                for spec in config.registries.values()
            ],
            "exports": [
                {
                    "name": spec.name,
                    "within": spec.within,
                    "paths": list(spec.paths),
                    "subclass_of": list(spec.subclass_of),
                    "audience": selector_tree(spec.audience),
                    "shape": self.shape_at[spec.shape],
                }
                for spec in config.exports.values()
            ],
            "shapes": [
                {
                    "name": policy.name,
                    "data": list(policy.data),
                    "allow": list(policy.allow),
                    "deny": list(policy.deny),
                    "behavior": None
                    if policy.behavior is None
                    else {"kind": policy.behavior.kind, "where": selector_tree(policy.behavior.where)},
                    "incomplete": policy.unknown == "incomplete",
                }
                for policy in config.shapes.values()
            ],
            "kinds": [
                {
                    "name": spec.name,
                    "subclass_of": list(spec.subclass_of),
                    "decorated_by": list(spec.decorated_by),
                    "abstract_": spec.abstract,
                    "decl": spec.decl,
                }
                for spec in config.kinds.values()
            ],
            "provided": provided,
            "provider_conflicts": provider_conflicts,
            "failed": sorted(self.labeler.providers.failed_fields),
            "rules": [self.rule(rule) for rule in self.rules],
            "repeats": sorted(self.graph.repeats.items()),
        }

    def text(self) -> str:
        return json.dumps(self.data())
