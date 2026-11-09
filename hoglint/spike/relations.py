"""Channel A: static model index and Relation records from L1 facts (port of spike A static_index.py).

Reads only fact records through l2.core. Outputs: Relation records, concrete models,
reverse-accessor edge lines and their crossings-ledger form.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast

from hoglint.spike.core import (
    CLASS,
    NO_CONST,
    SITE_MARKER,
    ClassRef,
    Core,
    ModuleRef,
    const_of,
    is_const,
    scope_ancestry,
)

FIRST_PARTY_ROOTS = ("posthog", "ee", "products", "common")
FIRST_PARTY = tuple(f"{r}." for r in FIRST_PARTY_ROOTS)
SETTINGS_MODULE = "posthog.settings.web"
SWAPPABLE_SETTINGS = {"oauth2_settings": "OAUTH2_PROVIDER_"}

MODEL_BASE = "django.db.models.base.Model"
APP_CONFIG = "django.apps.config.AppConfig"
FK = "django.db.models.fields.related.ForeignKey"
O2O = "django.db.models.fields.related.OneToOneField"
M2M = "django.db.models.fields.related.ManyToManyField"
GENERIC = "django.contrib.contenttypes.fields.GenericRelation"


def render(value: object) -> str:
    """Display text for a V value (stands in for ast.unparse in unresolved targets)."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and "s" in value:
        return repr(value["s"])
    return json.dumps(value, sort_keys=True)


class ClassBody:
    """Direct body facts of one class def: assigns, nested defs, Meta."""

    def __init__(self, record: dict, idx: int) -> None:
        defs = record["defs"]
        node = defs[idx]
        body_flags = node.get("f", 0) | CLASS
        self.assigns = [a for a in record.get("assigns", ()) if a["s"] == idx and a.get("f", 0) == body_flags]
        self.children = [i for i, d in enumerate(defs) if d["s"] == idx and d.get("f", 0) == body_flags]
        self.body_names: set[str] = set()
        self.meta_idx: int | None = None
        for row in self.assigns:
            if row["op"] == "=":
                self.body_names.update(t for t in row["t"] if isinstance(t, str) and "." not in t)
            elif row["op"] == ":" and isinstance(row["t"][0], str) and "." not in row["t"][0]:
                self.body_names.add(row["t"][0])
        for i in self.children:
            d = defs[i]
            if d["k"] == "class" and d["n"] == "Meta":
                self.meta_idx = i
            else:
                self.body_names.add(d["n"])

    def constant_assigns(self) -> dict[str, object]:
        """`name = <Constant>` statements, as spike A reads Meta and AppConfig bodies."""
        out: dict[str, object] = {}
        for row in self.assigns:
            if row["op"] != "=" or "v" not in row or not is_const(row["v"]):
                continue
            value = const_of(row["v"])
            if value is NO_CONST:
                value = render(row["v"])
            for target in row["t"]:
                if isinstance(target, str) and "." not in target:
                    out[target] = value
        return out


