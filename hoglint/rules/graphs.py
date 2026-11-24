"""Primitives over paths in a view: reach (a denied target through a chain) and cycle."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from hoglint.config.model import Config, RuleSpec
from hoglint.model import Edge, IncompleteKind
from hoglint.rules.base import EdgeRule, IncompleteItem, Judgment, RuleContext, RuleRun, Seen, named_seen, subjects_of
from hoglint.rules.plan import identity_paths, selector_tree
from hoglint.selectors import Selector, Truth


@dataclass(frozen=True, slots=True)
class Found:
    """What a search a rule context ran found (`RuleContext.reach_found`), in order:
    - "start": a start module (`source`) whose `from` is unknown; no path;
    - "unresolved": a hop from `source` whose destination did not resolve; `path` is the hop;
    - "unknown": a hop the rule cannot tell whether to follow; `path` is the hop;
    - "denied": a denied target, `path` the edges to it from `source`, `verdict` its truth.
    `seen` is what the path's last hop names, seen from `source`."""

    what: str
    source: str
    path: tuple[Edge, ...]
    seen: Seen | None
    verdict: Truth | None


class ReachPrimitive(EdgeRule):
    """Is a denied target reachable from a selected module through the view's edges?

    `from` picks the start modules and reads the module alone (`src.*`, no edge). `select` picks
    the edges the search may follow, each judged with its own source. `deny` is judged with the
    start module as `src`, so a condition on the start holds along the whole chain.
    """

    PRIMITIVE = MODE = "reach"
    NATIVE_MODE = "reach"
    READS = frozenset(
        {"kinds", "contexts", "view", "from", "select", "deny", "unless", "through_denied", "on_unresolved"}
    )

    def selectors(self) -> tuple[Selector | None, ...]:
        return (self.spec.select,)

    def evaluate(self) -> RuleRun:
        for item in self.ctx.reach_found(self):
            self.report_found(item)
        return self.run

    def report_found(self, found: Found) -> None:
        """What the search reports for one thing the evaluator found."""
        if found.what == "start":
            reason = f"cannot tell whether {found.source} is selected"
            item = IncompleteItem(
                self.spec.id, found.source, reason, (found.source,), kind=IncompleteKind.UNKNOWN_LABEL
            )
            self.run.incomplete.append(item)
            return
        seen = found.seen
        assert seen is not None
        if found.what == "unresolved":
            self.on_unresolved(seen)
        elif found.what == "unknown":
            reason = f"cannot tell whether to follow {seen.edge.kind} {seen.edge.surface!r}"
            self.incomplete(seen, reason, IncompleteKind.UNKNOWN_LABEL)
        else:
            assert found.verdict is not None
            self.report(seen, list(found.path), found.verdict)

    def plan(self) -> dict[str, Any]:
        """Its `from` reads the start module alone; `deny` and `unless` judge each hop seen from
        the start."""
        spec = self.spec
        return {
            "id": spec.id,
            "mode": self.NATIVE_MODE,
            "selectors": self.selectors_plan(),
            "edges": self.edges_plan(),
            "identity": identity_paths(spec.identity),
            "identified": sorted(self.identified()),
            "start": selector_tree(spec.from_),
            "deny": selector_tree(spec.deny),
            "unless": selector_tree(spec.unless),
            "through_denied": spec.through_denied,
        }

    def report(self, seen: Seen, path: list[Edge], verdict: Truth) -> None:
        chain = " -> ".join([seen.src.module or "", *(e.surface for e in path)])
        message = f"{seen.src.module} reaches {seen.dst.symbol or seen.dst.module}: {chain}"
        if verdict is Truth.UNKNOWN:
            message += " (cannot tell, a value is unassigned)"
        judgment = Judgment.UNKNOWN_VIOLATION if verdict is Truth.UNKNOWN else Judgment.VIOLATION
        self.emit(seen, judgment, message, witnesses=tuple(path))


# The edges a cycle finding names as its witnesses.
WITNESSES = 3


@dataclass(frozen=False, slots=True)
class Pair:
    """The edges from one group to another: how many, the first few as witnesses, and the first
    one, which a finding takes its identity, owner and subjects from: what it names (`first`) and
    its place (`step`, edge and destination). Its identity is read only for a pair inside a
    cycle."""

    count: int = 0
    witnesses: list[Edge] = field(default_factory=list)
    first: Seen | None = None
    step: tuple[int, int] = (0, 0)

    def add(self, edge: Edge) -> None:
        self.count += 1
        if len(self.witnesses) < WITNESSES:
            self.witnesses.append(edge)


@dataclass(frozen=True, slots=True)
class CycleRow:
    """One destination a cycle rule cannot leave alone, as a rule context found it
    (`RuleContext.cycle_rows`): unresolved, with an unknown selection, or one edge of `pair`.
    `named` is what the evaluator names of it (`named_seen`): for a row reported alone and for
    the first row of each pair."""

    edge: int
    target: int
    judgment: Judgment
    pair: tuple[str, str] | None
    src: str
    named: tuple[Any, ...] | None


