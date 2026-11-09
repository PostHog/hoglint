"""How producers run: one item that fails is an unknown edge, one producer that fails is a
`failed_producers` entry with the edge kinds it would have made. Never a crash, never silence."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TypeVar

from hoglint.model import Context, Edge, Graph, Resolution, Site
from hoglint.plugins import ProducerFailure

PRODUCER_ERROR = "producer-error"

Item = TypeVar("Item")


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
    edges_for: Callable[[Item], Iterable[Edge]],
    on_error: Callable[[Item, Exception], Edge],
) -> list[Edge]:
    """Edges of every item; an item that raises gives its `on_error` edge instead."""
    out: list[Edge] = []
    for item in items:
        try:
            out.extend(edges_for(item))
        except Exception as error:  # noqa: BLE001 - one unreadable item is unknown, the others still count
            out.append(on_error(item, error))
    return out


@dataclass(frozen=True, slots=True)
class ProducerStep:
    """One producer: its id (also the `producer` of its edges), the edge kinds it makes, the
    steps it needs, and the call that makes its edges."""

    name: str
    kinds: frozenset[str]
    run: Callable[[], Iterable[Edge]]
    after: tuple[str, ...] = ()


class ProducerPlan:
    """Runs steps in order. A step that raises is failed, and so is every step after it that
    needs it. A step may also record a partial failure itself and return its edges; the steps
    that need it still run."""

    def __init__(self, steps: Iterable[ProducerStep]) -> None:
        self.steps = list(steps)

    def run(self, graph: Graph) -> None:
        broken: set[str] = set()
        for step in self.steps:
            missing = next((dep for dep in step.after if dep in broken), None)
            if missing is not None:
                broken.add(step.name)
                graph.failed_producers[step.name] = ProducerFailure(f"{missing} failed", step.kinds)
                continue
            try:
                edges = list(step.run())
            except Exception as error:  # noqa: BLE001 - a failed producer makes its rules incomplete, never clean
                broken.add(step.name)
                graph.failed_producers[step.name] = ProducerFailure(failure_text(error), step.kinds)
                continue
            graph.extend(edges)
