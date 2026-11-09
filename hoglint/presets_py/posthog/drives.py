"""`drives` edges: a test outside a product that executes the product's wiring.

Port of the spike drives channel. The deriver logic is unchanged; the output is hoglint edges,
one per drive, with the wiring location, the trigger, and how the test reaches the dispatcher.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from hoglint.model import LANGUAGE_PYTHON, Context, Edge, EntityId, Resolution, Site
from hoglint.spike.core import CLASS, SITE_MARKER, Core, absolute_import, module_of_path, qualname

SCANNED_ROOTS = ("posthog/", "ee/", "products/", "common/")
QUERY_DISPATCHER = "posthog/hogql_queries/query_runner.py"
NODE_KIND_ENUM = "posthog/schema_enums.py"
QUERY_WIRING_LOCATION = "backend/hogql_queries/"
COMPUTED_WIRING_LOCATIONS = ("backend/hogql_queries/",)
DISPATCH_CALLS = frozenset(
    {
        "process_query_dict",
        "process_query_model",
        "process_query",
        "get_query_runner",
        "get_query_runner_or_none",
        "execute_hogql_query",
    }
)
TEST_CLIENT_METHODS = frozenset({"get", "post", "put", "patch", "delete"})
PATCH_CALLS = frozenset({"patch", "object"})
QUERY_DICT_KEYS = frozenset({"query", "source"})
MAX_CALL_DEPTH = 8

CAMEL = re.compile(r"^[A-Z]\w*$")
UPPER = re.compile(r"^[A-Z][A-Z0-9_]*$")
TRAILER = re.compile(r"\.\w+|\(\)|\[\]")


@dataclass(frozen=True)
class ImportEdge:
    module: str
    exported: str
    bound: str


@dataclass(frozen=False)
class Body:
    fid: str
    name: str
    calls: frozenset[str]
    client: bool
    patches: frozenset[str]
    const_params: frozenset[str]


@dataclass(frozen=False)
class ScopeFacts:
    calls: frozenset[str]
    client: bool
    rest_patches: frozenset[str]
    methods: dict[str, list[str]]
    bases: list[str]


@dataclass(frozen=True)
class Mention:
    shape: str  # const | tag | attr | call
    value: str
    base: str | None
    fid: str | None
    cls: str | None
    line: int


@dataclass(frozen=True)
class Pending:
    dispatch_callee: str | None
    under_query_key: bool
    value_shape: str
    value_name: str | None
    value_base: str | None
    fid: str | None
    cls: str | None


@dataclass(frozen=False)
class FileFacts:
    rel: str
    dotted: str
    is_test: bool
    imports: list[ImportEdge]
    module_aliases: list[tuple[str, str]]
    top_names: list[str]
    lazy_map: dict[str, str]
    lazy_prefixes: list[str]
    bodies: dict[str, Body]
    top_functions: dict[str, list[str]]
    class_methods: dict[str, list[str]]
    top_classes: dict[str, str]
    scopes: dict[str, ScopeFacts]
    mentions: list[Mention]
    pending: list[Pending]
    name_uses: Counter = field(default_factory=Counter)
    attr_uses: Counter = field(default_factory=Counter)
    dispatch_branches: list[tuple[list[str], list[str], list[str]]] = field(default_factory=list)
    enum_members: dict[str, dict[str, str]] = field(default_factory=dict)


def is_test_path(rel: str) -> bool:
    path = PurePosixPath(rel)
    if "test" in path.parts or "tests" in path.parts:
        return True
    return path.name.startswith("test_") or path.name.endswith("_test.py") or path.name == "conftest.py"


def callee_name(callee: str) -> str | None:
    """The spike's callee_name() from the L1 callee text: Name id or Attribute attr."""
    if callee == "?":
        return None
    return callee.rsplit(".", 1)[-1]


def is_client_call(callee: str) -> bool:
    if callee.startswith("?") or "." not in callee:
        return False
    receiver, _, method = callee.rpartition(".")
    return method in TEST_CLIENT_METHODS and receiver.endswith("client")


def is_sv_constant(value: object) -> bool:
    if value is None or isinstance(value, (bool, int)):
        return True
    if isinstance(value, dict):
        return "s" in value or value.get("o") in ("float", "complex", "bytes", "...", "int", "longstr", "badstr")
    return False


