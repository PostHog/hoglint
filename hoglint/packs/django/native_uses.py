"""`django.uses` on the native engine: the link judges every use and refines its edge (D49), with
no Python object per use. The aggregate edges and the failure edges stay here: there are few.

`UseProducer` in uses.py stays the meaning. The Python engine runs it, a test holds the two
equal, and it words the failure of a file the link could not read, so both engines write the
same failure edge.
"""

from __future__ import annotations

from typing import Any

from hoglint.core.sites import FileScope
from hoglint.model import Edge
from hoglint.native.graph import NativeSink
from hoglint.packs.django.uses import USES_PRODUCER, UseProducer, Uses
from hoglint.plugins import Refinements


def file_id(scope: FileScope) -> int:
    record: Any = scope.record
    return record.file


class NativeUses:
    """Runs `django.uses` in the link, into a native sink."""

    def __init__(self, producer: UseProducer, sink: NativeSink) -> None:
        self.producer = producer
        self.sink = sink
        self.uses: Uses | None = None

    def produce(self, files: list[FileScope], kinds: frozenset[str]) -> list[Edge]:
        """The producer's edges by file, as `UseProducer.produce` makes them. Its refinements go
        into the sink last, once nothing else can fail."""
        models = self.producer.registry.models
        aggregates = {scope.path: found for scope, found in self.producer.reader.uses_to(files, models)}
        link = self.sink.graph.link
        staged = link.django_uses([file_id(scope) for scope in files], [(m.module, m.symbol) for m in models])
        failed = set(staged.failed())
        out: list[Edge] = []
        for scope in files:
            if file_id(scope) in failed:
                out.append(self.producer.failure(scope, self.error_of(scope)))
                continue
            out.extend(self.producer.aggregate_edges(scope, aggregates.get(scope.path, [])))
        self.sink.refine_staged(USES_PRODUCER, kinds, staged)
        return out

    def error_of(self, scope: FileScope) -> Exception:
        """What the Python producer raises on a file the link could not read."""
        if self.uses is None:
            self.uses = self.producer.uses()
        try:
            self.producer.shaped(scope, self.uses, self.uses.by_file.get(scope.path, []), Refinements())
        except Exception as error:  # noqa: BLE001 - the Python producer's own error is the failure's text
            return error
        raise RuntimeError(f"the link could not read the model uses of {scope.path}, the Python producer could")
