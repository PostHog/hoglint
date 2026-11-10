"""What a rule's decision reads, as the parts a grouping key holds.

A rule's reads are the fields its selectors name, read off the parsed selectors, and the fields its
primitive's own Python code reads (`EdgeRule.own_reads`). Grouped evaluation keys on them, and
the read audit (`hoglint.rules.audit`) checks that a decision reads nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass

from hoglint.config.model import Config
from hoglint.labels import Labeler
from hoglint.rules.base import EDGE_DEPENDENT_FIELDS, EdgeRule
from hoglint.selectors import Ref, refs

# What a grouping key holds whatever a rule reads: the edge's kind, and whether the destination
# resolved. Endpoint modules and paths are not in it: a key holds label values, so two modules
# with equal values share a group.
IMPLIED: frozenset[tuple[str, str]] = frozenset({("edge", "kind"), ("dst", "unresolved")})
# The edge fields `edge.label` is made of (`EdgeEnv.label`); whether the destination resolved is
# implied.
LABEL_PARTS: frozenset[str] = frozenset({"kind", "shape", "shape_detail"})


@dataclass(frozen=True, slots=True)
class Reads:
    """What a rule's decision reads, split by where the value comes from."""

    edge: frozenset[str]
    src: frozenset[str]
    dst: frozenset[str]
    exported: bool

    @staticmethod
    def of(ref_list: list[Ref], config: Config) -> Reads | None:
        """None when a read has no grouped form (a per-edge ordinal, a src export)."""
        edge: set[str] = set()
        src: set[str] = set()
        dst: set[str] = set()
        exported = False
        pending = list(ref_list)
        while pending:
            ref = pending.pop()
            if len(ref.path) != 2:
                continue
            root, name = ref.path
            if root == "edge":
                if name == "ordinal":
                    return None
                if name == "label":
                    dst.add("unresolved")
                    edge.update(LABEL_PARTS)
                else:
                    edge.add(name)
            elif root in ("src", "dst") and name == "context":
                edge.add("context")
            elif root == "src" and name in EDGE_DEPENDENT_FIELDS:
                return None
            elif root == "dst" and name == "exported":
                if not exported:
                    exported = True
                    for spec in config.exports.values():
                        if spec.audience is not None:
                            pending.extend(refs(spec.audience.node))
            elif root == "src":
                src.add(name)
            elif root == "dst":
                dst.add(name)
        return Reads(frozenset(edge), frozenset(src), frozenset(dst), exported)

    def roots(self) -> set[str]:
        out = set()
        if self.edge:
            out.add("edge")
        if self.src:
            out.add("src")
        if self.dst or self.exported:
            out.add("dst")
        return out

    def granted(self, src_scope: bool, dst_entity: bool) -> frozenset[tuple[str, str]]:
        """Every (root, field) a decision grouped on these reads may read: the reads, what the
        key implies, the label where the key holds its parts, and the symbol-level fields of an
        endpoint keyed at symbol level."""
        out = set(IMPLIED)
        out.update(("edge", name) for name in self.edge)
        out.update(("src", name) for name in self.src)
        out.update(("dst", name) for name in self.dst)
        if LABEL_PARTS <= self.edge:
            out.add(("edge", "label"))
        if "context" in self.edge:
            out.update({("edge", "context"), ("src", "context"), ("dst", "context")})
        if self.exported:
            out.update({("dst", "exported"), ("dst", "external")})
        if src_scope:
            out.update({("src", "scope"), ("src", "symbol"), ("src", "name")})
        if dst_entity:
            out.update({("dst", "symbol"), ("dst", "name")})
        return frozenset(out)


@dataclass(frozen=True, slots=True)
class Granularity:
    """What a grouping key over some reads holds of each end besides its module: a source's
    scope, a destination's entity, or a destination's entity only where an export that decides
    by class (`subclass_of`) covers its path (`dst_by_export`), since only there does
    `dst.exported` depend on the symbol."""

    src_scope: bool
    dst_entity: bool
    dst_by_export: bool


def granularity(reads: Reads, labeler: Labeler) -> Granularity:
    src_scope = not all(labeler.module_level(Ref(("src", name))) for name in reads.src)
    dst_entity = not all(labeler.module_level(Ref(("dst", name))) for name in reads.dst if name != "unresolved")
    by_class = reads.exported and any(spec.subclass_of for spec in labeler.config.exports.values())
    return Granularity(src_scope, dst_entity, by_class and not dst_entity)


def decision_reads(rule: EdgeRule) -> Reads | None:
    """The reads a grouped decision of `rule` keys on: its declared reads. A finding's identity is
    made per edge, so it is not part of the decision."""
    return Reads.of(rule.read_refs(), rule.ctx.config)
