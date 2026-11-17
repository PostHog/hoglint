"""`installs` edges: code that hands a callback to a hook function.

The hook list is config: qualified names of functions. A module-level function that hands one of
its parameters on to `<x>.connect(...)` is a hook too, found in the code: a call of it installs
its argument as a receiver of whatever it connects to. A call whose callee resolves to a hook
is an edge from the calling scope, which is the callback's entry module. A decorator that
resolves to a hook names the callback itself. A call or decorator whose last name is a hook's
name but that resolves to nothing, or to a value, is an unresolved edge. A callee that resolves
to a class or a module is no hook and makes no edge.

A hook the config names through a module-level instance (`registry.console.register`) is a
method of the instance's class: a call that resolves to that member of that class, such as one
through an attribute the class types, is a call of the hook. One that resolves to the member of
a class the instance's class inherits, or of any class when hoglint cannot tell the instance's
class, may reach the hook too, so it is an unresolved edge.

Candidates come from queries: callee rows by their spelled last name, and defs whose decorator
text names a hook. A hook imported under another name (`import ... as add_hook`) is found by
origin: the `import` edge that binds it and the `decorate` edge that applies it. A candidate
found only by origin makes an edge only when it resolves to a hook.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from hoglint.core.harness import FileItems, failure_edge
from hoglint.core.memo import ItemOutput, PerFileRead
from hoglint.core.sites import FileScope, Lookup
from hoglint.core.symbols import Hit
from hoglint.model import NO_LITERAL, Context, Edge, EntityId, Resolution, Site
from hoglint.native.queries import Words, entities_named
from hoglint.packs.django.signals import CONNECT, SignalValues, handing_hook
from hoglint.plugins import PackReader

INSTALLS_PRODUCER = "django.installs@1"
HOOK_KINDS = frozenset({"def", "member"})
NOT_HOOK_KINDS = frozenset({"class", "module"})


@dataclass(frozen=False)
class Aliases:
    """Hooks a file reaches under names that do not spell them: the names its imports bind, and
    the (line, column) of defs one decorates."""

    names: set[str] = field(default_factory=set)
    decorated: set[tuple[int, int]] = field(default_factory=set)


@dataclass(frozen=True)
class InstanceHook:
    """A hook named through a module-level instance: the hook as an entity, its method's name,
    and the instance's class (None when the instance is no call of a class hoglint can tell)."""

    entity: EntityId
    method: str
    cls: EntityId | None


def decorator_callees(d: dict) -> set[str]:
    return {dec["call"] for dec in d.get("dec", ()) if isinstance(dec, dict) and isinstance(dec.get("call"), str)}


def connect_helpers(reader: PackReader, signals: SignalValues, files: list[FileScope]) -> tuple[str, ...]:
    """Module-level functions that hand one of their parameters on to `<signal>.connect(...)`,
    sorted. A method is left out, and so is a function named `connect`: every `.connect` call
    would name it. A `connect` of anything but a signal (a database driver's) makes no helper."""
    found: set[str] = set()
    for scope, rows in reader.calls_named(files, "calls", (CONNECT,)):
        for at_scope, _, line, _, _, callee, args, kws in rows:
            hook = handing_hook(scope, at_scope, args, kws)
            if hook and signals.connected_signal(scope, callee, at_scope, line) is not None:
                found.add(hook)
    return tuple(sorted(found))