class ModelIndex:
    def __init__(self, core: Core) -> None:
        self.core = core
        self.resolver = core.resolver
        self.classes = core.classes
        self.body_cache: dict[tuple[str, int], ClassBody] = {}

    def body(self, module: str, idx: int) -> ClassBody:
        key = (module, idx)
        if key not in self.body_cache:
            self.body_cache[key] = ClassBody(self.core.index.record(module), idx)
        return self.body_cache[key]

    def info(self, ref: ClassRef) -> ClassBody:
        return self.body(ref.module, self.classes.def_index(ref))

    def mro(self, ref: ClassRef) -> list[ClassRef]:
        return self.classes.mro(ref)

    def bases(self, ref: ClassRef) -> list[ClassRef]:
        return self.classes.bases(ref)

    def is_subclass(self, ref: ClassRef, target_id: str) -> bool:
        return self.classes.is_subclass(ref, target_id)

    def meta_attrs(self, module: str, meta_idx: int, depth: int = 0) -> dict[str, object]:
        attrs: dict[str, object] = {}
        record = self.core.index.record(module)
        for base in record["defs"][meta_idx].get("bases", ()):
            parent_value = None
            if isinstance(base, str) and base.endswith(".Meta"):
                parent_value = base[: -len(".Meta")]
            elif isinstance(base, dict) and base.get("a") == "Meta":
                parent_value = base["v"]
            if parent_value is None or depth >= 5:
                continue
            parent = self.resolver.resolve_value(module, parent_value)
            if isinstance(parent, ClassRef):
                parent_meta = self.info(parent).meta_idx
                if parent_meta is not None:
                    inherited = self.meta_attrs(parent.module, parent_meta, depth + 1)
                    inherited.pop("abstract", None)
                    attrs.update(inherited)
        attrs.update(self.body(module, meta_idx).constant_assigns())
        return attrs

    def meta(self, ref: ClassRef) -> dict[str, object]:
        info = self.info(ref)
        if info.meta_idx is not None:
            return self.meta_attrs(ref.module, info.meta_idx)
        for base in self.mro(ref)[1:]:
            base_info = self.info(base)
            if base_info.meta_idx is not None:
                attrs = self.meta_attrs(base.module, base_info.meta_idx)
                attrs["abstract"] = False
                return attrs
        return {}

    def is_model(self, ref: ClassRef) -> bool:
        return self.is_subclass(ref, MODEL_BASE) and ref.id != MODEL_BASE

    def is_abstract(self, ref: ClassRef) -> bool:
        info = self.info(ref)
        if info.meta_idx is None:
            return False
        return bool(self.meta_attrs(ref.module, info.meta_idx).get("abstract", False))

    def is_proxy(self, ref: ClassRef) -> bool:
        return bool(self.meta(ref).get("proxy", False))

    def concrete_model(self, ref: ClassRef) -> ClassRef:
        if not self.is_proxy(ref):
            return ref
        for base in self.mro(ref)[1:]:
            if self.is_model(base) and not self.is_abstract(base) and not self.is_proxy(base):
                return base
        return ref


