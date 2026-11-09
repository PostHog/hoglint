"""Facade facts for the facade rules: exports, signature slots, declarations and wiring bases.

Port of the fact-gathering half of the spike facade channel. For each module in a product's
facade zone (`products/<name>/backend/facade/`) it emits:

- `export` edges: names the module hands out, through re-exports, with members of exported
  collections as their own `export` edges;
- `exposes` edges: one per name in each signature slot (return, parameter, dataclass field),
  including functions the facade re-exports from elsewhere;
- `declares` edges: each top-level def or class, with its stub and wiring-decorator facts;
- `inherit` and `decorate` edges for exported classes and their first-party bases.

Judging them (denied types, approved wiring bases, capability modules) is the rules' job.
"""

from __future__ import annotations

import builtins
from collections import Counter
from dataclasses import dataclass, field
from typing import cast

from hoglint.model import LANGUAGE_PYTHON, Context, Edge, EntityId, Graph, Resolution, Site
from hoglint.spike.core import TYPE_ONLY, Core, const_of

FACADE_PRODUCER = "posthog.facade-facts@1"
REPO_PACKAGES = frozenset({"common", "ee", "posthog", "products"})
DATACLASS_DECORATORS = frozenset({"dataclass", "frozen"})
REEXPORT_DUNDERS = frozenset({"__getattr__", "__dir__"})
SPECIAL_FORMS = frozenset({"Literal", "Annotated"})
MUTATORS = ("append", "extend", "insert", "add", "update")
CLASS_BODY = 8  # context flag of a statement directly in a top-level class body
PROD = frozenset({Context.PROD})


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


def direct_children(facts: ModuleFacts, idx: int) -> list[int]:
    """Defs directly in the body of def `idx` (not under an if/try/with inside it)."""
    body_flags = facts.defs[idx].get("f", 0) | CLASS_BODY
    return [i for i, d in enumerate(facts.defs) if d["s"] == idx and d.get("f", 0) == body_flags]


def direct_assigns(facts: ModuleFacts, idx: int) -> list[dict]:
    body_flags = facts.defs[idx].get("f", 0) | CLASS_BODY
    return [a for a in facts.record.get("assigns", ()) if a["s"] == idx and a.get("f", 0) == body_flags]


def has_wiring_decorator(d: dict) -> bool:
    for dec in d.get("dec", ()):
        if decorator_bare(dec) == "shared_task":
            return True
        target = dec["call"] if isinstance(dec, dict) and "call" in dec else dec
        if isinstance(target, str) and target.count(".") == 1 and target.endswith(".defn"):
            return True
    return False


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


def flag(value: bool) -> str:
    return "true" if value else "false"


@dataclass(frozen=True)
class Handed:
    bound: str
    target: str
    rel: str
    mode: str  # module (no functions) | all (__all__) | alias (`import x as x`) | lazy