class InstallsProducer:
    def __init__(self, reader: PackReader, hooks: tuple[str, ...]) -> None:
        self.reader = reader
        self.set_hooks(hooks)

    def set_hooks(self, hooks: tuple[str, ...]) -> None:
        self.hooks = frozenset(hooks)
        self.hook_names = frozenset(hook.rpartition(".")[2] for hook in hooks)
        self.hook_words = Words.ending(self.hook_names)
        found = (self.instance_hook(hook) for hook in sorted(hooks))
        self.instance_hooks = [hook for hook in found if hook is not None]

    def instance_hook(self, hook: str) -> InstanceHook | None:
        """The hook as a method of a module-level instance, when the config names it so."""
        owner, _, method = hook.rpartition(".")
        hit, rest = self.reader.resolver.resolve_dotted(owner)
        if hit is None or rest or hit.kind != "variable":
            return None
        origin = hit.origin
        table = self.reader.tables.get(origin.module)
        value = table.values.get(origin.symbol) if table is not None else None
        callee = value.get("call") if isinstance(value, dict) else None
        cls = self.reader.resolver.resolve_class(origin.module, callee) if callee is not None else None
        entity = EntityId(origin.language, origin.module, f"{origin.symbol}.{method}")
        return InstanceHook(entity, method, cls)

    def member_hook(self, hit: Hit) -> tuple[InstanceHook, bool] | None:
        """The instance-named hook a call that resolves to a class member may reach, and whether
        it reaches it: the member's class is the instance's class. A class the instance's class
        inherits, or an instance whose class hoglint cannot tell, may hold the hook too."""
        if hit.kind != "member":
            return None
        owner, _, method = hit.origin.symbol.rpartition(".")
        cls = EntityId(hit.origin.language, hit.origin.module, owner)
        maybe = None
        for hook in self.instance_hooks:
            if hook.method != method:
                continue
            if hook.cls == cls:
                return hook, True
            if hook.cls is None or cls in self.reader.classes.mro(hook.cls):
                maybe = maybe or hook
        return (maybe, False) if maybe is not None else None

    def names_a_hook(self, callee: str) -> bool:
        return not callee.startswith("?") and callee.rpartition(".")[2] in self.hook_names

    def edge(
        self, site: Site, contexts: frozenset[Context], callee: str, found: Lookup, form: str, callback: str
    ) -> Edge | None:
        """None when the callee resolves to a function that is no hook."""
        hit = found.hit if found.hit is not None and found.hit.kind in HOOK_KINDS else None
        target = hit.origin if hit is not None else None
        if hit is not None and str(hit.origin) not in self.hooks:
            matched = self.member_hook(hit)
            if matched is None:
                return None
            hook, reaches = matched
            target = hook.entity if reaches else None
        attrs = [("form", form)]
        if callback:
            attrs.append(("callback", callback))
        if hit is None or target is None:
            # A hook called through a value (a variable, `self`), which hoglint does not follow.
            return Edge(
                "installs",
                site,
                callee,
                (),
                Resolution.UNRESOLVED,
                contexts,
                INSTALLS_PRODUCER,
                shape=form,
                attrs=(*attrs, NO_LITERAL),
            )
        return Edge(
            "installs",
            site,
            found.surface,
            (target,),
            self.reader.first_party.resolution(target.module),
            contexts,
            INSTALLS_PRODUCER,
            via=hit.via,
            shape=form,
            attrs=tuple(attrs),
        )

    def lookup(self, scope: FileScope, callee: str, at_scope: int, line: int) -> Lookup | None:
        found = self.reader.context.value_lookup(scope, callee, at_scope, line)
        if found is not None and found.hit is not None and found.hit.kind in NOT_HOOK_KINDS:
            return None
        return found

    def is_hook(self, found: Lookup) -> bool:
        hit = found.hit
        return hit is not None and hit.kind in HOOK_KINDS and str(hit.origin) in self.hooks

    def aliases(self, files: list[FileScope]) -> dict[str, Aliases]:
        """Hooks bound or applied under another name, by origin: `import` edges that bind a hook,
        and `decorate` edges that apply one."""
        out: dict[str, Aliases] = defaultdict(Aliases)
        paths = {scope.path for scope in files}
        targets = [entity for hook in sorted(self.hooks) for entity in entities_named(hook)]
        for edge in self.reader.edges_to("import", targets):
            bound = edge.attr("binds")
            if edge.site.path in paths and bound not in self.hook_names:
                out[edge.site.path].names.add(bound)
        for edge in self.reader.edges_to("decorate", targets):
            if edge.site.path in paths:
                out[edge.site.path].decorated.add((edge.site.line, edge.site.column))
        return out

    def call_edges(self, scope: FileScope, rows: list[list], aliases: Aliases) -> list[Edge]:
        out: list[Edge] = []
        for at_scope, header, callee, count, line, column in rows:
            spelled = self.names_a_hook(callee)
            if not spelled and callee not in aliases.names:
                continue
            if header != -1 and callee in decorator_callees(scope.defs[header]):
                continue
            found = self.lookup(scope, callee, at_scope, line)
            if found is None or (not spelled and not self.is_hook(found)):
                continue
            site = scope.site(at_scope, line, column)
            edge = self.edge(site, scope.contexts(0, at_scope), callee, found, "call", "")
            if edge is not None:
                out.extend([edge] * count)
        return out

    def decorator_edges(self, scope: FileScope, aliases: Aliases) -> list[Edge]:
        out: list[Edge] = []
        if not aliases.decorated and not self.reader.may_mention(scope, ("defs",), self.hook_words):
            return out
        for idx, d in enumerate(scope.defs):
            for dec in d.get("dec", ()):
                callee = dec.get("call") if isinstance(dec, dict) else dec
                if not isinstance(callee, str):
                    continue
                spelled = self.names_a_hook(callee)
                if not spelled and (d["l"], d["c"]) not in aliases.decorated:
                    continue
                found = self.lookup(scope, callee, d["s"], d["l"])
                if found is None or (not spelled and not self.is_hook(found)):
                    continue
                qualname = scope.qualnames[idx]
                site = Site(scope.path, scope.module, qualname, d["l"], d["c"])
                callback = f"{scope.module}.{qualname}"
                edge = self.edge(site, scope.contexts(d.get("f", 0), d["s"]), callee, found, "decorator", callback)
                if edge is not None:
                    out.append(edge)
        return out

    @staticmethod
    def failure(scope: FileScope, error: Exception) -> Edge:
        return failure_edge("installs", INSTALLS_PRODUCER, scope.site(-1, 0, 0), scope.base_contexts, error)

    def file_edges(self, scope: FileScope, rows: list[list], aliases: Aliases) -> list[Edge]:
        if aliases.names:
            rows = scope.record.get("callees", [])
        return self.call_edges(scope, rows, aliases) + self.decorator_edges(scope, aliases)


