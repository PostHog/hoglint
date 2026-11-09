"""Facade channels (facade-accepts / -returns / -wiring / -logic) from L1 facts.

Port of spikes/f-facade/extract.py, parity mode. Reads only L1 records through l2.core.
"""

from __future__ import annotations

import builtins
from collections import Counter
from dataclasses import dataclass, field
from typing import cast

from hoglint.spike.core import TYPE_ONLY, Core, const_of

# ---- policy (copied from isolation.py / wiring_interfaces.py, as in the spike) ----
REPO_PACKAGES = frozenset({"common", "ee", "posthog", "products"})
GARAGE_PREFIXES = (
    "backend/hogql_queries/",
    "backend/max_tools.py",
    "backend/temporal/",
    "backend/tasks.py",
    "backend/tasks/",
)
MODEL_SOURCE_PREFIXES = ("backend/models/", "backend/models.py")
MODEL_SURFACE_PREFIXES = (*MODEL_SOURCE_PREFIXES, "backend/migrations/")
CAPABILITY_SUBMODULES = frozenset({"dags", "hogql", "max_tools", "models", "queries", "tasks", "temporal"})
NON_CAPABILITY_STEMS = frozenset({"contracts", "enums", "testing"})
UNTYPED_SUBJECT_PARAMS = frozenset({"request", "team", "user"})
LIBRARIES = (
    (("django.db.models", "django.http"), "django"),
    (("rest_framework",), "rest_framework"),
    (("typing", "typing_extensions"), "typing"),
)
APPROVED_WIRING_BASES = frozenset(
    {"ee.hogai.tool.MaxTool", "posthog.hogql_queries.query_runner.QueryRunner", "temporalio.worker.Interceptor"}
)
APPROVED_WIRING_DECORATORS = frozenset({"celery.shared_task", "temporalio.activity.defn", "temporalio.workflow.defn"})
CARVE_OUTS = frozenset(
    {("customer_analytics", "TeamCustomerAnalyticsConfig"), ("tasks", "Task"), ("workflows", "TeamWorkflowsConfig")}
)
MODEL_CROSSINGS = frozenset(
    {
        ("product_analytics", "Insight"),
        ("product_analytics", "InsightVariable"),
        ("warehouse_sources", "DataWarehouseCredential"),
        ("warehouse_sources", "DataWarehouseTable"),
        ("warehouse_sources", "ExternalDataDestination"),
        ("warehouse_sources", "ExternalDataJob"),
        ("warehouse_sources", "ExternalDataSchema"),
        ("warehouse_sources", "ExternalDataSchemaDestination"),
        ("warehouse_sources", "ExternalDataSource"),
        ("warehouse_sources", "ExternalDataSourceDestination"),
        ("warehouse_sources", "PendingSourceCredential"),
        ("warehouse_sources", "WarehouseColumnAnnotation"),
        ("warehouse_sources", "WarehouseColumnStatistics"),
        ("workflows", "HogFlow"),
    }
)
DJANGO_MODEL_ORIGINS = frozenset({"django.db.models.Model", "django.db.models.base.Model"})
DATACLASS_DECORATORS = frozenset({"dataclass", "frozen"})
REEXPORT_DUNDERS = frozenset({"__getattr__", "__dir__"})
SPECIAL_FORMS = frozenset({"Literal", "Annotated"})
MUTATORS = ("append", "extend", "insert", "add", "update")
CLASS_BODY = 8  # context flag of a statement directly in a top-level class body


def last_segment(value: object) -> str | None:
    """Name id or attribute name of a V / SV node (decorator_bare without the call)."""
    if isinstance(value, str):
        return value.rpartition(".")[2]
    if isinstance(value, dict) and "a" in value:
        return value["a"]
    return None


def decorator_bare(value: object) -> str | None:
    if isinstance(value, dict) and "call" in value:
        return last_segment(value["call"])
    return last_segment(value)


@dataclass(frozen=False)
class Binding:
    kind: str  # import | class | func | assign | var
    target: str = ""
    node: object = None  # V value for assign/var, def index for class/func