def str_of(value: object) -> str | None:
    if isinstance(value, dict) and "s" in value and len(value) == 1:
        return value["s"]
    return None


def first_step(up: str) -> str:
    return up.split(" ", 1)[0] if up else ""


class FactAdapter:
    """One record in, one FileFacts out (the spike's L1 view, rebuilt from fact lists)."""

    def __init__(self, record: dict) -> None:
        self.record = record
        self.rel = record["path"]
        self.dotted, self.is_package = module_of_path(self.rel)
        self.defs: list[dict] = record.get("defs", [])
        self.ancestry_cache: dict[int, list[int]] = {}
        self.children: dict[int, list[int]] = defaultdict(list)
        for i, d in enumerate(self.defs):
            self.children[d["s"]].append(i)

    def ancestry(self, idx: int) -> list[int]:
        """idx and every enclosing def, without -1."""
        cached = self.ancestry_cache.get(idx)
        if cached is not None:
            return cached
        out = []
        cur = idx
        while cur != -1:
            out.append(cur)
            cur = self.defs[cur]["s"]
        self.ancestry_cache[idx] = out
        return out

    @staticmethod
    def site(s: int, h: int) -> int:
        """The innermost def whose ast subtree holds the site (a header belongs to its def)."""
        return h if h != -1 else s

    def is_function(self, idx: int) -> bool:
        return self.defs[idx]["k"] != "class"

    def enclosing(self, s: int, h: int) -> tuple[str | None, str | None]:
        fid = cls = None
        for idx in self.ancestry(self.site(s, h)):
            if self.is_function(idx):
                if fid is None:
                    fid = self.fids[idx]
            elif cls is None:
                cls = self.quals[idx]
            if fid is not None and cls is not None:
                break
        return fid, cls

    def is_direct_child(self, idx: int, parent: int) -> bool:
        d = self.defs[idx]
        if d["s"] != parent:
            return False
        if parent == -1:
            return d.get("f", 0) == 0
        return d.get("f", 0) == self.defs[parent].get("f", 0) | CLASS

    def run(self) -> FileFacts:
        record = self.record
        defs = self.defs
        self.quals = [qualname(defs, i) for i in range(len(defs))]
        self.fids = {i: f"{self.quals[i]}@{d['l']}" for i, d in enumerate(defs) if d["k"] != "class"}

        imports: list[ImportEdge] = []
        aliases: list[tuple[str, str]] = []
        bound_names: set[str] = set()
        # The spike's stack walk visits the leaves right to left: reverse source order.
        for row in reversed(record.get("imports", [])):
            _, _, _, _, mod, level, name, asname = row
            if name is None:
                aliases.append((asname or mod, mod))
                bound_names.add(asname or mod)
                continue
            module = absolute_import(self.dotted, self.is_package, mod, level)
            if module is None:
                continue
            bound = asname or name
            imports.append(ImportEdge(module, name, bound))
            aliases.append((bound, f"{module}.{name}"))
            bound_names.add(bound)

        # Bodies and scopes from per-site rows.
        calls_in: dict[int, set[str]] = defaultdict(set)
        client_in: set[int] = set()
        patches_in: dict[int, set[str]] = defaultdict(set)
        module_calls: set[str] = set()
        module_client = False
        # Group by site first, then spread each site's names up its def ancestry once.
        site_calls: dict[int, set[str]] = defaultdict(set)
        site_client: set[int] = set()
        for s, h, callee, _count, _l, _c in record.get("callees", []):
            site = self.site(s, h)
            name = callee_name(callee)
            if name:
                module_calls.add(name)
                site_calls[site].add(name)
            if is_client_call(callee):
                module_client = True
                site_client.add(site)
        for site in site_calls.keys() | site_client:
            for idx in self.ancestry(site):
                calls_in[idx].update(site_calls.get(site, ()))
                if site in site_client:
                    client_in.add(idx)
        patch_rows: list[tuple[int, str]] = []
        for s, h, _l, _c, callee, args, _kws in record.get("calls", []):
            if callee_name(callee) not in PATCH_CALLS:
                continue
            site = self.site(s, h)
            for arg in args:
                value = str_of(arg)
                if value is not None:
                    leaf = value.rsplit(".", 1)[-1]
                    patch_rows.append((site, leaf))
                    for idx in self.ancestry(site):
                        patches_in[idx].add(leaf)

        bodies: dict[str, Body] = {}
        for idx, fid in self.fids.items():
            d = defs[idx]
            const_params = frozenset(p[0] for p in d.get("params", []) if len(p) > 3 and is_sv_constant(p[3]))
            bodies[fid] = Body(
                fid, d["n"], frozenset(calls_in[idx]), idx in client_in, frozenset(patches_in[idx]), const_params
            )

        top_functions: dict[str, list[str]] = defaultdict(list)
        class_methods: dict[str, list[str]] = defaultdict(list)
        top_classes: dict[str, str] = {}
        top_names: list[str] = []
        for idx in self.children[-1]:
            d = defs[idx]
            if not self.is_direct_child(idx, -1):
                continue
            if d["k"] != "class":
                top_functions[d["n"]].append(self.fids[idx])
                top_names.append(d["n"])
            else:
                top_classes[d["n"]] = d["n"]
                top_names.append(d["n"])
                for child in self.children[idx]:
                    cd = defs[child]
                    if cd["k"] != "class" and self.is_direct_child(child, idx):
                        class_methods[cd["n"]].append(self.fids[child])
        for row in record.get("assigns", []):
            if row["s"] != -1 or row.get("f", 0) or row["op"] not in ("=", ":"):
                continue
            top_names.extend(t for t in row["t"] if isinstance(t, str) and "." not in t)

        scopes = {"<module>": self.scope_facts(-1, module_calls, module_client, patch_rows)}
        for idx, d in enumerate(defs):
            if d["k"] == "class":
                bases = [b for b in d.get("bases", []) if isinstance(b, str) and "." not in b]
                scopes[self.quals[idx]] = self.scope_facts(
                    idx, frozenset(calls_in[idx]), idx in client_in, patch_rows, bases
                )

        mentions, pending, name_uses, attr_uses = self.mentions(bound_names)
        facts = FileFacts(
            rel=self.rel,
            dotted=self.dotted,
            is_test=is_test_path(self.rel),
            imports=imports,
            module_aliases=aliases,
            top_names=[n for n in top_names if not n.startswith("_")],
            lazy_map={},
            lazy_prefixes=[],
            bodies=bodies,
            top_functions=dict(top_functions),
            class_methods=dict(class_methods),
            top_classes=top_classes,
            scopes=scopes,
            mentions=mentions,
            pending=pending,
            name_uses=name_uses,
            attr_uses=attr_uses,
        )
        self.lazy(facts)
        if self.rel == QUERY_DISPATCHER:
            facts.dispatch_branches = self.dispatch_branches()
        if self.rel == NODE_KIND_ENUM:
            facts.enum_members = self.enum_members()
        return facts

    def scope_facts(self, idx: int, calls, client: bool, patch_rows, bases: list[str] | None = None) -> ScopeFacts:
        methods: dict[str, list[str]] = defaultdict(list)
        test_children: set[int] = set()
        for child in self.children[idx]:
            d = self.defs[child]
            if d["k"] != "class" and self.is_direct_child(child, idx):
                methods[d["n"]].append(self.fids[child])
                if d["n"].startswith("test"):
                    test_children.add(child)
        rest: set[str] = set()
        for site, leaf in patch_rows:
            chain = self.ancestry(site) if site != -1 else []
            if idx != -1 and idx not in chain:
                continue
            if test_children.intersection(chain):
                continue
            rest.add(leaf)
        return ScopeFacts(frozenset(calls), client, frozenset(rest), dict(methods), bases or [])

    def mentions(self, bound: set[str]) -> tuple[list[Mention], list[Pending], Counter, Counter]:
        record = self.record
        mentions: list[Mention] = []
        pending: list[Pending] = []
        name_uses: Counter = Counter()
        attr_uses: Counter = Counter()

        for s, h, value, count, line, _c in record.get("names", []):
            if CAMEL.match(value):
                fid, cls = self.enclosing(s, h)
                mentions.extend([Mention("const", value, None, fid, cls, line)] * count)
        for s, h, line, _c, name in record.get("tags", []):
            fid, cls = self.enclosing(s, h)
            mentions.append(Mention("tag", name, None, fid, cls, line))
        for s, h, callee, count, line, _c in record.get("callees", []):
            name = callee_name(callee)
            if name and CAMEL.match(name):
                fid, cls = self.enclosing(s, h)
                mentions.extend([Mention("call", name, None, fid, cls, line)] * count)

        # Name and attribute uses of import-bound roots: refs (tracked) and uses (the rest).
        for row in record.get("refs", []):
            if len(row) > 7:
                continue  # local-derived rows: the root is not import-bound
            s, h, line, _c, root, chain = row[:6]
            self.root_use(root, TRAILER.findall(chain), 1, s, h, line, bound, mentions, name_uses, attr_uses)
        for s, h, root, attrs, count, line, _c in record.get("uses", []):
            self.root_use(root, TRAILER.findall(attrs), count, s, h, line, bound, mentions, name_uses, attr_uses)

        for s, h, _line, _c, _key, value, chain, up in record.get("watched", []):
            if chain:
                continue
            step = first_step(up)
            callee = None
            if step.startswith("arg:"):
                callee = callee_name(step.split(":", 2)[2])
            under = step in ("dict:val:query", "dict:val:source")
            if callee is None and not under:
                continue
            if is_sv_constant(value):
                continue
            fid, cls = self.enclosing(s, h)
            if isinstance(value, str) and "." not in value:
                pending.append(Pending(callee, under, "name", value, None, fid, cls))
            elif isinstance(value, str):
                parts = value.split(".")
                if parts[-1] == "value":
                    parts = parts[:-1]
                if len(parts) >= 2:
                    pending.append(Pending(callee, under, "attr", parts[-1], parts[-2], fid, cls))
                else:
                    pending.append(Pending(callee, under, "other", None, None, fid, cls))
            elif value == {"o": "Attribute"}:
                # Attribute on a non-chain: the attr name is not in the SV form; base is None either way.
                pending.append(Pending(callee, under, "attr", None, None, fid, cls))
            else:
                pending.append(Pending(callee, under, "other", None, None, fid, cls))
        return mentions, pending, name_uses, attr_uses

    def root_use(self, root, tokens, count, s, h, line, bound, mentions, name_uses, attr_uses) -> None:
        if root in bound:
            name_uses[root] += count
        fid = cls = None
        located = False
        base: str | None = root
        dotted: str | None = root
        for token in tokens:
            if token[0] != ".":
                base = None
                dotted = None
                continue
            attr = token[1:]
            if dotted is not None and root in bound:
                attr_uses[(dotted, attr)] += count
            if UPPER.match(attr):
                if not located:
                    fid, cls = self.enclosing(s, h)
                    located = True
                mentions.extend([Mention("attr", attr, base, fid, cls, line)] * count)
            base = attr
            dotted = f"{dotted}.{attr}" if dotted is not None else None

    def lazy(self, facts: FileFacts) -> None:
        has_getattr = any(
            d["k"] != "class" and d["n"] == "__getattr__" and self.is_direct_child(i, -1)
            for i, d in enumerate(self.defs)
        )
        top_assigns = [
            r for r in self.record.get("assigns", []) if r["s"] == -1 and not r.get("f", 0) and r["op"] == "="
        ]
        facts.lazy_prefixes = [
            str_of(r.get("v")) or "" for r in top_assigns if (str_of(r.get("v")) or "").endswith(".")
        ]
        if not has_getattr:
            return
        for r in top_assigns:
            value = r.get("v")
            if not isinstance(value, dict) or "dict" not in value or not value["dict"]:
                continue
            entries = {}
            for k, v in value["dict"]:
                ks, vs = str_of(k), str_of(v)
                if ks is None or vs is None:
                    break
                entries[ks] = vs
            else:
                facts.lazy_map.update(entries)

    def dispatch_branches(self) -> list[tuple[list[str], list[str], list[str]]]:
        out = []
        imports = self.record.get("imports", [])
        for _s, _h, line, _c, body_end, test in self.record.get("branches", []):
            if test.get("cmp") != "kind":
                continue
            target = test["cs"][0]
            members = target["tuple"] if isinstance(target, dict) and "tuple" in target else [target]
            literals, enum_members = [], []
            for m in members:
                if str_of(m) is not None:
                    literals.append(str_of(m))
                elif isinstance(m, str) and m.count(".") == 1 and m.startswith("NodeKind."):
                    enum_members.append(m.split(".", 1)[1])
            products = sorted(
                {
                    row[4].split(".")[1]
                    for row in imports
                    if line <= row[1] <= body_end and row[6] is not None and row[4] and row[4].startswith("products.")
                }
            )
            out.append((literals, enum_members, products))
        return out

    def enum_members(self) -> dict[str, dict[str, str]]:
        out = {}
        assigns = self.record.get("assigns", [])
        for idx, d in enumerate(self.defs):
            if d["k"] != "class" or not self.is_direct_child(idx, -1) or not d["n"].endswith("NodeKind"):
                continue
            members = {}
            for r in assigns:
                if r["s"] != idx or r["op"] != "=" or r.get("f", 0) != (d.get("f", 0) | CLASS) or len(r["t"]) != 1:
                    continue
                target, value = r["t"][0], str_of(r.get("v"))
                if isinstance(target, str) and "." not in target and value is not None:
                    members[target] = value
            out[d["n"]] = members
        return out


