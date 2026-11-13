"""Per-file view of L1 records: scopes and contexts at a site, and the name a site looks up."""

from __future__ import annotations

from dataclasses import dataclass

from hoglint.core.symbols import FUNC, IF, TC_ELSE, TRY, TYPE_ONLY, Binding, Hit
from hoglint.model import Context, Site

MODULE_SCOPE = "<module>"


def binding_surface(binding: Binding) -> str:
    if binding[0] == "module":
        return binding[1]
    return f"{binding[1]}.{binding[2]}"


@dataclass(frozen=True, slots=True)
class Lookup:
    """A name at a site: the dotted surface it stands for and what it resolves to.

    `local` marks a name no scope binds, used inside a function: a parameter or local variable.
    """

    surface: str
    hit: Hit | None
    local: bool = False


class FileScope:
    """Scopes and contexts of one analyzed file. A subclass reads them from the file's record."""

    path: str
    module: str
    is_package: bool
    record: dict
    defs: list[dict]
    qualnames: list[str]
    in_function: list[bool]
    base_contexts: frozenset[Context]
    context_sets: dict[tuple[int, bool], frozenset[Context]]

    def scope_name(self, scope: int) -> str:
        return MODULE_SCOPE if scope == -1 else self.qualnames[scope]

    def is_function_scope(self, scope: int) -> bool:
        if scope == -1:
            return False
        return self.defs[scope]["k"] != "class" or self.in_function[scope]

    def site(self, scope: int, line: int, column: int) -> Site:
        return Site(self.path, self.module, self.scope_name(scope), line, column)

    def contexts(self, flags: int = 0, scope: int = -1) -> frozenset[Context]:
        """Path contexts plus syntax contexts. The else branch of `if TYPE_CHECKING:` runs at
        runtime, so only the body is type-only; the else branch gets its own context."""
        key = (flags, self.is_function_scope(scope))
        found = self.context_sets.get(key)
        if found is not None:
            return found
        extra: set[Context] = set()
        if flags & TYPE_ONLY:
            extra.add(Context.TYPE_ONLY)
        if flags & TC_ELSE:
            extra.add(Context.TYPE_CHECKING_ELSE)
        if flags & FUNC or key[1]:
            extra.add(Context.FUNCTION_LOCAL)
        if flags & (TRY | IF):
            extra.add(Context.CONDITIONAL)
        found = self.base_contexts | extra if extra else self.base_contexts
        self.context_sets[key] = found
        return found

    def ancestry(self, scope: int) -> list[int]:
        out = [scope]
        while scope != -1:
            scope = self.defs[scope]["s"]
            out.append(scope)
        return out