@dataclass(frozen=False)
class ModuleFacts:
    module: str
    is_package: bool
    record: dict
    bindings: dict[str, Binding] = field(default_factory=dict)
    runtime_imports: list[tuple[str, str, str | None]] = field(default_factory=list)  # (bound, target, asname)
    dunder_all: set[str] | None = None
    lazy: dict[str, str] = field(default_factory=dict)
    lazy_prefixes: list[str] = field(default_factory=list)
    has_functions: bool = False
    special_forms: dict[str, str] = field(default_factory=dict)
    top_defs: list[int] = field(default_factory=list)  # top-level def/class indexes in order

    @property
    def defs(self) -> list[dict]:
        return self.record.get("defs", [])


class ModuleIndex:
    """Dotted module -> ModuleFacts for the repository packages, from L1 records."""

    def __init__(self, core: Core) -> None:
        self.core = core
        self._facts: dict[str, ModuleFacts | None] = {}

    def locate(self, module: str) -> bool | None:
        """is_package for a repo module, None when it does not exist."""
        if module.split(".", 1)[0] not in REPO_PACKAGES:
            return None
        index = self.core.index
        if index.is_file_module(module):
            return index.package[module]
        if module in index.dirs:
            return True  # namespace package
        return None

    def is_module(self, module: str) -> bool:
        return self.locate(module) is not None

    def facts(self, module: str) -> ModuleFacts | None:
        if module in self._facts:
            return self._facts[module]
        is_package = self.locate(module)
        result = None
        if is_package is not None:
            record = self.core.index.record(module) if self.core.index.is_file_module(module) else {"status": "ok"}
            if record is not None and record.get("status") == "ok":
                result = build_facts(module, is_package, record)
        self._facts[module] = result
        return result


def build_facts(module: str, is_package: bool, record: dict) -> ModuleFacts:
    facts = ModuleFacts(module, is_package, record)
    package = module if is_package else module.rpartition(".")[0]
    events: list[tuple[int, int, str, object]] = []
    for row in record.get("imports", ()):
        if row[0] == -1 and row[3] in (0, TYPE_ONLY):
            events.append((row[1], row[2], "import", row))
    for i, d in enumerate(record.get("defs", ())):
        if d["s"] == -1 and not d.get("f"):
            events.append((d["l"], d["c"], "def", i))
    for row in record.get("assigns", ()):
        if row["s"] == -1 and not row.get("f"):
            events.append((row["l"], row["c"], "assign", row))
    events.sort(key=lambda e: (e[0], e[1]))
    defs = record.get("defs", [])
    for _, _, kind, payload in events:
        if kind == "import":
            add_import(facts, package, payload)
        elif kind == "def":
            d = defs[payload]
            facts.top_defs.append(payload)
            if d["k"] == "class":
                facts.bindings[d["n"]] = Binding("class", node=payload)
            else:
                facts.bindings[d["n"]] = Binding("func", node=payload)
                facts.has_functions = True
        else:
            add_assign(facts, payload)
    getattr_binding = facts.bindings.get("__getattr__")
    if getattr_binding is not None and getattr_binding.kind == "func":
        for row in record.get("assigns", ()):
            if row["s"] != -1 or row.get("f") or row["op"] != "=":
                continue
            value = row.get("v")
            if not isinstance(value, dict) or not value.get("dict"):
                continue
            pairs = value["dict"]
            consts = [(const_of(k) if k is not None else None, const_of(v)) for k, v in pairs]
            if all(isinstance(k, str) and isinstance(v, str) for k, v in consts):
                facts.lazy.update(dict(consts))
    return facts


def add_import(facts: ModuleFacts, package: str, row: list) -> None:
    _, _, _, flags, mod, level, name, asname = row
    type_only = flags == TYPE_ONLY
    if name is None:
        if asname:
            facts.bindings[asname] = Binding("import", mod)
        else:
            head = mod.split(".", 1)[0]
            facts.bindings[head] = Binding("import", head)
        return
    if level:
        parts = package.split(".")
        if level - 1 >= len(parts):
            return
        source = ".".join(parts[: len(parts) - (level - 1)])
        if mod:
            source = f"{source}.{mod}"
    else:
        source = mod or ""
    if name == "*":
        return
    bound = asname or name
    target = f"{source}.{name}"
    facts.bindings[bound] = Binding("import", target)
    if source in ("typing", "typing_extensions") and name in SPECIAL_FORMS:
        facts.special_forms[bound] = name
    if not type_only:
        facts.runtime_imports.append((bound, target, asname))


