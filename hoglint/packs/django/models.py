"""Static model index and effective relations (port of the spike relations channel).

Reads class, assign and call facts through the core resolver. Never boots Django.
Owners are never decided here: edges carry modules, and labels decide packages later.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import cast

from hoglint.core.build import CoreBuild
from hoglint.core.records import NO_CONST, RecordBodies, const_of, is_const, value_text
from hoglint.core.sites import FileScope
from hoglint.core.symbols import entity
from hoglint.model import Context, EntityId
from hoglint.packs.django.apps import AppRegistry
from hoglint.packs.django.config import DjangoPackConfig

MODEL_BASE = "django.db.models.base.Model"
FK = "django.db.models.fields.related.ForeignKey"
O2O = "django.db.models.fields.related.OneToOneField"
M2M = "django.db.models.fields.related.ManyToManyField"
GENERIC = "django.contrib.contenttypes.fields.GenericRelation"
AUTH_USER_MODEL = "AUTH_USER_MODEL"
RECURSIVE = "self"
UNRESOLVED_PREFIX = "?"


def ancestry(defs: list[dict], idx: int) -> list[int]:
    out = [idx]
    while idx != -1:
        idx = defs[idx]["s"]
        out.append(idx)
    return out


class ClassBody:
    """Direct body facts of one class def: assigns, nested defs, Meta."""

    def __init__(self, bodies: RecordBodies, idx: int) -> None:
        defs = bodies.defs
        self.bodies = bodies
        self.assigns = bodies.assigns(idx)
        children = bodies.children(idx)
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


class ModelIndex:
    """Model-ness, Meta, abstract and proxy over the class index. Every answer is memoized."""

    def __init__(self, build: CoreBuild) -> None:
        self.build = build
        self.resolver = build.resolver
        self.classes = build.classes
        self.body_cache: dict[tuple[str, int], ClassBody] = {}
        self.model_cache: dict[EntityId, bool] = {}
        self.abstract_cache: dict[EntityId, bool] = {}
        self.meta_cache: dict[EntityId, dict[str, object]] = {}

    def body_of(self, record: dict, idx: int) -> ClassBody:
        key = (record["path"], idx)
        if key not in self.body_cache:
            self.body_cache[key] = ClassBody(self.build.bodies(record), idx)
        return self.body_cache[key]

    def info(self, ref: EntityId) -> ClassBody:
        found = self.classes.located(ref)
        if found is None:
            raise LookupError(f"class {ref} has no facts")
        return self.body_of(*found)

    def mro(self, ref: EntityId) -> list[EntityId]:
        return self.classes.mro(ref)

    def bases(self, ref: EntityId) -> list[EntityId]:
        return self.classes.bases(ref)

    def meta_attrs(self, ref: EntityId, meta_idx: int, depth: int = 0) -> dict[str, object]:
        attrs: dict[str, object] = {}
        record = self.info(ref).bodies.record
        module = ref.module
        for base in record["defs"][meta_idx].get("bases", ()):
            parent_value = None
            if isinstance(base, str) and base.endswith(".Meta"):
                parent_value = base[: -len(".Meta")]
            elif isinstance(base, dict) and base.get("a") == "Meta":
                parent_value = base["v"]
            if parent_value is None or depth >= 5:
                continue
            parent = self.resolver.resolve_class(module, parent_value)
            if parent is None:
                continue
            parent_meta = self.info(parent).meta_idx
            if parent_meta is None:
                continue
            inherited = self.meta_attrs(parent, parent_meta, depth + 1)
            inherited.pop("abstract", None)
            attrs.update(inherited)
        attrs.update(self.info(ref).bodies.constants(meta_idx))
        return attrs

    def meta(self, ref: EntityId) -> dict[str, object]:
        """Own Meta, else the first inherited one. Django removes Meta from concrete models, so only
        abstract models and plain mixins pass theirs on."""
        if ref not in self.meta_cache:
            self.meta_cache[ref] = self.read_meta(ref)
        return self.meta_cache[ref]

    def read_meta(self, ref: EntityId) -> dict[str, object]:
        info = self.info(ref)
        if info.meta_idx is not None:
            return self.meta_attrs(ref, info.meta_idx)
        for base in self.mro(ref)[1:]:
            if self.is_model(base) and not self.is_abstract(base):
                continue
            base_info = self.info(base)
            if base_info.meta_idx is not None:
                attrs = self.meta_attrs(base, base_info.meta_idx)
                attrs["abstract"] = False
                return attrs
        return {}

    def is_model(self, ref: EntityId) -> bool:
        if ref not in self.model_cache:
            self.model_cache[ref] = str(ref) != MODEL_BASE and self.classes.is_subclass(ref, MODEL_BASE)
        return self.model_cache[ref]

    def is_abstract(self, ref: EntityId) -> bool:
        if ref not in self.abstract_cache:
            info = self.info(ref)
            abstract = info.meta_idx is not None and bool(self.meta_attrs(ref, info.meta_idx).get("abstract", False))
            self.abstract_cache[ref] = abstract
        return self.abstract_cache[ref]

    def is_proxy(self, ref: EntityId) -> bool:
        return bool(self.meta(ref).get("proxy", False))

    def concrete_model(self, ref: EntityId) -> EntityId:
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
    owner: EntityId
    target: object  # V value, or None
    target_default: str | None
    related_name: object
    related_query_name: object
    symmetrical: object
    parent_link: object
    line: int


@dataclass(frozen=False)
class ModelEntry:
    ref: EntityId
    path: str
    line: int
    app_label: str
    abstract: bool
    proxy: bool
    concrete: EntityId
    meta: dict[str, object]

    @property
    def label(self) -> str:
        return f"{self.app_label}.{self.ref.symbol}"


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
    target: EntityId | None


class ModelRegistry:
    """Every first-party model with its app label, plus the label table."""

    def __init__(self, build: CoreBuild, apps: AppRegistry) -> None:
        self.build = build
        self.apps = apps
        self.index = ModelIndex(build)
        self.models: dict[EntityId, ModelEntry] = {}
        self.by_label: dict[str, EntityId] = {}
        self.failures: list[str] = []

    def discover(self, files: Iterable[FileScope]) -> None:
        """Models in the given prod files: classes whose MRO reaches Django's Model."""
        index = self.build.index
        for scope in files:
            if scope.base_contexts & {Context.TEST, Context.MIGRATION} or "node_modules" in scope.path.split("/"):
                continue
            if index.path_of.get(scope.module) != scope.path:
                continue
            table = self.build.tables.get(scope.module)
            if table is None:
                continue
            for name, idx in table.classes().items():
                ref = entity(scope.module, name)
                try:
                    if self.index.is_model(ref):
                        self.register(ref, scope.path, scope.defs[idx]["l"])
                except Exception as error:  # noqa: BLE001 - one unreadable class is a gap, the other models still count
                    self.failures.append(f"{ref}: {type(error).__name__}: {error}")

    def register(self, ref: EntityId, path: str, line: int) -> None:
        meta = self.index.meta(ref)
        abstract = self.index.is_abstract(ref)
        app_label = meta.get("app_label") or self.apps.label_for_module(ref.module)
        if app_label is None and not abstract:
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

    def external_label(self, ref: EntityId) -> str | None:
        meta = self.index.meta(ref)
        app_label = meta.get("app_label") or self.apps.label_for_module(ref.module)
        return f"{app_label}.{ref.symbol}" if app_label else None

    def is_first_party(self, module: str) -> bool:
        return self.build.first_party.contains(module)

    def resolve_label(self, value: str, app_label: str, self_ref: EntityId | None) -> EntityId | None:
        """`app.Model`, `Model` (same app) or `self` to a model class, as Django's registry would."""
        if value == RECURSIVE:
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
            hit = self.build.resolver.resolve_name(f"{app.name}.models", model_name)
            if hit is not None and hit.kind == "class" and self.index.is_model(hit.origin):
                return hit.origin
        return None

    def target_info(self, ref: EntityId) -> tuple[EntityId, str] | None:
        entry = self.models.get(ref)
        if entry is not None:
            concrete = entry.concrete
            return concrete, self.models[concrete].label if concrete in self.models else str(concrete)
        if not self.index.is_model(ref):
            return None
        concrete = self.index.concrete_model(ref)
        return concrete, self.external_label(concrete) or str(concrete)