class AppRegistry:
    """INSTALLED_APPS from the settings module's assigns and append calls, labels from AppConfig."""

    def __init__(self, index: ModelIndex, settings_module: str) -> None:
        self.index = index
        self.apps: list[tuple[str, str]] = []
        table = index.core.tables.get(settings_module)
        self.auth_user_model = table.constants.get("AUTH_USER_MODEL")
        self.settings_constants = table.constants
        for entry in self.installed_apps(index.core.index.record(settings_module)):
            self.apps.append(self.app_for_entry(entry))
        self.apps.sort(key=lambda a: -len(a[0]))

    @staticmethod
    def installed_apps(record: dict) -> list[str]:
        lists: dict[str, list[str]] = {}

        def literal(value: object) -> list[str]:
            out: list[str] = []
            if isinstance(value, dict) and ("list" in value or "tuple" in value):
                for elt in value.get("list", value.get("tuple")):
                    if isinstance(elt, dict) and "s" in elt and len(elt) == 1:
                        out.append(elt["s"])
                    elif (
                        isinstance(elt, dict)
                        and "star" in elt
                        and isinstance(elt["star"], str)
                        and "." not in elt["star"]
                    ):
                        out.extend(lists.get(elt["star"], []))
            elif isinstance(value, dict) and "bin" in value:
                out = literal(value["l"]) + literal(value["r"])
            elif isinstance(value, str) and "." not in value:
                out = list(lists.get(value, []))
            return out

        events = []
        for row in record.get("assigns", ()):
            if row["op"] == "=" and len(row["t"]) == 1 and isinstance(row["t"][0], str) and "." not in row["t"][0]:
                value = row.get("v")
                if isinstance(value, dict) and ("list" in value or "tuple" in value or "bin" in value):
                    events.append((row["l"], row["c"], "assign", row["t"][0], value))
        for row in record.get("calls", ()):
            callee, args = row[4], row[5]
            receiver, _, attr = callee.rpartition(".")
            if attr == "append" and receiver and "." not in receiver and receiver != "?" and args:
                value = const_of(args[0])
                if args[0] is None or value is not NO_CONST:
                    events.append((row[2], row[3], "append", receiver, value))
        for _, _, kind, name, value in sorted(events, key=lambda e: (e[0], e[1])):
            if kind == "assign":
                lists[name] = literal(value)
            else:
                lists.setdefault(name, []).append(value)
        return lists.get("INSTALLED_APPS", [])

    def config_attrs(self, ref: ClassRef) -> dict[str, object]:
        attrs: dict[str, object] = {}
        for cls in reversed(self.index.mro(ref)):
            attrs.update(self.index.info(cls).constant_assigns())
        return attrs

    def app_for_entry(self, entry: str) -> tuple[str, str]:
        found = self.index.resolver.resolve_dotted(entry)
        config: ClassRef | None = None
        if isinstance(found, ClassRef) and self.index.is_subclass(found, APP_CONFIG):
            config = found
        elif isinstance(found, ModuleRef):
            apps_module = self.index.core.tables.get(f"{entry}.apps")
            if apps_module is not None:
                candidates = []
                for name in apps_module.classes:
                    ref = ClassRef(apps_module.name, name)
                    if self.index.is_subclass(ref, APP_CONFIG) and ref.id != APP_CONFIG:
                        candidates.append(ref)
                defaults = [c for c in candidates if self.config_attrs(c).get("default") is True]
                if len(defaults) == 1:
                    config = defaults[0]
                elif len(candidates) == 1:
                    config = candidates[0]
        if config is None:
            return entry, entry.rpartition(".")[2]
        attrs = self.config_attrs(config)
        name = str(attrs.get("name", entry))
        label = str(attrs.get("label") or name.rpartition(".")[2])
        return name, label

    def label_for_module(self, module: str) -> str | None:
        for name, label in self.apps:
            if module == name or module.startswith(name + "."):
                return label
        return None


@dataclass(frozen=True)
class FieldDecl:
    name: str
    kind: str
    owner: ClassRef
    target: object  # V value, or None
    target_default: str | None
    related_name: object
    related_query_name: object
    symmetrical: object
    parent_link: object
    line: int


@dataclass(frozen=False)
class ModelEntry:
    ref: ClassRef
    path: str
    line: int
    app_label: str
    abstract: bool
    proxy: bool
    concrete: ClassRef
    meta: dict[str, object]

    @property
    def label(self) -> str:
        return f"{self.app_label}.{self.ref.name}"


def is_test_path(parts: list[str]) -> bool:
    name = parts[-1]
    return bool({"test", "tests"} & set(parts)) or name.startswith("test_") or name == "conftest.py"


def has_module_class_line(record: dict) -> bool:
    """Stand-in for spike A's `^class \\w+\\(` byte prefilter: a column-0 class with a parenthesised header."""
    return any(d["k"] == "class" and d["c"] == 0 and ("bases" in d or "kw" in d) for d in record.get("defs", ()))


