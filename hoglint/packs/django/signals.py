"""`signal` edges: a receiver function to the sender it listens to.

Sources: `@receiver(<signal>, sender=X)` (and configured wrappers of it) on a def, and
`<signal>.connect(fn, sender=X)`. A string sender resolves through the model label table.
No sender, or a sender that is not a class, gives an unresolved edge: unknown is never clean.
"""

from __future__ import annotations

from dataclasses import dataclass

from hoglint.model import LANGUAGE_PYTHON, Edge, EntityId, Resolution, Site
from hoglint.packs.django.config import DjangoPackConfig
from hoglint.packs.django.models import ModelRegistry
from hoglint.packs.django.sites import PathKinds, make_site
from hoglint.packs.django.symbols import FileNames, Symbol, SymbolKind, SymbolResolver
from hoglint.spike.core import ClassRef, Core, const_of, module_of_path, qualname

SIGNAL_PRODUCER = "django.signals@1"
CONNECT = "connect"
UNKNOWN = "?"


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

    def decorator_edges(self, path: str, names: FileNames) -> list[Edge]:
        out: list[Edge] = []
        module = names.module
        defs = names.record.get("defs", [])
        for idx, d in enumerate(defs):
            for dec in d.get("dec", ()):
                if not isinstance(dec, dict) or not isinstance(dec.get("call"), str):
                    continue
                found = names.resolve(dec["call"])
                if found is None or found.qualified not in self.receivers:
                    continue
                args = dec.get("args", [])
                signals = [self.signal_name(names, args[0])] if args else [UNKNOWN]
                sender = self.sender(names, dec.get("kw", []))
                site = Site(path, module, qualname(defs, idx), d["l"], d["c"])
                receiver = f"{module}.{qualname(defs, idx)}"
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

    def produce(self, files: list[tuple[str, dict]]) -> list[Edge]:
        out: list[Edge] = []
        for path, record in files:
            module, is_package = module_of_path(path)
            names = FileNames(self.symbols, record, module, is_package)
            out.extend(self.decorator_edges(path, names))
            out.extend(self.connect_edges(path, names))
        return out
