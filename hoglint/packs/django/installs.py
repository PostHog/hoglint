"""`installs` edges: code that hands a callback to a declared hook function.

The hook list is config: qualified names or fnmatch globs (`**.register_*`). A call whose callee
resolves to a matching function is an edge from the calling scope, which is the callback's entry
module. A decorator that resolves to a hook names the callback itself. A call whose last name
matches a hook glob but whose callee does not resolve, or resolves to a value, is an unresolved
edge. A callee that resolves to a class or a module is no hook and makes no edge.
"""

from __future__ import annotations

import fnmatch

from hoglint.model import Edge, Resolution, Site
from hoglint.packs.django.sites import PathKinds, failure_edge, file_site, make_site
from hoglint.packs.django.symbols import FileNames, Symbol, SymbolKind, SymbolResolver
from hoglint.spike.core import module_of_path, qualname

INSTALLS_PRODUCER = "django.installs@1"
HOOK_KINDS = frozenset({SymbolKind.FUNCTION, SymbolKind.MEMBER})
NOT_HOOK_KINDS = frozenset({SymbolKind.CLASS, SymbolKind.MODULE})


def decorator_callees(d: dict) -> set[str]:
    return {dec["call"] for dec in d.get("dec", ()) if isinstance(dec, dict) and isinstance(dec.get("call"), str)}


class HookPatterns:
    def __init__(self, patterns: tuple[str, ...]) -> None:
        self.patterns = patterns
        self.names = tuple(p.rpartition(".")[2] for p in patterns)

    def name_matches(self, name: str) -> bool:
        return any(fnmatch.fnmatchcase(name, n) for n in self.names)

    def matches(self, qualified: str) -> bool:
        return any(fnmatch.fnmatchcase(qualified, p) for p in self.patterns)


class InstallsProducer:
    def __init__(self, symbols: SymbolResolver, hooks: tuple[str, ...], first_party: tuple[str, ...]) -> None:
        self.symbols = symbols
        self.hooks = HookPatterns(hooks)
        self.first_party = first_party

    @staticmethod
    def hook(found: Symbol | None) -> Symbol | None:
        """The hook function a resolved callee names. A value we cannot see into stays unknown."""
        if found is None or found.kind not in HOOK_KINDS:
            return None
        return found

    @staticmethod
    def is_not_a_hook(found: Symbol | None) -> bool:
        """A class or module named like a hook: calling it builds an object or fails, it installs nothing."""
        return found is not None and found.kind in NOT_HOOK_KINDS

    def edge(self, site: Site, path: str, callee: str, found: Symbol | None, form: str, callback: str) -> Edge | None:
        if found is not None and not self.hooks.matches(found.qualified):
            return None
        attrs = [("form", form)]
        if callback:
            attrs.append(("callback", callback))
        if found is None:
            return Edge(
                "installs",
                site,
                callee,
                (),
                Resolution.UNRESOLVED,
                PathKinds.contexts(path),
                INSTALLS_PRODUCER,
                shape=form,
                attrs=tuple(attrs),
            )
        surface, via = self.symbols.surface_and_via(found)
        first_party = found.module.split(".", 1)[0] in self.first_party
        return Edge(
            "installs",
            site,
            surface,
            (found.entity,),
            Resolution.RESOLVED if first_party else Resolution.EXTERNAL,
            PathKinds.contexts(path),
            INSTALLS_PRODUCER,
            via=via,
            shape=form,
            attrs=tuple(attrs),
        )

    def call_edges(self, path: str, names: FileNames) -> list[Edge]:
        out: list[Edge] = []
        record, module = names.record, names.module
        defs = record.get("defs", [])
        for scope, header, callee, count, line, column in record.get("callees", ()):
            if callee.startswith("?") or not self.hooks.name_matches(callee.rpartition(".")[2]):
                continue
            if header != -1 and callee in decorator_callees(defs[header]):
                continue
            found = names.resolve(callee)
            if self.is_not_a_hook(found):
                continue
            site = make_site(record, module, scope, line, column)
            for _ in range(count):
                edge = self.edge(site, path, callee, self.hook(found), "call", "")
                if edge is not None:
                    out.append(edge)
        return out

    def decorator_edges(self, path: str, names: FileNames) -> list[Edge]:
        out: list[Edge] = []
        module = names.module
        defs = names.record.get("defs", [])
        for idx, d in enumerate(defs):
            for dec in d.get("dec", ()):
                callee = dec.get("call") if isinstance(dec, dict) else dec
                if not isinstance(callee, str) or not self.hooks.name_matches(callee.rpartition(".")[2]):
                    continue
                found = names.resolve(callee)
                if self.is_not_a_hook(found):
                    continue
                site = Site(path, module, qualname(defs, idx), d["l"], d["c"])
                callback = f"{module}.{qualname(defs, idx)}"
                edge = self.edge(site, path, callee, self.hook(found), "decorator", callback)
                if edge is not None:
                    out.append(edge)
        return out

    def file_edges(self, path: str, record: dict) -> list[Edge]:
        module, is_package = module_of_path(path)
        names = FileNames(self.symbols, record, module, is_package)
        return self.call_edges(path, names) + self.decorator_edges(path, names)

    def produce(self, files: list[tuple[str, dict]]) -> list[Edge]:
        if not self.hooks.patterns:
            return []
        out: list[Edge] = []
        for path, record in files:
            try:
                out.extend(self.file_edges(path, record))
            except Exception as error:  # one unreadable file is unknown, the other files still count
                out.append(failure_edge("installs", INSTALLS_PRODUCER, file_site(path), error))
        return out
