"""Static model index and effective relations (port of the spike relations channel).

Reads class, assign and call facts through the core resolver. Never boots Django.
Owners are never decided here: edges carry modules, and labels decide packages later.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast

from hoglint.packs.django.apps import AppRegistry, class_constants
from hoglint.packs.django.config import DjangoPackConfig
from hoglint.packs.django.sites import PathKinds
from hoglint.spike.core import CLASS, NO_CONST, SITE_MARKER, ClassRef, Core, const_of, is_const, scope_ancestry

MODEL_BASE = "django.db.models.base.Model"
FK = "django.db.models.fields.related.ForeignKey"
O2O = "django.db.models.fields.related.OneToOneField"
M2M = "django.db.models.fields.related.ManyToManyField"
GENERIC = "django.contrib.contenttypes.fields.GenericRelation"
AUTH_USER_MODEL = "AUTH_USER_MODEL"
UNRESOLVED_PREFIX = "?"


def render(value: object) -> str:
    """Display text for a V value."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and "s" in value:
        return repr(value["s"])
    return json.dumps(value, sort_keys=True)


class ClassBody:
    """Direct body facts of one class def: assigns, nested defs, Meta."""

    def __init__(self, record: dict, idx: int) -> None:
        defs = record["defs"]
        body_flags = defs[idx].get("f", 0) | CLASS
        self.record = record
        self.assigns = [a for a in record.get("assigns", ()) if a["s"] == idx and a.get("f", 0) == body_flags]
        children = [i for i, d in enumerate(defs) if d["s"] == idx and d.get("f", 0) == body_flags]
        self.body_names: set[str] = set()
        self.meta_idx: int | None = None
        for row in self.assigns:
            if row["op"] == "=":
                self.body_names.update(t for t in row["t"] if isinstance(t, str) and "." not in t)
            elif row["op"] == ":" and isinstance(row["t"][0], str) and "." not in row["t"][0]:
                self.body_names.add(row["t"][0])
        for i in children:
            d = defs[i]
            if d["k"] == "class" and d["n"] == "Meta":
                self.meta_idx = i
            else:
                self.body_names.add(d["n"])

    def constant_assigns(self, idx: int) -> dict[str, object]:
        return class_constants(self.record, idx)


class ModelIndex:
    """Model-ness, Meta, abstract and proxy over the class index."""

    def __init__(self, core: Core) -> None:
        self.core = core
        self.resolver = core.resolver
        self.classes = core.classes
        self.body_cache: dict[tuple[str, int], ClassBody] = {}

    def body(self, module: str, idx: int) -> ClassBody:
        key = (module, idx)
        if key not in self.body_cache:
            self.body_cache[key] = ClassBody(self.core.index.record(module) or {}, idx)
        return self.body_cache[key]

    def info(self, ref: ClassRef) -> ClassBody:
        return self.body(ref.module, self.classes.def_index(ref))

    def mro(self, ref: ClassRef) -> list[ClassRef]:
        return self.classes.mro(ref)

    def bases(self, ref: ClassRef) -> list[ClassRef]:
        return self.classes.bases(ref)

    def meta_attrs(self, module: str, meta_idx: int, depth: int = 0) -> dict[str, object]:
        attrs: dict[str, object] = {}
        record = self.core.index.record(module) or {}
        for base in record["defs"][meta_idx].get("bases", ()):
            parent_value = None
            if isinstance(base, str) and base.endswith(".Meta"):
                parent_value = base[: -len(".Meta")]
            elif isinstance(base, dict) and base.get("a") == "Meta":
                parent_value = base["v"]
            if parent_value is None or depth >= 5:
                continue
            parent = self.resolver.resolve_value(module, parent_value)
            if not isinstance(parent, ClassRef):
                continue
            parent_meta = self.info(parent).meta_idx
            if parent_meta is None:
                continue
            inherited = self.meta_attrs(parent.module, parent_meta, depth + 1)
            inherited.pop("abstract", None)
            attrs.update(inherited)
        attrs.update(self.body(module, meta_idx).constant_assigns(meta_idx))
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
        return ref.id != MODEL_BASE and self.classes.is_subclass(ref, MODEL_BASE)

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