# --- call view (spike, unchanged) --------------------------------------------------------------
@dataclass(frozen=False)
class Reads:
    files: set[str] = field(default_factory=set)
    symbols: set[str] = field(default_factory=set)

    def add(self, other: Reads) -> None:
        self.files |= other.files
        self.symbols |= other.symbols


class CallView:
    def __init__(self, facts: dict[str, FileFacts]) -> None:
        self.facts = facts
        self.memo: dict[tuple, tuple[bool, list[str], Reads]] = {}

    def body_executes(self, body: Body, dispatchers: frozenset[str]) -> bool:
        return body.client or bool(body.calls & dispatchers)

    def targets(self, module: str, name: str, local: bool, reads: Reads) -> list[tuple[str, Body]]:
        found: list[tuple[str, Body]] = []
        ff = self.facts.get(module)
        if ff is None:
            return found
        reads.files.add(ff.rel)
        reads.symbols.add(f"{module}:<imports>")
        if local:
            for fid in ff.top_functions.get(name, []) + ff.class_methods.get(name, []):
                found.append((module, ff.bodies[fid]))
                reads.symbols.add(f"{module}:{fid}")
        for edge in ff.imports:
            if edge.bound != name:
                continue
            target = self.facts.get(edge.module)
            if target is None:
                continue
            reads.files.add(target.rel)
            for fid in target.top_functions.get(edge.exported, []):
                found.append((edge.module, target.bodies[fid]))
                reads.symbols.add(f"{edge.module}:{fid}")
        return found

    def executes(self, module: str, name: str, patched: frozenset[str], depth: int = 0, local: bool = False):
        if name in patched:
            return False, [], Reads(), True
        if depth >= MAX_CALL_DEPTH or module not in self.facts:
            return False, [], Reads(), False
        key = (module, name, patched, local)
        if key in self.memo:
            ok, chain, reads = self.memo[key]
            return ok, chain, reads, False
        reads = Reads()
        self.memo[key] = (False, [], reads)
        cut = False
        for target_module, body in self.targets(module, name, local, reads):
            hop = f"{target_module}:{body.fid}"
            if self.body_executes(body, DISPATCH_CALLS - patched):
                self.memo[key] = (True, [hop], reads)
                return True, [hop], reads, cut
            for call in sorted(body.calls):
                ok, chain, sub, subcut = self.executes(target_module, call, patched, depth + 1, local=True)
                reads.add(sub)
                cut = cut or subcut
                if ok:
                    self.memo[key] = (True, [hop, *chain], reads)
                    return True, [hop, *chain], reads, cut
        return False, [], reads, cut


