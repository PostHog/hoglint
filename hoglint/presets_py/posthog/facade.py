"""Facade facts for the facade rules: exports, signature slots, declarations and wiring bases.

Port of the fact-gathering half of the spike facade channel, on the core resolver. For each
module in a product's facade zone (`products/<name>/backend/facade/`) it emits:

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
from dataclasses import dataclass
from functools import cached_property

from hoglint.core.records import RecordBodies, chain_last, sequence_items, str_literal
from hoglint.core.sites import binding_surface
from hoglint.core.symbols import TYPE_ONLY, ModuleTable, absolute_import
from hoglint.model import LANGUAGE_PYTHON, Context, Edge, EntityId, Resolution, Site, flag
from hoglint.plugins import PackReader
from hoglint.presets_py.posthog.layout import backend_product, backend_rel

FACADE_PRODUCER = "posthog.facade-facts@1"
DATACLASS_DECORATORS = frozenset({"dataclass", "frozen"})
SPECIAL_FORMS = frozenset({"Literal", "Annotated"})
TYPING_MODULES = frozenset({"typing", "typing_extensions"})
MUTATORS = ("append", "extend", "insert", "add", "update")
PROD = frozenset({Context.PROD})
IMPORT_BINDINGS = frozenset({"module", "from"})
VALUE_BINDINGS = frozenset({"alias", "variable"})
HIT_KINDS = {"class": "class", "def": "func", "module": "module", "variable": "assign", "member": "nested"}


def is_name(value: object) -> bool:
    return isinstance(value, str) and "." not in value


def is_name_or_attr(value: object) -> bool:
    return isinstance(value, str) or (isinstance(value, dict) and "a" in value)


def is_alias_shape(value: object) -> bool:
    """Name, Attribute, Subscript or BinOp node."""
    return isinstance(value, str) or (isinstance(value, dict) and ("a" in value or "sub" in value or "bin" in value))


def has_wiring_decorator(d: dict) -> bool:
    for dec in d.get("dec", ()):
        if chain_last(dec) == "shared_task":
            return True
        target = dec["call"] if isinstance(dec, dict) and "call" in dec else dec
        if isinstance(target, str) and target.count(".") == 1 and target.endswith(".defn"):
            return True
    return False


@dataclass(frozen=True)
class Origin:
    kind: str  # class | func | module | assign | external | unresolved | nested
    qualified: str
    via: tuple[str, ...] = ()


class ModuleView:
    """One module as the facade rules read it: its core symbol table plus its top-level statements."""

    def __init__(self, module: str, table: ModuleTable, record: dict, bodies: RecordBodies) -> None:
        self.module = module
        self.table = table
        self.record = record
        self.bodies = bodies
        self.defs: list[dict] = record.get("defs", [])

    @cached_property
    def top_defs(self) -> list[int]:
        return self.bodies.children(-1)

    @cached_property
    def has_functions(self) -> bool:
        return any(self.defs[i]["k"] != "class" for i in self.top_defs)

    @cached_property
    def top_imports(self) -> list[tuple[str, str, str | None, bool]]:
        """(bound, imported dotted name, asname, type-only) of module-level `from` imports, in order."""
        out = []
        for _, _, _, flags, mod, level, name, asname in self.record.get("imports", ()):
            if name is None or name == "*" or flags not in (0, TYPE_ONLY):
                continue
            source = absolute_import(self.module, self.table.is_package, mod, level)
            if source is not None:
                out.append((asname or name, f"{source}.{name}", asname, flags == TYPE_ONLY))
        return out

    @cached_property
    def special_forms(self) -> dict[str, str]:
        return {
            bound: target.rpartition(".")[2]
            for bound, target, _, _ in self.top_imports
            if target.rpartition(".")[0] in TYPING_MODULES and target.rpartition(".")[2] in SPECIAL_FORMS
        }

    @cached_property
    def dunder_all(self) -> set[str] | None:
        found = None
        for row in self.bodies.assigns(-1):
            if row["op"] == "=" and "__all__" in row["t"]:
                items = sequence_items(row.get("v"))
                if items is not None and "set" not in row["v"]:
                    found = {text for text in (str_literal(e) for e in items) if text is not None}
        return found

    def binding(self, name: str) -> tuple | None:
        return self.table.names.get(name)

    def value(self, name: str) -> object:
        return self.table.values.get(name)

    def qualify_name(self, name: str) -> str | None:
        binding = self.binding(name)
        if binding is None:
            return f"builtins.{name}" if hasattr(builtins, name) else None
        if binding[0] in IMPORT_BINDINGS:
            return binding_surface(binding)
        return f"{self.module}.{name}"

    def qualify(self, value: object) -> str | None:
        """The qualified name a V value names, in this module's namespace."""
        if isinstance(value, str):
            head, _, rest = value.partition(".")
            q = self.qualify_name(head)
            if q is None:
                return None
            return f"{q}.{rest}" if rest else q
        if not isinstance(value, dict):
            return None
        for key in ("call", "sub"):
            if key in value:
                return self.qualify(value[key])
        if "a" in value:
            head = self.qualify(value["v"])
            return f"{head}.{value['a']}" if head else None
        return None

    def mutated(self, name: str) -> bool:
        """Module-level `name.append(...)`-style calls or `name += ...` (callees over-approximate statements)."""
        wanted = {f"{name}.{m}" for m in MUTATORS}
        for row in self.record.get("callees", ()):
            if row[0] == -1 and row[1] == -1 and row[2] in wanted:
                return True
        for row in self.bodies.assigns(-1):
            if row["op"] not in ("=", ":", "type") and row["t"] == [name]:
                return True
        return False

    def annotation_names(self, value: object, expanding: frozenset[str] = frozenset()) -> list[str]:
        """Every qualified name a typed V value names, aliases expanded in this module."""
        if value is None or isinstance(value, (bool, int)):
            return []
        if isinstance(value, str):
            if "." not in value and value not in expanding:
                binding, node = self.binding(value), self.value(value)
                if binding is not None and binding[0] in VALUE_BINDINGS and is_alias_shape(node):
                    return self.annotation_names(node, expanding | {value})
            q = self.qualify(value)
            return [q] if q else []
        if not isinstance(value, dict):
            return []
        if "s" in value:
            parsed = value.get("sa")
            return self.annotation_names(parsed, expanding) if parsed is not None else []
        if "a" in value:
            q = self.qualify(value)
            return [q] if q else []
        if "sub" in value:
            return self.subscript_names(value, expanding)
        if "bin" in value:
            return self.annotation_names(value["l"], expanding) + self.annotation_names(value["r"], expanding)
        if "tuple" in value or "list" in value:
            return [n for e in value.get("tuple", value.get("list")) for n in self.annotation_names(e, expanding)]
        return []

    def subscript_names(self, value: dict, expanding: frozenset[str]) -> list[str]:
        head_value = value["sub"]
        form = None
        if is_name(head_value):
            form = self.special_forms.get(head_value) or (head_value if head_value in SPECIAL_FORMS else None)
        elif chain_last(head_value) in SPECIAL_FORMS:
            form = chain_last(head_value)
        head = self.annotation_names(head_value, expanding)
        if form == "Literal":
            return head
        index = value["i"]
        if form == "Annotated":
            first = index["tuple"][0] if isinstance(index, dict) and index.get("tuple") else index
            return head + self.annotation_names(first, expanding)
        return head + self.annotation_names(index, expanding)


