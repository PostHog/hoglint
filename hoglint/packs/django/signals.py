"""`signal` edges: a receiver function to the sender it listens to.

Sources: `@receiver(<signal>, sender=X)` (and configured wrappers of it) on a def, and
`<signal>.connect(fn, sender=X)`. A string sender resolves through the model label table.
No sender, or a sender that is not a class, gives an unresolved edge: unknown is never clean.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from hoglint.core.build import CoreBuild
from hoglint.core.harness import each_item, failure_edge
from hoglint.core.records import sequence_items, str_literal, value_text
from hoglint.core.sites import FileScope
from hoglint.core.symbols import Hit
from hoglint.model import Edge, EntityId, Resolution, Site
from hoglint.packs.django.config import DjangoPackConfig
from hoglint.packs.django.models import ModelRegistry

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


class SignalProducer:
    def __init__(self, build: CoreBuild, registry: ModelRegistry, config: DjangoPackConfig) -> None:
        self.build = build
        self.registry = registry
        self.receivers = frozenset(config.receiver_decorators)
        self.signal_bases = config.signal_bases
        self.signal_cache: dict[EntityId, bool] = {}

    def lookup(self, scope: FileScope, chain: str, at_scope: int, line: int) -> Hit | None:
        found = self.build.context.value_lookup(scope, chain, at_scope, line)
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
        table = self.build.tables.get(origin.module)
        value = table.values.get(origin.symbol) if table is not None else None
        if not isinstance(value, dict) or not isinstance(value.get("call"), str):
            return False
        cls = self.build.resolver.resolve_class(origin.module, value["call"])
        return cls is not None and any(self.build.classes.is_subclass(cls, base) for base in self.signal_bases)

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
            return Sender(label, (ref,), self.build.first_party.resolution(ref.module))
        if not isinstance(value, str):
            return Sender(value_text(value), (), Resolution.UNRESOLVED, "variable-sender")
        hit = self.lookup(scope, value, at_scope, line)
        if hit is None or hit.kind != "class":
            return Sender(value, (), Resolution.UNRESOLVED, "variable-sender")
        return Sender(value, (hit.origin,), self.build.first_party.resolution(hit.origin.module))

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
        for row in scope.record.get("refs", ()):
            _, header, line, column, root, chain = row[:6]
            if len(row) > 7 or header != idx or not PLAIN_ATTRS.fullmatch(chain):
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

    def decorator_edges(self, scope: FileScope) -> list[Edge]:
        out: list[Edge] = []
        for idx, d in enumerate(scope.defs):
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

    def connect_edges(self, scope: FileScope) -> list[Edge]:
        """`<signal>.connect(fn, sender=X)`; without argument facts the edge stays unresolved."""
        out: list[Edge] = []
        with_args: set[tuple[int, str]] = set()
        for at_scope, _, line, column, callee, args, kws in scope.record.get("calls", ()):
            signal = self.connected_signal(scope, callee, at_scope, line)
            if signal is None:
                continue
            with_args.add((at_scope, callee))
            receiver = self.receiver_name(scope, at_scope, args[0], line) if args else UNKNOWN
            site = scope.site(at_scope, line, column)
            sender = self.sender(scope, kws, at_scope, line)
            flags = scope.flags_at(line, column)
            out.append(self.edge(scope, site, flags, sender, str(signal.origin), receiver, "connect"))
        for at_scope, _, callee, count, line, column in scope.record.get("callees", ()):
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

    def file_edges(self, scope: FileScope) -> list[Edge]:
        return self.decorator_edges(scope) + self.connect_edges(scope)

    @staticmethod
    def failure(scope: FileScope, error: Exception) -> Edge:
        return failure_edge("signal", SIGNALS_PRODUCER, scope.site(-1, 0, 0), scope.base_contexts, error)

    def produce(self, files: list[FileScope]) -> list[Edge]:
        return each_item(files, self.file_edges, self.failure)