@dataclass(frozen=False)
class DriveEdge:
    label: str
    test_module: str
    trigger: str
    detail: str
    site: str
    line: int
    execution: str
    chain: list[str]
    patched: bool
    reads: Reads

    @property
    def counted(self) -> bool:
        return self.execution != "none"


class Deriver:
    def __init__(self, facts: dict[str, FileFacts]) -> None:
        self.facts = facts
        self.by_rel = {f.rel: f for f in facts.values()}
        self.view = CallView(facts)
        self.registry_files = {QUERY_DISPATCHER, NODE_KIND_ENUM}

    def read_registry(self) -> None:
        enum_ff = self.by_rel[NODE_KIND_ENUM]
        disp_ff = self.by_rel[QUERY_DISPATCHER]
        node_kinds: dict[str, str] = {}
        for members in enum_ff.enum_members.values():
            node_kinds |= members
        kinds: dict[str, set[str]] = defaultdict(set)
        for literals, members, products in disp_ff.dispatch_branches:
            names = literals + [node_kinds[m] for m in members if m in node_kinds]
            for k in names:
                kinds[k].update(products)
        self.kinds = {k: frozenset(v) for k, v in kinds.items() if v}
        self.members = {m: k for m, k in node_kinds.items() if k in self.kinds}
        self.enums = frozenset(enum_ff.enum_members) | {"NodeKind"}
        self.locations = sorted(
            {
                (p.split("/")[1], loc)
                for loc in COMPUTED_WIRING_LOCATIONS
                for p in [f.rel for f in self.facts.values()]
                if p.startswith("products/") and p.count("/") >= 2 and p.split("/", 2)[2].startswith(loc.rstrip("/"))
            }
        )
        self.query_products = {p for p, loc in self.locations if loc == QUERY_WIRING_LOCATION}

    def module_exists(self, dotted: str) -> bool:
        return dotted in self.facts

    def grow_origins(self) -> None:
        seeds: dict[tuple[str, str], str] = {}
        for product, loc in self.locations:
            label = f"{product}:{loc}"
            prefix = f"products/{product}/{loc}"
            for ff in self.facts.values():
                if ff.rel.startswith(prefix):
                    for name in ff.top_names:
                        seeds[(ff.dotted, name)] = label
            root = f"products/{product}/{loc.rstrip('/')}"
            facade = f"products/{product}/backend/facade/"
            for ff in self.facts.values():
                if not ff.rel.startswith(facade) or is_test_path(ff.rel[len(facade) :]):
                    continue
                for name, value in ff.lazy_map.items():
                    for cand in [value, *(p + value for p in ff.lazy_prefixes), f"products.{product}.backend.{value}"]:
                        if self.module_exists(cand):
                            src = self.facts[cand].rel
                            if src.startswith(root + "/") or src == root + ".py":
                                seeds[(ff.dotted, name)] = label
                            break
        self.origins: dict[tuple[str, str], str] = dict(seeds)
        self.parent: dict[tuple[str, str], tuple[str, str] | None] = dict.fromkeys(seeds)
        while True:
            grew = False
            for ff in self.facts.values():
                for e in ff.imports:
                    label = self.origins.get((e.module, e.exported))
                    if label is None:
                        continue
                    local = (ff.dotted, e.bound)
                    if self.origins.get(local) != label:
                        self.origins[local] = label
                        self.parent[local] = (e.module, e.exported)
                        grew = True
            if not grew:
                break
        self.origin_modules = {m for m, _ in self.origins}

    def via_files(self, export: tuple[str, str]) -> set[str]:
        files = set()
        cur: tuple[str, str] | None = export
        seen = set()
        while cur is not None and cur not in seen:
            seen.add(cur)
            if cur[0] in self.facts:
                files.add(self.facts[cur[0]].rel)
            cur = self.parent.get(cur)
        return files

    @staticmethod
    def owner(rel: str) -> str | None:
        parts = rel.split("/")
        return parts[1] if parts[0] == "products" and len(parts) > 2 else None

    def kind_of(self, m: Mention, constructors: dict[str, str], enum_bases: frozenset[str]) -> str | None:
        if m.shape in ("const", "tag"):
            return m.value if m.value in self.kinds else None
        if m.shape == "attr":
            return self.members.get(m.value) if m.base in enum_bases else None
        if m.shape == "call":
            if m.value in self.kinds:
                return m.value
            return constructors.get(m.value)
        return None

    def derive_file(self, ff: FileFacts) -> list[DriveEdge]:
        edges: list[DriveEdge] = []
        owner = self.owner(ff.rel)
        base_reads = {ff.rel}
        prefixes = [
            (f"products.{p}." + loc.rstrip("/").replace("/", ".") + ".", f"{p}:{loc}", p) for p, loc in self.locations
        ]
        for _alias, module in ff.module_aliases:
            for prefix, label, product in prefixes:
                if module.startswith(prefix) and self.module_exists(module) and owner != product:
                    edges.append(
                        DriveEdge(
                            label,
                            ff.dotted,
                            "module-import",
                            module.rsplit(".", 1)[-1],
                            "<module>",
                            0,
                            "n/a",
                            [],
                            False,
                            Reads(base_reads | {self.facts[module].rel}, {f"{module}:<exists>"}),
                        )
                    )
        bound: dict[str, tuple[str, tuple[str, str]]] = {}
        for e in ff.imports:
            label = self.origins.get((e.module, e.exported))
            if label is not None:
                bound[e.bound] = (label, (e.module, e.exported))
        for name, (label, export) in bound.items():
            if owner == label.split(":")[0] or not ff.name_uses.get(name):
                continue
            reads = Reads(base_reads | self.via_files(export), {f"{export[0]}:{export[1]}"})
            for _ in range(ff.name_uses[name]):
                edges.append(DriveEdge(label, ff.dotted, "name", name, "<module>", 0, "n/a", [], False, reads))
        aliases = {a: m for a, m in ff.module_aliases if m in self.origin_modules}
        for (base, attr), n in ff.attr_uses.items():
            module = aliases.get(base)
            label = self.origins.get((module, attr)) if module else None
            if label is None or ":" not in label or owner == label.split(":")[0]:
                continue
            reads = Reads(base_reads | self.via_files((module, attr)), {f"{module}:{attr}"})
            for _ in range(n):
                edges.append(DriveEdge(label, ff.dotted, "name", attr, "<module>", 0, "n/a", [], False, reads))
        edges += self.kind_edges(ff, owner)
        return edges

    def executions(self, ff: FileFacts, dispatchers: frozenset[str], fid: str | None, cls: str | None):
        reads = Reads({ff.rel}, set())
        body = ff.bodies.get(fid) if fid else None
        scope_key = cls if cls is not None else "<module>"
        scope = ff.scopes[scope_key]
        if body is None or not body.name.startswith("test"):
            reads.symbols.add(f"{ff.dotted}:{scope_key}")
            ok = scope.client or bool(scope.calls & dispatchers)
            return ("direct" if ok else "none"), [], False, reads
        helpers: dict[str, list[str]] = {n: [fids[-1]] for n, fids in ff.top_functions.items()}
        if cls is not None:
            for n, fids in self.class_helpers(ff, cls).items():
                helpers.setdefault(n, []).extend(fids)
        seen: set[str] = set()
        pending = [(body.fid, [])]
        outward: set[str] = set()
        while pending:
            cur, chain = pending.pop()
            if cur in seen:
                continue
            seen.add(cur)
            b = ff.bodies[cur]
            reads.symbols.add(f"{ff.dotted}:{cur}")
            if b.client or b.calls & dispatchers:
                return ("direct" if not chain else "via-helper"), chain, False, reads
            for name in b.calls:
                if name in helpers:
                    pending.extend((h, [*chain, f"{ff.dotted}:{h}"]) for h in helpers[name])
                else:
                    outward.add(name)
        patched = frozenset(body.patches | scope.rest_patches)
        cut = bool(outward & patched)
        for name in sorted(outward - patched):
            ok, chain, sub, subcut = self.view.executes(ff.dotted, name, patched)
            reads.add(sub)
            cut = cut or subcut
            if ok:
                return "via-helper", chain, cut, reads
        return "none", [], cut, reads

    def class_helpers(self, ff: FileFacts, cls: str) -> dict[str, list[str]]:
        helpers: dict[str, list[str]] = {}
        pending = [cls]
        seen = set()
        while pending:
            cur = pending.pop()
            if cur in seen or cur not in ff.scopes:
                continue
            seen.add(cur)
            for n, fids in ff.scopes[cur].methods.items():
                helpers.setdefault(n, []).extend(fids)
            pending.extend(ff.top_classes[b] for b in ff.scopes[cur].bases if b in ff.top_classes)
        return helpers

    def kind_edges(self, ff: FileFacts, owner: str | None) -> list[DriveEdge]:
        if not ff.is_test:
            return []
        constructors = {e.bound: e.exported for e in ff.imports if e.exported in self.kinds}
        enum_bases = frozenset({e.bound for e in ff.imports if e.exported in self.enums}) | self.enums
        dispatchers = frozenset({e.bound for e in ff.imports if e.exported in DISPATCH_CALLS}) | DISPATCH_CALLS
        edges: list[DriveEdge] = []
        cache: dict[tuple, tuple] = {}
        resolved_fids: set[str | None] = set()
        for m in ff.mentions:
            kind = self.kind_of(m, constructors, enum_bases)
            if kind is None:
                continue
            resolved_fids.add(m.fid)
            key = (m.fid, m.cls)
            if key not in cache:
                cache[key] = self.executions(ff, dispatchers, m.fid, m.cls)
            execution, chain, patched, reads = cache[key]
            reads = Reads(reads.files | self.registry_files, reads.symbols | {"<registry>"})
            for product in sorted(self.kinds[kind]):
                if product not in self.query_products or owner == product:
                    continue
                edges.append(
                    DriveEdge(
                        f"{product}:{QUERY_WIRING_LOCATION}",
                        ff.dotted,
                        "query-kind",
                        kind,
                        m.fid or (m.cls or "<module>"),
                        m.line,
                        execution,
                        chain,
                        patched,
                        reads,
                    )
                )
        unresolved = 0
        ureads = Reads({ff.rel} | self.registry_files, {"<registry>"})
        for p in ff.pending:
            if p.dispatch_callee is not None and p.dispatch_callee not in dispatchers and not p.under_query_key:
                continue
            body = ff.bodies.get(p.fid) if p.fid else None
            if p.value_shape == "name" and body is not None and p.value_name in body.const_params:
                continue
            if p.value_shape == "attr" and p.value_base in enum_bases:
                continue
            if p.fid in resolved_fids:
                continue
            execution, _, _, reads = self.executions(ff, dispatchers, p.fid, p.cls)
            ureads.add(reads)
            unresolved += execution != "none"
        if unresolved:
            for product in sorted(self.query_products):
                if owner != product:
                    for _ in range(unresolved):
                        edges.append(
                            DriveEdge(
                                f"{product}:{QUERY_WIRING_LOCATION}",
                                ff.dotted,
                                "unresolved-kind",
                                "unresolved-kind",
                                "<module>",
                                0,
                                "direct",
                                [],
                                False,
                                ureads,
                            )
                        )
        return edges


