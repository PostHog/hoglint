"""`signal` edges: a receiver function to the sender it listens to.

Sources: `@receiver(<signal>, sender=X)` (and configured wrappers of it) on a def, and
`<signal>.connect(fn, sender=X)`. A string sender resolves through the model label table.
No sender, or a sender that is not a class, gives an unresolved edge: unknown is never clean.

Candidates come from queries: decorators by their `decorate` edge (by origin) or by a spelled
receiver name in the file, and `connect` calls by spelling. Each one then resolves here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from hoglint.core.harness import each_item, failure_edge
from hoglint.core.records import sequence_items, str_literal, value_text
from hoglint.core.sites import FileScope
from hoglint.core.symbols import Hit
from hoglint.model import Edge, EntityId, Resolution, Site
from hoglint.native.queries import Words, entities_named
from hoglint.packs.django.config import DjangoPackConfig
from hoglint.packs.django.models import ModelRegistry
from hoglint.plugins import PackReader

SIGNALS_PRODUCER = "django.signals@1"
CONNECT = "connect"
UNKNOWN = "?"
OPAQUE_SEQUENCES = frozenset({"List", "Tuple"})
PLAIN_ATTRS = re.compile(r"(\.\w+)*")


@dataclass(frozen=True)
class Sender:
    surface: str
    targets: tuple[EntityId, ...]
    resolution: Resolution
    reason: str = ""


@dataclass(frozen=False)
class FileCandidates:
    """One file's candidates: defs with a possible receiver decorator, and `connect` call rows."""

    defs: set[tuple[int, int]] = field(default_factory=set)
    calls: list[list] = field(default_factory=list)
    callees: list[list] = field(default_factory=list)


