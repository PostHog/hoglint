"""Validate the merged config and turn it into typed policy.

Everything is checked before any rule runs: unknown keys, unknown fields, bad enum values, typos
in selectors, rules without identity or growth key, exports without a shape policy.
"""

from __future__ import annotations

import json
import hashlib
import dataclasses
from typing import Any

from hoglint import adapters  # noqa: F401 - registers the builtin coverage adapters and providers
from hoglint.config.loader import NAMED_SECTIONS, ConfigError, MergedConfig
from hoglint.config.model import (
    Assignment,
    Behavior,
    Config,
    CoverSpec,
    Domain,
    ExportSpec,
    FieldSpec,
    KindSpec,
    LedgerSpec,
    LevelSpec,
    OutputSpec,
    ProviderSpec,
    RegistrySpec,
    Roles,
    RuleSpec,
    ShapePolicy,
    SymbolMatch,
    ZoneSpec,
    ZoneTable,
)
from hoglint.globs import CAPTURE_RE
from hoglint.model import Context, Resolution
from hoglint.plugins import REGISTRY
from hoglint.selectors import Catalog, FieldInfo, FieldType, Selector, SelectorError, suggest

CORE_EDGE_KINDS = (
    "import",
    "reference",
    "inherit",
    "decorate",
    "exposes",
    "dotted-path",
    "declares",
    "registers",
    "generates",
    "dynamic-import",
)
CORE_USES = (
    "call",
    "attribute",
    "subscript",
    "argument",
    "return",
    "annotation",
    "isinstance",
    "base",
    "decorator",
    "assign",
    "with",
    "name",
    "other",
)
CORE_SHAPES = ("frozen-dataclass", "dataclass", "abc", "enum", "protocol", "function", "constant", "class")
CORE_VIEWS: dict[str, tuple[str, ...]] = {
    "refs": (),
    "file-imports": ("import", "dotted-path"),
    "zones": ("import",),
    "packages": ("import", "reference", "dotted-path"),
}
ENDPOINTS = ("surface", "origin", "via")
ON_UNRESOLVED = ("finding", "block", "incomplete")
LIFECYCLES = ("setup", "first-request", "worker-start", "on-demand")
SELECTOR_MARKERS = ("==", "!=", " in ", "startswith", "subset_of", " and ", " or ", "not ")

NAMED_IDENTITIES: dict[str, tuple[str, ...]] = {
    "site": ("src.module", "src.scope", "edge.kind", "edge.surface", "edge.ordinal"),
    "scope-symbol": ("dst.symbol", "src.module", "src.scope", "edge.label"),
    "module-symbol": ("dst.symbol", "src.module", "edge.label"),
    "module-pair": ("src.module", "dst.module"),
    "package-pair": ("src.package", "dst.package"),
    "entity": ("subject",),
    "file": ("subject",),
}

COMMON_RULE_KEYS = frozenset(
    {"primitive", "doc", "report", "ledger", "identity", "counted", "growth_key", "remedy", "requires", "on_unresolved"}
)
EDGE_SELECT_KEYS = frozenset({"kinds", "contexts", "view", "from", "select", "endpoint", "unless"})
PRODUCER_OPTION_KEYS = {"flow": ("syntax", "def-use"), "resolve_calls": ("bare-name", "import")}
RULE_KEYS: dict[str, frozenset[str]] = {
    "edge": COMMON_RULE_KEYS | EDGE_SELECT_KEYS | {"deny", "allow", "require", *PRODUCER_OPTION_KEYS},
    "entrypoint": COMMON_RULE_KEYS | EDGE_SELECT_KEYS | {"deny", "allow", "require"},
    "use-shape": COMMON_RULE_KEYS | EDGE_SELECT_KEYS | {"allow_shapes", "deny_shapes", "flow"},
    "signature": COMMON_RULE_KEYS
    | {"kinds", "contexts", "from", "select", "deny", "unless", "on_unknown", "unknown_types"},
    "export": COMMON_RULE_KEYS | {"exports", "contexts", "on_unknown", "unknown_types"},
    "reach": COMMON_RULE_KEYS | {"kinds", "contexts", "view", "from", "select", "deny", "unless"},
    "cycle": COMMON_RULE_KEYS | {"kinds", "contexts", "view", "from", "select"},
    "layout": COMMON_RULE_KEYS | {"on", "select", "require", "within", "files", "absent", "registry"},
    "coverage": COMMON_RULE_KEYS | {"covers", "inputs"},
    "inventory": COMMON_RULE_KEYS | {"kinds", "contexts", "select", "from", "classify", "on", "exactly_one"},
}
DEFAULT_UNKNOWN_TYPES = ("typing.Any", "builtins.dict", "typing.Dict", "builtins.object")

