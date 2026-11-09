"""`drives` edges: a test outside a product that executes the product's wiring.

Port of the spike drives channel. The deriver logic is unchanged; the output is hoglint edges,
one per drive, with the wiring location, the trigger, and how the test reaches the dispatcher.

A file's facts are read on first use, so a run reads only what a drive can come from:
- every file's `from` imports, indexed by imported name, for the re-export closure of the
  wiring names;
- the dispatcher and the NodeKind enum, for the registry of query kinds;
- the test files that may name a kind (a prefilter on their fact text, see `TestFilter`), pass a
  `kind` entry, or import a wiring name or module;
- the bodies of the helpers the call view reaches, one module at a time.
"""

from __future__ import annotations

import re
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import cached_property

from hoglint.core.build import CoreBuild
from hoglint.core.producers import chain_tokens
from hoglint.core.records import RecordBodies, is_const, last_name, str_literal
from hoglint.core.sites import FileScope
from hoglint.core.symbols import absolute_import
from hoglint.model import LANGUAGE_PYTHON, Context, Edge, EntityId, Resolution, Site
from hoglint.native.queries import PackQueries, Words
from hoglint.presets_py.posthog.layout import location_module, product_of_path

QUERY_DISPATCHER = "posthog/hogql_queries/query_runner.py"
NODE_KIND_ENUM = "posthog/schema_enums.py"
QUERY_WIRING_LOCATION = "backend/hogql_queries/"
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
MAX_CALL_DEPTH = 16
UNKNOWN_EXECUTION = "unknown"
CALL_DEPTH_REASON = "call-depth-limit"
DRIVES_PRODUCER = "posthog.drives@1"
MODULE_SCOPE = "<module>"

# The fact kinds where a query kind is spelled, and where a NodeKind member is.
KIND_TEXT = ("names", "tags", "callees", "imports")
MEMBER_TEXT = ("refs", "uses")

CAMEL = re.compile(r"^[A-Z]\w*$")
UPPER = re.compile(r"^[A-Z][A-Z0-9_]*$")


@dataclass(frozen=True)
class ImportEdge:
    module: str
    exported: str
    bound: str


@dataclass(frozen=True)
class Body:
    fid: str
    name: str
    calls: frozenset[str]
    client: bool
    patches: frozenset[str] = frozenset()
    const_params: frozenset[str] = frozenset()


@dataclass(frozen=True)
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


@dataclass(frozen=True)
class TestFacts:
    """What only a test file needs: where kinds are named, and how its scopes reach a dispatcher."""

    scopes: dict[str, ScopeFacts]
    mentions: list[Mention]
    pending: list[Pending]
    name_uses: Counter[str]
    attr_uses: Counter[tuple[str, str]]


class FileFacts:
    """One module's facts, each read on first use: most modules give only their imports."""

    def __init__(self, reader: FactReader) -> None:
        self.reader = reader
        self.rel = reader.rel
        self.dotted = reader.scope.module
        self.is_test = reader.is_test

    @cached_property
    def import_facts(self) -> tuple[list[ImportEdge], list[tuple[str, str]], set[str]]:
        return self.reader.read_imports()

    @property
    def imports(self) -> list[ImportEdge]:
        return self.import_facts[0]

    @property
    def module_aliases(self) -> list[tuple[str, str]]:
        return self.import_facts[1]

    @cached_property
    def top(self) -> tuple[list[str], dict[str, list[str]], dict[str, list[str]], dict[str, str]]:
        return self.reader.read_top_names()

    @property
    def top_names(self) -> list[str]:
        return self.top[0]

    @property
    def top_functions(self) -> dict[str, list[str]]:
        return self.top[1]

    @property
    def class_methods(self) -> dict[str, list[str]]:
        return self.top[2]

    @property
    def top_classes(self) -> dict[str, str]:
        return self.top[3]

    @cached_property
    def lazy_map(self) -> dict[str, str]:
        return self.reader.lazy_map()

    @cached_property
    def test(self) -> TestFacts | None:
        if not self.is_test:
            return None
        return self.reader.read_test(self.import_facts[2], self.reader.read_scopes(*self.reader.calls))

    @cached_property
    def bodies(self) -> dict[str, Body]:
        """Function bodies by fid. The call view reaches few non-test files."""
        return self.reader.read_bodies()

    @cached_property
    def dispatch_branches(self) -> list[tuple[list[str], list[str], list[str]]]:
        return self.reader.dispatch_branches() if self.rel == QUERY_DISPATCHER else []

    @cached_property
    def enum_members(self) -> dict[str, dict[str, str]]:
        return self.reader.enum_members() if self.rel == NODE_KIND_ENUM else {}


