"""How producers run: one item that fails is an unknown edge, one producer that fails is a
`failed_producers` entry with the edge kinds it would have made. Never a crash, never silence."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TypeVar

from hoglint.core.memo import TRACER, ItemRun, Output, PackMemo, StepItems
from hoglint.model import Context, Edge, Graph, Resolution, Site, refined_edge
from hoglint.plugins import PackSink, ProducerFailure, RefinedFields, Refinements

PRODUCER_ERROR = "producer-error"

Item = TypeVar("Item")
Made = TypeVar("Made", Edge, Output)  # what a producer makes per item: edges, or edges and refinements


def failure_text(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"


def failure_edge(kind: str, producer: str, site: Site, contexts: frozenset[Context], error: Exception) -> Edge:
    """One item a producer could not read: unknown on that item, while the other items still count."""
    return Edge(
        kind=kind,
        site=site,
        surface="",
        targets=(),
        resolution=Resolution.UNRESOLVED,
        contexts=contexts,
        producer=producer,
        attrs=(("reason", PRODUCER_ERROR), ("error", failure_text(error))),
    )


def each_item(
    items: Iterable[Item],
    edges_for: Callable[[Item], Iterable[Made]],
    on_error: Callable[[Item, Exception], Edge],
) -> list[Made]:
    """What every item makes; an item that raises gives its `on_error` edge instead."""
    out: list[Made] = []
    for item in items:
        try:
            out.extend(edges_for(item))
        except Exception as error:  # noqa: BLE001 - one unreadable item is unknown, the others still count
            out.append(on_error(item, error))
    return out


@dataclass(frozen=True, slots=True)
class ProducerStep:
    """One producer: its id (also the `producer` of its edges), the edge kinds it makes or
    refines, the steps it needs, and the call that makes its edges and refinements.

    `kinds` names the kinds a failure leaves incomplete in every rule that reads them: the kinds
    of the edges it makes, and those of the edges it refines.

    A step may also run as items (`items`, see hoglint/core/memo.py): its output is then what
    `run` makes, followed by its items' output."""

    name: str
    kinds: frozenset[str]
    run: Callable[[], Iterable[Output]]
    after: tuple[str, ...] = ()
    items: Callable[[], StepItems] | None = None


class ProducerPlan:
    """Runs steps in order into a sink: each step's edges as one batch, its refinements as
    another. A step that raises is failed, and so is every step after it that needs it. A step
    may also record a partial failure itself and return its output; the steps that need it
    still run.

    With a memo, a step runs inside a frame of the tracer, so what it computes outside its items
    has its reads noted too (`TracedMemo`), and each item runs in a frame of its own."""

    def __init__(self, steps: Iterable[ProducerStep]) -> None:
        self.steps = list(steps)

    @staticmethod
    def outputs(step: ProducerStep, memo: PackMemo | None) -> list[Output]:
        output = list(step.run())
        if step.items is not None:
            output.extend(ItemRun(step.items(), memo.step(step.name) if memo is not None else None).outputs())
        return output

    @staticmethod
    def run_step(step: ProducerStep, sink: PackSink, memo: PackMemo | None = None) -> bool:
        """One step into the sink; False when it raised. Its output is freed on return."""
        try:
            if memo is None:
                output = ProducerPlan.outputs(step, None)
            else:
                TRACER.step = step.name
                with TRACER.frame():
                    output = ProducerPlan.outputs(step, memo)
        except Exception as error:  # noqa: BLE001 - a failed producer makes its rules incomplete, never clean
            sink.fail(step.name, ProducerFailure(failure_text(error), step.kinds))
            return False
        edges = [item for item in output if isinstance(item, Edge)]
        refinements = Refinements.of(output)
        if edges:
            sink.add_edges(step.name, edges)
        if refinements:
            sink.refine(step.name, step.kinds, refinements)
        return True

    def run(self, sink: PackSink, memo: PackMemo | None = None) -> None:
        broken: set[str] = set()
        for step in self.steps:
            missing = next((dep for dep in step.after if dep in broken), None)
            if missing is not None:
                broken.add(step.name)
                sink.fail(step.name, ProducerFailure(f"{missing} failed", step.kinds))
                continue
            if not self.run_step(step, sink, memo):
                broken.add(step.name)
            # A sink may drop what the step read (the native engine's decoded records), so the
            # next step does not start on top of it.
            step_ended = getattr(sink, "step_ended", None)
            if step_ended is not None:
                step_ended()


class GraphSink:
    """The Python engine's sink: edges join the graph, a refinement replaces the edge at its
    handle with the refined edge, which keeps the handle."""

    def __init__(self, graph: Graph) -> None:
        self.graph = graph
        self.refined = RefinedFields()

    def add_edges(self, producer: str, edges: Sequence[Edge]) -> None:
        self.graph.extend(edges)

    def own_attrs(self, handle: int) -> dict[str, str]:
        return dict(self.graph.edges[handle].attrs)

    def refine(self, producer: str, kinds: frozenset[str], refinements: Refinements) -> None:
        conflicts = self.refined.conflicts(producer, refinements, self.own_attrs)
        if conflicts:
            more = f" and {len(conflicts) - 1} more" if len(conflicts) > 1 else ""
            self.fail(producer, ProducerFailure(f"sets {conflicts[0]}{more} apart", kinds))
            return
        self.refined.record(producer, refinements)
        edges = self.graph.edges
        for at in range(len(refinements)):
            refinement = refinements.row(at)
            edges[refinement.handle] = refined_edge(edges[refinement.handle], refinement)
        self.graph.kind_index = {}
        self.graph.indexed = 0

    def add_symbols(self, facts: Mapping[str, Mapping[str, str]]) -> None:
        self.graph.symbols.update({name: dict(values) for name, values in facts.items()})

    def fail(self, producer: str, failure: ProducerFailure) -> None:
        self.graph.failed_producers[producer] = failure
