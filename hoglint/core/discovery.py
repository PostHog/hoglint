"""Which source files exist in a snapshot, and which of them are analyzed."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from hoglint.core.config import CoreConfig
from hoglint.ledger.listing import SOURCE_SUFFIXES

SOURCE_PATTERNS = tuple(f"*{suffix}" for suffix in SOURCE_SUFFIXES)


@dataclass(frozen=True)
class Inventory:
    """`files` all exist and resolve as modules; only `analyzed` files produce edges."""

    root: Path
    files: tuple[str, ...]
    analyzed: tuple[str, ...]


class Snapshot(Protocol):
    """The source files of one snapshot (`hoglint.native.snapshot.BlobSnapshot`)."""

    @property
    def root(self) -> Path: ...

    def list(self, patterns: tuple[str, ...]) -> list[str]: ...


class Discovery:
    """Lists source files and splits them into analyzed files and resolvable-only files."""

    def __init__(self, config: CoreConfig) -> None:
        self.config = config

    def is_skipped(self, rel: str) -> bool:
        if self.config.include_hidden:
            return False
        return any(part.startswith(".") for part in rel.split("/"))

    def run(self, snapshot: Snapshot) -> Inventory:
        files = [rel for rel in snapshot.list(SOURCE_PATTERNS) if not self.is_skipped(rel)]
        excludes = self.config.exclude_globs
        analyzed = [rel for rel in files if not excludes.matches(rel)]
        return Inventory(root=snapshot.root, files=tuple(files), analyzed=tuple(analyzed))
