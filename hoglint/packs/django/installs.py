"""`installs` edges: code that hands a callback to a declared hook function.

The hook list is config: qualified names of functions. A call whose callee resolves to a hook
is an edge from the calling scope, which is the callback's entry module. A decorator that
resolves to a hook names the callback itself. A call or decorator whose last name is a hook's
name but that resolves to nothing, or to a value, is an unresolved edge. A callee that resolves
to a class or a module is no hook and makes no edge.

Candidates come from queries: callee rows by their spelled last name, and defs whose decorator
text names a hook. A hook imported under another name (`import ... as add_hook`) is found by
origin: the `import` edge that binds it and the `decorate` edge that applies it. A candidate
found only by origin makes an edge only when it resolves to a hook.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from hoglint.core.harness import Output, each_item, failure_edge
from hoglint.core.memo import TRACER, ItemOutput, ItemRun, Items
from hoglint.core.reader import note_file_of
from hoglint.core.sites import FileScope, Lookup
from hoglint.model import Context, Edge, Resolution, Site
from hoglint.native.queries import Words, entities_named
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


def decorator_callees(d: dict) -> set[str]:
    return {dec["call"] for dec in d.get("dec", ()) if isinstance(dec, dict) and isinstance(dec.get("call"), str)}


class InstallsProducer:
    def __init__(self, reader: PackReader, hooks: tuple[str, ...]) -> None:
        self.reader = reader
        self.hooks = frozenset(hooks)
        self.hook_names = frozenset(hook.rpartition(".")[2] for hook in hooks)
        self.hook_words = Words.ending(self.hook_names)

    def names_a_hook(self, callee: str) -> bool:
        return not callee.startswith("?") and callee.rpartition(".")[2] in self.hook_names

    def edge(
        self, site: Site, contexts: frozenset[Context], callee: str, found: Lookup, form: str, callback: str
    ) -> Edge | None:
        """None when the callee resolves to a function that is no hook."""
        hit = found.hit if found.hit is not None and found.hit.kind in HOOK_KINDS else None
        if hit is not None and str(hit.origin) not in self.hooks:
            return None
        attrs = [("form", form)]
        if callback:
            attrs.append(("callback", callback))
        if hit is None:
            return Edge(
                "installs",
                site,
                callee,
                (),
                Resolution.UNRESOLVED,
                contexts,
                INSTALLS_PRODUCER,
                shape=form,
                attrs=tuple(attrs),
            )
        return Edge(
            "installs",
            site,
            found.surface,
            (hit.origin,),
            self.reader.first_party.resolution(hit.origin.module),
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

    def produce(self, files: list[FileScope]) -> list[Output]:
        return ItemRun(InstallsItems(self, files), None).outputs()


class InstallsItems(Items):
    """`django.installs` as one item per file. The hooks bound or applied under another name are
    asked of the whole graph once, and each item reads its own file's part of them."""

    ALIASES = "django.installs.aliases"

    def __init__(self, producer: InstallsProducer, files: list[FileScope]) -> None:
        self.producer = producer
        self.files = {scope.path: scope for scope in files} if producer.hooks else {}
        self.aliases = producer.aliases(files) if producer.hooks else {}
        self.calls: dict[str, list[list]] = {}

    def keys(self) -> list[str]:
        return list(self.files)

    def prepare(self, keys: Sequence[str]) -> None:
        scopes = [self.files[key] for key in keys]
        found = self.producer.reader.calls_named(scopes, "callees", self.producer.hook_names)
        self.calls = {scope.path: rows for scope, rows in found}

    def run_item(self, key: str) -> ItemOutput:
        scope = self.files[key]
        note_file_of(scope)
        aliases = self.aliases.get(key, Aliases())
        TRACER.note(self.ALIASES, key, (sorted(aliases.names), sorted(aliases.decorated)))
        rows = self.calls.get(key, [])

        def file_edges(one: FileScope) -> list[Edge]:
            return self.producer.file_edges(one, rows, aliases)

        return ItemOutput(list(each_item([scope], file_edges, self.producer.failure)))