class FactReader:
    """One file's records in, one FileFacts out (the spike's L1 view, rebuilt from fact lists)."""

    def __init__(self, scope: FileScope, build: CoreBuild) -> None:
        self.scope = scope
        self.build = build
        self.record = scope.record
        self.rel = scope.path
        self.is_test = Context.TEST in scope.base_contexts
        self.ancestry_cache: dict[int, list[int]] = {}

    @cached_property
    def defs(self) -> list[dict]:
        return self.scope.defs

    @cached_property
    def bodies(self) -> RecordBodies:
        return self.build.bodies(self.record)

    @cached_property
    def quals(self) -> list[str]:
        return self.scope.qualnames

    @cached_property
    def fids(self) -> dict[int, str]:
        return {i: f"{self.quals[i]}@{d['l']}" for i, d in enumerate(self.defs) if d["k"] != "class"}

    def lazy_map(self) -> dict[str, str]:
        """The PEP 562 lazy names of a product module's table."""
        if not self.rel.startswith("products/"):
            return {}
        table = self.build.tables.get(self.scope.module)
        return table.lazy if table is not None and self.build.index.path_of.get(self.scope.module) == self.rel else {}

    def ancestry(self, idx: int) -> list[int]:
        """idx and every enclosing def, without -1."""
        cached = self.ancestry_cache.get(idx)
        if cached is None:
            cached = [scope for scope in self.scope.ancestry(idx) if scope != -1]
            self.ancestry_cache[idx] = cached
        return cached

    @staticmethod
    def site(s: int, h: int) -> int:
        """The innermost def whose ast subtree holds the site (a header belongs to its def)."""
        return h if h != -1 else s

    def enclosing(self, s: int, h: int) -> tuple[str | None, str | None]:
        fid = cls = None
        for idx in self.ancestry(self.site(s, h)):
            if self.defs[idx]["k"] != "class":
                if fid is None:
                    fid = self.fids[idx]
            elif cls is None:
                cls = self.quals[idx]
            if fid is not None and cls is not None:
                break
        return fid, cls

    def read_imports(self) -> tuple[list[ImportEdge], list[tuple[str, str]], set[str]]:
        imports: list[ImportEdge] = []
        aliases: list[tuple[str, str]] = []
        bound_names: set[str] = set()
        # The spike's stack walk visits the leaves right to left: reverse source order.
        for row in reversed(self.record.get("imports", [])):
            _, _, _, _, mod, level, name, asname = row
            if name is None:
                aliases.append((asname or mod, mod))
                bound_names.add(asname or mod)
                continue
            module = absolute_import(self.scope.module, self.scope.is_package, mod, level)
            if module is None:
                continue
            bound = asname or name
            imports.append(ImportEdge(module, name, bound))
            aliases.append((bound, f"{module}.{name}"))
            bound_names.add(bound)
        return imports, aliases, bound_names

    @cached_property
    def calls(self) -> tuple[dict[int, set[str]], set[int], set[str], bool]:
        """Callee names per def (spread up its ancestry), defs with a test-client call, and the same for the module."""
        calls_in: dict[int, set[str]] = defaultdict(set)
        client_in: set[int] = set()
        module_calls: set[str] = set()
        module_client = False
        site_calls: dict[int, set[str]] = defaultdict(set)
        site_client: set[int] = set()
        for s, h, callee, _count, _l, _c in self.record.get("callees", []):
            site = self.site(s, h)
            name = last_name(callee)
            if name:
                module_calls.add(name)
                site_calls[site].add(name)
            if is_client_call(callee):
                module_client = True
                site_client.add(site)
        for site in site_calls.keys() | site_client:
            if site == -1:
                continue
            for idx in self.ancestry(site):
                calls_in[idx].update(site_calls.get(site, ()))
                if site in site_client:
                    client_in.add(idx)
        return calls_in, client_in, module_calls, module_client

    def read_patches(self) -> tuple[dict[int, set[str]], list[tuple[int, str]]]:
        patches_in: dict[int, set[str]] = defaultdict(set)
        patch_rows: list[tuple[int, str]] = []
        for s, h, _l, _c, _f, callee, args, _kws in self.record.get("calls", []):
            if last_name(callee) not in PATCH_CALLS:
                continue
            site = self.site(s, h)
            for arg in args:
                value = str_literal(arg)
                if value is not None:
                    leaf = value.rsplit(".", 1)[-1]
                    patch_rows.append((site, leaf))
                    for idx in self.ancestry(site) if site != -1 else []:
                        patches_in[idx].add(leaf)
        return patches_in, patch_rows

    def read_top_names(self) -> tuple[list[str], dict[str, list[str]], dict[str, list[str]], dict[str, str]]:
        top_functions: dict[str, list[str]] = defaultdict(list)
        class_methods: dict[str, list[str]] = defaultdict(list)
        top_classes: dict[str, str] = {}
        top_names: list[str] = []
        for idx in self.bodies.children(-1):
            d = self.defs[idx]
            top_names.append(d["n"])
            if d["k"] != "class":
                top_functions[d["n"]].append(self.fids[idx])
                continue
            top_classes[d["n"]] = d["n"]
            for child in self.bodies.children(idx):
                if self.defs[child]["k"] != "class":
                    class_methods[self.defs[child]["n"]].append(self.fids[child])
        for row in self.bodies.assigns(-1):
            if row["op"] in ("=", ":"):
                top_names.extend(t for t in row["t"] if isinstance(t, str) and "." not in t)
        names = [n for n in top_names if not n.startswith("_")]
        return names, dict(top_functions), dict(class_methods), top_classes

    def read_scopes(
        self, calls_in: dict[int, set[str]], client_in: set[int], module_calls: set[str], module_client: bool
    ) -> dict[str, ScopeFacts]:
        _, patch_rows = self.read_patches()
        scopes = {MODULE_SCOPE: self.scope_facts(-1, frozenset(module_calls), module_client, patch_rows)}
        for idx, d in enumerate(self.defs):
            if d["k"] == "class":
                bases = [b for b in d.get("bases", []) if isinstance(b, str) and "." not in b]
                scopes[self.quals[idx]] = self.scope_facts(
                    idx, frozenset(calls_in[idx]), idx in client_in, patch_rows, bases
                )
        return scopes

    def scope_facts(
        self,
        idx: int,
        calls: frozenset[str],
        client: bool,
        patch_rows: list[tuple[int, str]],
        bases: list[str] | None = None,
    ) -> ScopeFacts:
        methods: dict[str, list[str]] = defaultdict(list)
        test_children: set[int] = set()
        for child in self.bodies.children(idx):
            d = self.defs[child]
            if d["k"] != "class":
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

    def read_bodies(self) -> dict[str, Body]:
        calls_in, client_in, _, _ = self.calls
        bodies: dict[str, Body] = {}
        patches_in: dict[int, set[str]] = self.read_patches()[0] if self.is_test else {}
        for idx, fid in self.fids.items():
            calls, client = frozenset(calls_in[idx]), idx in client_in
            if not self.is_test:
                bodies[fid] = Body(fid, self.defs[idx]["n"], calls, client)
                continue
            params = self.defs[idx].get("params", [])
            const_params = frozenset(p[0] for p in params if len(p) > 3 and is_const(p[3]))
            patches = frozenset(patches_in.get(idx, ()))
            bodies[fid] = Body(fid, self.defs[idx]["n"], calls, client, patches, const_params)
        return bodies

    def read_test(self, bound: set[str], scopes: dict[str, ScopeFacts]) -> TestFacts:
        mentions: list[Mention] = []
        pending: list[Pending] = []
        name_uses: Counter[str] = Counter()
        attr_uses: Counter[tuple[str, str]] = Counter()
        record = self.record
        for s, h, value, count, line, _c in record.get("names", []):
            if CAMEL.match(value):
                fid, cls = self.enclosing(s, h)
                mentions.extend([Mention("const", value, None, fid, cls, line)] * count)
        for s, h, line, _c, _f, name in record.get("tags", []):
            fid, cls = self.enclosing(s, h)
            mentions.append(Mention("tag", name, None, fid, cls, line))
        for s, h, callee, count, line, _c in record.get("callees", []):
            name = last_name(callee)
            if name and CAMEL.match(name):
                fid, cls = self.enclosing(s, h)
                mentions.extend([Mention("call", name, None, fid, cls, line)] * count)
        # Name and attribute uses of import-bound roots: refs (tracked) and uses (the rest).
        for s, h, line, _c, _f, root, chain, _up in record.get("refs", []):
            uses = RootUse(root, chain_tokens(chain), 1, s, h, line)
            self.root_use(uses, bound, mentions, name_uses, attr_uses)
        for s, h, root, attrs, count, line, _c in record.get("uses", []):
            self.root_use(RootUse(root, chain_tokens(attrs), count, s, h, line), bound, mentions, name_uses, attr_uses)
        for s, h, _line, _c, _f, _key, value, chain, up in record.get("watched", []):
            found = self.pending(s, h, value, chain, up)
            if found is not None:
                pending.append(found)
        return TestFacts(scopes, mentions, pending, name_uses, attr_uses)

    def pending(self, s: int, h: int, value: object, chain: str, up: str) -> Pending | None:
        """A `kind` dict entry passed to a call or under a query key, whose value is not a constant."""
        target = pending_target(value, chain, up)
        if target is None:
            return None
        callee, under = target
        fid, cls = self.enclosing(s, h)
        if isinstance(value, str) and "." not in value:
            return Pending(callee, under, "name", value, None, fid, cls)
        if isinstance(value, str):
            parts = value.split(".")
            if parts[-1] == "value":
                parts = parts[:-1]
            if len(parts) >= 2:
                return Pending(callee, under, "attr", parts[-1], parts[-2], fid, cls)
            return Pending(callee, under, "other", None, None, fid, cls)
        if value == {"o": "Attribute"}:
            # Attribute on a non-chain: the attr name is not in the SV form; base is None either way.
            return Pending(callee, under, "attr", None, None, fid, cls)
        return Pending(callee, under, "other", None, None, fid, cls)

    def root_use(
        self,
        use: RootUse,
        bound: set[str],
        mentions: list[Mention],
        name_uses: Counter[str],
        attr_uses: Counter[tuple[str, str]],
    ) -> None:
        if use.root in bound:
            name_uses[use.root] += use.count
        fid = cls = None
        located = False
        base: str | None = use.root
        dotted: str | None = use.root
        for token in use.tokens:
            if token[0] != ".":
                base = None
                dotted = None
                continue
            attr = token[1:]
            if dotted is not None and use.root in bound:
                attr_uses[(dotted, attr)] += use.count
            if UPPER.match(attr):
                if not located:
                    fid, cls = self.enclosing(use.s, use.h)
                    located = True
                mentions.extend([Mention("attr", attr, base, fid, cls, use.line)] * use.count)
            base = attr
            dotted = f"{dotted}.{attr}" if dotted is not None else None

    def dispatch_branches(self) -> list[tuple[list[str], list[str], list[str]]]:
        out = []
        imports = self.record.get("imports", [])
        for _s, _h, line, _c, _f, body_end, test in self.record.get("branches", []):
            if test.get("cmp") != "kind":
                continue
            target = test["cs"][0]
            members = target["tuple"] if isinstance(target, dict) and "tuple" in target else [target]
            literals, enum_members = [], []
            for m in members:
                text = str_literal(m)
                if text is not None:
                    literals.append(text)
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
        for idx in self.bodies.children(-1):
            d = self.defs[idx]
            if d["k"] != "class" or not d["n"].endswith("NodeKind"):
                continue
            members = {}
            for r in self.bodies.assigns(idx):
                if r["op"] != "=" or len(r["t"]) != 1:
                    continue
                target, value = r["t"][0], str_literal(r.get("v"))
                if isinstance(target, str) and "." not in target and value is not None:
                    members[target] = value
            out[d["n"]] = members
        return out


