"""The `provenance` provider: which producer generated a file, from `[[generated]] producer, outputs`
entries in a TOML or JSON file. Outputs: `generated` (bool) and `producer` (a set)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from hoglint.config.loader import read_table
from hoglint.globs import glob_match
from hoglint.plugins import REGISTRY


class ProvenanceFile:
    def __init__(self, entries: list[dict[str, Any]]) -> None:
        self.entries = entries

    @staticmethod
    def read(path: Path) -> ProvenanceFile:
        return ProvenanceFile(list(read_table(path).get("generated", [])))

    def producers(self, path: str) -> frozenset[str]:
        out = set()
        for entry in self.entries:
            if any(glob_match(pattern, path) is not None for pattern in entry.get("outputs", [])):
                out.add(str(entry.get("producer", "")))
        return frozenset(out)


class ProvenanceProvider:
    use = "provenance"
    allowed_options = frozenset({"file", "sources"})
    outputs = ("generated", "producer")

    @staticmethod
    def file(options: Mapping[str, Any]) -> str:
        return str(options.get("file", "hoglint.provenance.toml"))

    def inputs(self, options: Mapping[str, Any]) -> tuple[str, ...]:
        return (self.file(options),)

    def open(self, options: Mapping[str, Any], root: Path) -> ProvenanceFile:
        return ProvenanceFile.read(root / self.file(options))

    def values(self, handle: ProvenanceFile, path: str) -> Mapping[str, Any]:
        producers = handle.producers(path)
        out: dict[str, Any] = {"generated": bool(producers)}
        if producers:
            out["producer"] = producers
        return out


REGISTRY.add_provider(ProvenanceProvider())
