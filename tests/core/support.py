"""Small synthetic L1 records, in the extractor's row shapes, for core tests."""

from __future__ import annotations

from hoglint.core.build import CoreBuild
from hoglint.core.config import CoreConfig
from hoglint.facts import FactStore
from hoglint.model import Edge, Graph

SITE = ".venv/lib/python3.14/site-packages/"


def import_row(module: str, name: str | None, scope: int = -1, flags: int = 0, line: int = 1) -> list:
    """An `imports` row: `from module import name`, or `import module` when name is None."""
    return [scope, line, 0, flags, module, 0, name, None]


def class_def(name: str, bases: list, line: int, scope: int = -1, flags: int = 0, **extra: object) -> dict:
    return {"k": "class", "n": name, "s": scope, "l": line, "c": 0, "e": line, "f": flags, "bases": bases, **extra}


def func_def(name: str, line: int, scope: int = -1, flags: int = 0, **extra: object) -> dict:
    return {"k": "def", "n": name, "s": scope, "l": line, "c": 0, "e": line, "f": flags, **extra}


def assign_row(target: str, value: object, line: int, scope: int = -1, flags: int = 0) -> dict:
    return {"s": scope, "l": line, "c": 0, "op": "=", "t": [target], "v": value, "f": flags}


class Repo:
    """Records keyed by path, completed with the envelope the store expects. Paths under SITE are
    third-party files: they resolve, and make no edges."""

    def __init__(self, *groups: dict[str, dict]) -> None:
        self.records: dict[str, dict] = {}
        for group in groups:
            for path, record in group.items():
                self.records[path] = {"path": path, "status": "ok", **record}

    def build(self, config: CoreConfig | None = None) -> CoreBuild:
        store = FactStore()
        store.records = dict(self.records)
        external = {path: path.removeprefix(SITE) for path in self.records if path.startswith(SITE)}
        return CoreBuild(config or CoreConfig(), store, external=external)


class FileFacts:
    """One file's record in fact schema v3: every site row starts with [scope, header, line, column, flags]."""

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

    def head(self, scope: int, flags: int) -> list[int]:
        """The site head every site row starts with: scope, header, line, column, statement flags."""
        return [scope, -1, self.next_line(), 0, flags]

    def ref(self, name: str, chain: str = "", up: str = "expr", scope: int = -1, flags: int = 0) -> FileFacts:
        self.record["refs"].append([*self.head(scope, flags), name, chain, up])
        return self

    def string(self, value: str, up: str = "expr", scope: int = -1, flags: int = 0) -> FileFacts:
        self.record["strs"].append([*self.head(scope, flags), value, "", up])
        return self

    def call(self, callee: str, args: list, kws: list, scope: int = -1, flags: int = 0) -> FileFacts:
        self.record["calls"].append([*self.head(scope, flags), callee, args, kws])
        return self


def build(files: list[FileFacts], config: CoreConfig | None = None) -> Graph:
    store = FactStore()
    for facts in files:
        store.records[facts.record["path"]] = facts.record
    return CoreBuild(config or CoreConfig(), store).graph()


def edges_of(graph: Graph, kind: str, path: str) -> list[Edge]:
    return [edge for edge in graph.edges if edge.kind == kind and edge.site.path == path]