@dataclass(frozen=True)
class RootUse:
    root: str
    tokens: list[str]
    count: int
    s: int
    h: int
    line: int


def pending_target(value: object, chain: str, up: str) -> tuple[str | None, bool] | None:
    """(the callee a `kind` entry is passed to, whether it sits under a query key), or None when
    the entry is no pending kind: it is a constant, or neither passed to a call nor under a key."""
    if chain:
        return None
    step = up.split(" ", 1)[0] if up else ""
    callee = last_name(step.split(":", 2)[2]) if step.startswith("arg:") else None
    under = step in ("dict:val:query", "dict:val:source")
    if (callee is None and not under) or is_const(value):
        return None
    return callee, under


def is_client_call(callee: str) -> bool:
    if callee.startswith("?") or "." not in callee:
        return False
    receiver, _, method = callee.rpartition(".")
    return method in TEST_CLIENT_METHODS and receiver.endswith("client")


def alias_modules(scope: FileScope) -> list[str]:
    """The module each import of a file names: `import a.b` names a.b, `from a import b` a.b."""
    out = []
    for _, _, _, _, target, level, name, _ in scope.record.get("imports", []):
        if name is None:
            out.append(target)
            continue
        module = absolute_import(scope.module, scope.is_package, target, level)
        if module is not None:
            out.append(f"{module}.{name}")
    return out