@dataclass(frozen=True)
class FieldDecl:
    name: str
    kind: str  # fk | o2o | m2m | generic
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


@dataclass(frozen=True)
class Relation:
    """One effective relation field of a concrete model (data model section 4)."""

    model: str
    model_label: str
    model_path: str
    model_line: int
    field: str
    kind: str
    declaring_model: str
    declared_in: str  # module of the class whose body holds the field
    declared_line: int
    concrete_target: str  # qualified class, or "?<text>" when unresolved
    target_label: str | None
    target_module: str
    from_abstract: str | None
    from_concrete_parent: bool
    hidden: bool
    accessor_name: str | None
    query_name: str | None
    auto_created: bool
    explicit_query_name: str | None
    target_surface: str

    @property
    def resolved(self) -> bool:
        return not self.concrete_target.startswith(UNRESOLVED_PREFIX)

    @property
    def sealed(self) -> bool:
        return self.hidden and not self.explicit_query_name

    def attrs(self) -> tuple[tuple[str, str], ...]:
        out: list[tuple[str, str]] = []
        for key in (
            "model",
            "model_label",
            "field",
            "kind",
            "declaring_model",
            "concrete_target",
            "target_label",
            "target_module",
            "from_abstract",
            "from_concrete_parent",
            "hidden",
            "accessor_name",
            "query_name",
            "auto_created",
            "explicit_query_name",
        ):
            value = getattr(self, key)
            if value is None:
                continue
            out.append((key, str(value).lower() if isinstance(value, bool) else str(value)))
        out.append(("sealed", str(self.sealed).lower()))
        return tuple(out)


@dataclass(frozen=True)
class LabelRef:
    """A string model label at a declaration: `ForeignKey("app.Model")` or a swappable setting."""

    module: str
    owner: str
    field: str
    line: int
    surface: str
    target: ClassRef | None


class ModelRegistry:
    """Every first-party model with its app label, plus the label table."""

    def __init__(self, core: Core, apps: AppRegistry, config: DjangoPackConfig) -> None:
        self.core = core
        self.apps = apps
        self.config = config
        self.index = ModelIndex(core)
        self.models: dict[ClassRef, ModelEntry] = {}
        self.by_label: dict[str, ClassRef] = {}
        self.unknowns: list[dict[str, str]] = []

    def discover(self) -> None:
        for path in sorted(self.core.store.records):
            if SITE_MARKER in path or path.split("/", 1)[0] not in self.config.first_party:
                continue
            if PathKinds.is_migration(path) or PathKinds.is_test(path) or "node_modules" in path.split("/"):
                continue
            record = self.core.store.records[path]
            if record.get("status") != "ok":
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
        app_label = meta.get("app_label") or self.apps.label_for_module(ref.module)
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
        app_label = meta.get("app_label") or self.apps.label_for_module(ref.module)
        return f"{app_label}.{ref.name}" if app_label else None

    def is_first_party(self, module: str) -> bool:
        return module.split(".", 1)[0] in self.config.first_party

    def resolve_label(self, value: str, app_label: str, self_ref: ClassRef | None) -> ClassRef | None:
        """`app.Model`, `Model` (same app) or `self` to a model class, as Django's registry would."""
        if value == "self":
            return self_ref
        if "." not in value:
            value = f"{app_label}.{value}"
        ref = self.by_label.get(value.lower())
        if ref is not None:
            return ref
        label, _, model_name = value.partition(".")
        for app in self.apps.apps:
            if app.label != label or self.is_first_party(app.name):
                continue
            found = self.core.resolver.resolve_name(f"{app.name}.models", model_name)
            if isinstance(found, ClassRef) and self.index.is_model(found):
                return found
        return None

    def target_info(self, ref: ClassRef) -> tuple[ClassRef, str] | None:
        entry = self.models.get(ref)
        if entry is not None:
            concrete = entry.concrete
            return concrete, self.models[concrete].label if concrete in self.models else concrete.id
        if not self.index.is_model(ref):
            return None
        concrete = self.index.concrete_model(ref)
        return concrete, self.external_label(concrete) or concrete.id