FIELD_KEYS = frozenset({"type", "values", "single", "on", "required_on", "doc"})
ASSIGN_KEYS = frozenset({"match", "set", "override", "except", "only", "except_paths", "symbols", "doc", "__source__"})
ZONE_KEYS = frozenset({"parent", "paths", "override", "doc"})
KIND_KEYS = frozenset({"subclass_of", "decorated_by", "abstract", "decl", "expected", "doc"})
SHAPE_KEYS = frozenset({"data", "behavior", "allow", "deny", "unknown", "doc"})
EXPORT_KEYS = frozenset({"within", "paths", "audience", "shape", "subclass_of", "doc"})
REGISTRY_KEYS = frozenset({"loader", "module", "attribute", "shape", "lifecycle", "doc"})
LEVEL_KEYS = frozenset({"doc", "group_by", "select", "requires", "strict"})
LEDGER_KEYS = frozenset({"file", "exceptions"})
ROLE_KEYS = frozenset({"owner", "group"})
OUTPUT_KEYS = frozenset({"view", "format", "path", "doc"})
DISCOVERY_KEYS = frozenset(
    {"source_roots", "first_party", "exclude", "include", "scripts", "external", "tests", "migrations", "plugins"}
)
VOCABULARY_KEYS = frozenset({"edge_kinds", "use_shapes", "sections", "views"})
CONTEXTS = tuple(c.value for c in Context)
# Built-in attributes of a module or symbol, readable in entity selectors and on edge endpoints.
ENTITY_FIELDS: dict[str, FieldInfo] = {
    "module": FieldInfo(FieldType.ID),
    "symbol": FieldInfo(FieldType.ID),
    "path": FieldInfo(FieldType.STRING),
    "name": FieldInfo(FieldType.STRING),
    "decl": FieldInfo(FieldType.STRING),
    "registry": FieldInfo(FieldType.STRING),
    "lifecycle": FieldInfo(FieldType.ENUM, LIFECYCLES),
}
# Endpoint-only attributes: where the edge names it from, and what the edge says about it.
ENDPOINT_FIELDS: dict[str, FieldInfo] = {
    "scope": FieldInfo(FieldType.STRING),
    "external": FieldInfo(FieldType.STRING),
    "context": FieldInfo(FieldType.ENUM, CONTEXTS),
}
BUILTIN_NAMES = frozenset({*ENTITY_FIELDS, *ENDPOINT_FIELDS, "kind", "exported", "subject"})
CORE_SECTIONS = ("assign", "zones", "vocabulary", "ledger", "roles")


def as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


