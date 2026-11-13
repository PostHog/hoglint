"""What a run of one snapshot is asked: its options, and what the registered packs need extracted."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from hoglint.core.config import CoreConfig
from hoglint.facts import FactNeeds, default_cache_dir
from hoglint.plugins import REGISTRY


def registered_needs() -> FactNeeds:
    """What every registered pack asks of the extractor with its defaults."""
    out = FactNeeds()
    for pack in REGISTRY.packs.values():
        fact_needs = getattr(pack, "fact_needs", None)
        if fact_needs is not None:
            out = out | fact_needs({})
    return out


@dataclass(frozen=True)
class AnalysisOptions:
    """`staged` reads the git index instead of the working tree. `cache_dir` None disables the cache.

    `needs` is what the packs that run afterwards ask of the extractor; None means every
    registered pack with its defaults.
    """

    repo: Path
    config: CoreConfig
    work_dir: Path
    staged: bool = False
    cache_dir: Path | None = field(default_factory=default_cache_dir)
    needs: FactNeeds | None = None
    tree: str = ""  # a git tree id: read that tree's blobs instead of the index or the working tree