def add_assign(facts: ModuleFacts, row: dict) -> None:
    op = row["op"]
    value = row.get("v")
    if op == "=":
        for target in row["t"]:
            if isinstance(target, str) and "." not in target:
                facts.bindings[target] = Binding("assign", node=value)
                if target == "__all__" and isinstance(value, dict) and ("list" in value or "tuple" in value):
                    elts = value.get("list", value.get("tuple"))
                    facts.dunder_all = {cast(str, const_of(e)) for e in elts if isinstance(const_of(e), str)}
        literal = const_of(value)
        if isinstance(literal, str) and literal.endswith("."):
            facts.lazy_prefixes.append(literal)
    elif op == ":":
        target = row["t"][0]
        if not (isinstance(target, str) and "." not in target) or "v" not in row:
            return
        ann = row.get("ann")
        is_alias = isinstance(ann, str) and (ann == "TypeAlias" or ann.endswith(".TypeAlias"))
        if isinstance(ann, dict) and ann.get("a") == "TypeAlias":
            is_alias = True
        facts.bindings[target] = Binding("assign" if is_alias else "var", node=value)
    elif op == "type":
        target = row["t"][0]
        if isinstance(target, str) and "." not in target:
            facts.bindings[target] = Binding("assign", node=value)


# ---------------------------------------------------------------- resolution
@dataclass(frozen=True)
class Origin:
    kind: str  # class | func | module | assign | external | unresolved | var | nested
    qualified: str
    via: tuple[str, ...] = ()


def is_name(value: object) -> bool:
    return isinstance(value, str) and "." not in value


def is_name_or_attr(value: object) -> bool:
    return isinstance(value, str) or (isinstance(value, dict) and "a" in value)


def qualify_name(name: str, facts: ModuleFacts) -> str | None:
    b = facts.bindings.get(name)
    if b is None:
        return f"builtins.{name}" if hasattr(builtins, name) else None
    if b.kind == "import":
        return b.target
    return f"{facts.module}.{name}"


def qualify_expr(value: object, facts: ModuleFacts) -> str | None:
    """The qualified name a V value names, in the namespace of one module."""
    if isinstance(value, str):
        head, _, rest = value.partition(".")
        q = qualify_name(head, facts)
        if q is None:
            return None
        return f"{q}.{rest}" if rest else q
    if not isinstance(value, dict):
        return None
    if "call" in value:
        return qualify_expr(value["call"], facts)
    if "sub" in value:
        return qualify_expr(value["sub"], facts)
    if "a" in value:
        head = qualify_expr(value["v"], facts)
        return f"{head}.{value['a']}" if head else None
    return None


class Resolver:
    def __init__(self, index: ModuleIndex) -> None:
        self.index = index
        self._cache: dict[str, Origin] = {}

    def split(self, qualified: str) -> tuple[str, str] | None:
        parts = qualified.split(".")
        for i in range(len(parts), 0, -1):
            module = ".".join(parts[:i])
            if self.index.is_module(module):
                return module, ".".join(parts[i:])
        return None

    def resolve(self, qualified: str, seen: frozenset[str] = frozenset()) -> Origin:
        if qualified in self._cache:
            return self._cache[qualified]
        result = self._resolve(qualified, seen)
        self._cache[qualified] = result
        return result

    def _resolve(self, qualified: str, seen: frozenset[str]) -> Origin:
        if qualified in seen:
            return Origin("unresolved", qualified)
        if qualified.split(".", 1)[0] not in REPO_PACKAGES:
            return Origin("external", qualified)
        split = self.split(qualified)
        if split is None:
            return Origin("unresolved", qualified)
        module, rest = split
        if not rest:
            return Origin("module", module)
        facts = self.index.facts(module)
        if facts is None:
            return Origin("unresolved", qualified)
        head, _, tail = rest.partition(".")
        binding = facts.bindings.get(head)
        if binding is None and head in facts.lazy:
            target = self.lazy_target(facts, head)
            if target is not None:
                o = self.resolve(f"{target}.{head}" + (f".{tail}" if tail else ""), seen | {qualified})
                return Origin(o.kind, o.qualified, (qualified, *o.via))
        if binding is None:
            return Origin("unresolved", qualified)
        if binding.kind == "import":
            o = self.resolve(binding.target + (f".{tail}" if tail else ""), seen | {qualified})
            return Origin(o.kind, o.qualified, (qualified, *o.via))
        if binding.kind == "assign" and is_name_or_attr(binding.node) and not tail:
            target = qualify_expr(binding.node, facts)
            if target:
                o = self.resolve(target, seen | {qualified})
                return Origin(o.kind, o.qualified, (qualified, *o.via))
        if tail:
            return Origin("nested", f"{module}.{head}.{tail}")
        return Origin(binding.kind, f"{module}.{head}")

    def lazy_target(self, facts: ModuleFacts, name: str) -> str | None:
        dotted = facts.lazy[name]
        parts = facts.module.split(".")
        backend_relative = [".".join(parts[:3]) + "." + dotted] if parts[0] == "products" and len(parts) > 3 else []
        for candidate in (dotted, *(p + dotted for p in facts.lazy_prefixes), *backend_relative):
            if self.index.is_module(candidate):
                return candidate
        return None


