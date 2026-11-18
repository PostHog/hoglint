"""`installs` edges: code that hands a callback to a hook function.

The hook list is config: qualified names of functions. A module-level function that hands one of
its parameters on to `<x>.connect(...)` is a hook too, found in the code: a call of it installs
its argument as a receiver of whatever it connects to. A call whose callee resolves to a hook
is an edge from the calling scope, which is the callback's entry module. A decorator that
resolves to a hook names the callback itself. A call or decorator whose last name is a hook's
name but that resolves to nothing, or to a value, is an unresolved edge. A callee that resolves
to a class or a module is no hook and makes no edge.

A call of a hook the config declares records the callables it hands the hook (the `callback`
attr, as a decorator's names the def it applies to): each argument that names a function or a
class, or calls one, resolved where the call stands. A hook declared as a table names the
arguments that hand it a callable, by keyword and position; its other arguments are data. Of a hook
declared by name alone, every argument may be a callable. A callable argument the facts cannot tell
(a parameter, a local variable, a first-party name that does not resolve, a `*` argument before a
declared position, a call with no recorded arguments) makes `callback` unknown. The import edges that run
when such a callable runs get `hook_callback`: `body` for an import inside a callable the file
defines (one hop: not what that module imports), `binds` for the import that binds a callable the
file imports, whose module defines it. A connect helper is no declared hook, so its calls record
nothing.

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
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from hoglint.core.harness import FileItems, each_item, failure_edge
from hoglint.core.memo import ItemOutput, Output, PerFileRead
from hoglint.core.sites import FileScope, Lookup
from hoglint.core.symbols import Hit
from hoglint.model import LANGUAGE_PYTHON, NO_LITERAL, UNKNOWN_ATTRS, Context, Edge, EntityId, Resolution, Site
from hoglint.native.queries import Words, entities_named
from hoglint.packs.django.signals import CONNECT, SignalValues, handing_hook
from hoglint.plugins import PackReader, Refinement

INSTALLS_PRODUCER = "django.installs@1"
HOOK_KINDS = frozenset({"def", "member"})
NOT_HOOK_KINDS = frozenset({"class", "module"})
CALLABLE_KINDS = frozenset({"def", "class"})
CLASS_KINDS = frozenset({"class"})
# The node kinds the extractor writes for a constant or a lambda argument: no callable a module defines.
CONSTANT_NODES = frozenset(
    {"JoinedStr", "TemplateStr", "Lambda", "badstr", "longstr", "int", "float", "complex", "bytes", "..."}
)
IMPORT = "import"
HOOK_CALLBACK = "hook_callback"
CALLBACK = "callback"
INSTANCE_HOOKS = "django.installs.instance-hooks"


@dataclass(frozen=False)
class Aliases:
    """Hooks a file reaches under names that do not spell them: the names its imports bind, and
    the (line, column) of defs one decorates."""

    names: set[str] = field(default_factory=set)
    decorated: set[tuple[int, int]] = field(default_factory=set)


@dataclass(frozen=False)
class Handed:
    """The callables the hook calls of one scope hand on: those the file defines, by qualname,
    and those it imports, by origin. `unknown` when an argument cannot be told."""

    local: set[str] = field(default_factory=set)
    imported: set[EntityId] = field(default_factory=set)
    unknown: bool = False

    def text(self, module: str) -> str:
        names = {f"{module}.{qualname}" for qualname in self.local} | {str(entity) for entity in self.imported}
        return ",".join(sorted(names))

    def add(self, other: Handed) -> None:
        self.local |= other.local
        self.imported |= other.imported

    def data(self) -> list[Any] | None:
        """The callables as an item's data; None when there are none."""
        if not self.local and not self.imported:
            return None
        imported = sorted([entity.module, entity.symbol] for entity in self.imported)
        return [sorted(self.local), imported]

    @staticmethod
    def restore(data: list[Any]) -> Handed:
        local, imported = data
        return Handed(set(local), {EntityId(LANGUAGE_PYTHON, module, symbol) for module, symbol in imported})