class SignalProducer:
    def __init__(self, reader: PackReader, registry: ModelRegistry, config: DjangoPackConfig) -> None:
        self.reader = reader
        self.registry = registry
        self.receivers = frozenset(config.receiver_decorators)
        self.signal_bases = config.signal_bases
        self.signal_cache: dict[EntityId, bool] = {}

    def lookup(self, scope: FileScope, chain: str, at_scope: int, line: int) -> Hit | None:
        found = self.reader.context.value_lookup(scope, chain, at_scope, line)
        return found.hit if found is not None else None

    def is_signal(self, hit: Hit | None) -> bool:
        """A module-level value built by calling a Signal subclass."""
        if hit is None or hit.kind != "variable":
            return False
        origin = hit.origin
        if origin not in self.signal_cache:
            self.signal_cache[origin] = self.built_by_signal_class(origin)
        return self.signal_cache[origin]

    def built_by_signal_class(self, origin: EntityId) -> bool:
        table = self.reader.tables.get(origin.module)
        value = table.values.get(origin.symbol) if table is not None else None
        if not isinstance(value, dict) or not isinstance(value.get("call"), str):
            return False
        cls = self.reader.resolver.resolve_class(origin.module, value["call"])
        return cls is not None and any(self.reader.classes.is_subclass(cls, base) for base in self.signal_bases)

    def signal_name(self, scope: FileScope, value: object, at_scope: int, line: int) -> str:
        if not isinstance(value, str):
            return UNKNOWN
        hit = self.lookup(scope, value, at_scope, line)
        if hit is not None and self.is_signal(hit):
            return str(hit.origin)
        return value

    def sender(self, scope: FileScope, kws: list, at_scope: int, line: int) -> Sender:
        value = next((v for k, v in kws if k == "sender"), None)
        if value is None:
            if any(k is None for k, _ in kws):
                return Sender("**", (), Resolution.UNRESOLVED, "sender-kwargs")
            return Sender("", (), Resolution.UNRESOLVED, "no-sender")
        label = str_literal(value)
        if label is not None:
            app = self.registry.apps.label_for_module(scope.module) or ""
            ref = self.registry.resolve_label(label, app, None)
            if ref is None:
                return Sender(label, (), Resolution.UNRESOLVED, "unknown-label")
            return Sender(label, (ref,), self.reader.first_party.resolution(ref.module))
        if not isinstance(value, str):
            return Sender(value_text(value), (), Resolution.UNRESOLVED, "variable-sender")
        hit = self.lookup(scope, value, at_scope, line)
        if hit is None or hit.kind != "class":
            return Sender(value, (), Resolution.UNRESOLVED, "variable-sender")
        return Sender(value, (hit.origin,), self.reader.first_party.resolution(hit.origin.module))

    def edge(
        self, scope: FileScope, site: Site, flags: int, sender: Sender, signal: str, receiver: str, how: str
    ) -> Edge:
        attrs = [("signal", signal), ("receiver", receiver), ("how", how)]
        if sender.reason:
            attrs.append(("reason", sender.reason))
        return Edge(
            kind="signal",
            site=site,
            surface=sender.surface,
            targets=sender.targets,
            resolution=sender.resolution,
            contexts=scope.contexts(flags),
            producer=SIGNALS_PRODUCER,
            shape=how,
            attrs=tuple(attrs),
        )

    def header_signals(self, scope: FileScope, idx: int) -> list[str]:
        """Signals named anywhere in the header of def `idx`, in source order."""
        enclosing = scope.defs[idx]["s"]
        found: list[tuple[int, int, str]] = []
        for _, header, root, attrs, count, line, column in scope.record.get("uses", ()):
            if header != idx or not PLAIN_ATTRS.fullmatch(attrs):
                continue
            hit = self.lookup(scope, root + attrs, enclosing, line)
            if hit is not None and self.is_signal(hit):
                found.extend([(line, column, str(hit.origin))] * count)
        for _, header, line, column, _, root, chain, _ in scope.record.get("refs", ()):
            if header != idx or not PLAIN_ATTRS.fullmatch(chain):
                continue
            hit = self.lookup(scope, root + chain, enclosing, line)
            if hit is not None and self.is_signal(hit):
                found.append((line, column, str(hit.origin)))
        return [qualified for _, _, qualified in sorted(found)]

    def decorator_signals(self, scope: FileScope, idx: int, receivers: list[dict]) -> list[list[str]]:
        """The signals of each receiver decorator on one def.

        `@receiver([a, b])` arrives as an opaque list argument. Its names still show up as uses
        in the def header, so a lone opaque list takes the header signals no other decorator names.
        """
        d = scope.defs[idx]
        out: list[list[str]] = []
        opaque: list[int] = []
        for position, dec in enumerate(receivers):
            args = dec.get("args", [])
            first = args[0] if args else None
            items = sequence_items(first)
            if items is not None:
                out.append([self.signal_name(scope, item, d["s"], d["l"]) for item in items])
            elif isinstance(first, dict) and first.get("o") in OPAQUE_SEQUENCES:
                out.append([UNKNOWN])
                opaque.append(position)
            else:
                out.append([self.signal_name(scope, first, d["s"], d["l"])] if args else [UNKNOWN])
        if len(opaque) != 1:
            return out
        named = {signal for position, signals in enumerate(out) if position not in opaque for signal in signals}
        listed = [signal for signal in self.header_signals(scope, idx) if signal not in named]
        if listed:
            out[opaque[0]] = listed
        return out

    def decorator_edges(self, scope: FileScope, positions: set[tuple[int, int]]) -> list[Edge]:
        out: list[Edge] = []
        for idx, d in enumerate(scope.defs):
            if (d["l"], d["c"]) not in positions:
                continue
            receivers = []
            for dec in d.get("dec", ()):
                if not isinstance(dec, dict) or not isinstance(dec.get("call"), str):
                    continue
                hit = self.lookup(scope, dec["call"], d["s"], d["l"])
                if hit is not None and str(hit.origin) in self.receivers:
                    receivers.append(dec)
            if not receivers:
                continue
            qualname = scope.qualnames[idx]
            site = Site(scope.path, scope.module, qualname, d["l"], d["c"])
            receiver = f"{scope.module}.{qualname}"
            flags = d.get("f", 0)
            for dec, signals in zip(receivers, self.decorator_signals(scope, idx, receivers), strict=True):
                sender = self.sender(scope, dec.get("kw", []), d["s"], d["l"])
                out.extend(self.edge(scope, site, flags, sender, signal, receiver, "decorator") for signal in signals)
        return out

    def receiver_name(self, scope: FileScope, at_scope: int, value: object, line: int) -> str:
        if not isinstance(value, str):
            return UNKNOWN
        local = [i for i, d in enumerate(scope.defs) if d["n"] == value and d["s"] == at_scope and d["k"] != "class"]
        if local:
            return f"{scope.module}.{scope.qualnames[local[-1]]}"
        hit = self.lookup(scope, value, at_scope, line)
        if hit is not None and hit.kind == "def":
            return str(hit.origin)
        return UNKNOWN

    def connected_signal(self, scope: FileScope, callee: str, at_scope: int, line: int) -> Hit | None:
        receiver_chain, _, attr = callee.rpartition(".")
        if attr != CONNECT or not receiver_chain or receiver_chain.startswith(UNKNOWN):
            return None
        hit = self.lookup(scope, receiver_chain, at_scope, line)
        return hit if self.is_signal(hit) else None

    def connect_edges(self, scope: FileScope, calls: list[list], callees: list[list]) -> list[Edge]:
        """`<signal>.connect(fn, sender=X)`; without argument facts the edge stays unresolved."""
        out: list[Edge] = []
        with_args: set[tuple[int, str]] = set()
        for at_scope, _, line, column, flags, callee, args, kws in calls:
            signal = self.connected_signal(scope, callee, at_scope, line)
            if signal is None:
                continue
            with_args.add((at_scope, callee))
            receiver = self.receiver_name(scope, at_scope, args[0], line) if args else UNKNOWN
            site = scope.site(at_scope, line, column)
            sender = self.sender(scope, kws, at_scope, line)
            out.append(self.edge(scope, site, flags, sender, str(signal.origin), receiver, "connect"))
        for at_scope, _, callee, count, line, column in callees:
            if (at_scope, callee) in with_args:
                continue
            signal = self.connected_signal(scope, callee, at_scope, line)
            if signal is None:
                continue
            site = scope.site(at_scope, line, column)
            sender = Sender("", (), Resolution.UNRESOLVED, "no-call-facts")
            edge = self.edge(scope, site, 0, sender, str(signal.origin), UNKNOWN, "connect")
            out.extend([edge] * count)
        return out

    def candidates(self, files: list[FileScope]) -> dict[str, FileCandidates]:
        """Defs whose decorator may resolve to a receiver: its `decorate` edge targets one, or the
        file spells a receiver's name in a decorator or an import, where a target can be unknown.
        And every `connect` call: its receiver decides."""
        out: dict[str, FileCandidates] = {}
        paths = {scope.path for scope in files}
        targets = [entity for name in sorted(self.receivers) for entity in entities_named(name)]
        for edge in self.reader.edges_to("decorate", targets):
            if edge.site.path in paths:
                out.setdefault(edge.site.path, FileCandidates()).defs.add((edge.site.line, edge.site.column))
        spelled = Words.ending(name.rpartition(".")[2] for name in self.receivers)
        for scope in files:
            if self.reader.may_mention(scope, ("defs", "imports"), spelled):
                decorated = {(d["l"], d["c"]) for d in scope.defs if d.get("dec")}
                out.setdefault(scope.path, FileCandidates()).defs.update(decorated)
        for scope, rows in self.reader.calls_named(files, "calls", (CONNECT,)):
            out.setdefault(scope.path, FileCandidates()).calls = rows
        for scope, rows in self.reader.calls_named(files, "callees", (CONNECT,)):
            out.setdefault(scope.path, FileCandidates()).callees = rows
        return out

    @staticmethod
    def failure(scope: FileScope, error: Exception) -> Edge:
        return failure_edge("signal", SIGNALS_PRODUCER, scope.site(-1, 0, 0), scope.base_contexts, error)

    def produce(self, files: list[FileScope]) -> list[Edge]:
        candidates = self.candidates(files)

        def file_edges(scope: FileScope) -> list[Edge]:
            found = candidates[scope.path]
            return self.decorator_edges(scope, found.defs) + self.connect_edges(scope, found.calls, found.callees)

        return each_item([scope for scope in files if scope.path in candidates], file_edges, self.failure)