class FacadeFacts:
    def __init__(self, core: Core) -> None:
        self.core = core
        self.index = ModuleIndex(core)
        self.resolver = Resolver(self.index)
        self.edges: list[Edge] = []
        self.classes_done: set[str] = set()
        self.signatures: Counter[tuple[str, str]] = Counter()

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
        if is_package is None or self.product_of(module) is None:
            return None
        return self.backend_rel(module, is_package)

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

    def entity(self, qualified: str) -> EntityId:
        split = self.resolver.split(qualified)
        if split is None:
            module, _, name = qualified.rpartition(".")
            return EntityId(LANGUAGE_PYTHON, module, name)
        return EntityId(LANGUAGE_PYTHON, split[0], split[1])

    def target(self, origin: Origin) -> tuple[tuple[EntityId, ...], Resolution]:
        if origin.kind == "unresolved":
            return (), Resolution.UNRESOLVED
        if origin.kind == "external":
            return (self.entity(origin.qualified),), Resolution.EXTERNAL
        return (self.entity(origin.qualified),), Resolution.RESOLVED

    def via(self, origin: Origin, surface: str) -> tuple[EntityId, ...]:
        return tuple(self.entity(hop) for hop in origin.via if hop not in (surface, origin.qualified))

    def site(self, dotted: str, scope: str, line: int = 0) -> Site:
        return Site(self.core.index.path_of.get(dotted, ""), dotted, scope, line)

    def add(self, kind: str, site: Site, surface: str, origin: Origin, attrs: list[tuple[str, str]]) -> None:
        targets, resolution = self.target(origin)
        attrs = [*attrs, ("origin", origin.qualified), ("origin_kind", origin.kind)]
        self.edges.append(
            Edge(
                kind=kind,
                site=site,
                surface=surface,
                targets=targets,
                resolution=resolution,
                contexts=PROD,
                producer=FACADE_PRODUCER,
                via=self.via(origin, surface),
                attrs=tuple(attrs),
            )
        )

    # exports -----------------------------------------------------------------------------

    def handed_out(self, facts: ModuleFacts) -> list[Handed]:
        pure = not facts.has_functions
        allowed = None if pure else facts.dunder_all
        out = []
        for bound, target, asname in facts.runtime_imports:
            original = target.rpartition(".")[2]
            if pure:
                mode = "module"
            elif allowed is not None and bound in allowed:
                mode = "all"
            elif asname == original:
                mode = "alias"
            else:
                continue
            module = target.rpartition(".")[0]
            rel = self.surface_rel(target) if self.index.is_module(target) else self.surface_rel(module)
            out.append(Handed(bound, target, rel or "", mode))
        for name in facts.lazy:
            found = self.resolver.lazy_target(facts, name)
            if found is not None:
                out.append(Handed(name, f"{found}.{name}", self.surface_rel(found) or "", "lazy"))
        return out

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

    def exports(self, facts: ModuleFacts, dotted: str, handed: list[Handed]) -> None:
        for item in handed:
            origin = self.resolver.resolve(item.target)
            attrs = [("bound", item.bound), ("rel", item.rel), ("mode", item.mode)]
            members: list[str] | None = []
            if origin.kind in ("assign", "var"):
                members = self.collection_members(item.target)
                attrs.append(("members", "unknown" if members is None else str(len(members))))
            self.add("export", self.site(dotted, item.bound), item.target, origin, attrs)
            if origin.kind == "class":
                self.class_facts(origin.qualified)
            for member in members or []:
                member_origin = self.resolver.resolve(member)
                member_attrs = [("bound", member.rpartition(".")[2]), ("rel", item.rel), ("member_of", item.bound)]
                self.add("export", self.site(dotted, item.bound), member, member_origin, member_attrs)
                if member_origin.kind == "class":
                    self.class_facts(member_origin.qualified)

    # signatures --------------------------------------------------------------------------

    def slot(
        self, annotation: object, facts: ModuleFacts, dotted: str, symbol: str, slot: str, extra: list[tuple[str, str]]
    ) -> None:
        names = annotation_names(annotation, facts)
        whole_any = (
            len(names) == 1
            and names[0].rpartition(".")[2] == "Any"
            and names[0].startswith(("typing.", "typing_extensions."))
        )
        attrs = [*extra, ("slot", slot), ("whole_any", flag(whole_any))]
        site = self.site(dotted, symbol)
        if annotation is None:
            self.add("exposes", site, "", Origin("unresolved", ""), [*attrs, ("annotation", "missing")])
            return
        for surface in names:
            in_repo = surface.split(".")[0] in REPO_PACKAGES
            origin = self.resolver.resolve(surface) if in_repo else Origin("external", surface)
            self.add("exposes", site, surface, origin, attrs)

    def signature(
        self, d: dict, facts: ModuleFacts, dotted: str, symbol: str, extra: list[tuple[str, str]] | None = None
    ) -> None:
        ordinal = self.signatures[(dotted, symbol)]
        self.signatures[(dotted, symbol)] += 1
        base = [*(extra or []), ("signature", str(ordinal))]
        self.slot(d.get("ret"), facts, dotted, symbol, "return", base)
        for param in d.get("params", ()):
            if param[0] in ("self", "cls"):
                continue
            annotation = param[2] if len(param) > 2 else None
            self.slot(annotation, facts, dotted, symbol, f"param:{param[0]}", base)

    def signatures_of(self, facts: ModuleFacts, dotted: str) -> None:
        for idx in facts.top_defs:
            d = facts.defs[idx]
            if d["n"].startswith("_"):
                continue
            if d["k"] != "class":
                self.signature(d, facts, dotted, d["n"])
                continue
            for child in direct_children(facts, idx):
                c = facts.defs[child]
                if c["k"] != "class" and (not c["n"].startswith("_") or c["n"] == "__init__"):
                    self.signature(c, facts, dotted, f"{d['n']}.{c['n']}")
            if not any(decorator_bare(dec) in DATACLASS_DECORATORS for dec in d.get("dec", ())):
                continue
            symbol = f"{d['n']}.__init__"
            ordinal = self.signatures[(dotted, symbol)]
            self.signatures[(dotted, symbol)] += 1
            for row in direct_assigns(facts, idx):
                target = row["t"][0]
                if row["op"] != ":" or not is_name(target):
                    continue
                ann = row.get("ann")
                if isinstance(ann, dict) and "sub" in ann and decorator_bare(ann["sub"]) == "ClassVar":
                    continue
                attrs = [("signature", f"field-{ordinal}")]
                self.slot(ann, facts, dotted, symbol, f"param:{target}", attrs)

    def reexported_signatures(self, facts: ModuleFacts, dotted: str, handed: list[Handed]) -> None:
        defined = {facts.defs[i]["n"] for i in facts.top_defs}
        for item in sorted(set(handed), key=lambda h: (h.bound, h.target, h.rel, h.mode)):
            origin = self.resolver.resolve(item.target)
            if origin.kind != "func":
                continue
            origin_module, _, name = origin.qualified.rpartition(".")
            src = self.index.facts(origin_module)
            if src is None:
                continue
            binding = src.bindings.get(name)
            if binding is None or binding.kind != "func":
                continue
            extra = [
                ("reexport", origin.qualified),
                ("rel", item.rel),
                ("hops", str(len(origin.via))),
                ("defined_here", flag(item.bound in defined)),
            ]
            self.signature(src.defs[cast(int, binding.node)], src, dotted, item.bound, extra)

    # declarations and classes ------------------------------------------------------------

    def declares(self, facts: ModuleFacts, dotted: str) -> None:
        for idx in facts.top_defs:
            d = facts.defs[idx]
            attrs = [
                ("def_kind", d["k"]),
                ("stub", flag(bool(d.get("stub")))),
                ("wiring_decorator", flag(has_wiring_decorator(d))),
            ]
            origin = Origin("class" if d["k"] == "class" else "func", f"{dotted}.{d['n']}")
            self.add("declares", self.site(dotted, d["n"], d["l"]), f"{dotted}.{d['n']}", origin, attrs)

    def class_facts(self, qualified: str) -> None:
        """`decorate` and `inherit` edges of a class and, recursively, of its first-party bases."""
        if qualified in self.classes_done:
            return
        self.classes_done.add(qualified)
        module, _, name = qualified.rpartition(".")
        facts = self.index.facts(module)
        binding = facts.bindings.get(name) if facts is not None else None
        if facts is None or binding is None or binding.kind != "class":
            return
        d = facts.defs[cast(int, binding.node)]
        site = self.site(module, name, d["l"])
        for dec in d.get("dec", ()):
            q = qualify_expr(dec, facts)
            origin = self.resolver.resolve(q) if q else Origin("unresolved", "")
            self.add("decorate", site, q or "", origin, [])
        for position, base in enumerate(d.get("bases", ())):
            q = qualify_expr(base, facts)
            origin = self.resolver.resolve(q) if q else Origin("unresolved", "")
            self.add("inherit", site, q or "", origin, [("position", str(position))])
            if origin.kind == "class":
                self.class_facts(origin.qualified)

    def produce(self) -> list[Edge]:
        for product, keys in sorted(self.facade_files().items()):
            for key in keys:
                parts = ["products", product, "backend", "facade", *key.removesuffix(".py").split("/")]
                if parts[-1] == "__init__":
                    parts.pop()
                dotted = ".".join(parts)
                facts = self.index.facts(dotted)
                if facts is None:
                    continue
                handed = self.handed_out(facts)
                self.declares(facts, dotted)
                self.signatures_of(facts, dotted)
                self.exports(facts, dotted, handed)
                self.reexported_signatures(facts, dotted, handed)
        return self.edges


def produce(core: Core, graph: Graph) -> None:
    try:
        graph.edges.extend(FacadeFacts(core).produce())
    except Exception as error:  # a broken producer makes the facade rules incomplete
        graph.failed_producers[FACADE_PRODUCER] = f"{type(error).__name__}: {error}"
