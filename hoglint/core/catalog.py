"""What the core producers make and read. The native link runs them; the shell reads this table to
ask the extractor for the facts they need and to name the edge kinds a failed snapshot leaves
incomplete."""

from __future__ import annotations

from dataclasses import dataclass

from hoglint.core.config import CoreConfig

# Fact kinds every producer needs, because the resolver builds module tables from them.
TABLE_KINDS = frozenset({"imports", "defs", "assigns"})
# What the declaration producer reads in a contract or export surface: a call on `__all__` makes a
# hand-out unknown (`callees`); `refs` and `uses` are the names the module refers to itself.
SURFACE_READS = frozenset({"callees", "refs", "uses"})


@dataclass(frozen=True)
class CoreProducer:
    """One core producer: the edge kinds it makes, the fact kinds it reads besides TABLE_KINDS,
    and those it reads only when a contract or export surface is configured."""

    kind: str
    edge_kinds: frozenset[str]
    reads: frozenset[str] = frozenset()
    surface_reads: frozenset[str] = frozenset()

    def fact_kinds(self, config: CoreConfig) -> frozenset[str]:
        if config.contract_surfaces or config.export_surfaces:
            return self.reads | self.surface_reads
        return self.reads


CORE_PRODUCERS: tuple[CoreProducer, ...] = (
    # An import edge reads `listed`: whether every load of the name it binds sits in a literal.
    CoreProducer("import", frozenset({"import"}), frozenset({"listed"})),
    # A reference edge reads `uses` too: a name read through a module alias of an export module.
    CoreProducer("reference", frozenset({"reference"}), frozenset({"refs", "uses"})),
    CoreProducer(
        "declaration",
        frozenset({"inherit", "decorate", "exposes", "declares", "rebind"}),
        surface_reads=SURFACE_READS,
    ),
    CoreProducer("dotted-path", frozenset({"dotted-path"}), frozenset({"strs"})),
    CoreProducer("dynamic-import", frozenset({"dynamic-import"}), frozenset({"calls"})),
    CoreProducer("unparsed", frozenset({"unparsed"})),
)


def core_fact_kinds(config: CoreConfig) -> frozenset[str]:
    """Every fact kind the core producers read under `config`."""
    return TABLE_KINDS.union(*(producer.fact_kinds(config) for producer in CORE_PRODUCERS))


def core_edge_kinds() -> frozenset[str]:
    return frozenset().union(*(producer.edge_kinds for producer in CORE_PRODUCERS))