class RelationBuilder:
    def __init__(self, core: Core) -> None:
        self.core = core
        self.index = ModelIndex(core)
        self.registry = AppRegistry(self.index, SETTINGS_MODULE)
        self.models: dict[ClassRef, ModelEntry] = {}
        self.by_label: dict[str, ClassRef] = {}
        self.field_class_kind: dict[ClassRef, tuple[str, str | None]] = {}
        self.unknowns: list[dict] = []

    def discover(self) -> None:
        for path in sorted(self.core.store.records):
            if SITE_MARKER in path:
                continue
            parts = path.split("/")
            if parts[0] not in FIRST_PARTY_ROOTS or "migrations" in parts or "node_modules" in parts:
                continue
            if is_test_path(parts):
                continue
            record = self.core.store.records[path]
            if record.get("status") != "ok" or not has_module_class_line(record):
                continue
            module = self.core.index.module_of[path]
            if self.core.index.path_of.get(module) != path:
                continue
            table = self.core.tables.get(module)
            if table is None:
                continue
            for name in table.classes:
                ref = ClassRef(module, name)
                if self.index.is_model(ref):
                    self.register(ref, path, table.class_lines[name])

    def register(self, ref: ClassRef, path: str, line: int) -> None:
        meta = self.index.meta(ref)
        abstract = self.index.is_abstract(ref)
        app_label = meta.get("app_label") or self.registry.label_for_module(ref.module)
        if app_label is None and not abstract:
            self.unknowns.append({"kind": "no-app", "model": ref.id})
            return
        entry = ModelEntry(
            ref=ref,
            path=path,
            line=line,
            app_label=str(app_label or ""),
            abstract=abstract,
            proxy=bool(meta.get("proxy", False)),
            concrete=self.index.concrete_model(ref),
            meta=meta,
        )
        self.models[ref] = entry
        if not abstract:
            self.by_label.setdefault(entry.label.lower(), ref)

    def external_label(self, ref: ClassRef) -> str | None:
        meta = self.index.meta(ref)
        app_label = meta.get("app_label") or self.registry.label_for_module(ref.module)
        return f"{app_label}.{ref.name}" if app_label else None

    # Fields -------------------------------------------------------------

    def field_kind(self, ref: ClassRef) -> tuple[str, str | None] | None:
        if ref in self.field_class_kind:
            return self.field_class_kind[ref]
        ids = [c.id for c in self.index.mro(ref)]
        kind = None
        if GENERIC in ids:
            kind = "generic"
        elif O2O in ids:
            kind = "o2o"
        elif FK in ids:
            kind = "fk"
        elif M2M in ids:
            kind = "m2m"
        if kind is None:
            return None
        result = (kind, self.default_to(ref))
        self.field_class_kind[ref] = result
        return result

    def default_to(self, ref: ClassRef) -> str | None:
        """`kwargs.setdefault("to", "app.Model")` anywhere inside a first-party field class."""
        if not ref.module.startswith(FIRST_PARTY):
            return None
        record = self.core.index.record(ref.module)
        idx = self.core.classes.def_index(ref)
        defs = record["defs"]
        for row in record.get("calls", ()):
            scope, header, callee, args = row[0], row[1], row[4], row[5]
            if header != idx and idx not in scope_ancestry(defs, scope):
                continue
            if not callee.endswith(".setdefault") or len(args) != 2:
                continue
            if const_of(args[0]) == "to" and is_const(args[1]):
                return cast(str | None, const_of(args[1]))
        return None

    def body_fields(self, ref: ClassRef) -> dict[str, FieldDecl]:
        info = self.index.info(ref)
        out: dict[str, FieldDecl] = {}
        for row in info.assigns:
            if row["op"] == "=" and len(row["t"]) == 1 and isinstance(row["t"][0], str) and "." not in row["t"][0]:
                name, value = row["t"][0], row.get("v")
            elif row["op"] == ":" and isinstance(row["t"][0], str) and "." not in row["t"][0] and "v" in row:
                name, value = row["t"][0], row["v"]
            else:
                continue
            if not (isinstance(value, dict) and "call" in value):
                continue
            field_class = self.index.resolver.resolve_value(ref.module, value["call"])
            kind = self.field_kind(field_class) if isinstance(field_class, ClassRef) else None
            args = value.get("args", [])
            if kind is None and args and isinstance(args[0], dict) and "call" in args[0]:
                # Rule W: a pass-through wrapper, field_access_control(models.ForeignKey(...), ...).
                value = args[0]
                args = value.get("args", [])
                field_class = self.index.resolver.resolve_value(ref.module, value["call"])
                kind = self.field_kind(field_class) if isinstance(field_class, ClassRef) else None
            if kind is None:
                continue
            kwargs = {k: v for k, v in value.get("kw", ()) if k}
            target = args[0] if args else kwargs.get("to")

            def const(key: str, kwargs: dict = kwargs, name: str = name) -> object:
                node = kwargs.get(key)
                if node is None:
                    return None
                if is_const(node):
                    found = const_of(node)
                    return render(node) if found is NO_CONST else found
                self.unknowns.append({"kind": f"non-constant-{key}", "where": f"{ref.id}.{name}", "expr": render(node)})
                return ("?", render(node))

            out[name] = FieldDecl(
                name=name,
                kind=kind[0],
                owner=ref,
                target=target,
                target_default=kind[1],
                related_name=const("related_name"),
                related_query_name=const("related_query_name"),
                symmetrical=const("symmetrical"),
                parent_link=const("parent_link"),
                line=row["l"],
            )
        return out

    def local_fields(self, ref: ClassRef) -> dict[str, FieldDecl]:
        result = dict(self.body_fields(ref))
        shadow = set(self.index.info(ref).body_names)
        direct = set(self.index.bases(ref))
        for base in self.index.mro(ref)[1:]:
            if base in direct and self.index.is_model(base):
                if not self.index.is_abstract(base):
                    continue
                for name, decl in self.local_fields(base).items():
                    if name not in result and name not in shadow:
                        result[name] = decl
            else:
                shadow |= self.index.info(base).body_names
        return result

    # Targets ------------------------------------------------------------

    def resolve_target(self, decl: FieldDecl, cls: ModelEntry) -> ClassRef | str | None:
        expr = decl.target
        if expr is None:
            if decl.target_default is None:
                return None
            return self.resolve_label(decl.target_default, cls)
        if isinstance(expr, dict) and "s" in expr and len(expr) == 1:
            return self.resolve_label(expr["s"], cls)
        attr = None
        if isinstance(expr, str) and "." in expr:
            attr = expr.rpartition(".")[2]
        elif isinstance(expr, dict) and "a" in expr:
            attr = expr["a"]
        if attr == "AUTH_USER_MODEL":
            return self.resolve_label(self.registry.auth_user_model or "", cls)
        if isinstance(expr, str) and expr.count(".") == 1:
            # Rule S: a third-party settings object maps X_MODEL to a Django setting with a prefix.
            head, _, name = expr.partition(".")
            if head in SWAPPABLE_SETTINGS:
                value = self.registry.settings_constants.get(SWAPPABLE_SETTINGS[head] + name)
                if isinstance(value, str):
                    return self.resolve_label(value, cls)
        found = self.index.resolver.resolve_value(decl.owner.module, expr)
        if isinstance(found, ClassRef):
            return found
        return f"?{render(expr)}"

    def resolve_label(self, value: str, cls: ModelEntry) -> ClassRef | str:
        if value == "self":
            return cls.ref
        if "." not in value:
            value = f"{cls.app_label}.{value}"
        ref = self.by_label.get(value.lower())
        if ref is not None:
            return ref
        app_label, _, model_name = value.partition(".")
        for name, label in self.registry.apps:
            if label != app_label or name.startswith(FIRST_PARTY):
                continue
            found = self.index.resolver.resolve_name(f"{name}.models", model_name)
            if isinstance(found, ClassRef) and self.index.is_model(found):
                return found
        return f"?{value}"

    def target_info(self, ref: ClassRef) -> tuple[ClassRef, str] | None:
        entry = self.models.get(ref)
        if entry is not None:
            concrete = entry.concrete
            return concrete, self.models[concrete].label if concrete in self.models else concrete.id
        if not self.index.is_model(ref):
            return None
        concrete = self.index.concrete_model(ref)
        return concrete, self.external_label(concrete) or concrete.id

    # Records ------------------------------------------------------------

    def concrete_parents(self, ref: ClassRef) -> list[ClassRef]:
        out = []
        for base in self.index.mro(ref)[1:]:
            if self.index.is_model(base) and not self.index.is_abstract(base) and not self.index.is_proxy(base):
                out.append(base)
        return out

    def ptr_fields(self, ref: ClassRef) -> list[tuple[str, ClassRef]]:
        explicit = {
            self.resolve_target(d, self.models[ref])
            for d in self.local_fields(ref).values()
            if d.kind == "o2o" and d.parent_link is True
        }
        out = []
        for base in self.index.bases(ref):
            if not self.index.is_model(base) or self.index.is_abstract(base):
                continue
            concrete = self.index.concrete_model(base)
            if concrete in explicit:
                continue
            out.append((f"{concrete.name.lower()}_ptr", concrete))
        return out

    def record(self, model: ModelEntry, decl: FieldDecl, contributed: ModelEntry, from_parent: bool) -> dict:
        target_ref = self.resolve_target(decl, contributed)
        resolved = self.target_info(target_ref) if isinstance(target_ref, ClassRef) else None
        if resolved is None:
            concrete_target = str(target_ref)
            target_label = None
            target_module = ""
        else:
            concrete_target = resolved[0].id
            target_label = resolved[1]
            target_module = resolved[0].module
        owner = target_module.split(".")[1] if target_module.startswith("products.") else "core"

        class_lower = contributed.ref.name.lower()
        app_lower = contributed.app_label.lower()
        related_name = decl.related_name
        if related_name is None:
            related_name = contributed.meta.get("default_related_name")
        query_name = decl.related_query_name
        symmetrical = decl.symmetrical
        if decl.kind == "m2m" and symmetrical is None:
            target = decl.target
            symmetrical = (
                isinstance(target, dict)
                and "s" in target
                and len(target) == 1
                and target["s"]
                in (
                    "self",
                    contributed.ref.name,
                )
            )
        if decl.kind == "generic":
            related_name = query_name or "+"
        if decl.kind == "m2m":
            is_self = concrete_target == contributed.ref.id
            if symmetrical and is_self:
                related_name = f"{decl.name}_rel_+"
            elif isinstance(related_name, str) and related_name.endswith("+"):
                related_name = f"_{contributed.app_label}_{class_lower}_{decl.name}_+"
        if isinstance(related_name, str) and related_name:
            related_name = related_name % {"class": class_lower, "model_name": class_lower, "app_label": app_lower}
        if isinstance(query_name, str) and query_name:
            query_name = query_name % {"class": class_lower, "app_label": app_lower}
        hidden = isinstance(related_name, str) and related_name.endswith("+")
        multiple = decl.kind != "o2o"
        if multiple and decl.kind == "m2m" and symmetrical and concrete_target == contributed.ref.id:
            accessor = None
        elif related_name:
            accessor = related_name
        else:
            accessor = class_lower + ("_set" if multiple else "")
        rqn = query_name or related_name or class_lower
        from_abstract = decl.owner.id if self.index.is_abstract(decl.owner) else None
        return {
            "model": model.ref.id,
            "model_label": model.label,
            "field": decl.name,
            "kind": decl.kind,
            "declaring_model": contributed.ref.id,
            "concrete_target": concrete_target,
            "target_label": target_label,
            "target_owner_by_module": owner,
            "from_abstract": from_abstract,
            "from_concrete_parent": from_parent,
            "hidden": hidden,
            "accessor_name": accessor if not isinstance(accessor, tuple) else str(accessor),
            "query_name": rqn if not isinstance(rqn, tuple) else str(rqn),
            "auto_created": False,
            "explicit_query_name": query_name if isinstance(query_name, str) else None,
            "_src": f"{self.models[decl.owner].path if decl.owner in self.models else decl.owner.module}:{decl.line}",
        }

    def ptr_record(
        self, model: ModelEntry, name: str, parent: ClassRef, contributed: ModelEntry, from_parent: bool
    ) -> dict:
        target = self.models[parent]
        class_lower = contributed.ref.name.lower()
        return {
            "model": model.ref.id,
            "model_label": model.label,
            "field": name,
            "kind": "o2o",
            "declaring_model": contributed.ref.id,
            "concrete_target": parent.id,
            "target_label": target.label,
            "target_owner_by_module": parent.module.split(".")[1] if parent.module.startswith("products.") else "core",
            "from_abstract": None,
            "from_concrete_parent": from_parent,
            "hidden": False,
            "accessor_name": class_lower,
            "query_name": class_lower,
            "auto_created": True,
            "explicit_query_name": None,
            "_src": f"{contributed.path}:{contributed.line}",
        }

    def fields_of(self, entry: ModelEntry, model: ModelEntry, from_parent: bool) -> list[dict]:
        out = []
        local = self.local_fields(entry.ref)
        for decl in local.values():
            if from_parent and decl.kind == "generic":
                # Rule P: Django deep-copies a concrete parent's private fields onto the child.
                out.append(self.record(model, decl, model, False))
                continue
            out.append(self.record(model, decl, entry, from_parent))
        for name, parent in self.ptr_fields(entry.ref):
            if name in local:
                continue
            out.append(self.ptr_record(model, name, parent, entry, from_parent))
        return out

    def relations(self) -> list[dict]:
        out = []
        for ref, entry in self.models.items():
            if entry.abstract or entry.proxy:
                continue
            out.extend(self.fields_of(entry, entry, False))
            for parent in self.concrete_parents(ref):
                parent_entry = self.models.get(parent)
                if parent_entry is None:
                    continue
                out.extend(self.fields_of(parent_entry, entry, True))
        return out


