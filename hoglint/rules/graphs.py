"""Primitives over paths in a view: reach (a denied target through a chain) and cycle."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from hoglint.config.model import Config, RuleSpec
from hoglint.index import has_endpoint_attrs
from hoglint.model import Edge
from hoglint.rules.base import (
    EDGE_DEPENDENT_FIELDS,
    EdgeEnv,
    EdgeRule,
    Endpoint,
    GroupedSurvivors,
    IncompleteItem,
    Judgment,
    ModuleEnv,
    RuleContext,
    RuleRun,
    project,
    read_endpoint,
)
from hoglint.selectors import UNKNOWN, ClassSelection, Ref, Selector, State, Truth, Value, render_value


@dataclass(frozen=True, slots=True)
class Hop:
    """One edge a reach search may follow: an Edge, or a core edge id the context makes into an
    Edge only when a finding or an incomplete needs it."""

    edge: Edge | int
    dst: Endpoint
    selected: Truth
    surface: str


@dataclass(frozen=True, slots=True)
class Found:
    """What a search run outside Python found from start module `source`: a hop whose
    destination did not resolve (`verdict` None, `path` the hop alone), or a denied target with
    the path to it, the hop that reaches it last. `dst` is that hop's destination."""

    source: str
    path: tuple[Edge, ...]
    dst: Endpoint
    verdict: Truth | None


class ReachSearch(Protocol):
    """A reach rule's search that a rule context runs itself (`RuleContext.reach_search`)."""

    def has_hops(self, module: str) -> bool: ...

    def run(self, sources: list[str]) -> list[Found]: ...


class ReachPrimitive(EdgeRule):
    """Is a denied target reachable from a selected module through the view's edges?

    `from` picks the start modules and reads the module alone (`src.*`, no edge). `select` picks
    the edges the search may follow, each judged with its own source. `deny` is judged with the
    start module as `src`, so a condition on the start holds along the whole chain.
    """

    def __init__(self, ctx: RuleContext, spec: RuleSpec) -> None:
        super().__init__(ctx, spec)
        self.from_classes = ClassSelection(spec.from_.node) if spec.from_ is not None else None
        self.hop_cache: dict[str, list[Hop]] = {}
        self.verdicts: dict[tuple[Endpoint, Endpoint], tuple[Truth, bool]] = {}
        judged = [selector for selector in (spec.deny, spec.unless) if selector is not None]
        self.endpoint_verdicts = all(
            len(ref.path) == 2 and ref.path[0] in ("src", "dst") and ref.path[1] not in EDGE_DEPENDENT_FIELDS
            for selector in judged
            for ref in selector.refs()
        )
        self.origin_free = self.endpoint_verdicts and all(
            ref.path[0] != "src" for selector in judged for ref in selector.refs()
        )
        self.hop_verdicts: dict[str, list[tuple[Truth, bool] | None]] = {}

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

    def sources(self, has_hops: Callable[[str], bool]) -> list[str]:
        out = []
        for module in self.ctx.source_modules(self.kinds()):
            truth = Truth.TRUE
            if self.from_classes is not None:
                truth = self.from_classes.truth(ModuleEnv(self.ctx, self.ctx.module_endpoint(module)).resolve)
            if truth is Truth.FALSE or not has_hops(module):
                continue
            if truth is Truth.TRUE:
                out.append(module)
                continue
            reason = f"cannot tell whether {module} is selected"
            self.run.incomplete.append(IncompleteItem(self.spec.id, module, reason, (module,)))
        return out

    def evaluate(self) -> RuleRun:
        search = self.ctx.reach_search(self)
        if search is None:
            for source in self.sources(lambda module: bool(self.hops(module))):
                self.search(source)
            return self.run
        for found in search.run(self.sources(search.has_hops)):
            env = EdgeEnv(self.ctx, found.path[-1], self.ctx.module_endpoint(found.source), found.dst)
            if found.verdict is None:
                self.on_unresolved(env)
            else:
                self.report(env, list(found.path), found.verdict)
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
        verdict = self.deny_classes.truth(env.resolve) if self.deny_classes is not None else Truth.FALSE
        excused = self.unless_classes is not None and self.unless_classes.truth(env.resolve) is Truth.TRUE
        return verdict, excused

    def kept_verdict(self, module: str, at: int, hop: Hop, origin: Endpoint) -> tuple[Truth, bool]:
        """The verdict of the `at`-th hop out of `module`, kept per hop when no start module
        changes it."""
        if not self.origin_free:
            return self.verdict(hop, origin)
        kept = self.hop_verdicts.get(module)
        if kept is None:
            kept = self.hop_verdicts[module] = [None] * len(self.hops(module))
        found = kept[at]
        if found is None:
            found = kept[at] = self.verdict(hop, origin)
        return found

    def search(self, source: str) -> None:
        origin = self.ctx.module_endpoint(source)
        parents: dict[str, tuple[str, Hop] | None] = {source: None}
        queue = deque([source])
        reported: set[str] = set()
        while queue:
            module = queue.popleft()
            for at, hop in enumerate(self.hops(module)):
                dst = hop.dst
                if dst.unresolved:
                    self.on_unresolved(self.hop_env(hop, origin))
                    continue
                if hop.selected is Truth.UNKNOWN:
                    env = self.hop_env(hop, origin)
                    self.incomplete(env, f"cannot tell whether to follow {env.edge.kind} {env.edge.surface!r}")
                    continue
                verdict, excused = self.kept_verdict(module, at, hop, origin)
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
        judgment = Judgment.UNKNOWN_VIOLATION if verdict is Truth.UNKNOWN else Judgment.VIOLATION
        self.emit(env, judgment, message, witnesses=tuple(path))


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


