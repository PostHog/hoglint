"""The plan the native evaluator runs (src/link/rules/plan.rs): the config of one run as data.

Python loads and validates the config; the plan hands Rust its fields and domains, assignments,
zones, registries, exports with their audiences, kinds, and each rule as its class plans itself.
Nothing in it is computed per module or per symbol, except the label column of a field a Python
provider sets, and only when the evaluator says something the plan holds reads that field (D54).
"""

from __future__ import annotations

import json
import hashlib
from collections.abc import Callable
from typing import Any

from hoglint.config.model import Config, FieldSpec
from hoglint.labels import Labeler
from hoglint.native.graph import NativeGraph
from hoglint.rules.base import RuleType
from hoglint.rules.plan import selector_tree
from hoglint.selectors import FieldType, State, Value


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
        self.shape_at = {name: at for at, name in enumerate(config.shapes)}

    def modules(self) -> list[str]:
        """The graph's modules in the order of every per-module column of the plan."""
        return list(self.graph.modules)

    def provided(self, names: list[str]) -> tuple[list[dict[str, Any]], list[int]]:
        """The label column of each provider-set module field `names` lists, module by module,
        and the places of the modules whose provider labels two sources set apart."""
        labeler = self.labeler
        fields = self.config.fields
        modules = self.modules()
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
                    "names": list(assign.symbols.names),
                },
            }
            for assign in self.config.assigns
        ]

    def data(self) -> dict[str, Any]:
        """The plan with no provider column: `text` adds the ones the evaluator reads."""
        config = self.config
        zones = config.zones
        modules = self.modules()
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
                    "symbols": list(spec.symbols),
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
                    "signatures": policy.signatures,
                    "allow_where": selector_tree(policy.allow_where),
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
            "provided": [],
            "provider_conflicts": [],
            "failed": sorted(self.labeler.providers.failed_fields),
            "rules": [rule.plan() for rule in self.rules],
            "levels": [
                {"group_by": level.group_by, "select": selector_tree(level.select)} for level in config.levels.values()
            ],
            "repeats": sorted(self.graph.repeats.items()),
        }

    def text(self, reads: Callable[[str], list[str]]) -> str:
        """The plan as JSON, with the column of every provider-set field the evaluator says its
        compiled plan reads (`reads`, D54)."""
        data = self.data()
        data["provided"], data["provider_conflicts"] = self.provided(reads(json.dumps(data)))
        return json.dumps(data)
