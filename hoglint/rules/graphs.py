"""Primitives over paths in a view: reach (a denied target through a chain) and cycle."""

from __future__ import annotations

from collections import deque

from hoglint.config.model import RuleSpec
from hoglint.model import Edge
from hoglint.rules.base import EdgeEnv, EdgeRule, Endpoint, IncompleteItem, ModuleEnv, RuleContext, RuleRun, project
from hoglint.selectors import Ref, Selector, State, Truth, render_value

Hop = tuple[Edge, Endpoint, Truth]


class ReachPrimitive(EdgeRule):
    """Is a denied target reachable from a selected module through the view's edges?

    `from` picks the start modules and reads the module alone (`src.*`, no edge). `select` picks
    the edges the search may follow, each judged with its own source. `deny` is judged with the
    start module as `src`, so a condition on the start holds along the whole chain.
    """

    def __init__(self, ctx: RuleContext, spec: RuleSpec) -> None:
        super().__init__(ctx, spec)
        self.hop_cache: dict[str, list[Hop]] = {}

    def selectors(self) -> tuple[Selector | None, ...]:
        return (self.spec.select,)

    def hops(self, module: str) -> list[Hop]:
        """The selected edges out of one module, built when the search first reaches it."""
        cached = self.hop_cache.get(module)
        if cached is not None:
            return cached
        out: list[Hop] = []
        for group in self.ctx.groups_of(self.kinds(), module):
            for env in self.group_envs(module, group):
                selected = self.selection(env)
                if selected is not Truth.FALSE:
                    out.append((env.edge, env.dst, selected))
        out.sort(key=lambda hop: (hop[1].module or "", hop[1].symbol or "", hop[0].surface))
        self.hop_cache[module] = out
        return out

    def sources(self) -> list[str]:
        out = []
        for module in sorted({module for module, _ in self.ctx.module_groups(self.kinds())}):
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

    def search(self, source: str) -> None:
        origin = self.ctx.module_endpoint(source)
        parents: dict[str, tuple[str, Edge] | None] = {source: None}
        queue = deque([source])
        reported: set[str] = set()
        while queue:
            module = queue.popleft()
            for edge, dst, selected in self.hops(module):
                env = EdgeEnv(self.ctx, edge, origin, dst)
                if dst.unresolved:
                    self.on_unresolved(env)
                    continue
                if selected is Truth.UNKNOWN:
                    self.incomplete(env, f"cannot tell whether to follow {edge.kind} {edge.surface!r}")
                    continue
                verdict = self.spec.deny.evaluate(env.resolve) if self.spec.deny is not None else Truth.FALSE
                target = dst.symbol or dst.module or ""
                if verdict is not Truth.FALSE and not self.excused(env) and target not in reported:
                    reported.add(target)
                    path = [*self.path_to(parents, module), edge]
                    self.report(env, path, verdict)
                    continue
                if dst.external is not None or dst.module is None or dst.module in parents:
                    continue
                parents[dst.module] = (module, edge)
                queue.append(dst.module)

    def path_to(self, parents: dict[str, tuple[str, Edge] | None], module: str) -> list[Edge]:
        path: list[Edge] = []
        step = parents.get(module)
        while step is not None:
            previous, edge = step
            path.append(edge)
            step = parents.get(previous)
        return list(reversed(path))

    def report(self, env: EdgeEnv, path: list[Edge], verdict: Truth) -> None:
        chain = " -> ".join([env.src.module or "", *(e.surface for e in path)])
        message = f"{env.src.module} reaches {env.dst.symbol or env.dst.module}: {chain}"
        if verdict is Truth.UNKNOWN:
            message += " (cannot tell, a value is unassigned)"
        self.emit(env, message, witnesses=tuple(path), unknown=verdict is Truth.UNKNOWN)


class CyclePrimitive(EdgeRule):
    """Strongly connected parts of a grouped view: every pair inside one is a finding."""

    def __init__(self, ctx: RuleContext, spec: RuleSpec) -> None:
        super().__init__(ctx, spec)
        self.group_field = ctx.config.roles.group if spec.view == "packages" else "module"
        self.group_cache: dict[tuple[str, Endpoint], str | None] = {}

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