class RelationBuilder:
    """Effective relations per concrete model, including fields from abstract and concrete parents."""

    def __init__(self, registry: ModelRegistry, config: DjangoPackConfig) -> None:
        self.registry = registry
        self.index = registry.index
        self.build = registry.build
        self.apps = registry.apps
        self.config = config
        self.field_class_kind: dict[EntityId, tuple[str, str | None] | None] = {}
        self.label_refs: dict[tuple[str, str], LabelRef] = {}
        self.local_cache: dict[EntityId, dict[str, FieldDecl]] = {}

    def field_kind(self, ref: EntityId) -> tuple[str, str | None] | None:
        if ref not in self.field_class_kind:
            self.field_class_kind[ref] = self.read_field_kind(ref)
        return self.field_class_kind[ref]

    def read_field_kind(self, ref: EntityId) -> tuple[str, str | None] | None:
        ids = [str(c) for c in self.index.mro(ref)]
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
        return kind, self.default_to(ref)

    def default_to(self, ref: EntityId) -> str | None:
        """`kwargs.setdefault("to", "app.Model")` anywhere inside a first-party field class."""
        if not self.registry.is_first_party(ref.module):
            return None
        found = self.build.classes.located(ref)
        if found is None:
            return None
        record, idx = found
        defs = record["defs"]
        for scope, header, _, _, _, callee, args, _ in record.get("calls", ()):
            if header != idx and idx not in ancestry(defs, scope):
                continue
            if not callee.endswith(".setdefault") or len(args) != 2:
                continue
            if const_of(args[0]) == "to" and is_const(args[1]):
                return cast(str | None, const_of(args[1]))
        return None

    def body_fields(self, ref: EntityId) -> dict[str, FieldDecl]:
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

    def field_decl(self, ref: EntityId, name: str, value: object, line: int) -> FieldDecl | None:
        if not (isinstance(value, dict) and "call" in value):
            return None
        field_class = self.build.resolver.resolve_class(ref.module, value["call"])
        kind = self.field_kind(field_class) if field_class is not None else None
        args = value.get("args", [])
        if kind is None and args and isinstance(args[0], dict) and "call" in args[0]:
            # A pass-through wrapper: wrap(models.ForeignKey(...), ...).
            value = args[0]
            args = value.get("args", [])
            field_class = self.build.resolver.resolve_class(ref.module, value["call"])
            kind = self.field_kind(field_class) if field_class is not None else None
        if kind is None:
            return None
        kwargs = {k: v for k, v in value.get("kw", ()) if k}

        def const(key: str) -> object:
            node = kwargs.get(key)
            if node is None:
                return None
            if is_const(node):
                found = const_of(node)
                return value_text(node) if found is NO_CONST else found
            return ("?", value_text(node))

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

    def local_fields(self, ref: EntityId) -> dict[str, FieldDecl]:
        if ref not in self.local_cache:
            self.local_cache[ref] = self.read_local_fields(ref)
        return self.local_cache[ref]

    def read_local_fields(self, ref: EntityId) -> dict[str, FieldDecl]:
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

    def label_target(self, decl: FieldDecl, cls: ModelEntry, text: str) -> EntityId | str:
        found = self.registry.resolve_label(text, cls.app_label, cls.ref)
        key = (str(decl.owner), decl.name)
        if key not in self.label_refs:
            self.label_refs[key] = LabelRef(decl.owner.module, decl.owner.symbol, decl.name, decl.line, text, found)
        if found is None:
            return f"{UNRESOLVED_PREFIX}{text if '.' in text else f'{cls.app_label}.{text}'}"
        return found

    def resolve_target(self, decl: FieldDecl, cls: ModelEntry) -> EntityId | str:
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
        found = self.build.resolver.resolve_class(decl.owner.module, expr)
        if found is not None:
            return found
        return f"{UNRESOLVED_PREFIX}{value_text(expr)}"

    def concrete_parents(self, ref: EntityId) -> list[EntityId]:
        out = []
        for base in self.index.mro(ref)[1:]:
            if self.index.is_model(base) and not self.index.is_abstract(base) and not self.index.is_proxy(base):
                out.append(base)
        return out

    def ptr_fields(self, ref: EntityId) -> list[tuple[str, EntityId]]:
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
            out.append((f"{concrete.symbol.lower()}_ptr", concrete))
        return out

    def record(self, model: ModelEntry, decl: FieldDecl, contributed: ModelEntry, from_parent: bool) -> Relation:
        target_ref = self.resolve_target(decl, contributed)
        resolved = self.registry.target_info(target_ref) if isinstance(target_ref, EntityId) else None
        if resolved is None:
            concrete_target = str(target_ref) if not isinstance(target_ref, EntityId) else str(target_ref)
            if not concrete_target.startswith(UNRESOLVED_PREFIX):
                concrete_target = f"{UNRESOLVED_PREFIX}{concrete_target}"
            target_label = None
            target_module = ""
        else:
            concrete_target = str(resolved[0])
            target_label = resolved[1]
            target_module = resolved[0].module

        class_lower = contributed.ref.symbol.lower()
        app_lower = contributed.app_label.lower()
        related_name = decl.related_name
        if related_name is None:
            related_name = contributed.meta.get("default_related_name")
        query_name = decl.related_query_name
        symmetrical = decl.symmetrical
        if decl.kind == "m2m" and symmetrical is None:
            symmetrical = const_of(decl.target) == RECURSIVE
        if decl.kind == "generic":
            related_name = query_name or "+"
        if decl.kind == "m2m":
            is_self = concrete_target == str(contributed.ref)
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
        if multiple and decl.kind == "m2m" and symmetrical and concrete_target == str(contributed.ref):
            accessor = None
        elif related_name:
            accessor = related_name
        else:
            accessor = class_lower + ("_set" if multiple else "")
        rqn = query_name or related_name or class_lower
        declaring = self.registry.models.get(decl.owner)
        return Relation(
            model=str(model.ref),
            model_label=model.label,
            model_path=model.path,
            model_line=model.line,
            field=decl.name,
            kind=decl.kind,
            declaring_model=str(contributed.ref),
            declared_in=decl.owner.module,
            declared_line=decl.line if declaring is not None else 0,
            concrete_target=concrete_target,
            target_label=target_label,
            target_module=target_module,
            from_abstract=str(decl.owner) if self.index.is_abstract(decl.owner) else None,
            from_concrete_parent=from_parent,
            hidden=hidden,
            accessor_name=None if accessor is None else str(accessor),
            query_name=str(rqn),
            auto_created=False,
            explicit_query_name=query_name if isinstance(query_name, str) else None,
            target_surface=value_text(decl.target) if decl.target is not None else (decl.target_default or ""),
        )

    def ptr_record(
        self, model: ModelEntry, name: str, parent: EntityId, contributed: ModelEntry, from_parent: bool
    ) -> Relation:
        found = self.registry.target_info(parent)
        target_label = found[1] if found is not None else str(parent)
        class_lower = contributed.ref.symbol.lower()
        return Relation(
            model=str(model.ref),
            model_label=model.label,
            model_path=model.path,
            model_line=model.line,
            field=name,
            kind="o2o",
            declaring_model=str(contributed.ref),
            declared_in=contributed.ref.module,
            declared_line=contributed.line,
            concrete_target=str(parent),
            target_label=target_label,
            target_module=parent.module,
            from_abstract=None,
            from_concrete_parent=from_parent,
            hidden=False,
            accessor_name=class_lower,
            query_name=class_lower,
            auto_created=True,
            explicit_query_name=None,
            target_surface=parent.symbol,
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

    def concrete_entries(self) -> list[ModelEntry]:
        return [entry for entry in self.registry.models.values() if not entry.abstract and not entry.proxy]

    def model_relations(self, entry: ModelEntry) -> list[Relation]:
        out = self.fields_of(entry, entry, False)
        for parent in self.concrete_parents(entry.ref):
            parent_entry = self.registry.models.get(parent)
            if parent_entry is None:
                continue
            out.extend(self.fields_of(parent_entry, entry, True))
        return out