# ---------------------------------------------------------------- annotations
def annotation_names(value: object, facts: ModuleFacts, expanding: frozenset[str] = frozenset()) -> list[str]:
    """Every qualified name a typed V value names, aliases expanded in the defining module."""
    if value is None or isinstance(value, (bool, int)):
        return []
    if isinstance(value, str):
        if "." not in value:
            b = facts.bindings.get(value)
            if b is not None and b.kind == "assign" and value not in expanding and is_alias_shape(b.node):
                return annotation_names(b.node, facts, expanding | {value})
        q = qualify_expr(value, facts)
        return [q] if q else []
    if not isinstance(value, dict):
        return []
    if "s" in value:
        parsed = value.get("sa")
        return annotation_names(parsed, facts, expanding) if parsed is not None else []
    if "a" in value:
        q = qualify_expr(value, facts)
        return [q] if q else []
    if "sub" in value:
        head_value = value["sub"]
        form = None
        if is_name(head_value):
            form = facts.special_forms.get(head_value) or (head_value if head_value in SPECIAL_FORMS else None)
        elif last_segment(head_value) in SPECIAL_FORMS:
            form = last_segment(head_value)
        head = annotation_names(head_value, facts, expanding)
        if form == "Literal":
            return head
        index = value["i"]
        if form == "Annotated":
            first = index["tuple"][0] if isinstance(index, dict) and index.get("tuple") else index
            return head + annotation_names(first, facts, expanding)
        return head + annotation_names(index, facts, expanding)
    if "bin" in value:
        return annotation_names(value["l"], facts, expanding) + annotation_names(value["r"], facts, expanding)
    if "tuple" in value or "list" in value:
        return [n for e in value.get("tuple", value.get("list")) for n in annotation_names(e, facts, expanding)]
    return []


def is_alias_shape(value: object) -> bool:
    """Name, Attribute, Subscript or BinOp node."""
    return isinstance(value, str) or (isinstance(value, dict) and ("a" in value or "sub" in value or "bin" in value))


