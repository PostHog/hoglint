"""`signal` edges: a receiver function to the sender it listens to.

Sources: `@receiver(<signal>, sender=X)` (and configured wrappers of it) on a def, and
`<signal>.connect(fn, sender=X)`. A string sender resolves through the model label table.
A receiver with no sender listens to whatever fires the signal, so its edge goes to the signal
itself: the module that defines a signal is what fires it. A sender that is not a class gives an
unresolved edge, and so does a signal that does not resolve: unknown is never clean.

A `connect` in the body of a hook (a module-level function that hands one of its parameters on
to it) has attr `hook`: the receivers are the hook's callers', placed by their `installs` edges.

Candidates come from queries: decorators by their `decorate` edge (by origin) or by a spelled
receiver name in the file, and `connect` calls by spelling. Each one then resolves here.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from hoglint.core.harness import FileItems, failure_edge
from hoglint.core.memo import NOTHING, ItemOutput, PerFileRead, TracedMemo
from hoglint.core.records import sequence_items, str_literal, value_text
from hoglint.core.sites import FileScope
from hoglint.core.symbols import Hit
from hoglint.model import NO_LITERAL, Edge, EntityId, Resolution, Site
from hoglint.native.queries import Words, entities_named
from hoglint.packs.django.config import DjangoPackConfig
from hoglint.packs.django.models import ModelRegistry
from hoglint.plugins import PackReader

SIGNALS_PRODUCER = "django.signals@1"
CONNECT = "connect"
UNKNOWN = "?"
OPAQUE_SEQUENCES = frozenset({"List", "Tuple"})
PLAIN_ATTRS = re.compile(r"(\.\w+)*")
# A `.connect` on what names no known signal may still be a signal's in a file that imports one of
# these: Django's dispatch package or a signals module. `EXPRESSION` names a receiver object that
# is no name (`getattr(signals, name).connect`).
SIGNAL_PARTS = frozenset({"signals", "dispatch"})
EXPRESSION = "<expression>"


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


def handing_hook(scope: FileScope, at_scope: int, args: list, kws: list) -> str:
    """The module-level function a `connect` call hands one of its parameters on to as the
    receiver, qualified; "" for any other call. A method is left out, and so is a function named
    `connect`: every `.connect` call would name it."""
    if at_scope < 0:
        return ""
    d = scope.defs[at_scope]
    if d["k"] == "class" or d["s"] != -1 or d["n"] == CONNECT:
        return ""
    handed = args[0] if args else next((value for key, value in kws if key == "receiver"), None)
    if not isinstance(handed, str) or handed not in {param[0] for param in d.get("params", ())}:
        return ""
    return f"{scope.module}.{scope.qualnames[at_scope]}"


# The reasons a sender is no literal name: hoglint does not follow the value.
NOT_LITERAL_SENDERS = frozenset({"sender-kwargs", "no-sender", "variable-sender"})


class SignalValues:
    """Which names are signals: module-level values built by calling a Signal subclass."""

    def __init__(self, reader: PackReader, signal_bases: tuple[str, ...]) -> None:
        self.reader = reader
        self.signal_bases = signal_bases
        self.signal_cache: TracedMemo[EntityId, bool] = TracedMemo(reader.tracer)

    def lookup(self, scope: FileScope, chain: str, at_scope: int, line: int) -> Hit | None:
        found = self.reader.context.value_lookup(scope, chain, at_scope, line)
        return found.hit if found is not None else None

    def is_signal(self, hit: Hit | None) -> bool:
        """A module-level value built by calling a Signal subclass."""
        if hit is None or hit.kind != "variable":
            return False
        origin = hit.origin
        return self.signal_cache.get(origin, lambda: self.built_by_signal_class(origin))

    def built_by_signal_class(self, origin: EntityId) -> bool:
        table = self.reader.tables.get(origin.module)
        value = table.values.get(origin.symbol) if table is not None else None
        if not isinstance(value, dict) or not isinstance(value.get("call"), str):
            return False
        cls = self.reader.resolver.resolve_class(origin.module, value["call"])
        return cls is not None and any(self.reader.classes.is_subclass(cls, base) for base in self.signal_bases)

    def connected_signal(self, scope: FileScope, callee: str, at_scope: int, line: int) -> Hit | None:
        """The signal a `<signal>.connect` callee names, or None."""
        receiver_chain, _, attr = callee.rpartition(".")
        if attr != CONNECT or not receiver_chain or receiver_chain.startswith(UNKNOWN):
            return None
        hit = self.lookup(scope, receiver_chain, at_scope, line)
        return hit if self.is_signal(hit) else None


class SignalProducer(SignalValues):
    def __init__(self, reader: PackReader, registry: ModelRegistry, config: DjangoPackConfig) -> None:
        super().__init__(reader, config.signal_bases)
        self.registry = registry
        self.receivers = frozenset(config.receiver_decorators)
        self.receiver_words = Words.ending(name.rpartition(".")[2] for name in self.receivers)

    def signal_name(self, scope: FileScope, value: object, at_scope: int, line: int) -> tuple[str, Hit | None]:
        """A signal argument's name (the signal's origin when it is one), and what it resolves to."""
        if not isinstance(value, str):
            return UNKNOWN, None
        hit = self.lookup(scope, value, at_scope, line)
        if hit is not None and self.is_signal(hit):
            return str(hit.origin), hit
        return value, hit

    def at_signal(self, hit: Hit | None) -> Sender:
        """The sender of a receiver with no sender: the signal itself, which its defining module
        fires. A signal that does not resolve leaves the edge unresolved."""
        if hit is None:
            return Sender("", (), Resolution.UNRESOLVED, "no-sender")
        origin = hit.origin
        return Sender(str(origin), (origin,), self.reader.first_party.resolution(origin.module), "no-sender")

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
        self,
        scope: FileScope,
        site: Site,
        flags: int,
        sender: Sender,
        signal: str,
        receiver: str,
        how: str,
        hook: str = "",
    ) -> Edge:
        attrs = [("signal", signal), ("receiver", receiver), ("how", how)]
        if sender.reason:
            attrs.append(("reason", sender.reason))
        if sender.resolution is Resolution.UNRESOLVED and sender.reason in NOT_LITERAL_SENDERS:
            attrs.append(NO_LITERAL)
        if hook:
            attrs.append(("hook", hook))
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

    def header_signals(self, scope: FileScope, idx: int) -> list[tuple[str, Hit | None]]:
        """Signals named anywhere in the header of def `idx`, in source order."""
        enclosing = scope.defs[idx]["s"]
        found: list[tuple[int, int, str, Hit]] = []
        for _, header, root, attrs, count, line, column in scope.record.get("uses", ()):
            if header != idx or not PLAIN_ATTRS.fullmatch(attrs):
                continue
            hit = self.lookup(scope, root + attrs, enclosing, line)
            if hit is not None and self.is_signal(hit):
                found.extend([(line, column, str(hit.origin), hit)] * count)
        for _, header, line, column, _, root, chain, _ in scope.record.get("refs", ()):
            if header != idx or not PLAIN_ATTRS.fullmatch(chain):
                continue
            hit = self.lookup(scope, root + chain, enclosing, line)
            if hit is not None and self.is_signal(hit):
                found.append((line, column, str(hit.origin), hit))
        found.sort(key=lambda item: item[:3])
        return [(qualified, hit) for _, _, qualified, hit in found]

    def decorator_signals(
        self, scope: FileScope, idx: int, receivers: list[dict]
    ) -> list[list[tuple[str, Hit | None]]]:
        """The signals of each receiver decorator on one def.

        `@receiver([a, b])` arrives as an opaque list argument. Its names still show up as uses
        in the def header, so a lone opaque list takes the header signals no other decorator names.
        """
        d = scope.defs[idx]
        out: list[list[tuple[str, Hit | None]]] = []
        opaque: list[int] = []
        for position, dec in enumerate(receivers):
            args = dec.get("args", [])
            first = args[0] if args else None
            items = sequence_items(first)
            if items is not None:
                out.append([self.signal_name(scope, item, d["s"], d["l"]) for item in items])
            elif isinstance(first, dict) and first.get("o") in OPAQUE_SEQUENCES:
                out.append([(UNKNOWN, None)])
                opaque.append(position)
            else:
                out.append([self.signal_name(scope, first, d["s"], d["l"])] if args else [(UNKNOWN, None)])
        if len(opaque) != 1:
            return out
        named = {name for position, signals in enumerate(out) if position not in opaque for name, _ in signals}
        listed = [signal for signal in self.header_signals(scope, idx) if signal[0] not in named]
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
                for signal, hit in signals:
                    placed = self.at_signal(hit) if sender.reason == "no-sender" else sender
                    out.append(self.edge(scope, site, flags, placed, signal, receiver, "decorator"))
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

    def imports_signals(self, scope: FileScope) -> bool:
        """The file imports a module or a name with a `signals` or `dispatch` part: Django's
        dispatch package, a signals module, or a name from one."""
        return any(SIGNAL_PARTS & set(name.split(".")) for name in self.reader.imported_modules(scope))

    def unknown_connect(self, scope: FileScope, callee: str, at_scope: int, line: int) -> str | None:
        """What a `.connect` call is on, when it may be a signal but names none the facts can tell:
        an expression (`getattr(signals, name)`, a subscript, a call's result) or a name that
        resolves to nothing. None for any other call."""
        receiver_chain, _, attr = callee.rpartition(".")
        if attr != CONNECT or not receiver_chain:
            return None
        if receiver_chain.startswith(UNKNOWN):
            return EXPRESSION
        if self.lookup(scope, receiver_chain, at_scope, line) is None:
            return receiver_chain
        return None

    def unknown_signal(self, scope: FileScope, site: Site, flags: int, connected: str, receiver: str) -> Edge:
        """A `connect` whose signal cannot be told is unresolved: the rules that read it go
        incomplete, as for a sender hoglint cannot place."""
        sender = Sender(connected, (), Resolution.UNRESOLVED, "unknown-signal")
        return self.edge(scope, site, flags, sender, UNKNOWN, receiver, "connect")

    def connect_edges(self, scope: FileScope, calls: list[list], callees: list[list]) -> list[Edge]:
        """`<signal>.connect(fn, sender=X)`; without argument facts the edge stays unresolved.

        In a file that imports signals, a `.connect` on what names no signal the facts can tell
        (`unknown_connect`) is an unresolved edge: it may be a signal's. Elsewhere it is taken for
        another library's `connect` (a database driver, a client), as before."""
        out: list[Edge] = []
        with_args: set[tuple[int, str]] = set()
        signals_imported = bool(calls or callees) and self.imports_signals(scope)
        for at_scope, _, line, column, flags, callee, args, kws in calls:
            signal = self.connected_signal(scope, callee, at_scope, line)
            if signal is None:
                connected = self.unknown_connect(scope, callee, at_scope, line) if signals_imported else None
                if connected is None:
                    continue
                with_args.add((at_scope, callee))
                receiver = self.receiver_name(scope, at_scope, args[0], line) if args else UNKNOWN
                site = scope.site(at_scope, line, column)
                out.append(self.unknown_signal(scope, site, flags, connected, receiver))
                continue
            with_args.add((at_scope, callee))
            receiver = self.receiver_name(scope, at_scope, args[0], line) if args else UNKNOWN
            site = scope.site(at_scope, line, column)
            sender = self.sender(scope, kws, at_scope, line)
            if sender.reason == "no-sender":
                sender = self.at_signal(signal)
            hook = handing_hook(scope, at_scope, args, kws)
            out.append(self.edge(scope, site, flags, sender, str(signal.origin), receiver, "connect", hook))
        for at_scope, _, callee, count, line, column in callees:
            if (at_scope, callee) in with_args:
                continue
            signal = self.connected_signal(scope, callee, at_scope, line)
            if signal is None:
                connected = self.unknown_connect(scope, callee, at_scope, line) if signals_imported else None
                if connected is not None:
                    site = scope.site(at_scope, line, column)
                    out.extend([self.unknown_signal(scope, site, 0, connected, UNKNOWN)] * count)
                continue
            site = scope.site(at_scope, line, column)
            sender = Sender("", (), Resolution.UNRESOLVED, "no-call-facts")
            edge = self.edge(scope, site, 0, sender, str(signal.origin), UNKNOWN, "connect")
            out.extend([edge] * count)
        return out

    def decorated(self, files: list[FileScope]) -> dict[str, set[tuple[int, int]]]:
        """The defs whose `decorate` edge targets a receiver, by file."""
        out: dict[str, set[tuple[int, int]]] = {}
        paths = {scope.path for scope in files}
        targets = [entity for name in sorted(self.receivers) for entity in entities_named(name)]
        for edge in self.reader.edges_to("decorate", targets):
            if edge.site.path in paths:
                out.setdefault(edge.site.path, set()).add((edge.site.line, edge.site.column))
        return out

    def candidates(
        self, scope: FileScope, decorated: set[tuple[int, int]], calls: list[list], callees: list[list]
    ) -> FileCandidates | None:
        """Defs whose decorator may resolve to a receiver: its `decorate` edge targets one, or the
        file spells a receiver's name in a decorator or an import, where a target can be unknown.
        And every `connect` call: its receiver decides. None for a file with no candidate."""
        found = FileCandidates(set(decorated), calls, callees)
        mentions = self.reader.may_mention(scope, ("defs", "imports"), self.receiver_words)
        if mentions:
            found.defs.update((d["l"], d["c"]) for d in scope.defs if d.get("dec"))
        if not (decorated or mentions or calls or callees):
            return None
        return found

    @staticmethod
    def failure(scope: FileScope, error: Exception) -> Edge:
        return failure_edge("signal", SIGNALS_PRODUCER, scope.site(-1, 0, 0), scope.base_contexts, error)

    def file_edges(self, scope: FileScope, found: FileCandidates) -> list[Edge]:
        return self.decorator_edges(scope, found.defs) + self.connect_edges(scope, found.calls, found.callees)


class SignalItems(FileItems):
    """`django.signals` as one item per file. Which defs a `decorate` edge marks is asked of the
    whole graph once, and each item reads its own file's part of it."""

    DECORATED = "django.signals.decorated"

    def __init__(self, producer: SignalProducer, files: list[FileScope]) -> None:
        super().__init__(producer.reader, files, producer.failure)
        self.producer = producer
        decorated = producer.decorated(files)
        self.decorated = PerFileRead(producer.reader.tracer, self.DECORATED, decorated, sorted, set())
        self.calls: dict[str, list[list]] = {}
        self.callees: dict[str, list[list]] = {}

    def prepare(self, keys: Sequence[str]) -> None:
        scopes = self.scopes(keys)
        self.calls = {scope.path: rows for scope, rows in self.reader.calls_named(scopes, "calls", (CONNECT,))}
        self.callees = {scope.path: rows for scope, rows in self.reader.calls_named(scopes, "callees", (CONNECT,))}

    def file_item(self, scope: FileScope) -> ItemOutput:
        decorated = self.decorated.read(scope.path)
        calls, callees = self.calls.get(scope.path, []), self.callees.get(scope.path, [])
        found = self.producer.candidates(scope, decorated, calls, callees)
        if found is None:
            return NOTHING
        return self.each_file(scope, lambda one: self.producer.file_edges(one, found))
