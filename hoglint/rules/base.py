"""What every primitive shares: endpoints, the selector environment, edge selection, findings.

Unknown semantics per position:
- `from` / `select` false: the edge is not the rule's business.
- the destination did not resolve: `on_unresolved` decides (finding, block, incomplete).
- `from` / `select` unknown on a resolved edge: the rule is incomplete for that edge.
- a predicate (`deny`, `allow`, `require`) unknown: a finding. Unknown never escapes a deny.
- `unless` excuses only when it is true.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from hoglint.config.model import Config, RuleSpec
from hoglint.index import MODULE_SCOPE, EdgeGroup
from hoglint.labels import BUILTIN_ENTITY_NAMES, Labeler
from hoglint.model import Context, Edge, EntityId, Finding, Graph, IncompleteCause, Remedy, Resolution, Site
from hoglint.selectors import (
    NONE,
    UNKNOWN,
    And,
    Node,
    Ref,
    Resolver,
    Selector,
    State,
    Truth,
    Value,
    all_of,
    any_of,
    evaluate,
    refs,
    render_value,
    split_list,
)

PRIMITIVE_KINDS: dict[str, tuple[str, ...]] = {
    "use-shape": ("reference",),
    "signature": ("exposes",),
    "export": ("import", "exposes", "inherit", "decorate"),
}
DEFAULT_CONTEXTS: frozenset[str] = frozenset({Context.PROD})
EDGE_DEPENDENT_FIELDS = frozenset({"context", "exported"})


@dataclass(frozen=True, slots=True)
class IncompleteItem:
    """One reason a rule could not decide. `key` has no line numbers, so base and head compare."""

    rule: str
    key: str
    reason: str
    subjects: tuple[str, ...] = ()
    cause: IncompleteCause = IncompleteCause.INHERITED


@dataclass(frozen=False, slots=True)
class RuleRun:
    spec: RuleSpec
    findings: list[Finding] = field(default_factory=list)
    incomplete: list[IncompleteItem] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Endpoint:
    """One end of an edge, as a rule judges it."""

    module: str | None
    symbol: str | None
    path: str | None = None
    external: str | None = None
    unresolved: bool = False
    scope: str = ""

    @property
    def label_symbol(self) -> str | None:
        """The top-level symbol that carries symbol labels (a method's class, a function)."""
        if self.symbol is None or self.module is None:
            return self.symbol
        if not self.symbol.startswith(self.module + "."):
            return self.symbol
        head = self.symbol[len(self.module) + 1 :].split(".", 1)[0]
        return f"{self.module}.{head}"


class RuleContext:
    """Shared state of one evaluation: config, graph, labels, per-edge ordinals and endpoint caches."""

    def __init__(self, config: Config, graph: Graph, labeler: Labeler, repo_root: Path | None = None) -> None:
        self.config = config
        self.graph = graph
        self.labeler = labeler
        self.repo_root = repo_root
        index = labeler.index
        self.by_kind = index.by_kind
        self.groups = index.groups
        self.endpoint_attr_edges = index.endpoint_attr_edges
        self.results: dict[str, RuleRun] = {}
        self.src_cache: dict[tuple[str, str, str], Endpoint] = {}
        self.symbol_cache: dict[tuple[str, Resolution], Endpoint] = {}
        self.target_cache: dict[tuple[EntityId, bool], Endpoint] = {}

    def edges_of(self, kinds: tuple[str, ...]) -> list[Edge]:
        """Edges of the given kinds, kind by kind in the order named; every edge when none is named."""
        if not kinds:
            return self.graph.edges
        return [edge for kind in dict.fromkeys(kinds) for edge in self.by_kind.get(kind, [])]

    def kinds_or_all(self, kinds: tuple[str, ...]) -> list[str]:
        return list(dict.fromkeys(kinds)) if kinds else list(self.groups)

    def module_groups(self, kinds: tuple[str, ...]) -> Iterator[tuple[str, EdgeGroup]]:
        """(source module, its edges) kind by kind in the order named; every kind when none is named."""
        for kind in self.kinds_or_all(kinds):
            yield from self.groups.get(kind, {}).items()

    def groups_of(self, kinds: tuple[str, ...], module: str) -> list[EdgeGroup]:
        out = []
        for kind in self.kinds_or_all(kinds):
            group = self.groups.get(kind, {}).get(module)
            if group is not None:
                out.append(group)
        return out

    def ordinal(self, edge: Edge) -> int:
        """The edge's place among edges with the same module, scope, kind and surface."""
        return self.labeler.index.ordinals.get(id(edge), 0)

    def src_endpoint(self, site: Site) -> Endpoint:
        key = (site.module, site.scope, site.path)
        cached = self.src_cache.get(key)
        if cached is not None:
            return cached
        scope = site.scope if site.scope and site.scope != MODULE_SCOPE else ""
        symbol = f"{site.module}.{scope}" if scope else None
        endpoint = Endpoint(site.module, symbol, site.path or self.labeler.path_of(site.module), scope=scope)
        self.src_cache[key] = endpoint
        return endpoint

    def module_endpoint(self, module: str) -> Endpoint:
        return Endpoint(module, None, self.labeler.path_of(module))

    def symbol_endpoint(self, name: str, resolution: Resolution) -> Endpoint:
        key = (name, resolution)
        cached = self.symbol_cache.get(key)
        if cached is None:
            cached = self.symbol_cache[key] = self.new_symbol_endpoint(name, resolution)
        return cached

    def new_symbol_endpoint(self, name: str, resolution: Resolution) -> Endpoint:
        if resolution is Resolution.EXTERNAL:
            module = self.labeler.module_of_symbol(name) or name
            return Endpoint(module, name, self.labeler.path_of(module), external=name.split(".", 1)[0])
        module = self.labeler.module_of_symbol(name)
        if module is None:
            return Endpoint(None, name, unresolved=True)
        symbol = name if name != module else None
        return Endpoint(module, symbol, self.labeler.path_of(module))

    def dst_endpoints(self, edge: Edge, role: str) -> list[Endpoint]:
        if role == "via":
            return [self.symbol_endpoint(str(hop), Resolution.RESOLVED) for hop in edge.via]
        if role == "surface":
            if edge.resolution is Resolution.UNRESOLVED:
                endpoint = self.symbol_endpoint(edge.surface, Resolution.RESOLVED)
                return [endpoint if not endpoint.unresolved else Endpoint(None, edge.surface, unresolved=True)]
            return [self.symbol_endpoint(edge.surface, edge.resolution)]
        if edge.resolution is Resolution.UNRESOLVED or not edge.targets:
            return [Endpoint(None, edge.surface, unresolved=True)]
        external = edge.resolution is Resolution.EXTERNAL
        return [self.target_endpoint(target, external) for target in edge.targets]

    def target_endpoint(self, target: EntityId, external: bool) -> Endpoint:
        key = (target, external)
        cached = self.target_cache.get(key)
        if cached is not None:
            return cached
        name = str(target)
        path = self.labeler.path_of(target.module)
        if external:
            endpoint = Endpoint(target.module, name, path, external=target.module.split(".", 1)[0])
        else:
            endpoint = Endpoint(target.module, name if target.symbol else None, path)
        self.target_cache[key] = endpoint
        return endpoint

    def finding(
        self,
        spec: RuleSpec,
        identity: tuple[str, ...],
        message: str,
        owner: str | None,
        subjects: tuple[str, ...] = (),
        witnesses: tuple[Edge, ...] = (),
        site: Site | None = None,
        ledgerable: bool = True,
        unknown: bool = False,
        remedies: tuple[Remedy, ...] = (),
    ) -> Finding:
        """A finding of `spec`; owners come from the `owner` module, the site from the first witness."""
        return Finding(
            rule=spec.id,
            identity=identity,
            message=message,
            site=site if site is not None or not witnesses else witnesses[0].site,
            witnesses=witnesses,
            remedies=remedies,
            owners=self.owners_of(owner),
            subjects=subjects,
            ledgerable=ledgerable and spec.ledger,
            unknown=unknown,
        )

    def owners_of(self, module: str | None) -> tuple[str, ...]:
        owner = self.config.roles.owner
        if module is None or not owner:
            return ()
        value, _ = self.labeler.field_value(module, None, owner)
        if value.state is State.KNOWN and isinstance(value.data, frozenset):
            return tuple(sorted(value.data))
        return ()


def rule_kinds(spec: RuleSpec, views: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    """Edge kinds a rule reads: its own list, else the primitive's or the view's default."""
    if spec.kinds:
        return spec.kinds
    if spec.primitive in PRIMITIVE_KINDS:
        return PRIMITIVE_KINDS[spec.primitive]
    return views.get(spec.view, ())


def contexts_of(edge: Edge) -> frozenset[str]:
    """The edge's contexts; `Context` is a `StrEnum`, so the members compare as their names."""
    return edge.contexts or DEFAULT_CONTEXTS


def context_ok(edge: Edge, include: frozenset[str], exclude: frozenset[str]) -> bool:
    contexts = contexts_of(edge)
    if include and not contexts & include:
        return False
    return not contexts & exclude


def read_endpoint(ctx: RuleContext, endpoint: Endpoint, name: str, edge: Edge | None, root: str) -> Value:
    """`src.<name>` or `dst.<name>` of one endpoint. `context` and `exported` need the edge: see EdgeEnv.

    The endpoint answers what it knows better than its module (its own path, scope, external
    package, an edge's `src.*`/`dst.*` attributes); other built-in names are the labeler's."""
    if name == "symbol":
        target = endpoint.symbol or endpoint.module
        return Value.known(target) if target else UNKNOWN
    if endpoint.unresolved:
        return UNKNOWN
    if name == "module":
        return Value.known(endpoint.module) if endpoint.module else NONE
    if name == "path":
        return Value.known(endpoint.path) if endpoint.path else NONE
    if name == "scope":
        return Value.known(endpoint.scope) if endpoint.scope else NONE
    if name == "name":
        target = endpoint.symbol or endpoint.module or ""
        return Value.known(target.rpartition(".")[2]) if target else NONE
    if name == "external":
        return Value.known(endpoint.external) if endpoint.external else NONE
    if name == "kind":
        if endpoint.symbol is None:
            return NONE
        return ctx.labeler.kind(endpoint.symbol, first_party=endpoint.external is None)[0]
    if name in BUILTIN_ENTITY_NAMES:
        return ctx.labeler.builtin(endpoint.module, endpoint.symbol, name)
    attr = edge.attr(f"{root}.{name}") if edge is not None and id(edge) in ctx.endpoint_attr_edges else ""
    if attr:
        spec = ctx.config.fields.get(name)
        if spec is not None and spec.type.value == "set":
            return Value.known(frozenset(split_list(attr)))
        return Value.known(attr)
    if name not in ctx.config.fields:
        return UNKNOWN
    if endpoint.external is not None:
        return NONE
    return ctx.labeler.field_value(endpoint.module, endpoint.label_symbol, name)[0]


class EdgeEnv:
    """Resolves `src.*`, `dst.*` and `edge.*` for one edge and one destination endpoint.

    `shape` and `shape_detail` default to the edge's; a primitive that refines the shape (use-shape
    with def-use flow) passes its own, so the edge itself stays the witness.
    """

    def __init__(
        self,
        ctx: RuleContext,
        edge: Edge,
        src: Endpoint,
        dst: Endpoint,
        shape: str | None = None,
        shape_detail: str | None = None,
    ) -> None:
        self.ctx = ctx
        self.edge = edge
        self.src = src
        self.dst = dst
        self.shape = edge.shape if shape is None else shape
        self.shape_detail = edge.shape_detail if shape_detail is None else shape_detail

    def label(self) -> str:
        if self.dst.unresolved:
            return f"unresolved-{self.edge.kind}"
        if self.shape:
            return f"{self.shape}({self.shape_detail})" if self.shape_detail else self.shape
        return self.edge.kind

    def resolve(self, ref: Ref) -> Value:
        if len(ref.path) != 2:
            return UNKNOWN
        root, name = ref.path
        if root == "edge":
            return self.edge_value(name)
        if root == "src":
            return self.endpoint_value(self.src, name, root)
        if root == "dst":
            return self.endpoint_value(self.dst, name, root)
        return UNKNOWN

    def edge_value(self, name: str) -> Value:
        edge = self.edge
        simple = {
            "kind": edge.kind,
            "shape": self.shape,
            "use": edge.use,
            "label": self.label(),
            "resolution": edge.resolution.value,
            "surface": edge.surface,
            "producer": edge.producer,
        }
        if name in simple:
            return Value.known(simple[name]) if simple[name] else NONE
        if name == "context":
            return Value.known(contexts_of(edge))
        if name == "ordinal":
            return Value.known(str(self.ctx.ordinal(edge)))
        raw = edge.attr(name)
        return Value.known(raw) if raw else NONE

    def endpoint_value(self, endpoint: Endpoint, name: str, root: str) -> Value:
        if name == "context":
            return Value.known(contexts_of(self.edge))
        if name == "exported":
            return UNKNOWN if endpoint.unresolved else self.exported(endpoint)
        return read_endpoint(self.ctx, endpoint, name, self.edge, root)

    def exported(self, endpoint: Endpoint) -> Value:
        if endpoint.external is not None:
            return NONE
        src = (self.src.module, self.src.path)
        truth = self.ctx.labeler.exported(endpoint.module, endpoint.symbol, self.resolve, src)
        if truth is Truth.UNKNOWN:
            return UNKNOWN
        return Value.known(truth is Truth.TRUE)


class ModuleEnv:
    """Resolves `src.*` for a module on its own, with no edge (reach's `from`)."""

    def __init__(self, ctx: RuleContext, endpoint: Endpoint) -> None:
        self.ctx = ctx
        self.endpoint = endpoint

    def resolve(self, ref: Ref) -> Value:
        if len(ref.path) != 2 or ref.path[0] != "src" or ref.path[1] in EDGE_DEPENDENT_FIELDS:
            return UNKNOWN
        return read_endpoint(self.ctx, self.endpoint, ref.path[1], None, "src")


@cache
def column_ref(column: str) -> Ref:
    return Ref(tuple(column.split(".")))


def project(columns: tuple[str, ...], resolve: Resolver) -> tuple[str, ...]:
    """A finding's identity: one string per column, never a line number."""
    return tuple(render_value(resolve(column_ref(column))) for column in columns)


# ── staged selection ─────────────────────────────────────────────


def conjuncts(selectors: tuple[Selector | None, ...]) -> list[Node]:
    out: list[Node] = []
    for selector in selectors:
        if selector is None:
            continue
        out.extend(selector.node.parts if isinstance(selector.node, And) else (selector.node,))
    return out


def endpoint_only(node: Node) -> str:
    """`src` or `dst` when the node reads only that endpoint's own fields, else ''."""
    roots: set[str] = set()
    node_refs = refs(node)
    for ref in node_refs:
        if len(ref.path) != 2 or ref.path[0] not in ("src", "dst") or ref.path[1] in EDGE_DEPENDENT_FIELDS:
            return ""
        roots.add(ref.path[0])
    return roots.pop() if len(roots) == 1 else ""


def joined(parts: list[Node]) -> Node | None:
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else And(tuple(parts))


class StagedSelection:
    """A rule's selection split into the parts that read only `src`, only `dst`, and the rest.

    The endpoint parts are evaluated once per endpoint and cached, so a rule over a million edges
    reads each module's labels once. An edge with its own `src.*` or `dst.*` attributes bypasses
    the cache, because those attributes override labels.
    """

    def __init__(self, ctx: RuleContext, selectors: tuple[Selector | None, ...]) -> None:
        self.ctx = ctx
        parts = conjuncts(selectors)
        self.src_part = joined([part for part in parts if endpoint_only(part) == "src"])
        self.dst_part = joined([part for part in parts if endpoint_only(part) == "dst"])
        self.rest = joined([part for part in parts if endpoint_only(part) == ""])
        self.module_level = self.module_only(self.src_part)
        self.rest_modules = self.module_only(self.rest)
        self.rest_cache: dict[tuple[object, ...], Truth] = {}
        self.module_cache: dict[str, Truth] = {}
        self.src_cache: dict[Endpoint, Truth] = {}
        self.dst_cache: dict[Endpoint, Truth] = {}

    def module_only(self, part: Node | None) -> bool:
        """Whether the part reads only endpoint fields that belong to a module, never a symbol."""
        return part is not None and all(self.ctx.labeler.module_level(ref) for ref in refs(part))

    def bypass(self, edge: Edge) -> bool:
        """An edge with its own `src.*`/`dst.*` attributes is judged alone: they override labels."""
        return id(edge) in self.ctx.endpoint_attr_edges

    def module_false(self, module: str) -> bool:
        """True when the `src` part is false for every regular edge of the module."""
        if not self.module_level or self.src_part is None:
            return False
        cached = self.module_cache.get(module)
        if cached is None:
            cached = evaluate(self.src_part, ModuleEnv(self.ctx, self.ctx.module_endpoint(module)).resolve)
            self.module_cache[module] = cached
        return cached is Truth.FALSE

    def part_truth(self, part: Node | None, cache: dict[Endpoint, Truth], edge: Edge, endpoint: Endpoint) -> Truth:
        if part is None:
            return Truth.TRUE
        if self.bypass(edge):
            return evaluate(part, EdgeEnv(self.ctx, edge, endpoint, endpoint).resolve)
        cached = cache.get(endpoint)
        if cached is None:
            cached = cache[endpoint] = evaluate(part, EdgeEnv(self.ctx, edge, endpoint, endpoint).resolve)
        return cached

    def src_truth(self, edge: Edge, src: Endpoint) -> Truth:
        return self.part_truth(self.src_part, self.src_cache, edge, src)

    def dst_truth(self, edge: Edge, dst: Endpoint) -> Truth:
        return self.part_truth(self.dst_part, self.dst_cache, edge, dst)

    def rest_truth(self, rest: Node, env: EdgeEnv) -> Truth:
        """The rest of the selection; one evaluation per module pair when it reads only module fields."""
        if not self.rest_modules or self.bypass(env.edge):
            return evaluate(rest, env.resolve)
        src, dst = env.src, env.dst
        key = (src.module, src.path, dst.module, dst.path, dst.external, dst.unresolved)
        cached = self.rest_cache.get(key)
        if cached is None:
            cached = self.rest_cache[key] = evaluate(rest, env.resolve)
        return cached

    def truth(self, env: EdgeEnv) -> Truth:
        parts = [self.src_truth(env.edge, env.src), self.dst_truth(env.edge, env.dst)]
        if self.rest is not None:
            parts.append(self.rest_truth(self.rest, env))
        return all_of(parts)


class EdgeRule:
    """Selection and the predicate step for every primitive that judges single edges."""

    def __init__(self, ctx: RuleContext, spec: RuleSpec) -> None:
        self.ctx = ctx
        self.spec = spec
        self.run = RuleRun(spec)
        self.kind_set = frozenset(self.kinds())
        self.include = frozenset(spec.contexts_include)
        self.exclude = frozenset(spec.contexts_exclude)
        self.staged = StagedSelection(ctx, self.selectors())

    def kinds(self) -> tuple[str, ...]:
        return rule_kinds(self.spec, self.ctx.config.views)

    def selectors(self) -> tuple[Selector | None, ...]:
        """The selectors that pick edges, in the order they apply."""
        return self.spec.from_, self.spec.select

    def sees(self, edge: Edge) -> bool:
        """Whether the rule looks at this edge at all. The engine and `explain` both ask this."""
        if self.kind_set and edge.kind not in self.kind_set:
            return False
        if edge.resolution is Resolution.NOT_APPLICABLE:
            return False
        return context_ok(edge, self.include, self.exclude)

    def env(self, edge: Edge, src: Endpoint, dst: Endpoint) -> EdgeEnv:
        return EdgeEnv(self.ctx, edge, src, dst)

    def envs_of(self, edge: Edge) -> list[EdgeEnv]:
        """One environment per destination of the edge, selected or not."""
        src = self.ctx.src_endpoint(edge.site)
        return [self.env(edge, src, dst) for dst in self.ctx.dst_endpoints(edge, self.spec.endpoint)]

    def group_envs(self, module: str, group: EdgeGroup) -> Iterator[EdgeEnv]:
        """The environments of one module's edges whose selection is not false. The module-wide and
        endpoint-only parts go first, so most edges are dropped before any per-edge work."""
        staged = self.staged
        for edge in group.irregular if staged.module_false(module) else group.edges:
            if not self.sees(edge):
                continue
            src = self.ctx.src_endpoint(edge.site)
            if staged.src_truth(edge, src) is Truth.FALSE:
                continue
            for dst in self.ctx.dst_endpoints(edge, self.spec.endpoint):
                if staged.dst_truth(edge, dst) is Truth.FALSE:
                    continue
                yield self.env(edge, src, dst)

    def envs(self) -> Iterator[EdgeEnv]:
        for module, group in self.ctx.module_groups(self.kinds()):
            yield from self.group_envs(module, group)

    def selection(self, env: EdgeEnv) -> Truth:
        return self.staged.truth(env)

    def violation(self, env: EdgeEnv) -> Truth:
        """True when the predicate says the edge breaks the rule. No predicate: selection is the finding."""
        parts: list[Truth] = []
        if self.spec.deny is not None:
            parts.append(self.spec.deny.evaluate(env.resolve))
        if self.spec.allow is not None:
            parts.append(self.spec.allow.evaluate(env.resolve).negate())
        if self.spec.require is not None:
            parts.append(self.spec.require.evaluate(env.resolve).negate())
        if not parts:
            return Truth.TRUE
        return any_of(parts)

    def excused(self, env: EdgeEnv) -> bool:
        return self.spec.unless is not None and self.spec.unless.evaluate(env.resolve) is Truth.TRUE

    def unknown_refs(self, selector: Selector | None, env: EdgeEnv) -> str:
        if selector is None:
            return ""
        names = [str(ref) for ref in selector.refs() if env.resolve(ref).is_unknown]
        return ", ".join(dict.fromkeys(names))

    def incomplete(self, env: EdgeEnv, reason: str) -> None:
        key = f"{env.src.module}|{env.edge.kind}|{env.edge.surface}"
        self.run.incomplete.append(IncompleteItem(self.spec.id, key, reason, self.subjects(env)))

    def subjects(self, env: EdgeEnv) -> tuple[str, ...]:
        out = [env.src.module]
        if env.dst.module and env.dst.module != env.src.module:
            out.append(env.dst.module)
        return tuple(m for m in out if m)

    def emit(
        self,
        env: EdgeEnv,
        message: str,
        ledgerable: bool = True,
        remedies: tuple[Remedy, ...] = (),
        witnesses: tuple[Edge, ...] = (),
        unknown: bool = False,
    ) -> Finding:
        finding = self.ctx.finding(
            self.spec,
            project(self.spec.identity, env.resolve),
            message,
            env.src.module,
            self.subjects(env),
            witnesses or (env.edge,),
            ledgerable=ledgerable,
            unknown=unknown,
            remedies=remedies or self.remedies(),
        )
        self.run.findings.append(finding)
        return finding

    def remedies(self) -> tuple[Remedy, ...]:
        if self.spec.remedy:
            return (Remedy("documented-replacement", self.spec.remedy),)
        return ()

    def on_unresolved(self, env: EdgeEnv) -> None:
        what = f"{env.edge.kind} {env.edge.surface!r} did not resolve"
        if self.spec.on_unresolved == "incomplete":
            self.incomplete(env, what)
            return
        self.emit(env, what, ledgerable=self.spec.on_unresolved == "finding", unknown=True)

    def judge(self, env: EdgeEnv, describe: str) -> None:
        """The common decision for one edge; `describe` is the finding message."""
        selected = self.selection(env)
        if selected is Truth.FALSE:
            return
        if env.dst.unresolved:
            self.on_unresolved(env)
            return
        if selected is Truth.UNKNOWN:
            unknown = self.unknown_refs(self.spec.from_, env) or self.unknown_refs(self.spec.select, env)
            self.incomplete(env, f"selection reads unknown {unknown or 'values'}")
            return
        verdict = self.violation(env)
        if verdict is Truth.FALSE or self.excused(env):
            return
        if verdict is Truth.UNKNOWN:
            unknown = ", ".join(
                filter(None, (self.unknown_refs(s, env) for s in (self.spec.deny, self.spec.allow, self.spec.require)))
            )
            self.emit(env, f"{describe}; cannot tell, unassigned: {unknown or 'a value'}", unknown=True)
            return
        self.emit(env, describe)

    def evaluate(self) -> RuleRun:
        for env in self.envs():
            self.judge(env, self.describe(env))
        return self.run

    def describe(self, env: EdgeEnv) -> str:
        return f"{env.src.module} -> {env.dst.symbol or env.dst.module} ({env.edge.kind})"
