"""Small synthetic L1 records, in the extractor's row shapes, for core tests."""

from __future__ import annotations

from hoglint.core.build import CoreBuild
from hoglint.core.config import CoreConfig
from hoglint.facts import FactStore
from hoglint.model import Edge, Graph


class FileFacts:
    """One file's record. Rows follow schema v1: positions are (line, column)."""

    def __init__(self, path: str) -> None:
        self.record: dict = {
            "path": path,
            "status": "ok",
            "imports": [],
            "defs": [],
            "refs": [],
            "assigns": [],
            "strs": [],
            "calls": [],
            "site_flags": [],
        }
        self.line = 0

    def next_line(self) -> int:
        self.line += 1
        return self.line

    def import_module(self, module: str, asname: str | None = None, scope: int = -1, flags: int = 0) -> FileFacts:
        self.record["imports"].append([scope, self.next_line(), 0, flags, module, 0, None, asname])
        return self

    def import_from(self, module: str | None, name: str, level: int = 0, scope: int = -1, flags: int = 0) -> FileFacts:
        self.record["imports"].append([scope, self.next_line(), 0, flags, module, level, name, None])
        return self

    def define(self, name: str, kind: str = "def", parent: int = -1, **extra: object) -> FileFacts:
        line = self.next_line()
        self.record["defs"].append({"n": name, "k": kind, "s": parent, "l": line, "c": 0, "e": line, **extra})
        return self

    def assign(self, target: str, value: object, op: str = "=", flags: int = 0) -> FileFacts:
        row = {"op": op, "t": [target], "s": -1, "l": self.next_line(), "c": 0, "v": value}
        if flags:
            row["f"] = flags
        self.record["assigns"].append(row)
        return self

    def site_line(self, flags: int) -> int:
        """A new line for a refs, strs or calls row; their statement flags go to `site_flags`."""
        line = self.next_line()
        if flags:
            self.record["site_flags"].append([line, 0, flags])
        return line

    def ref(self, name: str, chain: str = "", up: str = "expr", scope: int = -1, flags: int = 0) -> FileFacts:
        self.record["refs"].append([scope, -1, self.site_line(flags), 0, name, chain, up])
        return self

    def string(self, value: str, up: str = "expr", scope: int = -1, flags: int = 0) -> FileFacts:
        self.record["strs"].append([scope, -1, self.site_line(flags), 0, value, "", up])
        return self

    def call(self, callee: str, args: list, kws: list, scope: int = -1, flags: int = 0) -> FileFacts:
        self.record["calls"].append([scope, -1, self.site_line(flags), 0, callee, args, kws])
        return self


def build(files: list[FileFacts], config: CoreConfig | None = None) -> Graph:
    store = FactStore()
    for facts in files:
        store.records[facts.record["path"]] = facts.record
    return CoreBuild(config or CoreConfig(), store).graph()


def edges_of(graph: Graph, kind: str, path: str) -> list[Edge]:
    return [edge for edge in graph.edges if edge.kind == kind and edge.site.path == path]
