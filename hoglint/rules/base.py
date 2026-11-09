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
from pathlib import Path

from hoglint.config.model import Config, RuleSpec
from hoglint.labels import Labeler
from hoglint.model import Context, Edge, Finding, Graph, IncompleteCause, Remedy, Resolution, Site
from hoglint.selectors import NONE, UNKNOWN, Ref, Selector, State, Truth, Value, all_of, any_of

MODULE_SCOPE = "<module>"
VIEW_KINDS: dict[str, tuple[str, ...]] = {
    "file-imports": ("import", "dotted-path"),
    "zones": ("import",),
    "packages": ("import", "reference", "dotted-path", "model-label"),
    "migrations": ("migration-parent",),
}
PRIMITIVE_KINDS: dict[str, tuple[str, ...]] = {
    "use-shape": ("reference",),
    "signature": ("exposes",),
}


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
    """Shared state of one evaluation: config, graph, labels and per-edge ordinals."""

    def __init__(self, config: Config, graph: Graph, labeler: Labeler, repo_root: Path | None = None) -> None:
        self.config = config
        self.graph = graph
        self.labeler = labeler
        self.repo_root = repo_root
        self.ordinals: dict[int, int] = {}
        counters: dict[tuple[str, str, str, str], int] = {}
        for edge in graph.edges:
            key = (edge.site.module, edge.site.scope, edge.kind, edge.surface)
            self.ordinals[id(edge)] = counters.get(key, 0)
            counters[key] = counters.get(key, 0) + 1
        self.results: dict[str, RuleRun] = {}

    def ordinal(self, edge: Edge) -> int:
        return self.ordinals.get(id(edge), 0)

    def src_endpoint(self, site: Site) -> Endpoint:
        scope = site.scope if site.scope and site.scope != MODULE_SCOPE else ""
        symbol = f"{site.module}.{scope}" if scope else None
        return Endpoint(site.module, symbol, site.path or self.labeler.path_of(site.module), scope=scope)

    def symbol_endpoint(self, name: str, resolution: Resolution) -> Endpoint:
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
        endpoints = []
        for target in edge.targets:
            name = str(target)
            if edge.resolution is Resolution.EXTERNAL:
                endpoints.append(
                    Endpoint(
                        target.module,
                        name,
                        self.labeler.path_of(target.module),
                        external=target.module.split(".", 1)[0],
                    )
                )
                continue
            symbol = name if target.symbol else None
            endpoints.append(Endpoint(target.module, symbol, self.labeler.path_of(target.module)))
        return endpoints

    def owners_of(self, module: str | None) -> tuple[str, ...]:
        if module is None or "owners" not in self.config.fields:
            return ()
        value, _ = self.labeler.field_value(module, None, "owners")
        if value.state is State.KNOWN and isinstance(value.data, frozenset):
            return tuple(sorted(value.data))
        return ()


def rule_kinds(spec: RuleSpec) -> tuple[str, ...]:
    """Edge kinds a rule reads: its own list, else the primitive's or the view's default."""
    if spec.kinds:
        return spec.kinds
    if spec.primitive in PRIMITIVE_KINDS:
        return PRIMITIVE_KINDS[spec.primitive]
    if spec.primitive == "export":
        return ("import", "exposes", "inherit", "decorate")
    return VIEW_KINDS.get(spec.view, ())


def contexts_of(edge: Edge) -> frozenset[str]:
    return frozenset(c.value for c in edge.contexts) or frozenset({Context.PROD.value})


def split_values(raw: str) -> frozenset[str]:
    return frozenset(item.strip() for item in raw.split(",") if item.strip())