DRIVES_PRODUCER = "posthog.drives@1"
TEST_CONTEXT = frozenset({Context.TEST})


def scanned(path: str) -> bool:
    return SITE_MARKER not in path and path.startswith(SCANNED_ROOTS)


def build_facts(core: Core) -> dict[str, FileFacts]:
    facts: dict[str, FileFacts] = {}
    for path in sorted(core.store.records):
        record = core.store.records[path]
        if not scanned(path) or record.get("status") != "ok":
            continue
        ff = FactAdapter(record).run()
        facts[ff.dotted] = ff
    return facts


class DrivesProducer:
    """Runs the deriver over every test file and turns each drive into an edge."""

    def __init__(self, core: Core) -> None:
        self.core = core

    @staticmethod
    def location_module(label: str) -> str:
        product, _, location = label.partition(":")
        return ".".join(["products", product, *location.rstrip("/").split("/")])

    def edge(self, drive: DriveEdge, path: str) -> Edge:
        unresolved = drive.trigger == "unresolved-kind"
        scope = drive.site.split("@", 1)[0]
        return Edge(
            kind="drives",
            site=Site(path, drive.test_module, scope, drive.line),
            surface=drive.detail,
            targets=(EntityId(LANGUAGE_PYTHON, self.location_module(drive.label)),),
            resolution=Resolution.CANDIDATES if unresolved else Resolution.RESOLVED,
            contexts=TEST_CONTEXT,
            producer=DRIVES_PRODUCER,
            shape=drive.trigger,
            shape_detail="unresolved-kind" if unresolved else drive.detail,
            attrs=(
                ("location", drive.label),
                ("execution", drive.execution),
                ("chain", " > ".join(drive.chain)),
                ("patched", "true" if drive.patched else "false"),
            ),
        )

    def produce(self) -> list[Edge]:
        facts = build_facts(self.core)
        deriver = Deriver(facts)
        deriver.read_registry()
        deriver.grow_origins()
        out: list[Edge] = []
        for ff in facts.values():
            if ff.is_test:
                out.extend(self.edge(drive, ff.rel) for drive in deriver.derive_file(ff))
        return out
