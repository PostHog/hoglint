"""The PostHog pack: the repo's own producers (drives, facade facts), for the `[posthog]` section."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from hoglint.core.harness import ProducerPlan, ProducerStep
from hoglint.facts import FactNeeds
from hoglint.model import Edge
from hoglint.plugins import PackReader, PackSink
from hoglint.presets_py.posthog.drives import DRIVES_PRODUCER, DrivesProducer
from hoglint.presets_py.posthog.facade import FACADE_PRODUCER, FacadeFacts

SECTION = "posthog"
# Only the facade makes `export` edges, and its `exposes` add the slots of re-exported functions.
# Its inherit, decorate and declares edges copy core's, so a facade failure leaves them complete.
FACADE_KINDS = frozenset({"export", "exposes"})
EDGE_KINDS = frozenset({"drives", *FACADE_KINDS})
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
        def drives() -> list[Edge]:
            return DrivesProducer(reader).produce()

        def facade() -> list[Edge]:
            return FacadeFacts(reader).produce()

        ProducerPlan(
            [
                ProducerStep(DRIVES_PRODUCER, frozenset({"drives"}), drives),
                ProducerStep(FACADE_PRODUCER, FACADE_KINDS, facade),
            ]
        ).run(sink)


POSTHOG_PACK = PosthogPack()
