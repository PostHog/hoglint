"""The PostHog pack: the repo's own producer (drives), for the `[posthog]` section.

The facade facts producer is no part of a check: core derives the signature slots of contract
surfaces itself, and only the legacy ledger script runs the producer.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from hoglint.core.harness import ProducerPlan, ProducerStep
from hoglint.facts import FactNeeds
from hoglint.model import Edge
from hoglint.plugins import PackReader, PackSink
from hoglint.presets_py.posthog.drives import DRIVES_PRODUCER, DriveItems, DrivesProducer

SECTION = "posthog"
EDGE_KINDS = frozenset({"drives"})
FACT_NEEDS = FactNeeds(
    kinds=frozenset(
        {"imports", "defs", "assigns", "refs", "uses", "calls", "callees", "names", "tags", "watched", "branches"}
    ),
    watch_callees=frozenset({"patch", "object"}),
    watch_keys=frozenset({"kind"}),
)


class PosthogPack:
    name = SECTION
    allowed_keys: frozenset[str] = frozenset()
    edge_kinds = EDGE_KINDS

    def fact_needs(self, section: Mapping[str, Any]) -> FactNeeds:
        return FACT_NEEDS

    def produce(self, section: Mapping[str, Any], reader: PackReader, sink: PackSink, root: Path) -> None:
        self.run(reader, sink)

    @staticmethod
    def run(reader: PackReader, sink: PackSink) -> None:
        def nothing() -> list[Edge]:
            return []

        def items() -> DriveItems:
            return DriveItems(DrivesProducer(reader))

        step = ProducerStep(DRIVES_PRODUCER, EDGE_KINDS, nothing, items=items)
        ProducerPlan([step]).run(sink, reader)


POSTHOG_PACK = PosthogPack()
