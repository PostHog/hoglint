"""`signal` edges: a receiver function to the sender it listens to.

Sources: `@receiver(<signal>, sender=X)` (and configured wrappers of it) on a def, and
`<signal>.connect(fn, sender=X)`. A string sender resolves through the model label table.
No sender, or a sender that is not a class, gives an unresolved edge: unknown is never clean.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from hoglint.model import LANGUAGE_PYTHON, Edge, EntityId, Resolution, Site
from hoglint.packs.django.config import DjangoPackConfig
from hoglint.packs.django.models import ModelRegistry
from hoglint.packs.django.sites import PathKinds, failure_edge, file_site, make_site
from hoglint.packs.django.symbols import FileNames, Symbol, SymbolKind, SymbolResolver
from hoglint.spike.core import ClassRef, Core, const_of, module_of_path, qualname

SIGNAL_PRODUCER = "django.signals@1"
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


def sv_text(value: object) -> str:
    if isinstance(value, str):
        return value
    found = const_of(value)
    if isinstance(found, str):
        return found
    if isinstance(value, dict) and "o" in value:
        return f"<{value['o']}>"
    return UNKNOWN


def sequence_items(value: object) -> list[object] | None:
    """Elements of a literal list or tuple V value, else None."""
    if not isinstance(value, dict):
        return None
    items = value.get("list", value.get("tuple"))
    return items if isinstance(items, list) else None


class SignalProducer:
    def __init__(self, core: Core, symbols: SymbolResolver, registry: ModelRegistry, config: DjangoPackConfig) -> None:
        self.core = core
        self.symbols = symbols
        self.registry = registry
        self.receivers = frozenset(config.receiver_decorators)
        self.signal_bases = config.signal_bases
        self.signal_cache: dict[str, bool] = {}

    def is_signal(self, symbol: Symbol | None) -> bool:
        """A module-level value built by calling a Signal subclass."""
        if symbol is None or symbol.kind != SymbolKind.VALUE:
            return False
        key = symbol.qualified
        if key in self.signal_cache:
            return self.signal_cache[key]
        self.signal_cache[key] = False
        value = self.symbols.value_of(symbol)
        result = False
        if isinstance(value, dict) and isinstance(value.get("call"), str):
            cls = self.symbols.resolve_chain(symbol.module, value["call"])
            if cls is not None and cls.kind == SymbolKind.CLASS:
                ref = ClassRef(cls.module, cls.name)
                result = any(self.core.classes.is_subclass(ref, base) for base in self.signal_bases)
        self.signal_cache[key] = result
        return result

    def signal_name(self, names: FileNames, value: object) -> str:
        if not isinstance(value, str):
            return UNKNOWN
        found = names.resolve(value)
        if self.is_signal(found) and found is not None:
            return found.qualified
        return value

    def sender(self, names: FileNames, kws: list) -> Sender:
        value = next((v for k, v in kws if k == "sender"), None)
        if value is None:
            if any(k is None for k, _ in kws):
                return Sender("**", (), Resolution.UNRESOLVED, "sender-kwargs")
            return Sender("", (), Resolution.UNRESOLVED, "no-sender")
        label = const_of(value)
        if isinstance(label, str):
            app = self.registry.apps.label_for_module(names.module) or ""
            ref = self.registry.resolve_label(label, app, None)
            if ref is None:
                return Sender(label, (), Resolution.UNRESOLVED, "unknown-label")
            return Sender(label, (EntityId(LANGUAGE_PYTHON, ref.module, ref.name),), self.resolution(ref.module))
        if not isinstance(value, str):
            return Sender(sv_text(value), (), Resolution.UNRESOLVED, "variable-sender")
        found = names.resolve(value)
        if found is None or found.kind != SymbolKind.CLASS:
            return Sender(value, (), Resolution.UNRESOLVED, "variable-sender")
        return Sender(value, (found.entity,), self.resolution(found.module))

    def resolution(self, module: str) -> Resolution:
        return Resolution.RESOLVED if self.registry.is_first_party(module) else Resolution.EXTERNAL

    def edge(self, site: Site, path: str, sender: Sender, signal: str, receiver: str, how: str) -> Edge:
        attrs = [("signal", signal), ("receiver", receiver), ("how", how)]
        if sender.reason:
            attrs.append(("reason", sender.reason))
        return Edge(
            kind="signal",
            site=site,
            surface=sender.surface,
            targets=sender.targets,
            resolution=sender.resolution,
            contexts=PathKinds.contexts(path),
            producer=SIGNAL_PRODUCER,
            shape=how,
            attrs=tuple(attrs),
        )

    def header_signals(self, names: FileNames, idx: int) -> list[str]:
        """Signals named anywhere in the header of def `idx`, in source order."""
        found: list[tuple[int, int, str]] = []
        for _, header, root, attrs, count, line, column in names.record.get("uses", ()):
            if header != idx or not PLAIN_ATTRS.fullmatch(attrs):
                continue
            symbol = names.resolve(root + attrs)
            if self.is_signal(symbol) and symbol is not None:
                found.extend([(line, column, symbol.qualified)] * count)
        for row in names.record.get("refs", ()):
            _, header, line, column, root, chain = row[:6]
            if len(row) > 7 or header != idx or not PLAIN_ATTRS.fullmatch(chain):
                continue
            symbol = names.resolve(root + chain)
            if self.is_signal(symbol) and symbol is not None:
                found.append((line, column, symbol.qualified))
        return [qualified for _, _, qualified in sorted(found)]

    def decorator_signals(self, names: FileNames, idx: int, receivers: list[dict]) -> list[list[str]]:
        """The signals of each receiver decorator on one def.

        `@receiver([a, b])` arrives as an opaque list argument. Its names still show up as uses
        in the def header, so a lone opaque list takes the header signals no other decorator names.
        """
        out: list[list[str]] = []
        opaque: list[int] = []
        for position, dec in enumerate(receivers):
            args = dec.get("args", [])
            first = args[0] if args else None
            items = sequence_items(first)
            if items is not None:
                out.append([self.signal_name(names, item) for item in items])
            elif isinstance(first, dict) and first.get("o") in OPAQUE_SEQUENCES:
                out.append([UNKNOWN])
                opaque.append(position)
            else:
                out.append([self.signal_name(names, first)] if args else [UNKNOWN])
        if len(opaque) != 1:
            return out
        named = {signal for position, signals in enumerate(out) if position not in opaque for signal in signals}
        listed = [signal for signal in self.header_signals(names, idx) if signal not in named]
        if listed:
            out[opaque[0]] = listed
        return out

    def decorator_edges(self, path: str, names: FileNames) -> list[Edge]:
        out: list[Edge] = []
        module = names.module
        defs = names.record.get("defs", [])
        for idx, d in enumerate(defs):
            receivers = []
            for dec in d.get("dec", ()):
                if not isinstance(dec, dict) or not isinstance(dec.get("call"), str):
                    continue
                found = names.resolve(dec["call"])
                if found is not None and found.qualified in self.receivers:
                    receivers.append(dec)
            if not receivers:
                continue
            site = Site(path, module, qualname(defs, idx), d["l"], d["c"])
            receiver = f"{module}.{qualname(defs, idx)}"
            for dec, signals in zip(receivers, self.decorator_signals(names, idx, receivers), strict=True):
                sender = self.sender(names, dec.get("kw", []))
                out.extend(self.edge(site, path, sender, signal, receiver, "decorator") for signal in signals)
        return out

    def receiver_name(self, names: FileNames, scope: int, value: object) -> str:
        if not isinstance(value, str):
            return UNKNOWN
        defs = names.record.get("defs", [])
        local = [i for i, d in enumerate(defs) if d["n"] == value and d["s"] == scope and d["k"] != "class"]
        if local:
            return f"{names.module}.{qualname(defs, local[-1])}"
        found = names.resolve(value)
        if found is not None and found.kind == SymbolKind.FUNCTION:
            return found.qualified
        return UNKNOWN

    def connect_edges(self, path: str, names: FileNames) -> list[Edge]:
        """`<signal>.connect(fn, sender=X)`; without argument facts the edge stays unresolved."""
        out: list[Edge] = []
        record, module = names.record, names.module
        with_args: set[tuple[int, str]] = set()
        for scope, _, line, column, callee, args, kws in record.get("calls", ()):
            receiver_chain, _, attr = callee.rpartition(".")
            if attr != CONNECT or not receiver_chain or receiver_chain.startswith(UNKNOWN):
                continue
            signal = names.resolve(receiver_chain)
            if not self.is_signal(signal) or signal is None:
                continue
            with_args.add((scope, callee))
            receiver = self.receiver_name(names, scope, args[0]) if args else UNKNOWN
            site = make_site(record, module, scope, line, column)
            out.append(self.edge(site, path, self.sender(names, kws), signal.qualified, receiver, "connect"))
        for scope, _, callee, count, line, column in record.get("callees", ()):
            receiver_chain, _, attr = callee.rpartition(".")
            if attr != CONNECT or not receiver_chain or (scope, callee) in with_args:
                continue
            signal = names.resolve(receiver_chain)
            if not self.is_signal(signal) or signal is None:
                continue
            site = make_site(record, module, scope, line, column)
            sender = Sender("", (), Resolution.UNRESOLVED, "no-call-facts")
            out.extend(self.edge(site, path, sender, signal.qualified, UNKNOWN, "connect") for _ in range(count))
        return out

    def file_edges(self, path: str, record: dict) -> list[Edge]:
        module, is_package = module_of_path(path)
        names = FileNames(self.symbols, record, module, is_package)
        return self.decorator_edges(path, names) + self.connect_edges(path, names)

    def produce(self, files: list[tuple[str, dict]]) -> list[Edge]:
        out: list[Edge] = []
        for path, record in files:
            try:
                out.extend(self.file_edges(path, record))
            except Exception as error:  # one unreadable file is unknown, the other files still count
                out.append(failure_edge("signal", SIGNAL_PRODUCER, file_site(path), error))
        return out