class CyclePrimitive(EdgeRule):
    """Strongly connected parts of a grouped view: every pair inside one is a finding."""

    PRIMITIVE = MODE = "cycle"
    NATIVE_MODE = "cycle"
    READS = frozenset({"kinds", "contexts", "view", "from", "select", "on_unresolved"})

    def __init__(self, ctx: RuleContext, spec: RuleSpec) -> None:
        super().__init__(ctx, spec)
        self.group_field = self.group_of(ctx.config, spec)

    @staticmethod
    def group_of(config: Config, spec: RuleSpec) -> str:
        """The field whose values are the nodes of the rule's graph."""
        return config.roles.group if spec.view == "packages" else "module"

    def plan(self) -> dict[str, Any]:
        return {
            "id": self.spec.id,
            "mode": self.NATIVE_MODE,
            "selectors": self.selectors_plan(),
            "edges": self.edges_plan(),
            "identity": identity_paths(self.spec.identity),
            "identified": sorted(self.identified()),
            "group": self.group_field,
        }

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
            words = None
            if row.named is not None:
                unresolved = row.judgment is Judgment.UNRESOLVED
                words = named_seen(edges[row.edge], row.src, row.named, unresolved)
            if row.pair is None:
                assert words is not None
                if row.judgment is Judgment.UNRESOLVED:
                    self.on_unresolved(words)
                else:
                    self.incomplete(words, "selection reads an unknown value", IncompleteKind.UNKNOWN_LABEL)
                continue
            pair = pairs.setdefault(row.pair, Pair())
            if pair.first is None:
                pair.first, pair.step = words, (row.edge, row.target)
            pair.count += 1
            if len(pair.witnesses) < WITNESSES:
                pair.witnesses.append(edges[row.edge])
        return pairs

    def evaluate(self) -> RuleRun:
        pairs = self.pairs_of(self.ctx.cycle_rows(self))
        components = strongly_connected(sorted(pairs))
        cyclic = [
            (key, pair)
            for key, pair in sorted(pairs.items())
            if components.get(key[0]) is not None and components.get(key[0]) == components.get(key[1])
        ]
        identities = self.ctx.cycle_identities(self, [pair.step for _, pair in cyclic])
        way_back = WayBack(sorted(pairs))
        for ((src, dst), pair), identity in zip(cyclic, identities, strict=True):
            first = pair.first
            assert first is not None
            edges = f"{pair.count} {'edge' if pair.count == 1 else 'edges'}"
            steps = ", ".join(self.step_words(pairs, step) for step in way_back.steps(dst, src))
            finding = self.ctx.finding(
                self.spec,
                identity,
                f"the dependency {src} -> {dst} closes a cycle ({edges}); way back: {steps}",
                first.src.module,
                subjects_of(first.src.module, first.dst.module),
                tuple(pair.witnesses),
            )
            self.run.findings.append(finding)
        return self.run

    @staticmethod
    def step_words(pairs: dict[tuple[str, str], Pair], step: tuple[str, str]) -> str:
        """One step of a way back with the place of its pair's first witness."""
        site = pairs[step].witnesses[0].site
        return f"{step[0]} -> {step[1]} via {site.path}:{site.line}"


class WayBack:
    """The shortest existing path between two groups of a cycle rule's pairs, counted in steps.
    Of several shortest paths it takes the one whose groups sort first, step by step, so the
    words of a finding do not depend on the order the pairs came in."""

    def __init__(self, pairs: list[tuple[str, str]]) -> None:
        self.after: dict[str, list[str]] = {}
        self.before: dict[str, list[str]] = {}
        for src, dst in sorted(pairs):
            self.after.setdefault(src, []).append(dst)
            self.before.setdefault(dst, []).append(src)
        self.distances: dict[str, dict[str, int]] = {}

    def distances_to(self, goal: str) -> dict[str, int]:
        """Each group's number of steps to `goal`, for the groups with a path there."""
        known = self.distances.get(goal)
        if known is not None:
            return known
        found = {goal: 0}
        queue = deque([goal])
        while queue:
            node = queue.popleft()
            for previous in self.before.get(node, []):
                if previous in found:
                    continue
                found[previous] = found[node] + 1
                queue.append(previous)
        self.distances[goal] = found
        return found

    def steps(self, start: str, goal: str) -> list[tuple[str, str]]:
        """The steps of the shortest path from `start` to `goal`; none when there is no path."""
        distances = self.distances_to(goal)
        if start not in distances:
            return []
        out: list[tuple[str, str]] = []
        node = start
        while node != goal:
            # `after` is sorted: the first group one step closer is the one that sorts first.
            closer = next(nxt for nxt in self.after[node] if distances.get(nxt) == distances[node] - 1)
            out.append((node, closer))
            node = closer
        return out


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