# The edges a cycle finding names as its witnesses.
WITNESSES = 3


@dataclass(frozen=False, slots=True)
class Pair:
    """The edges from one group to another: how many, and the first few as witnesses."""

    count: int = 0
    envs: list[EdgeEnv] = field(default_factory=list)

    def add(self, env: EdgeEnv) -> None:
        self.count += 1
        if len(self.envs) < WITNESSES:
            self.envs.append(env)


@dataclass(frozen=True, slots=True)
class CycleRow:
    """One destination a cycle rule cannot leave alone, as a rule context found it
    (`RuleContext.cycle_rows`): unresolved, with an unknown selection, or one edge of `pair`."""

    edge: int
    target: int
    judgment: Judgment
    pair: tuple[str, str] | None


class CyclePrimitive(EdgeRule):
    """Strongly connected parts of a grouped view: every pair inside one is a finding."""

    def __init__(self, ctx: RuleContext, spec: RuleSpec) -> None:
        super().__init__(ctx, spec)
        self.group_field = self.group_of(ctx.config, spec)
        self.group_cache: dict[tuple[str, Endpoint], str | None] = {}

    @staticmethod
    def group_of(config: Config, spec: RuleSpec) -> str:
        """The field whose values are the nodes of the rule's graph."""
        return config.roles.group if spec.view == "packages" else "module"

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

    def consider(self, env: EdgeEnv, pairs: dict[tuple[str, str], Pair]) -> None:
        """Pairs one environment's groups, or reports it when it cannot."""
        selected = self.selection(env)
        if selected is Truth.FALSE:
            return
        if env.dst.unresolved:
            self.on_unresolved(env)
            return
        if selected is Truth.UNKNOWN:
            self.incomplete(env, "selection reads an unknown value")
            return
        src, dst = self.group(env, "src"), self.group(env, "dst")
        if src is None or dst is None or src == dst:
            return
        pairs.setdefault((src, dst), Pair()).add(env)

    def action(self, env: EdgeEnv) -> tuple[str, str] | str | None:
        """What `consider` does with an environment, as one value a group of them shares: its
        pair, "unresolved", "unknown", or None to drop it."""
        selected = self.selection(env)
        if selected is Truth.FALSE:
            return None
        if env.dst.unresolved:
            return "unresolved"
        if selected is Truth.UNKNOWN:
            return "unknown"
        src, dst = self.group(env, "src"), self.group(env, "dst")
        return None if src is None or dst is None or src == dst else (src, dst)

    def env_of(self, edge: Edge) -> EdgeEnv:
        return self.env(edge, self.ctx.src_endpoint(edge.site), self.ctx.dst_endpoints(edge, self.spec.endpoint)[0])

    def grouped_pairs(self, grouped: GroupedSurvivors) -> dict[tuple[str, str], Pair]:
        """`consider` over handle arrays: each group decides once from its representative, and
        an Edge is made only for an edge reported on its own or among a pair's first witnesses."""
        actions = [self.action(self.env_of(rep)) for rep in grouped.reps]
        wanted: list[int] = []
        seen: dict[tuple[str, str], int] = {}
        for edge_id, group in zip(grouped.ids, grouped.groups):
            action = actions[group] if group is not None else "alone"
            if isinstance(action, tuple):
                if seen.get(action, 0) < WITNESSES:
                    wanted.append(edge_id)
                seen[action] = seen.get(action, 0) + 1
            elif action is not None:
                wanted.append(edge_id)
        edges = dict(zip(wanted, grouped.edges(wanted)))
        pairs: dict[tuple[str, str], Pair] = {}
        for edge_id, group in zip(grouped.ids, grouped.groups):
            if group is None:
                for env in self.edge_envs(edges[edge_id]):
                    self.consider(env, pairs)
                continue
            action = actions[group]
            if isinstance(action, tuple):
                pair = pairs.setdefault(action, Pair())
                pair.count += 1
                if len(pair.envs) < WITNESSES:
                    pair.envs.append(self.env_of(edges[edge_id]))
            elif action == "unresolved":
                self.on_unresolved(self.env_of(edges[edge_id]))
            elif action == "unknown":
                self.incomplete(self.env_of(edges[edge_id]), "selection reads an unknown value")
        return pairs

    def pairs_of(self, rows: list[CycleRow]) -> dict[tuple[str, str], Pair]:
        """`consider` over the rows a rule context found: an Edge is made only for a destination
        reported on its own or among a pair's first witnesses."""
        seen: dict[tuple[str, str], int] = {}
        wanted: list[int] = []
        for row in rows:
            if row.pair is not None:
                seen[row.pair] = seen.get(row.pair, 0) + 1
                if seen[row.pair] > WITNESSES:
                    continue
            wanted.append(row.edge)
        edges = dict(zip(wanted, self.ctx.index.edges_at(wanted)))
        pairs: dict[tuple[str, str], Pair] = {}
        for row in rows:
            if row.pair is None:
                env = self.envs_of(edges[row.edge])[row.target]
                if row.judgment is Judgment.UNRESOLVED:
                    self.on_unresolved(env)
                else:
                    self.incomplete(env, "selection reads an unknown value")
                continue
            pair = pairs.setdefault(row.pair, Pair())
            pair.count += 1
            if len(pair.envs) < WITNESSES:
                pair.envs.append(self.envs_of(edges[row.edge])[row.target])
        return pairs

    def evaluate(self) -> RuleRun:
        rows = self.ctx.cycle_rows(self)
        pairs: dict[tuple[str, str], Pair] = {}
        if rows is not None:
            pairs = self.pairs_of(rows)
        else:
            for env in self.envs():
                self.consider(env, pairs)
        components = strongly_connected(sorted(pairs))
        for (src, dst), pair in sorted(pairs.items()):
            if components.get(src) is None or components.get(src) != components.get(dst):
                continue
            first = pair.envs[0]
            finding = self.ctx.finding(
                self.spec,
                project(self.spec.identity, first.resolve),
                f"{src} -> {dst} closes a cycle ({pair.count} edges)",
                first.src.module,
                self.subjects(first),
                tuple(env.edge for env in pair.envs),
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