class ImportIndex:
    """The `from` imports of every file by imported name, each file's rows in reverse source order
    (the spike's walk). A row's absolute module is worked out only when the re-export closure asks
    for its name."""

    def __init__(self, facts: dict[str, FileFacts]) -> None:
        self.by_name: dict[str, list[tuple[FileFacts, list]]] = defaultdict(list)
        self.found: dict[tuple[str, str], list[tuple[FileFacts, str]]] = {}
        for ff in facts.values():
            for row in reversed(ff.reader.record.get("imports", [])):
                if row[6] is not None:
                    self.by_name[row[6]].append((ff, row))

    def importers(self, module: str, name: str) -> list[tuple[FileFacts, str]]:
        """Files with `from <module> import <name> [as bound]`, and the bound name, in file order."""
        key = (module, name)
        found = self.found.get(key)
        if found is not None:
            return found
        found = self.found[key] = []
        for ff, row in self.by_name.get(name, ()):
            scope = ff.reader.scope
            if absolute_import(scope.module, scope.is_package, row[4], row[5]) == module:
                found.append((ff, row[7] or name))
        return found


class TestFilter:
    """Whether a test file may name a query kind that drives another product, by its fact text:
    the kind as a string, a tag, a callee or an imported name (a constructor), or its NodeKind
    member as an attribute. A file it rejects has no mention the deriver turns into a drive."""

    def __init__(self, queries: PackQueries, kinds: dict[str, frozenset[str]], members: dict[str, str]) -> None:
        self.queries = queries
        self.kinds = kinds
        self.members = members
        self.words: dict[str | None, tuple[Words, Words]] = {}

    def words_for(self, owner: str | None) -> tuple[Words, Words]:
        """The kinds and members whose drives reach a product other than `owner`."""
        found = self.words.get(owner)
        if found is None:
            kinds = {kind for kind, products in self.kinds.items() if products - {owner}}
            members = [member for member, kind in self.members.items() if kind in kinds]
            found = self.words[owner] = (Words.ending(kinds), Words.attributes(members))
        return found

    def names_a_kind(self, ff: FileFacts, owner: str | None) -> bool:
        kinds, members = self.words_for(owner)
        scope = ff.reader.scope
        return self.queries.may_mention(scope, KIND_TEXT, kinds) or self.queries.may_mention(
            scope, MEMBER_TEXT, members
        )