class InstallsItems(FileItems):
    """`django.installs` as one item per file. The connect helpers and the hooks bound or applied
    under another name are asked of the whole graph once. Each item reads its own file's part of
    the aliases, and every helper: a new one can make an edge in any file."""

    ALIASES = "django.installs.aliases"
    HELPERS = "django.installs.helpers"

    def __init__(self, producer: InstallsProducer, signals: SignalValues, files: list[FileScope]) -> None:
        helpers = connect_helpers(producer.reader, signals, files)
        producer.set_hooks(tuple(sorted({*producer.hooks, *helpers})))
        super().__init__(producer.reader, files if producer.hooks else [], producer.failure)
        self.producer = producer
        aliases = producer.aliases(files) if producer.hooks else {}
        self.aliases = PerFileRead(producer.reader.tracer, self.ALIASES, aliases, self.form, Aliases())
        self.helpers = PerFileRead(producer.reader.tracer, self.HELPERS, {}, list, helpers)
        self.calls: dict[str, list[list]] = {}

    @staticmethod
    def form(aliases: Aliases) -> object:
        return (sorted(aliases.names), sorted(aliases.decorated))

    def prepare(self, keys: Sequence[str]) -> None:
        found = self.reader.calls_named(self.scopes(keys), "callees", self.producer.hook_names)
        self.calls = {scope.path: rows for scope, rows in found}

    def file_item(self, scope: FileScope) -> ItemOutput:
        self.helpers.read(scope.path)
        aliases = self.aliases.read(scope.path)
        rows = self.calls.get(scope.path, [])
        return self.each_file(scope, lambda one: self.producer.file_edges(one, rows, aliases))
