"""Primitives over paths in a view: reach (a denied target through a chain) and cycle."""

from __future__ import annotations

from collections import deque

from hoglint.model import Edge, Finding
from hoglint.rules.base import EdgeEnv, EdgeRule, Endpoint, project, render_value
from hoglint.selectors import Ref, State, Truth


class ReachPrimitive(EdgeRule):
    """Is a denied target reachable from a selected module through the view's edges?"""

    def adjacency(self) -> dict[str, list[tuple[Edge, Endpoint]]]:
        out: dict[str, list[tuple[Edge, Endpoint]]] = {}
        for edge in self.edges():
            for dst in self.ctx.dst_endpoints(edge, "origin"):
                out.setdefault(edge.site.module, []).append((edge, dst))
        for hops in out.values():
            hops.sort(key=lambda hop: (hop[1].module or "", hop[1].symbol or "", hop[0].surface))
        return out

    def sources(self, adjacency: dict[str, list[tuple[Edge, Endpoint]]]) -> list[str]:
        out = []
        for module in sorted(adjacency):
            if self.spec.from_ is None:
                out.append(module)
                continue
            first_edge = adjacency[module][0][0]
            env = EdgeEnv(self.ctx, first_edge, self.ctx.src_endpoint(first_edge.site), Endpoint(None, None))
            truth = self.spec.from_.evaluate(env.resolve)
            if truth is Truth.TRUE:
                out.append(module)
            elif truth is Truth.UNKNOWN:
                self.incomplete(env, f"cannot tell whether {module} is selected")
        return out

    def evaluate(self):  # type: ignore[override]
        adjacency = self.adjacency()
        for source in self.sources(adjacency):
            self.search(source, adjacency)
        return self.run

    def search(self, source: str, adjacency: dict[str, list[tuple[Edge, Endpoint]]]) -> None:
        origin = self.ctx.src_endpoint(adjacency[source][0][0].site)
        origin = Endpoint(origin.module, None, origin.path)
        parents: dict[str, tuple[str, Edge] | None] = {source: None}
        queue = deque([source])
        reported: set[str] = set()
        while queue:
            module = queue.popleft()
            for edge, dst in adjacency.get(module, []):
                env = EdgeEnv(self.ctx, edge, origin, dst)
                if dst.unresolved:
                    self.on_unresolved(env)
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

    def group_field(self) -> str:
        return "package" if self.spec.view == "packages" else "module"

    def group(self, env: EdgeEnv, root: str) -> str | None:
        value = env.resolve(Ref((root, self.group_field())))
        if value.state is not State.KNOWN:
            return None
        return render_value(value)

    def evaluate(self):  # type: ignore[override]
        pairs: dict[tuple[str, str], list[EdgeEnv]] = {}
        for env in self.envs():
            selected = self.selection(env)
            if selected is Truth.FALSE:
                continue
            if env.dst.unresolved:
                if self.spec.on_unresolved == "incomplete":
                    self.incomplete(env, f"{env.edge.kind} {env.edge.surface!r} did not resolve")
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
            self.run.findings.append(
                Finding(
                    rule=self.spec.id,
                    identity=project(self.spec.identity, first.resolve),
                    message=f"{src} -> {dst} closes a cycle ({len(envs)} edges)",
                    site=first.edge.site,
                    witnesses=tuple(env.edge for env in envs[:3]),
                    owners=self.ctx.owners_of(first.src.module),
                    subjects=self.subjects(first),
                    ledgerable=self.spec.ledger,
                )
            )
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