# --- call view ----------------------------------------------------------------------------------
@dataclass(frozen=True)
class Reach:
    """What the call view finds from one called name: a chain of hops to a dispatcher, or a miss.

    `cut`: a patched name stopped a call on the way. `exhausted`: names were left to walk when the
    depth limit stopped the walk, so a miss is unknown, not none."""

    ok: bool
    chain: tuple[str, ...] = ()
    cut: bool = False
    exhausted: bool = False


MISS = Reach(False)
PATCHED = Reach(False, cut=True)

Node = tuple[str, str, bool]  # (module, called name, whether the module's own defs count)


class CallView:
    """Follows a test's outward calls into helper modules, one call level at a time, at most
    MAX_CALL_DEPTH levels deep. Each name is walked once per query, at the first level that
    reaches it, so a query's answer (and its memo) depends only on the call graph and the limit,
    never on which queries ran before it."""

    def __init__(self, facts: dict[str, FileFacts]) -> None:
        self.facts = facts
        self.memo: dict[tuple[str, str, frozenset[str]], Reach] = {}
        self.found_targets: dict[Node, list[tuple[str, Body]]] = {}
        self.imports_by_bound: dict[str, dict[str, list[ImportEdge]]] = {}

    def imports_named(self, ff: FileFacts, name: str) -> list[ImportEdge]:
        table = self.imports_by_bound.get(ff.dotted)
        if table is None:
            table = defaultdict(list)
            for edge in ff.imports:
                table[edge.bound].append(edge)
            self.imports_by_bound[ff.dotted] = table
        return table.get(name, [])

    @staticmethod
    def body_executes(body: Body, dispatchers: frozenset[str]) -> bool:
        return body.client or bool(body.calls & dispatchers)

    def targets(self, node: Node) -> list[tuple[str, Body]]:
        """The bodies a call of `name` in `module` may run: the module's own top functions and
        methods of that name (for a call inside a helper), and top functions it imports by it."""
        found = self.found_targets.get(node)
        if found is not None:
            return found
        module, name, local = node
        found = self.found_targets[node] = []
        ff = self.facts.get(module)
        if ff is None:
            return found
        if local:
            for fid in ff.top_functions.get(name, []) + ff.class_methods.get(name, []):
                found.append((module, ff.bodies[fid]))
        for edge in self.imports_named(ff, name):
            target = self.facts.get(edge.module)
            if target is None:
                continue
            for fid in target.top_functions.get(edge.exported, []):
                found.append((edge.module, target.bodies[fid]))
        return found

    def executes(self, module: str, name: str, patched: frozenset[str]) -> Reach:
        if name in patched:
            return PATCHED
        if module not in self.facts:
            return MISS
        key = (module, name, patched)
        found = self.memo.get(key)
        if found is None:
            found = self.memo[key] = self.walk((module, name, False), patched)
        return found

    def walk(self, start: Node, patched: frozenset[str]) -> Reach:
        """The first body, in level order, that runs a dispatcher, with the hops that reach it."""
        dispatchers = DISPATCH_CALLS - patched
        seen = {start}
        level: list[tuple[Node, tuple[str, ...]]] = [(start, ())]
        cut = False
        for _ in range(MAX_CALL_DEPTH):
            following: list[tuple[Node, tuple[str, ...]]] = []
            for node, chain in level:
                for target_module, body in self.targets(node):
                    hops = (*chain, f"{target_module}:{body.fid}")
                    if self.body_executes(body, dispatchers):
                        return Reach(True, hops, cut)
                    for call in sorted(body.calls):
                        if call in patched:
                            cut = True
                            continue
                        callee = (target_module, call, True)
                        if callee not in seen:
                            seen.add(callee)
                            following.append((callee, hops))
            if not following:
                return Reach(False, (), cut)
            level = following
        return Reach(False, (), cut, exhausted=True)