class RelationBuilder:
    """Effective relations per concrete model, including fields from abstract and concrete parents."""

    def __init__(self, registry: ModelRegistry) -> None:
        self.registry = registry
        self.index = registry.index
        self.core = registry.core
        self.apps = registry.apps
        self.config = registry.config
        self.field_class_kind: dict[ClassRef, tuple[str, str | None]] = {}
        self.label_refs: dict[tuple[str, str], LabelRef] = {}
        self.unknowns = registry.unknowns

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
        if not self.registry.is_first_party(ref.module):
            return None
        record = self.core.index.record(ref.module) or {}
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
            decl = self.field_decl(ref, name, value, row["l"])
            if decl is not None:
                out[name] = decl
        return out

    def field_decl(self, ref: ClassRef, name: str, value: object, line: int) -> FieldDecl | None:
        if not (isinstance(value, dict) and "call" in value):
            return None
        field_class = self.core.resolver.resolve_value(ref.module, value["call"])
        kind = self.field_kind(field_class) if isinstance(field_class, ClassRef) else None
        args = value.get("args", [])
        if kind is None and args and isinstance(args[0], dict) and "call" in args[0]:
            # A pass-through wrapper: wrap(models.ForeignKey(...), ...).
            value = args[0]
            args = value.get("args", [])
            field_class = self.core.resolver.resolve_value(ref.module, value["call"])
            kind = self.field_kind(field_class) if isinstance(field_class, ClassRef) else None
        if kind is None:
            return None
        kwargs = {k: v for k, v in value.get("kw", ()) if k}

        def const(key: str) -> object:
            node = kwargs.get(key)
            if node is None:
                return None
            if is_const(node):
                found = const_of(node)
                return render(node) if found is NO_CONST else found
            self.unknowns.append({"kind": f"non-constant-{key}", "where": f"{ref.id}.{name}", "expr": render(node)})
            return ("?", render(node))

        return FieldDecl(
            name=name,
            kind=kind[0],
            owner=ref,
            target=args[0] if args else kwargs.get("to"),
            target_default=kind[1],
            related_name=const("related_name"),
            related_query_name=const("related_query_name"),
            symmetrical=const("symmetrical"),
            parent_link=const("parent_link"),
            line=line,
        )

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

    def label_target(self, decl: FieldDecl, cls: ModelEntry, text: str) -> ClassRef | str:
        found = self.registry.resolve_label(text, cls.app_label, cls.ref)
        key = (decl.owner.id, decl.name)
        if key not in self.label_refs:
            self.label_refs[key] = LabelRef(decl.owner.module, decl.owner.name, decl.name, decl.line, text, found)
        if found is None:
            return f"{UNRESOLVED_PREFIX}{text if '.' in text else f'{cls.app_label}.{text}'}"
        return found

    def resolve_target(self, decl: FieldDecl, cls: ModelEntry) -> ClassRef | str:
        expr = decl.target
        if expr is None:
            if decl.target_default is None:
                return f"{UNRESOLVED_PREFIX}None"
            return self.label_target(decl, cls, decl.target_default)
        if isinstance(expr, dict) and "s" in expr and len(expr) == 1:
            return self.label_target(decl, cls, expr["s"])
        attr = None
        if isinstance(expr, str) and "." in expr:
            attr = expr.rpartition(".")[2]
        elif isinstance(expr, dict) and "a" in expr:
            attr = expr["a"]
        if attr == AUTH_USER_MODEL:
            return self.label_target(decl, cls, self.apps.setting(AUTH_USER_MODEL) or "")
        if isinstance(expr, str) and expr.count(".") == 1:
            head, _, name = expr.partition(".")
            for proxy in self.config.settings_proxies:
                value = self.apps.setting(proxy.prefix + name) if head == proxy.name else None
                if value is not None:
                    return self.label_target(decl, cls, value)
        found = self.core.resolver.resolve_value(decl.owner.module, expr)
        if isinstance(found, ClassRef):
            return found
        return f"{UNRESOLVED_PREFIX}{render(expr)}"

    def concrete_parents(self, ref: ClassRef) -> list[ClassRef]:
        out = []
        for base in self.index.mro(ref)[1:]:
            if self.index.is_model(base) and not self.index.is_abstract(base) and not self.index.is_proxy(base):
                out.append(base)
        return out

    def ptr_fields(self, ref: ClassRef) -> list[tuple[str, ClassRef]]:
        explicit = {
            self.resolve_target(d, self.registry.models[ref])
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

    def record(self, model: ModelEntry, decl: FieldDecl, contributed: ModelEntry, from_parent: bool) -> Relation:
        target_ref = self.resolve_target(decl, contributed)
        resolved = self.registry.target_info(target_ref) if isinstance(target_ref, ClassRef) else None
        if resolved is None:
            concrete_target = str(target_ref) if not isinstance(target_ref, ClassRef) else target_ref.id
            if not concrete_target.startswith(UNRESOLVED_PREFIX):
                concrete_target = f"{UNRESOLVED_PREFIX}{concrete_target}"
            target_label = None
            target_module = ""
        else:
            concrete_target = resolved[0].id
            target_label = resolved[1]
            target_module = resolved[0].module

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
                and target["s"] in ("self", contributed.ref.name)
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
        accessor: object
        if multiple and decl.kind == "m2m" and symmetrical and concrete_target == contributed.ref.id:
            accessor = None
        elif related_name:
            accessor = related_name
        else:
            accessor = class_lower + ("_set" if multiple else "")
        rqn = query_name or related_name or class_lower
        declaring = self.registry.models.get(decl.owner)
        return Relation(
            model=model.ref.id,
            model_label=model.label,
            model_path=model.path,
            model_line=model.line,
            field=decl.name,
            kind=decl.kind,
            declaring_model=contributed.ref.id,
            declared_in=decl.owner.module,
            declared_line=decl.line if declaring is not None else 0,
            concrete_target=concrete_target,
            target_label=target_label,
            target_module=target_module,
            from_abstract=decl.owner.id if self.index.is_abstract(decl.owner) else None,
            from_concrete_parent=from_parent,
            hidden=hidden,
            accessor_name=None if accessor is None else str(accessor),
            query_name=str(rqn),
            auto_created=False,
            explicit_query_name=query_name if isinstance(query_name, str) else None,
            target_surface=render(decl.target) if decl.target is not None else (decl.target_default or ""),
        )

    def ptr_record(
        self, model: ModelEntry, name: str, parent: ClassRef, contributed: ModelEntry, from_parent: bool
    ) -> Relation:
        target = self.registry.models[parent]
        class_lower = contributed.ref.name.lower()
        return Relation(
            model=model.ref.id,
            model_label=model.label,
            model_path=model.path,
            model_line=model.line,
            field=name,
            kind="o2o",
            declaring_model=contributed.ref.id,
            declared_in=contributed.ref.module,
            declared_line=contributed.line,
            concrete_target=parent.id,
            target_label=target.label,
            target_module=parent.module,
            from_abstract=None,
            from_concrete_parent=from_parent,
            hidden=False,
            accessor_name=class_lower,
            query_name=class_lower,
            auto_created=True,
            explicit_query_name=None,
            target_surface=parent.name,
        )

    def fields_of(self, entry: ModelEntry, model: ModelEntry, from_parent: bool) -> list[Relation]:
        out = []
        local = self.local_fields(entry.ref)
        for decl in local.values():
            if from_parent and decl.kind == "generic":
                # Django deep-copies a concrete parent's private fields onto the child.
                out.append(self.record(model, decl, model, False))
                continue
            out.append(self.record(model, decl, entry, from_parent))
        for name, parent in self.ptr_fields(entry.ref):
            if name in local:
                continue
            out.append(self.ptr_record(model, name, parent, entry, from_parent))
        return out

    def relations(self) -> list[Relation]:
        out = []
        for ref, entry in self.registry.models.items():
            if entry.abstract or entry.proxy:
                continue
            out.extend(self.fields_of(entry, entry, False))
            for parent in self.concrete_parents(ref):
                parent_entry = self.registry.models.get(parent)
                if parent_entry is None:
                    continue
                out.extend(self.fields_of(parent_entry, entry, True))
        return out
