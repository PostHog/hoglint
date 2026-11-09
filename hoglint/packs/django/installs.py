"""`installs` edges: code that hands a callback to a declared hook function.

The hook list is config: qualified names of functions. A call whose callee resolves to a hook
is an edge from the calling scope, which is the callback's entry module. A decorator that
resolves to a hook names the callback itself. A call or decorator whose last name is a hook's
name but that resolves to nothing, or to a value, is an unresolved edge. A callee that resolves
to a class or a module is no hook and makes no edge.

Candidates come from queries: callee rows by their spelled last name, and defs whose decorator
text names a hook. Each one then resolves here.
"""

from __future__ import annotations

from hoglint.core.build import CoreBuild
from hoglint.core.harness import each_item, failure_edge
from hoglint.core.sites import FileScope, Lookup
from hoglint.model import Context, Edge, Resolution, Site
from hoglint.native.queries import PackQueries, Words

INSTALLS_PRODUCER = "django.installs@1"
HOOK_KINDS = frozenset({"def", "member"})
NOT_HOOK_KINDS = frozenset({"class", "module"})


def decorator_callees(d: dict) -> set[str]:
    return {dec["call"] for dec in d.get("dec", ()) if isinstance(dec, dict) and isinstance(dec.get("call"), str)}


class InstallsProducer:
    def __init__(self, build: CoreBuild, hooks: tuple[str, ...], queries: PackQueries) -> None:
        self.build = build
        self.queries = queries
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
            self.build.first_party.resolution(hit.origin.module),
            contexts,
            INSTALLS_PRODUCER,
            via=hit.via,
            shape=form,
            attrs=tuple(attrs),
        )

    def lookup(self, scope: FileScope, callee: str, at_scope: int, line: int) -> Lookup | None:
        found = self.build.context.value_lookup(scope, callee, at_scope, line)
        if found is not None and found.hit is not None and found.hit.kind in NOT_HOOK_KINDS:
            return None
        return found

    def call_edges(self, scope: FileScope, rows: list[list]) -> list[Edge]:
        out: list[Edge] = []
        for at_scope, header, callee, count, line, column in rows:
            if not self.names_a_hook(callee):
                continue
            if header != -1 and callee in decorator_callees(scope.defs[header]):
                continue
            found = self.lookup(scope, callee, at_scope, line)
            if found is None:
                continue
            site = scope.site(at_scope, line, column)
            edge = self.edge(site, scope.contexts(0, at_scope), callee, found, "call", "")
            if edge is not None:
                out.extend([edge] * count)
        return out

    def decorator_edges(self, scope: FileScope) -> list[Edge]:
        out: list[Edge] = []
        if not self.queries.may_mention(scope, ("defs",), self.hook_words):
            return out
        for idx, d in enumerate(scope.defs):
            for dec in d.get("dec", ()):
                callee = dec.get("call") if isinstance(dec, dict) else dec
                if not isinstance(callee, str) or not self.names_a_hook(callee):
                    continue
                found = self.lookup(scope, callee, d["s"], d["l"])
                if found is None:
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

    def produce(self, files: list[FileScope]) -> list[Edge]:
        if not self.hooks:
            return []
        calls = {scope.path: rows for scope, rows in self.queries.calls_named(files, "callees", self.hook_names)}

        def file_edges(scope: FileScope) -> list[Edge]:
            return self.call_edges(scope, calls.get(scope.path, [])) + self.decorator_edges(scope)

        return each_item(files, file_edges, self.failure)