def local_def(scope: FileScope, name: str, at_scope: int) -> str | None:
    """The qualname of the def or class that `name` names in scope `at_scope` of the file, by
    Python's rules: the scope itself, then each enclosing function, then the module. A class body
    binds no name its methods see."""
    current = at_scope
    while True:
        for idx, d in enumerate(scope.defs):
            if d["s"] == current and d["n"] == name:
                return scope.qualnames[idx]
        if current == -1:
            return None
        parent = scope.defs[current]["s"]
        while parent != -1 and scope.defs[parent]["k"] == "class":
            parent = scope.defs[parent]["s"]
        current = parent


@dataclass(frozen=True)
class InstanceHook:
    """A hook named through a module-level instance: the hook as an entity, its method's name,
    and the instance's class (None when the instance is no call of a class hoglint can tell)."""

    entity: EntityId
    method: str
    cls: EntityId | None


def instance_hooks_form(hooks: list[InstanceHook]) -> object:
    return [(str(hook.entity), hook.method, None if hook.cls is None else str(hook.cls)) for hook in hooks]


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
    def __init__(
        self, reader: PackReader, hooks: tuple[str, ...], callables: Mapping[str, tuple[str | int, ...]] | None = None
    ) -> None:
        self.reader = reader
        self.declared = frozenset(hooks)
        self.callables = dict(callables or {})
        self.set_hooks(hooks)

    def set_hooks(self, hooks: tuple[str, ...]) -> None:
        self.hooks = frozenset(hooks)
        self.hook_names = frozenset(hook.rpartition(".")[2] for hook in hooks)
        self.hook_words = Words.ending(self.hook_names)
        by_method: dict[str, list[InstanceHook]] = defaultdict(list)
        for hook in sorted(hooks):
            found = self.instance_hook(hook)
            if found is not None:
                by_method[found.method].append(found)
        # Resolved outside any item, so an item reads the instance hooks of a method when a call
        # resolves to a member of that name (keyed by method, not by file): a change to an
        # instance's class, in a file the item never reads, still reaches the item.
        tracer = self.reader.tracer
        self.instance_hooks = PerFileRead(tracer, INSTANCE_HOOKS, dict(by_method), instance_hooks_form, [])

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
        for hook in self.instance_hooks.read(method):
            if hook.cls == cls:
                return hook, True
            if hook.cls is None or cls in self.reader.classes.mro(hook.cls):
                maybe = maybe or hook
        return (maybe, False) if maybe is not None else None

    def names_a_hook(self, callee: str) -> bool:
        return not callee.startswith("?") and callee.rpartition(".")[2] in self.hook_names

    def target(self, found: Lookup) -> tuple[bool, EntityId | None]:
        """(whether the callee may be a hook, the hook it reaches when it can tell). False when the
        callee resolves to a function that is no hook."""
        hit = found.hit if found.hit is not None and found.hit.kind in HOOK_KINDS else None
        if hit is None:
            return True, None
        if str(hit.origin) in self.hooks:
            return True, hit.origin
        matched = self.member_hook(hit)
        if matched is None:
            return False, None
        hook, reaches = matched
        return True, hook.entity if reaches else None

    def edge(
        self,
        site: Site,
        contexts: frozenset[Context],
        callee: str,
        found: Lookup,
        form: str,
        callback: str,
        unknown: bool = False,
    ) -> Edge | None:
        """None when the callee resolves to a function that is no hook."""
        hook, target = self.target(found)
        if not hook:
            return None
        hit = found.hit if found.hit is not None and found.hit.kind in HOOK_KINDS else None
        attrs = [("form", form)]
        if callback:
            attrs.append((CALLBACK, callback))
        if unknown:
            attrs.append((UNKNOWN_ATTRS, CALLBACK))
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

    def declared_call(self, found: Lookup) -> tuple[bool, tuple[str | int, ...] | None]:
        """Whether a hook call's callables are recorded (its hook is one the config declares, or one
        hoglint cannot tell; a connect helper's calls record none), and the arguments that hand
        the hook a callable: None for every argument."""
        hook, target = self.target(found)
        if not hook or (target is not None and str(target) not in self.declared):
            return False, None
        return True, self.callables.get(str(target)) if target is not None else None

    def hand(self, scope: FileScope, value: object, at_scope: int, line: int, handed: Handed, declared: bool) -> None:
        """One argument that may hand the hook a callable, as the extractor writes a hook call's
        argument: a name or an attribute chain, a list or tuple of them, a call of a chain, a
        constant, else the kind of node. A constant or a lambda hands on no callable a module
        defines. A call hands on what it builds: an instance of a first-party class is a callable,
        the result of third-party code is data, anything else cannot be told. Of a name, a
        function or a class is a callable; a module, a variable or an enum member is data, unless
        the hook's table declares the argument (`declared`), whose value then holds the callable."""
        if isinstance(value, dict):
            items = value.get("list") or value.get("tuple")
            if isinstance(items, list):
                for item in items:
                    self.hand(scope, item, at_scope, line, handed, declared)
                return
            node, callee = value.get("o"), value.get("call")
            if "s" in value or node in CONSTANT_NODES:
                return
            if node == "Call" and isinstance(callee, str):
                self.hand_name(scope, callee, at_scope, line, handed, CLASS_KINDS)
                return
            handed.unknown = True
            return
        if isinstance(value, str) and not value.startswith("?"):
            kinds = CALLABLE_KINDS | {"variable"} if declared else CALLABLE_KINDS
            self.hand_name(scope, value, at_scope, line, handed, kinds)

    def hand_name(
        self, scope: FileScope, name: str, at_scope: int, line: int, handed: Handed, kinds: frozenset[str]
    ) -> None:
        """A name an argument holds or calls, resolved where the call stands: kept as a callable
        when it resolves to one of `kinds`, unknown when it resolves to nothing first-party."""
        if "." not in name:
            qualname = local_def(scope, name, at_scope)
            if qualname is not None:
                handed.local.add(qualname)
                return
        found = self.reader.context.value_lookup(scope, name, at_scope, line)
        hit = found.hit if found is not None else None
        if hit is None:
            # Third-party code is no product's; anything else may be a callable hoglint cannot follow.
            external = found is not None and not found.local and not self.reader.first_party.contains(found.surface)
            handed.unknown = handed.unknown or not external
            return
        if hit.kind == "def" and kinds == CLASS_KINDS:
            # A first-party function builds the value: what it returns cannot be told.
            handed.unknown = True
            return
        if hit.kind not in kinds:
            return
        if hit.origin.module == scope.module:
            handed.local.add(hit.origin.symbol)
        else:
            handed.imported.add(hit.origin)

    def handed(self, scope: FileScope, calls: list[list], callables: tuple[str | int, ...] | None) -> Handed:
        """What the hook calls of one callee in one scope hand the hook, through the arguments
        `callables` names (None: every argument). No recorded call: the arguments cannot be told."""
        out = Handed(unknown=not calls)
        for at_scope, _, line, _, _, _, args, kws in calls:
            for position, value in enumerate(args):
                starred = isinstance(value, dict) and value.get("o") == "Starred"
                if starred and (callables is None or any(isinstance(item, int) for item in callables)):
                    # The positions after a `*` argument cannot be told.
                    out.unknown = True
                    break
                if callables is None or position in callables:
                    self.hand(scope, value, at_scope, line, out, callables is not None)
            for keyword, value in kws:
                spread = keyword is None and (callables is None or any(isinstance(item, str) for item in callables))
                if spread:
                    out.unknown = True
                elif callables is None or keyword in callables:
                    self.hand(scope, value, at_scope, line, out, callables is not None)
        return out

    def callback_imports(self, scope: FileScope, handed: Handed) -> list[Refinement]:
        """The file's import edges that run when a handed callable runs: each import in the body of
        a callable the file defines, and the import that binds a callable the file imports. An
        import counts when its surface ends in the last name of a module the file imports."""
        found: dict[int, str] = {}
        if handed.local:
            names = {module.rpartition(".")[2] for module in self.reader.imported_modules(scope)}
            for edge in self.reader.edges_in(IMPORT, [scope]) if names else ():
                if edge.surface.rpartition(".")[2] not in names:
                    continue
                inside = any(edge.site.scope.startswith(f"{qualname}.") for qualname in handed.local)
                if edge.site.scope in handed.local or inside:
                    found.setdefault(edge.handle, "body")
        if handed.imported:
            for edge in self.reader.edges_to(IMPORT, sorted(handed.imported, key=str)):
                if edge.site.path == scope.path:
                    found.setdefault(edge.handle, "binds")
        return [Refinement(handle, attrs=((HOOK_CALLBACK, value),)) for handle, value in sorted(found.items())]

    def call_edges(
        self, scope: FileScope, rows: list[list], calls: list[list], aliases: Aliases, handed: Handed
    ) -> list[Edge]:
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
            text, unknown = "", False
            declared, callables = self.declared_call(found)
            if declared:
                own = [call for call in calls if call[0] == at_scope and call[5] == callee]
                mine = self.handed(scope, own, callables)
                text, unknown = mine.text(scope.module), mine.unknown
                handed.local |= mine.local
                handed.imported |= mine.imported
            edge = self.edge(site, scope.contexts(0, at_scope), callee, found, "call", text, unknown)
            if edge is not None:
                out.extend([edge] * count)
        return out

    def decorator_edges(self, scope: FileScope, aliases: Aliases, handed: Handed) -> list[Edge]:
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
                    if self.declared_call(found)[0]:
                        handed.local.add(qualname)
        return out

    @staticmethod
    def failure(scope: FileScope, error: Exception) -> Edge:
        return failure_edge("installs", INSTALLS_PRODUCER, scope.site(-1, 0, 0), scope.base_contexts, error)

    def file_edges(
        self, scope: FileScope, rows: list[list], calls: list[list], aliases: Aliases, handed: Handed
    ) -> list[Edge]:
        """The file's `installs` edges. The callables its hook calls hand on go into `handed` once
        every edge is made, so a file that fails hands on none."""
        if aliases.names:
            rows = scope.record.get("callees", [])
        found = Handed()
        edges = self.call_edges(scope, rows, calls, aliases, found) + self.decorator_edges(scope, aliases, found)
        handed.add(found)
        return edges