class SchemaBuilder:
    """Turns one merged table into a Config, collecting every problem before failing."""

    def __init__(self, merged: MergedConfig) -> None:
        self.merged = merged
        self.raw = merged.data
        self.problems: list[str] = []
        self.fields: dict[str, FieldSpec] = {}
        self.zone_names: tuple[str, ...] = ()
        self.kind_names: tuple[str, ...] = ()
        self.edge_kinds: tuple[str, ...] = ()
        self.use_shapes: tuple[str, ...] = ()
        self.views: dict[str, tuple[str, ...]] = dict(CORE_VIEWS)
        self.identities: dict[str, tuple[str, ...]] = dict(NAMED_IDENTITIES)

    # ── helpers ──

    def source(self, key: str) -> str:
        return self.merged.provenance.get(key, "?")

    def fail(self, key: str, message: str) -> None:
        self.problems.append(f"{key}: {message}  [{self.source(key)}]")

    def check_keys(self, key: str, table: dict[str, Any], allowed: frozenset[str]) -> None:
        for name in table:
            if name in allowed:
                continue
            self.fail(key, f"unknown key {name!r}{suggest(name, allowed)}")

    def check_enum(self, key: str, value: Any, allowed: tuple[str, ...]) -> str:
        if value in allowed:
            return str(value)
        self.fail(key, f"{value!r} is not one of {', '.join(allowed)}{suggest(str(value), allowed)}")
        return allowed[0]

    def parse(self, key: str, text: Any, catalog: Catalog) -> Selector | None:
        if text is None:
            return None
        if not isinstance(text, str):
            self.fail(key, "a selector must be a string")
            return None
        try:
            return Selector.parse(text, catalog)
        except SelectorError as error:
            self.fail(key, str(error))
            return None

    # ── catalogs ──

    def field_infos(self) -> dict[str, FieldInfo]:
        infos: dict[str, FieldInfo] = {}
        for spec in self.fields.values():
            values = self.zone_names if spec.type is FieldType.ZONE else spec.values
            infos[spec.name] = FieldInfo(spec.type, values)
        return infos

    def entity_fields(self) -> dict[str, FieldInfo]:
        return {**self.field_infos(), **ENTITY_FIELDS, "kind": FieldInfo(FieldType.ENUM, self.kind_names)}

    def endpoint_fields(self, dst: bool) -> dict[str, FieldInfo]:
        fields = {**self.entity_fields(), **ENDPOINT_FIELDS}
        if dst:
            fields["exported"] = FieldInfo(FieldType.BOOL)
        return fields

    def edge_fields(self) -> dict[str, FieldInfo]:
        return {
            "kind": FieldInfo(FieldType.ENUM, self.edge_kinds),
            "shape": FieldInfo(FieldType.STRING),
            "use": FieldInfo(FieldType.STRING),
            "label": FieldInfo(FieldType.STRING),
            "context": FieldInfo(FieldType.ENUM, CONTEXTS),
            "resolution": FieldInfo(FieldType.ENUM, tuple(r.value for r in Resolution)),
            "surface": FieldInfo(FieldType.STRING),
            "ordinal": FieldInfo(FieldType.INT),
        }

    def edge_catalog(self, roots: tuple[str, ...] = ("src", "dst", "edge")) -> Catalog:
        all_roots = {
            "src": self.endpoint_fields(dst=False),
            "dst": self.endpoint_fields(dst=True),
            "edge": self.edge_fields(),
        }
        return Catalog({root: all_roots[root] for root in roots}, open_roots=frozenset({"edge"}) & set(roots))

    def module_catalog(self) -> Catalog:
        """`src.*` of a module alone: reach's `from` sees no edge, so no `edge.*` and no context."""
        fields = self.endpoint_fields(dst=False)
        del fields["context"]
        return Catalog({"src": fields})

    def entity_catalog(self) -> Catalog:
        return Catalog({"": {**self.entity_fields(), "subject": FieldInfo(FieldType.ID)}})

    # ── sections ──

    def build(self) -> Config:
        self.check_top_level()
        self.read_vocabulary()
        zones_raw = self.raw.get("zones", {})
        self.zone_names = self.read_zone_names(zones_raw)
        self.kind_names = tuple(self.raw.get("kinds", {}))
        self.fields = self.read_fields()
        zones = self.read_zones(zones_raw)
        kinds = self.read_kinds()
        self.read_identities()
        shapes = self.read_shapes()
        exports = self.read_exports(shapes)
        registries = self.read_registries()
        providers = self.read_providers()
        assigns = self.read_assigns()
        rules = self.read_rules(registries, exports)
        levels = self.read_levels(rules)
        ledger = self.read_ledger()
        roles = self.read_roles(rules)
        outputs = self.read_outputs()
        self.read_discovery()
        pack_sections = self.read_pack_sections()
        if self.problems:
            raise ConfigError(self.problems)
        sections = {name: value for name, value in self.raw.items() if name not in (*NAMED_SECTIONS, *CORE_SECTIONS)}
        return Config(
            fields=self.fields,
            assigns=assigns,
            zones=zones,
            kinds=kinds,
            shapes=shapes,
            exports=exports,
            registries=registries,
            providers=providers,
            rules=rules,
            levels=levels,
            ledger=ledger,
            outputs=outputs,
            identities=self.identities,
            roles=roles,
            views=self.views,
            edge_kinds=self.edge_kinds,
            use_shapes=self.use_shapes,
            sections=sections,
            pack_sections=pack_sections,
            policy_hash=policy_hash(self.raw),
            provenance=dict(self.merged.provenance),
            expanded=self.raw,
        )

    def check_top_level(self) -> None:
        pack_sections = tuple(self.raw.get("vocabulary", {}).get("sections", ()))
        allowed = frozenset({*NAMED_SECTIONS, *CORE_SECTIONS, "discovery", *pack_sections})
        for name in self.raw:
            if name not in allowed:
                self.problems.append(f"{name}: unknown section{suggest(name, allowed)}  [{self.source(name)}]")

    def read_vocabulary(self) -> None:
        vocabulary = self.raw.get("vocabulary", {})
        self.check_keys("vocabulary", vocabulary, VOCABULARY_KEYS)
        self.edge_kinds = (*CORE_EDGE_KINDS, *(k for k in vocabulary.get("edge_kinds", ()) if k not in CORE_EDGE_KINDS))
        self.use_shapes = (*CORE_USES, *(k for k in vocabulary.get("use_shapes", ()) if k not in CORE_USES))
        views = vocabulary.get("views", {})
        if not isinstance(views, dict):
            self.fail("vocabulary.views", "must be a table of view name -> edge kinds")
            views = {}
        for name, kinds in views.items():
            for kind in kinds:
                if kind not in self.edge_kinds:
                    self.fail(f"vocabulary.views.{name}", f"unknown edge kind {kind!r}{suggest(kind, self.edge_kinds)}")
            current = self.views.get(name, ())
            self.views[name] = (*current, *(kind for kind in kinds if kind not in current))

    def read_zone_names(self, zones_raw: dict[str, Any]) -> tuple[str, ...]:
        names = [name for name in zones_raw if name not in ("within", "default")]
        default = zones_raw.get("default", "internal")
        if default not in names:
            names.append(str(default))
        return tuple(names)

    def read_domain(self, key: str, value: Any) -> Domain | None:
        if value is None:
            return None
        if isinstance(value, list):
            return Domain(globs=as_tuple(value))
        if not isinstance(value, str):
            self.fail(key, "required_on must be a glob, a list of globs or a selector")
            return None
        if value.strip() in ("*", "**"):
            return Domain(everywhere=True)
        if any(marker in f" {value} " for marker in SELECTOR_MARKERS):
            return Domain(selector=self.parse(key, value, self.entity_catalog()))
        return Domain(globs=tuple(part.strip() for part in value.split("|") if part.strip()))

    def read_fields(self) -> dict[str, FieldSpec]:
        out: dict[str, FieldSpec] = {}
        for name, table in self.raw.get("fields", {}).items():
            key = f"fields.{name}"
            self.check_keys(key, table, FIELD_KEYS)
            if name in BUILTIN_NAMES:
                self.fail(key, f"{name!r} is a built-in attribute and cannot be declared as a field")
                continue
            type_name = self.check_enum(f"{key}.type", table.get("type"), tuple(t.value for t in FieldType))
            field_type = FieldType(type_name)
            values = as_tuple(table.get("values"))
            if field_type in (FieldType.ENUM, FieldType.ORDERED) and not values:
                self.fail(key, f"a {type_name} field needs `values`")
            on = self.check_enum(f"{key}.on", table.get("on", "module"), ("module", "symbol"))
            out[name] = FieldSpec(name, field_type, values, on, None, self.source(key))
        self.fields = out
        for name, table in self.raw.get("fields", {}).items():
            if name not in out:
                continue
            domain = self.read_domain(f"fields.{name}.required_on", table.get("required_on"))
            out[name] = dataclasses.replace(out[name], domain=domain)
        return out

    def read_zones(self, zones_raw: dict[str, Any]) -> ZoneTable:
        specs: list[ZoneSpec] = []
        for name in self.zone_names:
            table = zones_raw.get(name, {})
            key = f"zones.{name}"
            self.check_keys(key, table, ZONE_KEYS)
            parent = str(table.get("parent", ""))
            if parent and parent not in self.zone_names:
                self.fail(key, f"parent {parent!r} is not a zone{suggest(parent, self.zone_names)}")
            specs.append(
                ZoneSpec(
                    name, parent, as_tuple(table.get("paths")), bool(table.get("override", False)), self.source(key)
                )
            )
        table = ZoneTable(as_tuple(zones_raw.get("within")), str(zones_raw.get("default", "internal")), tuple(specs))
        for spec in specs:
            if len(table.chain(spec.name)) > len(self.zone_names):
                self.fail(f"zones.{spec.name}", "zone parents form a cycle")
        return table

    def read_kinds(self) -> dict[str, KindSpec]:
        out: dict[str, KindSpec] = {}
        for name, table in self.raw.get("kinds", {}).items():
            key = f"kinds.{name}"
            self.check_keys(key, table, KIND_KEYS)
            abstract = table.get("abstract")
            out[name] = KindSpec(
                name,
                as_tuple(table.get("subclass_of")),
                as_tuple(table.get("decorated_by")),
                abstract if isinstance(abstract, bool) else None,
                str(table.get("decl", "")),
                self.source(key),
                bool(table.get("expected", False)),
            )
            if not (out[name].subclass_of or out[name].decorated_by or out[name].decl):
                self.fail(key, "a kind needs subclass_of, decorated_by or decl")
        return out

    def read_identities(self) -> None:
        for name, table in self.raw.get("identities", {}).items():
            columns = as_tuple(table.get("columns"))
            if not columns:
                self.fail(f"identities.{name}", "an identity needs a list of columns")
                continue
            self.identities[name] = columns

    def shape_names(self) -> tuple[str, ...]:
        return (*CORE_SHAPES, *self.kind_names)

    def read_shapes(self) -> dict[str, ShapePolicy]:
        out: dict[str, ShapePolicy] = {}
        known = self.shape_names()
        for name, table in self.raw.get("shapes", {}).items():
            key = f"shapes.{name}"
            self.check_keys(key, table, SHAPE_KEYS)
            behavior = None
            raw_behavior = table.get("behavior")
            if isinstance(raw_behavior, dict):
                self.check_keys(f"{key}.behavior", raw_behavior, frozenset({"kind", "where"}))
                behavior = Behavior(
                    self.check_enum(f"{key}.behavior.kind", raw_behavior.get("kind"), known),
                    self.parse(f"{key}.behavior.where", raw_behavior.get("where"), self.entity_catalog()),
                )
            policy = ShapePolicy(
                name,
                as_tuple(table.get("data")),
                behavior,
                as_tuple(table.get("allow")),
                as_tuple(table.get("deny")),
                self.check_enum(f"{key}.unknown", table.get("unknown", "error"), ("error", "incomplete")),
                self.source(key),
            )
            for shape in (*policy.data, *policy.allow, *policy.deny):
                if shape not in known:
                    self.fail(key, f"unknown shape {shape!r}{suggest(shape, known)}")
            out[name] = policy
        return out

    def read_exports(self, shapes: dict[str, ShapePolicy]) -> dict[str, ExportSpec]:
        out: dict[str, ExportSpec] = {}
        for name, table in self.raw.get("exports", {}).items():
            key = f"exports.{name}"
            self.check_keys(key, table, EXPORT_KEYS)
            shape = table.get("shape")
            if not shape:
                self.fail(key, "an export needs a shape policy (`shape = ...`)")
            elif shape not in shapes:
                self.fail(key, f"shape policy {shape!r} is not defined under [shapes]{suggest(str(shape), shapes)}")
            audience_text = table.get("audience", "*")
            audience = (
                None
                if audience_text == "*"
                else self.parse(f"{key}.audience", audience_text, self.edge_catalog(("src",)))
            )
            paths = as_tuple(table.get("paths"))
            if not paths:
                self.fail(key, "an export needs `paths`")
            out[name] = ExportSpec(
                name,
                str(table.get("within", "")),
                paths,
                str(shape or ""),
                audience,
                as_tuple(table.get("subclass_of")),
                self.source(key),
            )
        return out

    def read_registries(self) -> dict[str, RegistrySpec]:
        out: dict[str, RegistrySpec] = {}
        for name, table in self.raw.get("registries", {}).items():
            key = f"registries.{name}"
            self.check_keys(key, table, REGISTRY_KEYS)
            for required in ("loader", "module", "attribute"):
                if not table.get(required):
                    self.fail(key, f"a registry needs `{required}`")
            lifecycle = table.get("lifecycle", "")
            if lifecycle:
                self.check_enum(f"{key}.lifecycle", lifecycle, LIFECYCLES)
            shape = str(table.get("shape", ""))
            if shape and not shape.startswith("abc:"):
                self.fail(key, "registry shape must be `abc:<dotted base class>`")
            out[name] = RegistrySpec(
                name,
                str(table.get("loader", "")),
                str(table.get("module", "")),
                str(table.get("attribute", "")),
                shape,
                str(lifecycle),
                self.source(key),
            )
        return out

    def read_providers(self) -> dict[str, ProviderSpec]:
        out: dict[str, ProviderSpec] = {}
        for name, table in self.raw.get("providers", {}).items():
            key = f"providers.{name}"
            use = table.get("use")
            provider = REGISTRY.providers.get(str(use))
            if provider is None:
                known = sorted(REGISTRY.providers)
                self.fail(key, f"unknown provider {use!r}; one of {', '.join(known)}{suggest(str(use), known)}")
                continue
            self.check_keys(key, table, provider.allowed_options | {"use", "sets", "doc"})
            bindings = self.read_bindings(key, table.get("sets"), tuple(getattr(provider, "outputs", ())))
            for _, field_name in bindings:
                if field_name not in self.fields:
                    self.fail(key, f"sets undeclared field {field_name!r}{suggest(field_name, self.fields)}")
            options = tuple((k, v) for k, v in table.items() if k not in ("use", "sets", "doc"))
            sets = tuple(field_name for _, field_name in bindings)
            out[name] = ProviderSpec(name, str(use), sets, options, self.source(key), bindings)
        return out

    def read_bindings(self, key: str, raw: Any, outputs: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
        """(output, field) pairs. A list names fields: a single-output provider fills each, otherwise
        each field takes the output of its own name. A table maps outputs to fields."""
        if isinstance(raw, dict):
            for output in raw:
                if outputs and output not in outputs:
                    self.fail(key, f"the provider has no output {output!r}; it has {', '.join(outputs)}")
            return tuple((str(output), str(field_name)) for output, field_name in raw.items())
        fields = as_tuple(raw)
        if len(outputs) == 1:
            return tuple((outputs[0], field_name) for field_name in fields)
        for field_name in fields:
            if outputs and field_name not in outputs:
                self.fail(
                    key,
                    f'the provider has no output {field_name!r}; map one with `sets = {{ output = "{field_name}" }}`',
                )
        return tuple((field_name, field_name) for field_name in fields)

    def read_assigns(self) -> tuple[Assignment, ...]:
        out: list[Assignment] = []
        for index, table in enumerate(self.raw.get("assign", [])):
            key = f"assign[{index}]"
            self.check_keys(key, table, ASSIGN_KEYS)
            match = as_tuple(table.get("match"))
            symbols = None
            raw_symbols = table.get("symbols")
            if isinstance(raw_symbols, dict):
                self.check_keys(f"{key}.symbols", raw_symbols, frozenset({"decorated_by", "subclass_of"}))
                symbols = SymbolMatch(
                    as_tuple(raw_symbols.get("decorated_by")), as_tuple(raw_symbols.get("subclass_of"))
                )
            if not match and symbols is None:
                self.fail(key, "an assignment needs `match` or `symbols`")
            captures = {name for pattern in match for name in CAPTURE_RE.findall(pattern)}
            sets = table.get("set", {})
            if not isinstance(sets, dict) or not sets:
                self.fail(key, "an assignment needs a `set` table")
                sets = {}
            for field_name, value in sets.items():
                self.check_assigned_value(key, field_name, value, captures, symbols is not None)
            filters = []
            for filter_key in ("except", "only"):
                raw_filter = table.get(filter_key, {})
                for capture in raw_filter:
                    if capture not in captures:
                        self.fail(key, f"`{filter_key}` names {capture!r}, which no match pattern captures")
                filters.append(tuple((capture, as_tuple(values)) for capture, values in raw_filter.items()))
            out.append(
                Assignment(
                    index,
                    match,
                    tuple(sets.items()),
                    bool(table.get("override", False)),
                    filters[0],
                    filters[1],
                    as_tuple(table.get("except_paths")),
                    symbols,
                    str(table.get("__source__", "")),
                )
            )
        return tuple(out)

    def check_assigned_value(
        self, key: str, field_name: str, value: Any, captures: set[str], symbol_level: bool
    ) -> None:
        spec = self.fields.get(field_name)
        if spec is None:
            self.fail(key, f"sets undeclared field {field_name!r}{suggest(field_name, self.fields)}")
            return
        if symbol_level and spec.on != "symbol":
            self.fail(
                key, f'field {field_name!r} lives on modules; a symbols assignment needs a field with `on = "symbol"`'
            )
        if isinstance(value, str):
            for capture in CAPTURE_RE.findall(value):
                if capture not in captures:
                    self.fail(key, f"`{{{capture}}}` is not captured by any match pattern")
            if CAPTURE_RE.search(value):
                return
        allowed = self.zone_names if spec.type is FieldType.ZONE else spec.values
        if spec.type in (FieldType.ENUM, FieldType.ORDERED, FieldType.ZONE) and value not in allowed:
            self.fail(key, f"{value!r} is not a value of field {field_name!r}{suggest(str(value), allowed)}")
        if spec.type is FieldType.BOOL and not isinstance(value, bool):
            self.fail(key, f"field {field_name!r} is a bool")

    def read_rule_identity(self, key: str, table: dict[str, Any], entity_rule: bool) -> tuple[str, tuple[str, ...]]:
        raw = table.get("identity")
        if raw is None:
            return "", ()
        if isinstance(raw, str):
            if raw not in self.identities:
                self.fail(key, f"unknown identity {raw!r}{suggest(raw, self.identities)}")
                return raw, ()
            columns = self.identities[raw]
            name = raw
        else:
            columns = as_tuple(raw)
            name = ""
        catalog = self.entity_catalog() if entity_rule else self.edge_catalog()
        for column in columns:
            try:
                Selector.parse(column, catalog)
            except SelectorError:
                self.fail(key, f"identity column {column!r} names nothing this rule can read")
        return name, columns

    def read_contexts(self, key: str, raw: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
        if raw is None:
            return (), ()
        if not isinstance(raw, dict):
            self.fail(key, "contexts must be `{ include = [...], exclude = [...] }`")
            return (), ()
        self.check_keys(key, raw, frozenset({"include", "exclude"}))
        known = CONTEXTS
        out = []
        for part in ("include", "exclude"):
            values = as_tuple(raw.get(part))
            for value in values:
                if value not in known:
                    self.fail(key, f"unknown context {value!r}{suggest(value, known)}")
            out.append(values)
        return out[0], out[1]

    def read_covers(self, key: str, raw: Any, rule_ids: set[str]) -> tuple[CoverSpec, ...]:
        out: list[CoverSpec] = []
        for text in as_tuple(raw):
            if text.startswith("rules."):
                parts = text.split(".", 2)
                if len(parts) != 3 or parts[1] not in rule_ids:
                    self.fail(key, f"covers {text!r}: expected rules.<rule id>.<column>, with a defined rule")
                    continue
                out.append(CoverSpec(text, rule=parts[1], column=parts[2]))
                continue
            selector_text, _, endpoint = text.rpartition(":")
            endpoint = endpoint.strip()
            if not selector_text or endpoint not in ("src", "dst"):
                self.fail(key, f"covers {text!r}: expected `<selector>: src` or `<selector>: dst`")
                continue
            selector = self.parse(key, selector_text, self.edge_catalog())
            out.append(CoverSpec(text, selector=selector, endpoint=endpoint))
        return tuple(out)

    def read_inputs(self, key: str, inputs: Any) -> dict[str, Any]:
        if not isinstance(inputs, dict):
            self.fail(key, "inputs must be a table with an `adapter`")
            return {}
        adapter = REGISTRY.coverage_adapters.get(str(inputs.get("adapter")))
        if adapter is None:
            known = sorted(REGISTRY.coverage_adapters)
            self.fail(key, f"unknown coverage adapter {inputs.get('adapter')!r}; one of {', '.join(known)}")
            return inputs
        self.check_keys(key, inputs, adapter.allowed_options | {"adapter"})
        return inputs

    def read_rules(self, registries: dict[str, RegistrySpec], exports: dict[str, ExportSpec]) -> dict[str, RuleSpec]:
        out: dict[str, RuleSpec] = {}
        rule_ids = set(self.raw.get("rules", {}))
        for rule_id, table in self.raw.get("rules", {}).items():
            spec = self.read_rule(rule_id, table, rule_ids, registries, exports)
            if spec is not None:
                out[rule_id] = spec
        return out

    def read_rule(
        self,
        rule_id: str,
        table: dict[str, Any],
        rule_ids: set[str],
        registries: dict[str, RegistrySpec],
        exports: dict[str, ExportSpec],
    ) -> RuleSpec | None:
        key = f"rules.{rule_id}"
        primitive = table.get("primitive")
        if primitive == "level":
            self.fail(key, "levels are declared under [levels.<name>], not as rules")
            return None
        if primitive not in RULE_KEYS:
            self.fail(key, f"unknown primitive {primitive!r}{suggest(str(primitive), RULE_KEYS)}")
            return None
        self.check_keys(key, table, RULE_KEYS[primitive])
        entity_rule = primitive in ("layout", "coverage") or (primitive == "inventory" and table.get("on") is not None)
        edge_catalog = self.edge_catalog()
        entity_catalog = self.entity_catalog()
        predicate_catalog = entity_catalog if entity_rule else edge_catalog
        from_catalog = self.module_catalog() if primitive == "reach" else self.edge_catalog(("src", "edge"))

        kinds = as_tuple(table.get("kinds"))
        for kind in kinds:
            if kind not in self.edge_kinds:
                self.fail(key, f"unknown edge kind {kind!r}{suggest(kind, self.edge_kinds)}")
        include, exclude = self.read_contexts(f"{key}.contexts", table.get("contexts"))
        on_unresolved = table.get("on_unresolved", "finding")
        if on_unresolved == "ignore":
            self.fail(key, "on_unresolved has no `ignore`: unknown is never clean (finding, block or incomplete)")
        on_unresolved = self.check_enum(f"{key}.on_unresolved", on_unresolved, ON_UNRESOLVED)
        identity_name, identity = self.read_rule_identity(key, table, entity_rule)
        ledgered = bool(table.get("ledger", True))
        growth_key = as_tuple(table.get("growth_key"))
        if ledgered and not identity:
            self.fail(key, "a ledgered rule needs `identity` (or `ledger = false`)")
        if ledgered and not growth_key:
            self.fail(key, "a ledgered rule needs `growth_key`: the identity columns the growth check sums over")
        for column in growth_key:
            if identity and column not in identity:
                self.fail(key, f"growth_key column {column!r} is not an identity column ({', '.join(identity)})")
        for shape in (*as_tuple(table.get("allow_shapes")), *as_tuple(table.get("deny_shapes"))):
            base = shape.split("(", 1)[0]
            if base not in self.use_shapes:
                self.fail(key, f"unknown use shape {shape!r}{suggest(base, self.use_shapes)}")
        registry = as_tuple(table.get("registry"))
        for name in registry:
            if name not in registries:
                self.fail(key, f"unknown registry {name!r}{suggest(name, registries)}")
        for export in as_tuple(table.get("exports")):
            if export not in exports:
                self.fail(key, f"unknown export {export!r}{suggest(export, exports)}")
        producer_options = []
        for option, allowed in PRODUCER_OPTION_KEYS.items():
            if option in table:
                producer_options.append((option, self.check_enum(f"{key}.{option}", table[option], allowed)))
        inputs = table.get("inputs", {})
        if primitive == "coverage":
            inputs = self.read_inputs(f"{key}.inputs", inputs)
        if primitive == "inventory" and not (table.get("classify") or table.get("exactly_one")):
            self.fail(key, "an inventory needs `classify` (edges) or `exactly_one` (entities)")
        return RuleSpec(
            id=rule_id,
            primitive=primitive,
            doc=str(table.get("doc", "")),
            kinds=kinds,
            contexts_include=include,
            contexts_exclude=exclude,
            view=self.check_enum(f"{key}.view", table.get("view", "refs"), tuple(self.views)),
            endpoint=self.check_enum(
                f"{key}.endpoint",
                table.get("endpoint", "surface" if primitive == "entrypoint" else "origin"),
                ENDPOINTS,
            ),
            from_=self.parse(f"{key}.from", table.get("from"), from_catalog),
            select=self.parse(f"{key}.select", table.get("select"), predicate_catalog),
            deny=self.parse(f"{key}.deny", table.get("deny"), predicate_catalog),
            allow=self.parse(f"{key}.allow", table.get("allow"), predicate_catalog),
            require=self.parse(f"{key}.require", table.get("require"), predicate_catalog),
            unless=self.parse(f"{key}.unless", table.get("unless"), predicate_catalog),
            on_unresolved=on_unresolved,
            on_unknown=self.check_enum(f"{key}.on_unknown", table.get("on_unknown", "finding"), ON_UNRESOLVED),
            identity_name=identity_name,
            identity=identity,
            counted=bool(table.get("counted", False)),
            growth_key=growth_key,
            ledger=ledgered,
            report=self.check_enum(f"{key}.report", table.get("report", "blocking"), ("blocking", "advisory")),
            remedy=str(table.get("remedy", "")),
            allow_shapes=as_tuple(table.get("allow_shapes")),
            deny_shapes=as_tuple(table.get("deny_shapes")),
            requires=as_tuple(table.get("requires")),
            classify=str(table.get("classify", "")),
            exactly_one=str(table.get("exactly_one", "")),
            covers=self.read_covers(f"{key}.covers", table.get("covers"), rule_ids),
            inputs=tuple(inputs.items()) if isinstance(inputs, dict) else (),
            on=self.check_enum(f"{key}.on", table.get("on", "module"), ("module", "symbol")),
            within=str(table.get("within", "")),
            files=as_tuple(table.get("files")),
            absent=as_tuple(table.get("absent")),
            registry=registry,
            exports=as_tuple(table.get("exports")),
            unknown_types=as_tuple(table.get("unknown_types")) or DEFAULT_UNKNOWN_TYPES,
            producer_options=tuple(producer_options),
            source=self.source(key),
        )

    def read_levels(self, rules: dict[str, RuleSpec]) -> dict[str, LevelSpec]:
        out: dict[str, LevelSpec] = {}
        for name, table in self.raw.get("levels", {}).items():
            key = f"levels.{name}"
            self.check_keys(key, table, LEVEL_KEYS)
            group_by = str(table.get("group_by", ""))
            if group_by not in self.fields:
                self.fail(key, f"group_by {group_by!r} is not a declared field{suggest(group_by, self.fields)}")
            requires = as_tuple(table.get("requires"))
            if not requires:
                self.fail(key, "a level needs an explicit `requires` list")
            for rule_id in requires:
                if rule_id not in rules and rule_id not in self.raw.get("rules", {}):
                    self.fail(key, f"requires unknown rule {rule_id!r}{suggest(rule_id, rules)}")
            strict = as_tuple(table.get("strict"))
            for rule_id in strict:
                if rule_id not in requires:
                    self.fail(key, f"strict rule {rule_id!r} must also be in `requires`")
            out[name] = LevelSpec(
                name,
                group_by,
                requires,
                str(table.get("doc", "")),
                self.parse(f"{key}.select", table.get("select"), self.entity_catalog()),
                strict,
                self.source(key),
            )
        return out

    def read_ledger(self) -> LedgerSpec:
        table = self.raw.get("ledger", {})
        self.check_keys("ledger", table, LEDGER_KEYS)
        return LedgerSpec(
            str(table.get("file", "hoglint.ledger")), str(table.get("exceptions", "hoglint.exceptions.toml"))
        )

    def read_roles(self, rules: dict[str, RuleSpec]) -> Roles:
        table = self.raw.get("roles", {})
        self.check_keys("roles", table, ROLE_KEYS)
        for role in ROLE_KEYS:
            name = table.get(role)
            if name is not None and name not in self.fields:
                self.fail(f"roles.{role}", f"{name!r} is not a declared field{suggest(str(name), self.fields)}")
        roles = Roles(str(table.get("owner", "")), str(table.get("group", "")))
        for rule in rules.values():
            if rule.primitive == "cycle" and rule.view == "packages" and not roles.group:
                self.fail(f"rules.{rule.id}", "a cycle over the packages view needs `[roles] group = <field>`")
        return roles

    def read_outputs(self) -> dict[str, OutputSpec]:
        out: dict[str, OutputSpec] = {}
        for name, table in self.raw.get("outputs", {}).items():
            key = f"outputs.{name}"
            self.check_keys(key, table, OUTPUT_KEYS)
            out[name] = OutputSpec(
                name,
                self.check_enum(f"{key}.view", table.get("view", "file-imports"), tuple(self.views)),
                self.check_enum(f"{key}.format", table.get("format", "tach-map"), ("tach-map", "edges-json")),
                str(table.get("path", f".hoglint/{name}.json")),
            )
        return out

    def read_pack_sections(self) -> tuple[str, ...]:
        """Each pack section is checked against the keys its registered pack allows."""
        names = as_tuple(self.raw.get("vocabulary", {}).get("sections"))
        for name in names:
            table = self.raw.get(name)
            if table is None:
                continue
            pack = REGISTRY.packs.get(name)
            if pack is None:
                self.fail(
                    name, f"no pack registered for [{name}]; extend the pack or name its module in [discovery] plugins"
                )
                continue
            if not isinstance(table, dict):
                self.fail(name, f"[{name}] must be a table")
                continue
            self.check_keys(name, table, pack.allowed_keys)
        return names

    def read_discovery(self) -> None:
        self.check_keys("discovery", self.raw.get("discovery", {}), DISCOVERY_KEYS)


def without_sources(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: without_sources(v) for k, v in sorted(value.items()) if k != "__source__"}
    if isinstance(value, list):
        return [without_sources(v) for v in value]
    return value


def canonical(value: Any) -> str:
    """Stable JSON of a raw config value, without the loader's `__source__` notes."""
    return json.dumps(without_sources(value), sort_keys=True, default=str)


def policy_hash(raw: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(raw).encode()).hexdigest()[:16]


def build_config(merged: MergedConfig) -> Config:
    return SchemaBuilder(merged).build()
