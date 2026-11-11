"""The native reader's reads, answered again: what checks a stored pack item against this run.

Each kind here is a kind the reader notes (hoglint/native/build.py, hoglint/core/reader.py), and
each answer is built exactly as its note builds it, so equal answers have equal digests. A kind
the reader notes with a key it cannot be asked again by (an edge query, `uses-to`) has no answer
here, so a stored item that read it always runs again.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from hoglint.core.memo import parts_of
from hoglint.core.reader import BuildReader
from hoglint.native.build import NativeBuild


class ReaderAnswers:
    """Registers an answer for each kind the native reader notes."""

    def __init__(self, reader: BuildReader, build: NativeBuild) -> None:
        self.reader = reader
        self.build = build
        self.link = build.link
        self.paths = build.paths

    def table(self, module: str) -> object:
        table = self.build.tables.get(module)
        if table is None:
            return None
        return (table.path, table.is_package, table.names, table.star_imports, table.lazy)

    def located(self, key: str) -> object:
        found = self.link.located(*parts_of(key))
        return None if found is None else (self.paths[found[0]], found[1])

    def value_lookup(self, key: str) -> object:
        path, value, at_scope, line = parts_of(key)
        scope: Any = self.build.scope_of[path]
        return self.link.value_lookup(scope.scope, value, at_scope, line)

    def chain_lookup(self, key: str) -> object:
        path, chain, at_scope, line = parts_of(key)
        scope: Any = self.build.scope_of[path]
        found, rest = self.link.chain_lookup(scope.scope, chain, at_scope, line)
        return (found, rest)

    @staticmethod
    def index_answer(values: dict[str, str], key: str) -> object:
        """A module index map's answer as `TracedDict` notes it: a value, or every entry for "*"."""
        if key == "*":
            return sorted(dict.items(values))
        return dict.get(values, key)

    def listing(self, key: str) -> object:
        if key == "files":
            return [(scope.path, scope.module) for scope in self.reader.scopes]
        return self.reader.record_paths

    def importing(self, key: str) -> object:
        found = self.reader.queries.importing(parts_of(key)[0])
        return [(scope.path, list(row)) for scope, row in found]

    def register(self) -> None:
        index: Any = self.build.index
        link = self.link
        records = self.build.store.records
        answers: dict[str, Callable[[str], object]] = {
            "table": self.table,
            "module-path": lambda module: self.index_answer(index.path_of, module),
            "path-module": lambda path: self.index_answer(index.module_of, path),
            "module-exists": lambda module: dict.__contains__(index.path_of, module) or module in index.dirs,
            "module-package": lambda module: index.package.get(module, module in index.dirs),
            "module-external": index.is_external,
            "resolve-name": lambda key: link.resolve_name(*parts_of(key)),
            "resolve-dotted": lambda key: tuple(link.resolve_dotted(key)),
            "resolve-value": lambda key: link.resolve_value(*parts_of(key)),
            "resolve-class": lambda key: link.resolve_class(*parts_of(key)),
            "located": self.located,
            "bases": lambda key: link.bases(*parts_of(key)),
            "mro": lambda key: link.mro(*parts_of(key)),
            "is-subclass": lambda key: link.is_subclass(*parts_of(key)),
            "value-lookup": self.value_lookup,
            "chain-lookup": self.chain_lookup,
            "record": lambda path: path in records,
            "listing": self.listing,
            "importing": self.importing,
            "subclasses": lambda qualified: [str(ref) for ref in self.reader.queries.subclasses(qualified)],
        }
        for kind, answer in answers.items():
            self.reader.tracer.answer_with(kind, answer)