class InstallsItems(FileItems):
    """`django.installs` as one item per file. The connect helpers and the hooks bound or applied
    under another name are asked of the whole graph once. Each item reads its own file's part of
    the aliases, and every helper: a new one can make an edge in any file.

    An item keeps the callables its file hands on as its data. The step refines their import
    edges after the items (`finish`): a refinement names an edge by its handle in this run, so no
    item could store it."""

    ALIASES = "django.installs.aliases"
    HELPERS = "django.installs.helpers"

    def __init__(self, producer: InstallsProducer, signals: SignalValues, files: list[FileScope]) -> None:
        helpers = connect_helpers(producer.reader, signals, files)
        producer.set_hooks(tuple(sorted({*producer.hooks, *helpers})))
        super().__init__(producer.reader, files if producer.hooks else [], producer.failure)
        self.producer = producer
        aliases = producer.aliases(files) if producer.hooks else {}
        self.alias_names = {path: frozenset(found.names) for path, found in aliases.items()}
        self.aliases = PerFileRead(producer.reader.tracer, self.ALIASES, aliases, self.form, Aliases())
        self.helpers = PerFileRead(producer.reader.tracer, self.HELPERS, {}, list, helpers)
        self.callees: dict[str, list[list]] = {}
        self.calls: dict[str, list[list]] = {}
        self.handed: dict[str, Handed] = {}

    @staticmethod
    def form(aliases: Aliases) -> object:
        return (sorted(aliases.names), sorted(aliases.decorated))

    def prepare(self, keys: Sequence[str]) -> None:
        scopes = self.scopes(keys)
        found = self.reader.calls_named(scopes, "callees", self.producer.hook_names)
        self.callees = {scope.path: rows for scope, rows in found}
        # A hook imported under another name is called by that name.
        names = self.producer.hook_names | {name for aliases in self.alias_names.values() for name in aliases}
        found = self.reader.calls_named(scopes, "calls", names)
        self.calls = {scope.path: rows for scope, rows in found}

    def file_item(self, scope: FileScope) -> ItemOutput:
        self.helpers.read(scope.path)
        aliases = self.aliases.read(scope.path)
        rows = self.callees.get(scope.path, [])
        calls = self.calls.get(scope.path, [])
        handed = Handed()
        made = self.each_file(scope, lambda one: self.producer.file_edges(one, rows, calls, aliases, handed))
        return ItemOutput(made.outputs, handed.data())

    def take(self, key: str, data: Any) -> None:
        if data is not None:
            self.handed[key] = Handed.restore(data)

    def callback_imports(self, scope: FileScope) -> list[Refinement]:
        return self.producer.callback_imports(scope, self.handed[scope.path])

    def finish(self) -> Iterable[Output]:
        """The import edges each file's handed callables run, in file order."""
        scopes = [self.files[key] for key in self.handed]
        return each_item(scopes, self.callback_imports, self.producer.failure)