@dataclass(frozen=True)
class DriveEdge:
    label: str
    test_module: str
    trigger: str
    detail: str
    site: str = MODULE_SCOPE
    line: int = 0
    execution: str = "n/a"
    chain: tuple[str, ...] = ()
    patched: bool = False


class Deriver:
    def __init__(self, facts: dict[str, FileFacts], queries: PackQueries) -> None:
        self.facts = facts
        self.queries = queries
        self.by_rel = {f.rel: f for f in facts.values()}
        self.view = CallView(facts)
        self.imports = ImportIndex(facts)
        self.importer_products: dict[str, set[str]] = defaultdict(set)

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
        location = QUERY_WIRING_LOCATION.rstrip("/")
        self.query_products = sorted(
            {
                rel.split("/")[1]
                for rel in self.by_rel
                if rel.startswith("products/") and rel.count("/") >= 2 and rel.split("/", 2)[2].startswith(location)
            }
        )
        self.labels = {product: f"{product}:{QUERY_WIRING_LOCATION}" for product in self.query_products}
        self.prefixes = [
            (location_module(product, QUERY_WIRING_LOCATION) + ".", label, product)
            for product, label in self.labels.items()
        ]
        labelled = {kind: products & self.labels.keys() for kind, products in self.kinds.items()}
        self.test_filter = TestFilter(self.queries, labelled, self.members)

    def module_exists(self, dotted: str) -> bool:
        return dotted in self.facts

    def seeds(self) -> dict[tuple[str, str], str]:
        """Top-level names of each product's wiring location, and facade lazy names that load them."""
        seeds: dict[tuple[str, str], str] = {}
        for product, label in self.labels.items():
            prefix = f"products/{product}/{QUERY_WIRING_LOCATION}"
            root = f"products/{product}/{QUERY_WIRING_LOCATION.rstrip('/')}"
            facade = f"products/{product}/backend/facade/"
            for ff in self.facts.values():
                if ff.rel.startswith(prefix):
                    for name in ff.top_names:
                        seeds[(ff.dotted, name)] = label
                if not ff.rel.startswith(facade) or ff.is_test:
                    continue
                for name, module in ff.lazy_map.items():
                    src = self.facts[module].rel if module in self.facts else ""
                    if src.startswith(root + "/") or src == root + ".py":
                        seeds[(ff.dotted, name)] = label
        return seeds

    def grow_origins(self) -> None:
        """Every (module, name) that re-exports a seed, through imports. Also notes the products
        each module imports a seed of: only those can make a `name` drive."""
        seeds = self.seeds()
        self.origins: dict[tuple[str, str], str] = dict(seeds)
        work = list(seeds)
        while work:
            export = work.pop()
            label = self.origins[export]
            for importer, bound in self.imports.importers(*export):
                local = (importer.dotted, bound)
                if self.origins.get(local) != label:
                    self.origins[local] = label
                    work.append(local)
        self.origin_modules = {m for m, _ in self.origins}
        self.origin_products: dict[str, set[str]] = defaultdict(set)
        for origin, label in self.origins.items():
            product = label.split(":")[0]
            self.origin_products[origin[0]].add(product)
            for importer, _ in self.imports.importers(*origin):
                self.importer_products[importer.dotted].add(product)

    def may_drive(self, ff: FileFacts) -> bool:
        """False only for a test file that can make no drive. Each test is a part of a drive's
        own condition: another product's wiring name imported, a module of another product's
        wiring or re-exports aliased, a `kind` entry that may count, or a kind named."""
        owner = product_of_path(ff.rel)
        if self.importer_products.get(ff.dotted, set()) - {owner}:
            return True
        if self.may_count_kind(ff) or self.test_filter.names_a_kind(ff, owner):
            return True
        prefixes = tuple(prefix for prefix, _, product in self.prefixes if product != owner)
        for module in alias_modules(ff.reader.scope):
            if module.startswith(prefixes) or self.origin_products.get(module, set()) - {owner}:
                return True
        return False

    def may_count_kind(self, ff: FileFacts) -> bool:
        """A `kind` entry that is a pending kind, passed to a dispatcher or under a query key."""
        if not self.queries.has_rows(ff.reader.scope, "watched"):
            return False
        dispatchers = frozenset({e.bound for e in ff.imports if e.exported in DISPATCH_CALLS}) | DISPATCH_CALLS
        for *_, value, chain, up in ff.reader.record.get("watched", []):
            target = pending_target(value, chain, up)
            if target is not None and (target[1] or target[0] is None or target[0] in dispatchers):
                return True
        return False

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

    def derive_file(self, ff: FileFacts, test: TestFacts) -> list[DriveEdge]:
        edges: list[DriveEdge] = []
        owner = product_of_path(ff.rel)
        for _alias, module in ff.module_aliases:
            for prefix, label, product in self.prefixes:
                if module.startswith(prefix) and self.module_exists(module) and owner != product:
                    edges.append(DriveEdge(label, ff.dotted, "module-import", module.rsplit(".", 1)[-1]))
        bound: dict[str, str] = {}
        for e in ff.imports:
            label = self.origins.get((e.module, e.exported))
            if label is not None:
                bound[e.bound] = label
        for name, label in bound.items():
            if owner == label.split(":")[0] or not test.name_uses.get(name):
                continue
            edges.extend(DriveEdge(label, ff.dotted, "name", name) for _ in range(test.name_uses[name]))
        aliases = {a: m for a, m in ff.module_aliases if m in self.origin_modules}
        for (base, attr), n in test.attr_uses.items():
            module = aliases.get(base)
            label = self.origins.get((module, attr)) if module else None
            if module is None or label is None or ":" not in label or owner == label.split(":")[0]:
                continue
            edges.extend(DriveEdge(label, ff.dotted, "name", attr) for _ in range(n))
        return edges + self.kind_edges(ff, test, owner)

    def executions(
        self, ff: FileFacts, test: TestFacts, dispatchers: frozenset[str], fid: str | None, cls: str | None
    ) -> tuple[str, list[str], bool]:
        body = ff.bodies.get(fid) if fid else None
        scope_key = cls if cls is not None else MODULE_SCOPE
        scope = test.scopes[scope_key]
        if body is None or not body.name.startswith("test"):
            ok = scope.client or bool(scope.calls & dispatchers)
            return ("direct" if ok else "none"), [], False
        helpers: dict[str, list[str]] = {n: [fids[-1]] for n, fids in ff.top_functions.items()}
        if cls is not None:
            for n, fids in self.class_helpers(ff, test, cls).items():
                helpers.setdefault(n, []).extend(fids)
        seen: set[str] = set()
        pending: list[tuple[str, list[str]]] = [(body.fid, [])]
        outward: set[str] = set()
        while pending:
            cur, chain = pending.pop()
            if cur in seen:
                continue
            seen.add(cur)
            b = ff.bodies[cur]
            if b.client or b.calls & dispatchers:
                return ("direct" if not chain else "via-helper"), chain, False
            for name in sorted(b.calls):
                if name in helpers:
                    pending.extend((h, [*chain, f"{ff.dotted}:{h}"]) for h in helpers[name])
                else:
                    outward.add(name)
        patched = frozenset(body.patches | scope.rest_patches)
        cut = bool(outward & patched)
        exhausted = False
        for name in sorted(outward - patched):
            found = self.view.executes(ff.dotted, name, patched)
            cut = cut or found.cut
            exhausted = exhausted or found.exhausted
            if found.ok:
                return "via-helper", list(found.chain), cut
        return (UNKNOWN_EXECUTION if exhausted else "none"), [], cut

    @staticmethod
    def class_helpers(ff: FileFacts, test: TestFacts, cls: str) -> dict[str, list[str]]:
        helpers: dict[str, list[str]] = {}
        pending = [cls]
        seen = set()
        while pending:
            cur = pending.pop()
            if cur in seen or cur not in test.scopes:
                continue
            seen.add(cur)
            for n, fids in test.scopes[cur].methods.items():
                helpers.setdefault(n, []).extend(fids)
            pending.extend(ff.top_classes[b] for b in test.scopes[cur].bases if b in ff.top_classes)
        return helpers

    def kind_edges(self, ff: FileFacts, test: TestFacts, owner: str | None) -> list[DriveEdge]:
        constructors = {e.bound: e.exported for e in ff.imports if e.exported in self.kinds}
        enum_bases = frozenset({e.bound for e in ff.imports if e.exported in self.enums}) | self.enums
        dispatchers = frozenset({e.bound for e in ff.imports if e.exported in DISPATCH_CALLS}) | DISPATCH_CALLS
        edges: list[DriveEdge] = []
        cache: dict[tuple[str | None, str | None], tuple[str, list[str], bool]] = {}
        resolved_fids: set[str | None] = set()
        for m in test.mentions:
            kind = self.kind_of(m, constructors, enum_bases)
            if kind is None:
                continue
            resolved_fids.add(m.fid)
            key = (m.fid, m.cls)
            if key not in cache:
                cache[key] = self.executions(ff, test, dispatchers, m.fid, m.cls)
            execution, chain, patched = cache[key]
            for product in sorted(self.kinds[kind]):
                if product not in self.labels or owner == product:
                    continue
                edges.append(
                    DriveEdge(
                        self.labels[product],
                        ff.dotted,
                        "query-kind",
                        kind,
                        site=m.fid or (m.cls or MODULE_SCOPE),
                        line=m.line,
                        execution=execution,
                        chain=tuple(chain),
                        patched=patched,
                    )
                )
        unresolved = 0
        for p in test.pending:
            if p.dispatch_callee is not None and p.dispatch_callee not in dispatchers and not p.under_query_key:
                continue
            body = ff.bodies.get(p.fid) if p.fid else None
            if p.value_shape == "name" and body is not None and p.value_name in body.const_params:
                continue
            if p.value_shape == "attr" and p.value_base in enum_bases:
                continue
            if p.fid in resolved_fids:
                continue
            execution, _, _ = self.executions(ff, test, dispatchers, p.fid, p.cls)
            unresolved += execution != "none"
        for product in self.query_products if unresolved else ():
            if owner != product:
                drive = DriveEdge(
                    self.labels[product], ff.dotted, "unresolved-kind", "unresolved-kind", execution="direct"
                )
                edges.extend([drive] * unresolved)
        return edges


