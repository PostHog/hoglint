"""Channels D + G: get_model, write, lock and the use-shape kinds of crossing model classes.

Today's scanner semantics (spikes d-ledger and g-shapes), read from `refs` (chain + up),
`calls`, `imports`, `assigns` and `defs` facts.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import cast

from hoglint.spike.core import SITE_MARKER, Core, const_of, module_of_path

SCANNED_ROOTS = ("posthog", "ee", "products", "common")
ISOLATION_PATH = "tools/hogli-commands/hogli_commands/product/isolation.py"

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
ALLOWED = ("annotation", "exception", "nested-class-attr", "_meta", "scalar-chain", "subquery")

# AST class of the parent named by the first escape step (for `other(<class>)`).
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
ANNOTATION_TERMINALS = ("param-ann:", "returns", "annotation")


@dataclass(frozen=False)
class Edge:
    kind: str  # model-label | reference
    module: str
    path: str
    line: int
    surface: str
    targets: list[str]
    resolution: str
    shape_kind: str | None = None
    shape_detail: str | None = None
    via: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ImportEdge:
    module: str
    exported: str
    bound: str


@dataclass(frozen=True)
class FileInfo:
    path: str
    module: str
    imports: tuple[ImportEdge, ...]
    aliases: tuple[tuple[str, str], ...]  # (alias, module)


def call_name(callee: str) -> str | None:
    """Callee name as `func.id` / `func.attr` (None when the callee is not a Name or Attribute)."""
    if callee == "?":
        return None
    return callee.rsplit(".", 1)[-1]


def step_parent(step: str) -> str:
    if step.startswith("?"):
        return step[1:].split(".", 1)[0]
    if step in STEP_PARENT:
        return STEP_PARENT[step]
    head = step.split(":", 1)[0]
    if step.startswith("comp:"):
        return {"comp:elt": "ListComp"}.get(step, STEP_PARENT.get(step, "comprehension"))
    return STEP_PARENT.get(head, head)


def split_chain(chain: str) -> list[str]:
    """`.a.b()[]` -> ['.a', '.b', '()', '[]']."""
    out: list[str] = []
    i = 0
    while i < len(chain):
        if chain[i] == ".":
            j = i + 1
            while j < len(chain) and chain[j] not in ".([":
                j += 1
            out.append(chain[i:j])
            i = j
        else:
            out.append(chain[i : i + 2])
            i += 2
    return out


class Use:
    """One tracked use: trailers above the used node, then the escape steps."""

    def __init__(self, trailers: list[str], up: list[str]) -> None:
        self.trailers = trailers
        self.up = up
        methods: list[str] = []
        rest_at = len(trailers)
        for i, t in enumerate(trailers):
            if t == "[]":
                rest_at = i
                break
            if t != "()":
                methods.append(t[1:])
        self.methods = methods
        self.rest = trailers[rest_at:]

    def enclosing_call(self) -> str | None:
        """First Call above the top of the attribute/call chain with a Name or Attribute func."""
        previous = None
        for t in self.rest:
            if t == "()" and previous is not None and previous.startswith("."):
                return previous[1:]
            previous = t
        previous = self.rest[-1] if self.rest else None
        for step in self.up:
            if step.startswith(("arg:", "kw:")):
                name = call_name(step.split(":", 2)[2])
                if name:
                    return name
            elif step == "()" and previous is not None and previous.startswith("."):
                return previous[1:]
            elif step.startswith("classkw:"):
                return None
            previous = step
        return None

    def in_annotation(self) -> bool:
        if any(t == "()" for t in self.trailers):
            return False
        for step in self.up:
            if step.startswith(ANNOTATION_TERMINALS):
                return True
            if step in ("slice", "elt:tuple", "[]") or step.startswith(("bin:", ".")):
                continue
            return False
        return False

    def manager_chain(self, methods: list[str]) -> tuple[str, str | None]:
        enclosing = self.enclosing_call()
        terminal = methods[-1] if methods else "manager"
        if "select_for_update" in methods:
            return "lock", None
        writes = [m for m in methods if m in WRITE_METHODS]
        if writes:
            return "write", ",".join(writes)
        if enclosing == "Prefetch":
            return "prefetch", None
        if enclosing in SUBQUERY_CALLS:
            return "subquery", enclosing
        if terminal in SCALAR_TERMINALS:
            return "scalar-chain", terminal
        if enclosing is not None and enclosing.endswith("Field"):
            return "drf-field-queryset", None
        if terminal in SINGLE_TERMINALS:
            return "instance-single", None
        return "instance-many", terminal

    def attribute_chain(self, methods: list[str]) -> tuple[str, str | None]:
        head = methods[0]
        if head == "DoesNotExist":
            return "exception", None
        if head == "_meta":
            if len(methods) == 1:
                return "_meta", None
            if methods[1] in META_MANAGERS:
                return self.manager_chain(methods[2:])
            if methods[1] in META_CLASS_ATTRS:
                return self.attribute_chain(methods[2:]) if len(methods) > 2 else ("_meta", None)
            return ("_meta", None) if methods[1] != "managers" else ("other", "Attribute:_meta.managers")
        if head in MANAGERS:
            return self.manager_chain(methods[1:])
        if head[:1].isupper():
            return "nested-class-attr", head
        return "other", f"Attribute:{head}"

    def classify(self) -> tuple[str, str | None]:
        if self.in_annotation():
            return "annotation", None
        if self.methods:
            return self.attribute_chain(self.methods)
        if self.trailers:
            # Parent is the Call that calls the node, or the Subscript that indexes it.
            return ("construct", None) if self.trailers[0] == "()" else ("in-collection", None)
        step = self.up[0] if self.up else "?"
        if step.startswith("arg:"):
            callee = step.split(":", 2)[2]
            name = "?" if callee.startswith("?") else callee.rsplit(".", 1)[-1]
            return ("isinstance", None) if name == "isinstance" else ("passed-as-class", name)
        if step.startswith("assign:") and "model" in step[len("assign:") :].split(","):
            return "drf-model-serializer", None
        if step.startswith(("kw:model:", "classkw:model")) and step.split(":")[1] == "model":
            return "drf-model-serializer", None
        if step.startswith(("elt:", "dict:")) or step == "slice":
            return "in-collection", None
        return "other", step_parent(step)


def resolve_from(module: str | None, level: int, package: str) -> str | None:
    if not level:
        return module
    parts = package.split(".")
    climb = level - 1
    if climb:
        parts = parts[:-climb]
    if not parts:
        return None
    base = ".".join(parts)
    return f"{base}.{module}" if module else base


def is_test(path: str) -> bool:
    parts = path.split("/")
    name = parts[-1]
    return (
        "test" in parts
        or "tests" in parts
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
    )


def owner_of(path: str) -> str | None:
    parts = path.split("/")
    return parts[1] if parts[0] == "products" and len(parts) > 2 else None


class Extractor:
    def __init__(self, core: Core) -> None:
        self.core = core
        self.records = core.store.records
        self.files: list[FileInfo] = []
        self.app_labels: dict[str, str] = {}
        self.model_by_lower: dict[str, str] = {}
        self.crossings: dict[tuple[str, str], str] = {}
        self.edges: list[Edge] = []
        self.incomplete: Counter[str] = Counter()

    def inventory(self) -> None:
        for path in sorted(self.records):
            if SITE_MARKER in path or path.split("/", 1)[0] not in SCANNED_ROOTS:
                continue
            record = self.records[path]
            module, is_package = module_of_path(path)
            edges: list[ImportEdge] = []
            aliases: list[tuple[str, str]] = []
            for _, _, _, _, mod, level, name, asname in record.get("imports", ()):
                if name is None:
                    aliases.append((asname or mod, mod))
                    continue
                source = resolve_from(mod, level, module if is_package else module.rsplit(".", 1)[0])
                if source is None:
                    continue
                bound = asname or name
                edges.append(ImportEdge(source, name, bound))
                aliases.append((bound, f"{source}.{name}"))
            self.files.append(FileInfo(path, module, tuple(edges), tuple(aliases)))

    def read_config(self) -> None:
        for path, record in sorted(self.records.items()):
            parts = path.split("/")
            if len(parts) != 4 or parts[0] != "products" or parts[2:] != ["backend", "apps.py"]:
                continue
            for row in record.get("assigns", ()):
                if row["op"] == "=" and row["t"][0] == "label" and isinstance(row.get("v"), dict) and "s" in row["v"]:
                    self.app_labels[str(row["v"]["s"])] = parts[1]
        under_models: dict[str, list[str]] = {}
        for path in sorted(self.records):
            parts = path.split("/")
            if len(parts) > 4 and parts[0] == "products" and parts[2:4] == ["backend", "models"]:
                under_models.setdefault(parts[1], []).append(path)
        model_modules: dict[str, list[tuple[str, dict]]] = {}
        for product in sorted(set(self.app_labels.values())):
            backend = f"products/{product}/backend/"
            paths = [backend + "models.py"] if backend + "models.py" in self.records else []
            paths += under_models.get(product, [])
            for path in paths:
                record = self.records[path]
                if record.get("status") == "ok":
                    model_modules.setdefault(product, []).append((module_of_path(path)[0], record))
        for product, modules in model_modules.items():
            for _, record in modules:
                for d in record.get("defs", ()):
                    if d["s"] == -1 and d["k"] == "class" and not d.get("f") and d.get("bases"):
                        label = f"{product}.{d['n']}"
                        self.model_by_lower[label.lower()] = label
        for product, class_name in self.read_model_crossings():
            for module, record in model_modules.get(product, []):
                if any(
                    d["s"] == -1 and d["k"] == "class" and not d.get("f") and d["n"] == class_name
                    for d in record.get("defs", ())
                ):
                    self.crossings[(module, class_name)] = f"{product}.{class_name}"
                    break

    def read_model_crossings(self) -> list[tuple[str, str]]:
        for row in self.records[ISOLATION_PATH].get("assigns", ()):
            if row["s"] == -1 and "MODEL_CROSSINGS" in row["t"]:
                value = row["v"]["args"][0] if "call" in row["v"] else row["v"]
                elts = value.get("set") or value.get("list") or value.get("tuple")
                return [cast(tuple[str, str], tuple(const_of(e) for e in elt["tuple"])) for elt in elts]
        raise RuntimeError("MODEL_CROSSINGS not found")

    def grow_origins(self) -> dict[tuple[str, str], tuple[str, list[str]]]:
        origins = {key: (label, []) for key, label in self.crossings.items()}
        while True:
            grew = False
            for info in self.files:
                for edge in info.imports:
                    found = origins.get((edge.module, edge.exported))
                    if found is None:
                        continue
                    local = (info.module, edge.bound)
                    if local not in origins or origins[local][0] != found[0]:
                        origins[local] = (found[0], [f"{edge.module}.{edge.exported}", *found[1]])
                        grew = True
            if not grew:
                return origins

    def run(self) -> None:
        self.inventory()
        self.read_config()
        origins = self.grow_origins()
        origin_modules = {module for module, _ in origins}
        for info in self.files:
            if is_test(info.path) or "migrations" in info.path.split("/"):
                continue
            record = self.records[info.path]
            if record.get("status") != "ok":
                continue
            names = {
                e.bound: origins[(e.module, e.exported)] for e in info.imports if (e.module, e.exported) in origins
            }
            aliases = {a: m for a, m in info.aliases if m in origin_modules}
            owner = owner_of(info.path)
            if names or aliases:
                self.reference_edges(info, record, names, aliases, origins, owner)
            self.get_model_edges(info, record, owner)

    def reference_edges(
        self, info: FileInfo, record: dict, names: dict, aliases: dict, origins: dict, owner: str | None
    ) -> None:
        for row in record.get("refs", ()):
            if len(row) > 7:
                continue  # local-derived rows are not today's scanner's uses
            _, _, line, _, root, chain, up = row
            trailers = split_chain(chain)
            hits: list[tuple[tuple[str, list[str]], str, int]] = []
            if root in names:
                hits.append((names[root], root, 0))
            if aliases:
                dotted = root
                for i, t in enumerate(trailers):
                    if not t.startswith("."):
                        break
                    module = aliases.get(dotted)
                    if module and (module, t[1:]) in origins:
                        hits.append((origins[(module, t[1:])], f"{module}.{t[1:]}", i + 1))
                    dotted += t
            for (label, via), surface, cut in hits:
                if label.split(".")[0] == owner:
                    continue
                kind, detail = Use(trailers[cut:], up.split()).classify()
                self.edges.append(
                    Edge("reference", info.module, info.path, line, surface, [label], "resolved", kind, detail, via)
                )
        self.count_untracked(record, aliases, origins)

    def count_untracked(self, record: dict, aliases: dict, origins: dict) -> None:
        """Crossing-class uses that L1 only counts (`uses` rows have no chain or up): `import a.b` as `a.b.Class`."""
        for _, _, root, attrs, count, _, _ in record.get("uses", ()):
            dotted = root
            for part in attrs.split(".")[1:]:
                module = aliases.get(dotted)
                if module and (module, part) in origins:
                    self.incomplete[f"{record['path']}:{root}{attrs}"] += count
                    break
                dotted += "." + part

    def get_model_edges(self, info: FileInfo, record: dict, owner: str | None) -> None:
        for row in record.get("calls", ()):
            callee, args, kws = row[4], row[5], row[6]
            if "." not in callee or callee.rsplit(".", 1)[-1] != "get_model":
                continue
            literal = [a["s"] for a in args if isinstance(a, dict) and "s" in a and len(a) == 1]
            kw = {k: v["s"] for k, v in kws if k in ("app_label", "model_name") and isinstance(v, dict) and "s" in v}
            app_label = kw.get("app_label", literal[0] if literal else None)
            class_name = kw.get("model_name", literal[1] if len(literal) > 1 else None)
            if app_label is not None and class_name is None and "." in app_label:
                app_label, class_name = app_label.split(".", 1)
            if app_label is None or class_name is None:
                self.edges.append(
                    Edge("model-label", info.module, info.path, row[2], callee, [], "unresolved", "get_model")
                )
                continue
            product = self.app_labels.get(app_label)
            label = self.model_by_lower.get(f"{product}.{class_name}".lower()) if product else None
            if label is None:
                self.edges.append(
                    Edge(
                        "model-label",
                        info.module,
                        info.path,
                        row[2],
                        callee,
                        [f"{app_label}.{class_name}"],
                        "external",
                        "get_model",
                    )
                )
                continue
            if product == owner:
                continue
            self.edges.append(
                Edge("model-label", info.module, info.path, row[2], callee, [label], "resolved", "get_model")
            )


def kind_text(edge: Edge) -> str:
    return f"{edge.shape_kind}({edge.shape_detail})" if edge.shape_detail else (edge.shape_kind or "")


def is_ledger_kind(kind: str) -> bool:
    return not kind.startswith(ALLOWED) and kind != "prefetch"


def run(core: Core) -> dict:
    extractor = Extractor(core)
    extractor.run()
    counts: Counter[tuple[str, str, str]] = Counter()
    for edge in extractor.edges:
        if edge.resolution != "resolved" or not is_ledger_kind(edge.shape_kind or ""):
            continue
        counts[(edge.targets[0], edge.module, kind_text(edge))] += 1
    ledger = sorted(f"{t} {c} {k} {n}" for (t, c, k), n in counts.items())
    by_resolution = Counter((e.kind, e.resolution) for e in extractor.edges)
    return {
        "ledger": ledger,
        "edges": extractor.edges,
        "by_resolution": {f"{k}:{r}": n for (k, r), n in sorted(by_resolution.items())},
        "incomplete_alias_uses": dict(extractor.incomplete),
    }