class EdgeEnv:
    """Resolves `src.*`, `dst.*` and `edge.*` for one edge and one destination endpoint."""

    def __init__(self, ctx: RuleContext, edge: Edge, src: Endpoint, dst: Endpoint) -> None:
        self.ctx = ctx
        self.edge = edge
        self.src = src
        self.dst = dst

    def label(self) -> str:
        if self.dst.unresolved:
            return f"unresolved-{self.edge.kind}"
        if self.edge.shape:
            return f"{self.edge.shape}({self.edge.shape_detail})" if self.edge.shape_detail else self.edge.shape
        return self.edge.kind

    def resolve(self, ref: Ref) -> Value:
        if len(ref.path) != 2:
            return UNKNOWN
        root, name = ref.path
        if root == "edge":
            return self.edge_value(name)
        if root == "src":
            return self.endpoint_value(self.src, name, is_dst=False)
        if root == "dst":
            return self.endpoint_value(self.dst, name, is_dst=True)
        return UNKNOWN

    def edge_value(self, name: str) -> Value:
        edge = self.edge
        simple = {
            "kind": edge.kind,
            "shape": edge.shape,
            "use": edge.use,
            "label": self.label(),
            "resolution": edge.resolution.value,
            "surface": edge.surface,
        }
        if name in simple:
            return Value.known(simple[name]) if simple[name] else NONE
        if name == "context":
            return Value.known(contexts_of(edge))
        if name == "ordinal":
            return Value.known(str(self.ctx.ordinal(edge)))
        raw = edge.attr(name)
        return Value.known(raw) if raw else NONE

    def endpoint_value(self, endpoint: Endpoint, name: str, is_dst: bool) -> Value:
        if name == "context":
            return Value.known(contexts_of(self.edge))
        if name == "symbol":
            target = endpoint.symbol or endpoint.module
            return Value.known(target) if target else UNKNOWN
        if endpoint.unresolved:
            return UNKNOWN
        labeler = self.ctx.labeler
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
            return labeler.kind(endpoint.symbol, first_party=endpoint.external is None)[0]
        if name == "decl":
            decl = labeler.facts.decl(endpoint.symbol) if endpoint.symbol else ""
            return Value.known(decl) if decl else NONE
        if name == "registry":
            return labeler.entity_resolver(endpoint.module, endpoint.symbol)(Ref(("registry",)))
        if name == "exported":
            return self.exported(endpoint)
        attr = self.edge.attr(f"{'dst' if is_dst else 'src'}.{name}")
        if attr:
            spec = self.ctx.config.fields.get(name)
            return (
                Value.known(split_values(attr)) if spec is not None and spec.type.value == "set" else Value.known(attr)
            )
        if name not in self.ctx.config.fields:
            return UNKNOWN
        if endpoint.external is not None:
            return NONE
        return labeler.field_value(endpoint.module, endpoint.label_symbol, name)[0]

    def exported(self, endpoint: Endpoint) -> Value:
        if endpoint.external is not None:
            return NONE

        def audience(ref: Ref) -> Value:
            return self.resolve(ref)

        truth = self.ctx.labeler.exported(endpoint.module, endpoint.symbol, audience)
        if truth is Truth.UNKNOWN:
            return UNKNOWN
        return Value.known(truth is Truth.TRUE)


def render_value(value: Value) -> str:
    if value.state is State.UNKNOWN:
        return "?"
    if value.state is State.NONE:
        return "-"
    if isinstance(value.data, (frozenset, tuple)):
        return ",".join(sorted(value.data)) or "-"
    if isinstance(value.data, bool):
        return "true" if value.data else "false"
    return str(value.data)


def project(columns: tuple[str, ...], resolve: object) -> tuple[str, ...]:
    """A finding's identity: one string per column, never a line number."""
    out: list[str] = []
    for column in columns:
        ref = Ref(tuple(column.split(".")))
        out.append(render_value(resolve(ref)))  # type: ignore[operator]
    return tuple(out)


class EdgeRule:
    """Selection and the predicate step for every primitive that judges single edges."""

    def __init__(self, ctx: RuleContext, spec: RuleSpec) -> None:
        self.ctx = ctx
        self.spec = spec
        self.run = RuleRun(spec)

    def kinds(self) -> tuple[str, ...]:
        return rule_kinds(self.spec)

    def context_ok(self, edge: Edge) -> bool:
        contexts = contexts_of(edge)
        if self.spec.contexts_include and not contexts & set(self.spec.contexts_include):
            return False
        return not contexts & set(self.spec.contexts_exclude)

    def edges(self) -> Iterator[Edge]:
        kinds = set(self.kinds())
        for edge in self.ctx.graph.edges:
            if kinds and edge.kind not in kinds:
                continue
            if edge.resolution is Resolution.NOT_APPLICABLE:
                continue
            if self.context_ok(edge):
                yield edge

    def envs(self) -> Iterator[EdgeEnv]:
        for edge in self.edges():
            src = self.ctx.src_endpoint(edge.site)
            for dst in self.ctx.dst_endpoints(edge, self.spec.endpoint):
                yield EdgeEnv(self.ctx, edge, src, dst)

    def selection(self, env: EdgeEnv) -> Truth:
        parts = [
            selector.evaluate(env.resolve) for selector in (self.spec.from_, self.spec.select) if selector is not None
        ]
        return all_of(parts)

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
        witnesses = witnesses or (env.edge,)
        finding = Finding(
            rule=self.spec.id,
            identity=project(self.spec.identity, env.resolve),
            message=message,
            site=witnesses[0].site,
            witnesses=witnesses,
            remedies=remedies or self.remedies(),
            owners=self.ctx.owners_of(env.src.module),
            subjects=self.subjects(env),
            ledgerable=ledgerable and self.spec.ledger,
            unknown=unknown,
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
