"""Primitives over paths in a view: reach (a denied target through a chain) and cycle."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from hoglint.config.model import RuleSpec
from hoglint.index import has_endpoint_attrs
from hoglint.model import Edge
from hoglint.rules.base import (
    EDGE_DEPENDENT_FIELDS,
    EdgeEnv,
    EdgeRule,
    Endpoint,
    IncompleteItem,
    ModuleEnv,
    RuleContext,
    RuleRun,
    project,
    read_endpoint,
)
from hoglint.selectors import UNKNOWN, Ref, Selector, State, Truth, Value, render_value


@dataclass(frozen=True, slots=True)
class Hop:
    """One edge a reach search may follow: an Edge, or a core edge id the context makes into an
    Edge only when a finding or an incomplete needs it."""

    edge: Edge | int
    dst: Endpoint
    selected: Truth
    surface: str


class ReachPrimitive(EdgeRule):
    """Is a denied target reachable from a selected module through the view's edges?

    `from` picks the start modules and reads the module alone (`src.*`, no edge). `select` picks
    the edges the search may follow, each judged with its own source. `deny` is judged with the
    start module as `src`, so a condition on the start holds along the whole chain.
    """

    def __init__(self, ctx: RuleContext, spec: RuleSpec) -> None:
        super().__init__(ctx, spec)
        self.hop_cache: dict[str, list[Hop]] = {}
        self.verdicts: dict[tuple[Endpoint, Endpoint], tuple[Truth, bool]] = {}
        judged = [selector for selector in (spec.deny, spec.unless) if selector is not None]
        self.endpoint_verdicts = all(
            len(ref.path) == 2 and ref.path[0] in ("src", "dst") and ref.path[1] not in EDGE_DEPENDENT_FIELDS
            for selector in judged
            for ref in selector.refs()
        )

    def selectors(self) -> tuple[Selector | None, ...]:
        return (self.spec.select,)

    def hops(self, module: str) -> list[Hop]:
        """The selected edges out of one module, built when the search first reaches it."""
        cached = self.hop_cache.get(module)
        if cached is not None:
            return cached
        out = self.ctx.reach_hops(self, module)
        if out is None:
            out = self.build_hops(module)
        out.sort(key=lambda hop: (hop.dst.module or "", hop.dst.symbol or "", hop.surface))
        self.hop_cache[module] = out
        return out

    def build_hops(self, module: str) -> list[Hop]:
        out: list[Hop] = []
        for group in self.ctx.groups_of(self.kinds(), module):
            for env in self.group_envs(module, group):
                selected = self.selection(env)
                if selected is not Truth.FALSE:
                    out.append(Hop(env.edge, env.dst, selected, env.edge.surface))
        return out

    def sources(self) -> list[str]:
        out = []
        for module in self.ctx.source_modules(self.kinds()):
            truth = Truth.TRUE
            if self.spec.from_ is not None:
                truth = self.spec.from_.evaluate(ModuleEnv(self.ctx, self.ctx.module_endpoint(module)).resolve)
            if truth is Truth.FALSE or not self.hops(module):
                continue
            if truth is Truth.TRUE:
                out.append(module)
                continue
            reason = f"cannot tell whether {module} is selected"
            self.run.incomplete.append(IncompleteItem(self.spec.id, module, reason, (module,)))
        return out

    def evaluate(self) -> RuleRun:
        for source in self.sources():
            self.search(source)
        return self.run

    def hop_env(self, hop: Hop, origin: Endpoint) -> EdgeEnv:
        return EdgeEnv(self.ctx, self.ctx.edge_of(hop.edge), origin, hop.dst)

    def verdict(self, hop: Hop, origin: Endpoint) -> tuple[Truth, bool]:
        """(`deny`, `unless` excuses) for one hop seen from `origin`. When neither reads the edge,
        the answer depends on the two endpoints alone and is kept."""
        edge = hop.edge
        if not self.endpoint_verdicts or (isinstance(edge, Edge) and has_endpoint_attrs(edge)):
            return self.judge_hop(self.hop_env(hop, origin))
        key = (origin, hop.dst)
        found = self.verdicts.get(key)
        if found is None:
            found = self.verdicts[key] = self.judge_hop(EndpointsEnv(self.ctx, origin, hop.dst))
        return found

    def judge_hop(self, env: EdgeEnv | EndpointsEnv) -> tuple[Truth, bool]:
        verdict = self.spec.deny.evaluate(env.resolve) if self.spec.deny is not None else Truth.FALSE
        excused = self.spec.unless is not None and self.spec.unless.evaluate(env.resolve) is Truth.TRUE
        return verdict, excused

    def search(self, source: str) -> None:
        origin = self.ctx.module_endpoint(source)
        parents: dict[str, tuple[str, Hop] | None] = {source: None}
        queue = deque([source])
        reported: set[str] = set()
        while queue:
            module = queue.popleft()
            for hop in self.hops(module):
                dst = hop.dst
                if dst.unresolved:
                    self.on_unresolved(self.hop_env(hop, origin))
                    continue
                if hop.selected is Truth.UNKNOWN:
                    env = self.hop_env(hop, origin)
                    self.incomplete(env, f"cannot tell whether to follow {env.edge.kind} {env.edge.surface!r}")
                    continue
                verdict, excused = self.verdict(hop, origin)
                target = dst.symbol or dst.module or ""
                if verdict is not Truth.FALSE and not excused and target not in reported:
                    reported.add(target)
                    path = [*self.path_to(parents, module), hop]
                    self.report(self.hop_env(hop, origin), [self.ctx.edge_of(step.edge) for step in path], verdict)
                    continue
                if dst.external is not None or dst.module is None or dst.module in parents:
                    continue
                parents[dst.module] = (module, hop)
                queue.append(dst.module)

    def path_to(self, parents: dict[str, tuple[str, Hop] | None], module: str) -> list[Hop]:
        path: list[Hop] = []
        step = parents.get(module)
        while step is not None:
            previous, hop = step
            path.append(hop)
            step = parents.get(previous)
        return list(reversed(path))

    def report(self, env: EdgeEnv, path: list[Edge], verdict: Truth) -> None:
        chain = " -> ".join([env.src.module or "", *(e.surface for e in path)])
        message = f"{env.src.module} reaches {env.dst.symbol or env.dst.module}: {chain}"
        if verdict is Truth.UNKNOWN:
            message += " (cannot tell, a value is unassigned)"
        self.emit(env, message, witnesses=tuple(path), unknown=verdict is Truth.UNKNOWN)


class EndpointsEnv:
    """Resolves `src.*` and `dst.*` of two endpoints with no edge: what reach's `deny` reads
    when it names no edge field."""

    def __init__(self, ctx: RuleContext, src: Endpoint, dst: Endpoint) -> None:
        self.ctx = ctx
        self.src = src
        self.dst = dst

    def resolve(self, ref: Ref) -> Value:
        if len(ref.path) != 2 or ref.path[0] not in ("src", "dst"):
            return UNKNOWN
        root, name = ref.path
        return read_endpoint(self.ctx, self.src if root == "src" else self.dst, name, None, root)


class CyclePrimitive(EdgeRule):
    """Strongly connected parts of a grouped view: every pair inside one is a finding."""

    def __init__(self, ctx: RuleContext, spec: RuleSpec) -> None:
        super().__init__(ctx, spec)
        self.group_field = ctx.config.roles.group if spec.view == "packages" else "module"
        self.group_cache: dict[tuple[str, Endpoint], str | None] = {}

    def own_reads(self) -> list[Ref]:
        return [Ref(("src", self.group_field)), Ref(("dst", self.group_field))]

    def droppable(self, env: EdgeEnv) -> bool:
        """True when `evaluate` neither pairs this environment nor reports it."""
        selected = self.selection(env)
        if selected is Truth.FALSE:
            return True
        if env.dst.unresolved or selected is Truth.UNKNOWN:
            return False
        src, dst = self.group(env, "src"), self.group(env, "dst")
        return src is None or dst is None or src == dst

    def group(self, env: EdgeEnv, root: str) -> str | None:
        """The endpoint's group; cached per endpoint unless the edge's own attributes override labels."""
        endpoint = env.src if root == "src" else env.dst
        key = (root, endpoint)
        if key in self.group_cache and not self.staged.bypass(env.edge):
            return self.group_cache[key]
        value = env.resolve(Ref((root, self.group_field)))
        group = render_value(value) if value.state is State.KNOWN else None
        if not self.staged.bypass(env.edge):
            self.group_cache[key] = group
        return group

    def evaluate(self) -> RuleRun:
        pairs: dict[tuple[str, str], list[EdgeEnv]] = {}
        for env in self.envs():
            selected = self.selection(env)
            if selected is Truth.FALSE:
                continue
            if env.dst.unresolved:
                self.on_unresolved(env)
                continue
            if selected is Truth.UNKNOWN:
                self.incomplete(env, "selection reads an unknown value")
                continue
            src, dst = self.group(env, "src"), self.group(env, "dst")
            if src is None or dst is None or src == dst:
                continue
            pairs.setdefault((src, dst), []).append(env)
        components = strongly_connected(sorted(pairs))
        for (src, dst), envs in sorted(pairs.items()):
            if components.get(src) is None or components.get(src) != components.get(dst):
                continue
            first = envs[0]
            finding = self.ctx.finding(
                self.spec,
                project(self.spec.identity, first.resolve),
                f"{src} -> {dst} closes a cycle ({len(envs)} edges)",
                first.src.module,
                self.subjects(first),
                tuple(env.edge for env in envs[:3]),
            )
            self.run.findings.append(finding)
        return self.run


def strongly_connected(pairs: list[tuple[str, str]]) -> dict[str, int]:
    """Tarjan, iterative. Maps each node in a non-trivial component to the component's index."""
    graph: dict[str, list[str]] = {}
    for src, dst in pairs:
        graph.setdefault(src, []).append(dst)
        graph.setdefault(dst, [])
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    out: dict[str, int] = {}
    counter = 0
    component = 0
    for root in sorted(graph):
        if root in index:
            continue
        work = [(root, 0)]
        while work:
            node, position = work.pop()
            if position == 0:
                index[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            neighbours = graph[node]
            if position < len(neighbours):
                work.append((node, position + 1))
                nxt = neighbours[position]
                if nxt not in index:
                    work.append((nxt, 0))
                elif nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
                continue
            if low[node] == index[node]:
                members = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    members.append(member)
                    if member == node:
                        break
                if len(members) > 1:
                    for member in members:
                        out[member] = component
                    component += 1
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
    return out