# ---------------------------------------------------------------- kind projection
class Kinds:
    def __init__(self, index: ModuleIndex, resolver: Resolver) -> None:
        self.index = index
        self.resolver = resolver
        self._model: dict[str, bool] = {}
        self._wiring: dict[str, str] = {}

    def class_node(self, qualified: str) -> tuple[int, ModuleFacts] | None:
        module, _, name = qualified.rpartition(".")
        facts = self.index.facts(module)
        if facts is None:
            return None
        b = facts.bindings.get(name)
        if b is None or b.kind != "class":
            return None
        return cast(int, b.node), facts

    def is_model(self, qualified: str) -> bool:
        if qualified in self._model:
            return self._model[qualified]
        self._model[qualified] = False
        found = self.class_node(qualified)
        result = False
        if found is not None:
            idx, facts = found
            result = self.reaches_model_bases(idx, facts, 1) and not is_abstract(facts, idx)
        self._model[qualified] = result
        return result

    def reaches_model_bases(self, idx: int, facts: ModuleFacts, depth: int) -> bool:
        for base in facts.defs[idx].get("bases", ()):
            q = qualify_expr(base, facts)
            if q is None:
                continue
            o = self.resolver.resolve(q)
            if o.kind == "external" and o.qualified in DJANGO_MODEL_ORIGINS:
                return True
            if o.kind == "class" and self.reaches_model(o.qualified, depth + 1):
                return True
        return False

    def reaches_model(self, qualified: str, depth: int) -> bool:
        found = self.class_node(qualified)
        if found is None or depth > 20:
            return False
        return self.reaches_model_bases(found[0], found[1], depth)

    def wiring_verdict(self, qualified: str, seen: frozenset[str] = frozenset()) -> str:
        if qualified in self._wiring:
            return self._wiring[qualified]
        found = self.class_node(qualified)
        if found is None:
            return "unresolved"
        idx, facts = found
        for dec in facts.defs[idx].get("dec", ()):
            q = qualify_expr(dec, facts)
            if q and self.approved_name(q, APPROVED_WIRING_DECORATORS):
                self._wiring[qualified] = "approved"
                return "approved"
        verdict = self.bases_verdict(idx, facts, seen | {qualified})
        self._wiring[qualified] = verdict
        return verdict

    def approved_name(self, q: str, approved: frozenset[str]) -> bool:
        if q in approved:
            return True
        o = self.resolver.resolve(q)
        return o.qualified in approved or any(v in approved for v in o.via)

    def bases_verdict(self, idx: int, facts: ModuleFacts, seen: frozenset[str]) -> str:
        verdicts = []
        for base in facts.defs[idx].get("bases", ()):
            q = qualify_expr(base, facts)
            if q is None:
                verdicts.append("unresolved")
                continue
            if self.approved_name(q, APPROVED_WIRING_BASES):
                return "approved"
            o = self.resolver.resolve(q)
            if o.kind == "class":
                if o.qualified in seen:
                    verdicts.append("unresolved")
                    continue
                found = self.class_node(o.qualified)
                if found is None:
                    verdicts.append("unresolved")
                    continue
                verdicts.append(self.bases_verdict(found[0], found[1], seen | {o.qualified}))
            elif o.kind == "external":
                verdicts.append("unapproved")
            else:
                verdicts.append("unresolved")
        if "approved" in verdicts:
            return "approved"
        if "unresolved" in verdicts:
            return "unresolved"
        return "unapproved"


def direct_children(facts: ModuleFacts, idx: int) -> list[int]:
    """Defs directly in the body of def `idx` (not under an if/try/with inside it)."""
    body_flags = facts.defs[idx].get("f", 0) | CLASS_BODY
    return [i for i, d in enumerate(facts.defs) if d["s"] == idx and d.get("f", 0) == body_flags]


def direct_assigns(facts: ModuleFacts, idx: int) -> list[dict]:
    body_flags = facts.defs[idx].get("f", 0) | CLASS_BODY
    return [a for a in facts.record.get("assigns", ()) if a["s"] == idx and a.get("f", 0) == body_flags]


def is_abstract(facts: ModuleFacts, idx: int) -> bool:
    for child in direct_children(facts, idx):
        d = facts.defs[child]
        if d["k"] != "class" or d["n"] != "Meta":
            continue
        for row in direct_assigns(facts, child):
            if row["op"] == "=" and "abstract" in row["t"] and row.get("v") is True:
                return True
    return False


def has_wiring_decorator(d: dict) -> bool:
    for dec in d.get("dec", ()):
        if decorator_bare(dec) == "shared_task":
            return True
        target = dec["call"] if isinstance(dec, dict) and "call" in dec else dec
        if isinstance(target, str) and target.count(".") == 1 and target.endswith(".defn"):
            return True
    return False


# ---------------------------------------------------------------- facade channels
@dataclass(frozen=True)
class Denied:
    source: str
    type_name: str