class DrivesProducer:
    """Runs the deriver over the test files that may drive a wiring location, and turns each
    drive into an edge.

    `shuffle_seed` derives the tests in a shuffled order (edges still come out in file order):
    tests use it to show that no answer depends on which tests ran first."""

    def __init__(self, build: CoreBuild, queries: PackQueries, shuffle_seed: int | None = None) -> None:
        self.build = build
        self.queries = queries
        self.shuffle_seed = shuffle_seed

    def facts(self) -> dict[str, FileFacts]:
        facts: dict[str, FileFacts] = {}
        for scope in self.build.files:
            if scope.record.get("status") != "ok" or not self.build.first_party.contains(scope.module):
                continue
            facts[scope.module] = FileFacts(FactReader(scope, self.build))
        return facts

    @staticmethod
    def resolution(drive: DriveEdge) -> Resolution:
        """A kind the test may pass is a candidate; a test the call view could not follow to its
        end may or may not run the wiring, so the drive is unresolved: an obligation, never none."""
        if drive.trigger == "unresolved-kind":
            return Resolution.CANDIDATES
        if drive.execution == UNKNOWN_EXECUTION:
            return Resolution.UNRESOLVED
        return Resolution.RESOLVED

    def edge(self, drive: DriveEdge, path: str) -> Edge:
        unresolved = drive.trigger == "unresolved-kind"
        product, _, location = drive.label.partition(":")
        attrs = [
            ("location", drive.label),
            ("execution", drive.execution),
            ("chain", " > ".join(drive.chain)),
            ("patched", "true" if drive.patched else "false"),
        ]
        if drive.execution == UNKNOWN_EXECUTION:
            attrs.append(("reason", CALL_DEPTH_REASON))
        return Edge(
            kind="drives",
            site=Site(path, drive.test_module, drive.site.split("@", 1)[0], drive.line),
            surface=drive.detail,
            targets=(EntityId(LANGUAGE_PYTHON, location_module(product, location)),),
            resolution=self.resolution(drive),
            contexts=frozenset({Context.TEST}),
            producer=DRIVES_PRODUCER,
            shape=drive.trigger,
            shape_detail="unresolved-kind" if unresolved else drive.detail,
            attrs=tuple(attrs),
        )

    def produce(self) -> list[Edge]:
        facts = self.facts()
        deriver = Deriver(facts, self.queries)
        deriver.read_registry()
        deriver.grow_origins()
        tests = [ff for ff in facts.values() if ff.is_test and deriver.may_drive(ff)]
        order = list(tests)
        if self.shuffle_seed is not None:
            random.Random(self.shuffle_seed).shuffle(order)
        drives: dict[str, list[DriveEdge]] = {}
        for ff in order:
            test = ff.test
            assert test is not None
            drives[ff.dotted] = deriver.derive_file(ff, test)
        return [self.edge(drive, ff.rel) for ff in tests for drive in drives[ff.dotted]]