def reverse_accessor_edges(records: list[dict]) -> list[str]:
    def boundary(model_id: str) -> str:
        return model_id.split(".")[1] if model_id.startswith("products.") else "core"

    def label(model_id: str, model_label: str) -> str:
        owner = boundary(model_id)
        return owner if owner != "core" else model_label.split(".")[0]

    out = set()
    for r in records:
        if r["from_concrete_parent"] or r["concrete_target"] == "self":
            continue
        target = r["concrete_target"]
        if not target.startswith(FIRST_PARTY) or r["target_label"] is None:
            continue
        if boundary(r["model"]) == boundary(target):
            continue
        if r["hidden"] and not r["explicit_query_name"]:
            continue
        accessor = f"query:{r['query_name']}" if r["hidden"] else r["accessor_name"]
        target_name = target.rpartition(".")[2]
        out.add(
            f"{label(target, r['target_label'])}.{target_name} "
            f"{label(r['model'], r['model_label'])}.{r['model'].rpartition('.')[2]}.{r['field']} {accessor}"
        )
    return sorted(out)


def ledger_lines(edges: list[str]) -> list[str]:
    out = []
    for line in edges:
        target, field, accessor = line.split(" ")
        out.append(f"{target} {field} reverse-accessor({accessor}) 1")
    return sorted(out)


def run(core: Core) -> dict:
    builder = RelationBuilder(core)
    builder.discover()
    records = builder.relations()
    models = [
        {"model": e.ref.id, "label": e.label, "proxy": e.proxy, "concrete": e.concrete.id, "path": f"{e.path}:{e.line}"}
        for e in builder.models.values()
        if not e.abstract
    ]
    edges = reverse_accessor_edges(records)
    return {
        "relations": records,
        "models": models,
        "edges": edges,
        "ledger": ledger_lines(edges),
        "unknowns": builder.unknowns,
        "installed_apps": builder.registry.apps,
    }