@dataclass(frozen=True)
class Handed:
    bound: str
    target: str
    rel: str
    mode: str  # module (no functions) | all (__all__) | alias (`import x as x`) | lazy


class FacadeFacts:
    def __init__(self, reader: PackReader) -> None:
        self.reader = reader
        self.index = reader.index
        self.edges: list[Edge] = []
        self.classes_done: set[str] = set()
        self.signatures: Counter[tuple[str, str]] = Counter()
        self.views: dict[str, ModuleView | None] = {}
        self.origins: dict[str, Origin] = {}

    def view(self, module: str) -> ModuleView | None:
        if module not in self.views:
            self.views[module] = self.read_view(module)
        return self.views[module]

    def read_view(self, module: str) -> ModuleView | None:
        if not self.reader.first_party.contains(module):
            return None
        table = self.reader.tables.get(module)
        record = self.reader.tables.record(module) if table is not None else None
        if table is None or record is None or record.get("status") != "ok":
            return None
        return ModuleView(module, table, record, self.reader.bodies(record))

    def is_module(self, module: str) -> bool:
        return self.reader.first_party.contains(module) and self.index.exists(module)

    def resolve(self, qualified: str) -> Origin:
        if qualified not in self.origins:
            self.origins[qualified] = self.read_origin(qualified)
        return self.origins[qualified]

    def read_origin(self, qualified: str) -> Origin:
        """Where a qualified name leads, stopping at the first name outside the repo."""
        if not self.reader.first_party.contains(qualified):
            return Origin("external", qualified)
        hit, rest = self.reader.resolver.resolve_dotted(qualified)
        if hit is None:
            return Origin("unresolved", qualified)
        chain = [str(step) for step in hit.chain]
        outside = next((step for step in chain if not self.reader.first_party.contains(step)), None)
        if outside is not None:
            return Origin("external", outside, tuple(chain[: chain.index(outside)]))
        via = tuple(chain[:-1])
        if rest:
            if hit.kind != "class":
                return Origin("unresolved", qualified)
            return Origin("nested", ".".join([chain[-1], *rest]), via)
        kind = HIT_KINDS.get(hit.kind)
        return Origin(kind, chain[-1], via) if kind is not None else Origin("unresolved", qualified)

    def surface_rel(self, module: str) -> str | None:
        if not self.is_module(module) or backend_product(module) is None:
            return None
        return backend_rel(module, self.index.is_package(module))

    def facade_files(self) -> list[str]:
        """Facade modules in path order, like rglob over products/<p>/backend/facade."""
        out = []
        for scope in self.reader.files:
            parts = scope.path.split("/")
            if len(parts) < 5 or parts[0] != "products" or parts[2] != "backend" or parts[3] != "facade":
                continue
            name = parts[-1]
            if name.endswith(".py") and not name.startswith("test_") and not name.endswith("_test.py"):
                out.append(scope.module)
        return out

    def entity(self, qualified: str) -> EntityId:
        module = self.index.longest_module(qualified) if self.is_module(qualified.partition(".")[0]) else None
        if module is None:
            module, _, name = qualified.rpartition(".")
            return EntityId(LANGUAGE_PYTHON, module, name)
        return EntityId(LANGUAGE_PYTHON, module, qualified[len(module) + 1 :])

    def target(self, origin: Origin) -> tuple[tuple[EntityId, ...], Resolution]:
        if origin.kind == "unresolved":
            return (), Resolution.UNRESOLVED
        if origin.kind == "external":
            return (self.entity(origin.qualified),), Resolution.EXTERNAL
        return (self.entity(origin.qualified),), Resolution.RESOLVED

    def via(self, origin: Origin, surface: str) -> tuple[EntityId, ...]:
        return tuple(self.entity(hop) for hop in origin.via if hop not in (surface, origin.qualified))

    def site(self, dotted: str, scope: str, line: int = 0) -> Site:
        return Site(self.index.path_of.get(dotted, ""), dotted, scope, line)

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

    def handed_out(self, view: ModuleView) -> list[Handed]:
        pure = not view.has_functions
        allowed = None if pure else view.dunder_all
        out = []
        for bound, target, asname, type_only in view.top_imports:
            if type_only:
                continue
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
            rel = self.surface_rel(target) if self.is_module(target) else self.surface_rel(module)
            out.append(Handed(bound, target, rel or "", mode))
        for name, module in view.table.lazy.items():
            out.append(Handed(name, f"{module}.{name}", self.surface_rel(module) or "", "lazy"))
        return out

    def collection_members(self, qualified: str, seen: frozenset[str] = frozenset()) -> list[str] | None:
        if qualified in seen:
            return []
        module = self.index.longest_module(qualified)
        if module is None or module == qualified:
            return []
        name = qualified[len(module) + 1 :]
        view = self.view(module)
        binding = view.binding(name) if view is not None else None
        if view is None or binding is None:
            return []
        if binding[0] in IMPORT_BINDINGS:
            return self.collection_members(binding_surface(binding), seen | {qualified})
        if binding[0] not in VALUE_BINDINGS:
            return []
        if view.mutated(name):
            return None
        return self.expr_members(view.value(name), view, seen | {qualified})

    def expr_members(self, value: object, view: ModuleView, seen: frozenset[str]) -> list[str] | None:
        items = sequence_items(value)
        if items is not None:
            out: list[str] = []
            for e in items:
                if isinstance(e, dict) and "star" in e:
                    sub = self.expr_members(e["star"], view, seen)
                    if sub is None:
                        return None
                    out += sub
                else:
                    q = view.qualify(e)
                    if q:
                        out.append(q)
            return out
        if isinstance(value, dict) and value.get("bin") == "+":
            left = self.expr_members(value["l"], view, seen)
            right = self.expr_members(value["r"], view, seen)
            return None if left is None or right is None else left + right
        if is_name_or_attr(value):
            q = view.qualify(value)
            return self.collection_members(q, seen) if q else []
        return []

    def exports(self, dotted: str, handed: list[Handed]) -> None:
        for item in handed:
            origin = self.resolve(item.target)
            attrs = [("bound", item.bound), ("rel", item.rel), ("mode", item.mode)]
            members: list[str] | None = []
            if origin.kind == "assign":
                members = self.collection_members(item.target)
                attrs.append(("members", "unknown" if members is None else str(len(members))))
            self.add("export", self.site(dotted, item.bound), item.target, origin, attrs)
            if origin.kind == "class":
                self.class_facts(origin.qualified)
            for member in members or []:
                member_origin = self.resolve(member)
                member_attrs = [("bound", member.rpartition(".")[2]), ("rel", item.rel), ("member_of", item.bound)]
                self.add("export", self.site(dotted, item.bound), member, member_origin, member_attrs)
                if member_origin.kind == "class":
                    self.class_facts(member_origin.qualified)

    # signatures --------------------------------------------------------------------------

    def slot(
        self, annotation: object, view: ModuleView, dotted: str, symbol: str, slot: str, extra: list[tuple[str, str]]
    ) -> None:
        names = view.annotation_names(annotation)
        whole_any = (
            len(names) == 1 and names[0].rpartition(".")[2] == "Any" and names[0].rpartition(".")[0] in TYPING_MODULES
        )
        attrs = [*extra, ("slot", slot), ("whole_any", flag(whole_any))]
        site = self.site(dotted, symbol)
        if annotation is None:
            self.add("exposes", site, "", Origin("unresolved", ""), [*attrs, ("annotation", "missing")])
            return
        for surface in names:
            self.add("exposes", site, surface, self.resolve(surface), attrs)

    def signature(
        self, d: dict, view: ModuleView, dotted: str, symbol: str, extra: list[tuple[str, str]] | None = None
    ) -> None:
        ordinal = self.signatures[(dotted, symbol)]
        self.signatures[(dotted, symbol)] += 1
        base = [*(extra or []), ("signature", str(ordinal))]
        # The facts write `-> None` as "None" and leave a missing return annotation out.
        if d.get("ret") is not None:
            self.slot(d["ret"], view, dotted, symbol, "return", base)
        for param in d.get("params", ()):
            if param[0] in ("self", "cls"):
                continue
            annotation = param[2] if len(param) > 2 else None
            self.slot(annotation, view, dotted, symbol, f"param:{param[0]}", base)

    def signatures_of(self, view: ModuleView, dotted: str) -> None:
        for idx in view.top_defs:
            d = view.defs[idx]
            if d["n"].startswith("_"):
                continue
            if d["k"] != "class":
                self.signature(d, view, dotted, d["n"])
                continue
            for child in view.bodies.children(idx):
                c = view.defs[child]
                if c["k"] != "class" and (not c["n"].startswith("_") or c["n"] == "__init__"):
                    self.signature(c, view, dotted, f"{d['n']}.{c['n']}")
            if any(chain_last(dec) in DATACLASS_DECORATORS for dec in d.get("dec", ())):
                self.dataclass_fields(view, dotted, idx)

    def dataclass_fields(self, view: ModuleView, dotted: str, idx: int) -> None:
        symbol = f"{view.defs[idx]['n']}.__init__"
        ordinal = self.signatures[(dotted, symbol)]
        self.signatures[(dotted, symbol)] += 1
        for row in view.bodies.assigns(idx):
            target = row["t"][0]
            if row["op"] != ":" or not is_name(target):
                continue
            ann = row.get("ann")
            if ann is None:
                continue  # `x: None`: an annotated row always has an annotation
            if isinstance(ann, dict) and "sub" in ann and chain_last(ann["sub"]) == "ClassVar":
                continue
            self.slot(ann, view, dotted, symbol, f"param:{target}", [("signature", f"field-{ordinal}")])

    def reexported_signatures(self, view: ModuleView, dotted: str, handed: list[Handed]) -> None:
        defined = {view.defs[i]["n"] for i in view.top_defs}
        for item in sorted(set(handed), key=lambda h: (h.bound, h.target, h.rel, h.mode)):
            origin = self.resolve(item.target)
            if origin.kind != "func":
                continue
            origin_module, _, name = origin.qualified.rpartition(".")
            src = self.view(origin_module)
            binding = src.binding(name) if src is not None else None
            if src is None or binding is None or binding[0] != "def" or binding[2]:
                continue
            extra = [
                ("reexport", origin.qualified),
                ("rel", item.rel),
                ("hops", str(len(origin.via))),
                ("defined_here", flag(item.bound in defined)),
            ]
            self.signature(src.defs[binding[1]], src, dotted, item.bound, extra)

    # declarations and classes ------------------------------------------------------------

    def declares(self, view: ModuleView, dotted: str) -> None:
        for idx in view.top_defs:
            d = view.defs[idx]
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
        view = self.view(module)
        binding = view.binding(name) if view is not None else None
        if view is None or binding is None or binding[0] != "class" or binding[2]:
            return
        d = view.defs[binding[1]]
        site = self.site(module, name, d["l"])
        for dec in d.get("dec", ()):
            q = view.qualify(dec)
            origin = self.resolve(q) if q else Origin("unresolved", "")
            self.add("decorate", site, q or "", origin, [])
        for position, base in enumerate(d.get("bases", ())):
            q = view.qualify(base)
            origin = self.resolve(q) if q else Origin("unresolved", "")
            self.add("inherit", site, q or "", origin, [("position", str(position))])
            if origin.kind == "class":
                self.class_facts(origin.qualified)

    def produce(self) -> list[Edge]:
        for dotted in self.facade_files():
            view = self.view(dotted)
            if view is None:
                continue
            handed = self.handed_out(view)
            self.declares(view, dotted)
            self.signatures_of(view, dotted)
            self.exports(dotted, handed)
            self.reexported_signatures(view, dotted, handed)
        return self.edges