class FacadeScanner:
    def __init__(self, core: Core, wiring_products: frozenset[str] | None) -> None:
        self.core = core
        self.index = ModuleIndex(core)
        self.resolver = Resolver(self.index)
        self.kinds = Kinds(self.index, self.resolver)
        self.wiring_products = wiring_products
        self.lines: Counter[str] = Counter()

    @staticmethod
    def product_of(module: str) -> str | None:
        parts = module.split(".")
        if len(parts) >= 3 and parts[0] == "products" and parts[2] == "backend":
            return parts[1]
        return None

    @staticmethod
    def backend_rel(module: str, is_package: bool) -> str:
        parts = module.split(".")[3:]
        if not parts:
            return "backend/"
        return "backend/" + "/".join(parts) + ("/" if is_package else ".py")

    def surface_rel(self, module: str) -> str | None:
        is_package = self.index.locate(module)
        if is_package is None:
            return None
        return self.backend_rel(module, is_package)

    def denied(self, origin: Origin, surface: str, product: str) -> Denied | None:
        if origin.kind == "external" or surface.split(".")[0] not in REPO_PACKAGES:
            q = origin.qualified if origin.kind == "external" else surface
            for prefixes, lib in LIBRARIES:
                if any(q == p or q.startswith(p + ".") for p in prefixes):
                    name = q.rpartition(".")[2]
                    if lib == "typing":
                        return Denied("typing", "Any") if name == "Any" else None
                    return Denied(lib, name)
            return None
        if origin.kind != "class":
            return None
        owner = self.product_of(origin.qualified)
        if owner is None or not self.kinds.is_model(origin.qualified):
            return None
        rel = surface.rpartition(".")[0].split(".")[3:]
        if rel[:1] == ["facade"]:
            rel = rel[1:]
        if not rel or rel[0] != "models":
            return None
        name = origin.qualified.rpartition(".")[2]
        if owner == product and ((product, name) in CARVE_OUTS or (product, name) in MODEL_CROSSINGS):
            return None
        return Denied(owner, name)

    def denied_in(self, annotation: object, facts: ModuleFacts, product: str) -> list[Denied]:
        found: dict[tuple[str, str], Denied] = {}
        for surface in annotation_names(annotation, facts):
            in_repo = surface.split(".")[0] in REPO_PACKAGES
            if in_repo and self.product_of(surface) is None:
                continue
            origin = self.resolver.resolve(surface) if in_repo else Origin("external", surface)
            d = self.denied(origin, surface, product)
            if d is not None:
                found.setdefault((d.source, d.type_name), d)
        return list(found.values())

    def is_whole_any(self, annotation: object, facts: ModuleFacts) -> bool:
        names = annotation_names(annotation, facts)
        return (
            len(names) == 1
            and names[0].rpartition(".")[2] == "Any"
            and names[0].startswith(("typing.", "typing_extensions."))
        )

    def emit_signature(self, d: dict, facts: ModuleFacts, product: str, dotted: str, symbol: str) -> None:
        ret = d.get("ret")
        for den in self.denied_in(ret, facts, product):
            if den == Denied("typing", "Any") and not self.is_whole_any(ret, facts):
                continue
            self.lines[f"{den.source}.{den.type_name} {dotted}.{symbol} facade-returns"] += 1
        for param in d.get("params", ()):
            name = param[0]
            if name in ("self", "cls"):
                continue
            annotation = param[2] if len(param) > 2 else None
            self.emit_accepts(annotation, facts, product, dotted, symbol, name)

    def emit_accepts(
        self, annotation: object, facts: ModuleFacts, product: str, dotted: str, symbol: str, param: str
    ) -> None:
        for den in self.denied_in(annotation, facts, product):
            if den == Denied("typing", "Any") and param not in UNTYPED_SUBJECT_PARAMS:
                continue
            self.lines[f"{den.source}.{den.type_name} {dotted}.{symbol} facade-accepts({param})"] += 1

    def handed_out(self, facts: ModuleFacts, product: str) -> list[tuple[str, str, str]]:
        pure = not facts.has_functions
        allowed = None if pure else facts.dunder_all
        out = []
        for bound, target, asname in facts.runtime_imports:
            if self.product_of(target) != product:
                continue
            original = target.rpartition(".")[2]
            if not (pure or (allowed is not None and bound in allowed) or asname == original):
                continue
            mod = target.rpartition(".")[0]
            rel = self.surface_rel(target) if self.index.is_module(target) else self.surface_rel(mod)
            if rel is None:
                continue
            out.append((bound, target, rel))
        for name in facts.lazy:
            t = self.resolver.lazy_target(facts, name)
            if t is not None and self.product_of(t) == product:
                rel = self.surface_rel(t)
                if rel:
                    out.append((name, f"{t}.{name}", rel))
        return out

    def facade_files(self) -> dict[str, list[str]]:
        """product -> facade-relative keys, like rglob over products/<p>/backend/facade."""
        out: dict[str, list[str]] = {}
        for path in self.core.repo_paths():
            parts = path.split("/")
            if len(parts) < 5 or parts[0] != "products" or parts[2] != "backend" or parts[3] != "facade":
                continue
            name = parts[-1]
            if not name.endswith(".py") or name.startswith("test_") or name.endswith("_test.py"):
                continue
            out.setdefault(parts[1], []).append("/".join(parts[4:]))
        return {p: sorted(keys) for p, keys in out.items()}

    def scan_product(self, product: str, keys: list[str]) -> None:
        for key in keys:
            mparts = ["products", product, "backend", "facade", *key.removesuffix(".py").split("/")]
            if mparts[-1] == "__init__":
                mparts.pop()
            dotted = ".".join(mparts)
            facts = self.index.facts(dotted)
            if facts is None:
                continue
            self.scan_signatures(facts, product, dotted)
            handed = self.handed_out(facts, product)
            self.scan_reexports(facts, product, dotted, handed)
            if self.is_capability(key, handed):
                self.scan_logic(facts, product, key, dotted)
            if key not in ("contracts.py", "enums.py"):
                self.scan_wiring(product, dotted, handed)

    def scan_signatures(self, facts: ModuleFacts, product: str, dotted: str) -> None:
        for idx in facts.top_defs:
            d = facts.defs[idx]
            if d["n"].startswith("_"):
                continue
            if d["k"] != "class":
                self.emit_signature(d, facts, product, dotted, d["n"])
                continue
            for child in direct_children(facts, idx):
                c = facts.defs[child]
                if c["k"] != "class" and (not c["n"].startswith("_") or c["n"] == "__init__"):
                    self.emit_signature(c, facts, product, dotted, f"{d['n']}.{c['n']}")
            if any(decorator_bare(dec) in DATACLASS_DECORATORS for dec in d.get("dec", ())):
                for row in direct_assigns(facts, idx):
                    target = row["t"][0]
                    if row["op"] != ":" or not is_name(target):
                        continue
                    ann = row.get("ann")
                    if isinstance(ann, dict) and "sub" in ann and decorator_bare(ann["sub"]) == "ClassVar":
                        continue
                    self.emit_accepts(ann, facts, product, dotted, f"{d['n']}.__init__", target)

    def scan_reexports(self, facts: ModuleFacts, product: str, dotted: str, handed: list[tuple[str, str, str]]) -> None:
        defined = {facts.defs[i]["n"] for i in facts.top_defs}
        seen = set()
        for bound, target, rel in sorted(set(handed)):
            if bound.startswith("_") or bound in defined or rel.startswith("backend/facade/"):
                continue
            origin = self.resolver.resolve(target)
            if origin.kind != "func":
                continue
            owner = self.product_of(origin.qualified)
            origin_mod = origin.qualified.rpartition(".")[0]
            if owner != product:
                continue
            if self.backend_rel(origin_mod, False).startswith("backend/facade/") or origin_mod.split(".")[3:4] == [
                "facade"
            ]:
                continue
            if len(origin.via) > 6:
                continue
            if (bound, origin.qualified) in seen:
                continue
            seen.add((bound, origin.qualified))
            src = self.index.facts(origin_mod)
            if src is None:
                continue
            binding = src.bindings[origin.qualified.rpartition(".")[2]]
            if binding.kind == "func":
                self.emit_signature(src.defs[binding.node], src, product, dotted, bound)

    def is_capability(self, key: str, handed: list[tuple[str, str, str]]) -> bool:
        if "/" not in key:
            stem = key.removesuffix(".py")
            if stem in CAPABILITY_SUBMODULES:
                return True
            if stem.startswith("api") or stem in NON_CAPABILITY_STEMS:
                return False
        return any(rel.startswith((*GARAGE_PREFIXES, *MODEL_SURFACE_PREFIXES)) for _, _, rel in handed)

    def scan_logic(self, facts: ModuleFacts, product: str, key: str, dotted: str) -> None:
        bodies = 0
        for idx in facts.top_defs:
            d = facts.defs[idx]
            if d["n"] in REEXPORT_DUNDERS:
                continue
            if has_wiring_decorator(d) or not d.get("stub"):
                bodies += 1
        if bodies:
            self.lines[f"{product}:backend/facade/{key} {dotted} facade-logic"] += bodies

    def scan_wiring(self, product: str, dotted: str, handed: list[tuple[str, str, str]]) -> None:
        if self.wiring_products is not None and product not in self.wiring_products:
            return
        found: dict[str, str] = {}
        for bound, target, rel in handed:
            if not rel.startswith(GARAGE_PREFIXES):
                continue
            origin = self.resolver.resolve(target)
            if origin.kind == "class":
                exported = {bound: origin.qualified}
            else:
                members = self.collection_members(target)
                if members is None:
                    found[bound] = "unresolved"
                    continue
                exported = {}
                for m in members:
                    o = self.resolver.resolve(m)
                    if o.kind == "class":
                        exported[m.rpartition(".")[2]] = o.qualified
            for name, q in exported.items():
                verdict = self.kinds.wiring_verdict(q)
                if verdict != "approved":
                    found[name] = verdict
        for name in found:
            self.lines[f"{product}.{name} {dotted} facade-wiring"] += 1

    def collection_members(self, qualified: str, seen: frozenset[str] = frozenset()) -> list[str] | None:
        if qualified in seen:
            return []
        split = self.resolver.split(qualified)
        if split is None or not split[1]:
            return []
        module, name = split
        facts = self.index.facts(module)
        if facts is None:
            return []
        b = facts.bindings.get(name)
        if b is None:
            return []
        if b.kind == "import":
            return self.collection_members(b.target, seen | {qualified})
        if b.kind not in ("assign", "var"):
            return []
        if mutated(facts, name):
            return None
        return self.expr_members(b.node, facts, seen | {qualified})

    def expr_members(self, value: object, facts: ModuleFacts, seen: frozenset[str]) -> list[str] | None:
        if isinstance(value, dict) and ("list" in value or "tuple" in value or "set" in value):
            out: list[str] = []
            for e in value.get("list", value.get("tuple", value.get("set"))):
                if isinstance(e, dict) and "star" in e:
                    sub = self.expr_members(e["star"], facts, seen)
                    if sub is None:
                        return None
                    out += sub
                else:
                    q = qualify_expr(e, facts)
                    if q:
                        out.append(q)
            return out
        if isinstance(value, dict) and value.get("bin") == "+":
            left = self.expr_members(value["l"], facts, seen)
            right = self.expr_members(value["r"], facts, seen)
            return None if left is None or right is None else left + right
        if is_name_or_attr(value):
            q = qualify_expr(value, facts)
            return self.collection_members(q, seen) if q else []
        return []

    def run(self) -> None:
        for product, keys in sorted(self.facade_files().items()):
            self.scan_product(product, keys)


def mutated(facts: ModuleFacts, name: str) -> bool:
    """Module-level `name.append(...)`-style calls or `name += ...` (callees over-approximate statements)."""
    wanted = {f"{name}.{m}" for m in MUTATORS}
    for row in facts.record.get("callees", ()):
        if row[0] == -1 and row[1] == -1 and row[2] in wanted:
            return True
    for row in facts.record.get("assigns", ()):
        if row["s"] == -1 and not row.get("f") and row["op"] not in ("=", ":", "type") and row["t"] == [name]:
            return True
    return False


def run(core: Core, wiring_products: frozenset[str] | None = None) -> dict:
    """Facade ledger lines. `wiring_products` = Isolated products (rung filter for facade-wiring)."""
    scanner = FacadeScanner(core, wiring_products)
    scanner.run()
    return {"ledger": sorted(f"{k} {v}" for k, v in scanner.lines.items())}
