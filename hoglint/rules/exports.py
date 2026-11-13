"""The export primitive: does a public door hand out a denied shape?

The check is recursive. An exported class must have an allowed shape, and the types in its
fields, its bases' fields and its methods' signatures must too. An exported function's
signature types are checked the same way. A module exports its public definitions and the
imports it hands out; a re-export is judged at its origin, so a shim module hides nothing. Shape names are core recognizers plus the config's [kinds].

Membership is the labeler's: an export's paths, narrowed by `subclass_of`. The audience is not
read here: it limits who may use an export, not what the export hands out, so every member is
checked whatever its audience. `from` leaves out the export modules it is false for.

The rule judges only the shape of a class named in a slot of a member where a signature rule
it names in `leave_slots_to` reads that slot's row: a denied kind, an unknown type, a
missing annotation or an unresolved name there is the signature rule's to report. A signature rule
reads the rows of a public callable in a module its `from` and the source-only parts of its
`select` hold for; the other parts of its selection judge the slot's type. So a slot is left to it
where the row's own module is such a module, or where the exporting module is one and holds the
producer's copy of the slot. This holds at every depth: the fields of a class a slot names are left
to the signature rule where it reads their rows. A slot no signature rule reads, such as a method of
a class handed out from outside a facade, is judged in full.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from hoglint.config.model import ExportSpec, RuleSpec, ShapePolicy
from hoglint.globs import compile_label_glob
from hoglint.labels import NOT_HANDED_CONTEXTS
from hoglint.ledger.envelope import ConfigInputs
from hoglint.model import Edge, Resolution, context_bits
from hoglint.rules.base import (
    IncompleteItem,
    RuleContext,
    RuleRun,
    RuleType,
    context_ok,
    project,
    rule_kinds,
    source_part,
)
from hoglint.rules.edges import SignaturePrimitive
from hoglint.rules.plan import identity_paths, selector_tree, tree
from hoglint.selectors import NONE, Node, Ref, Resolver, Selector, State, Truth, Value, evaluate

ABC_BASES = frozenset({"abc.ABC"})
ABC_METAS = frozenset({"abc.ABCMeta"})
ENUM_BASES = frozenset({"enum.Enum", "enum.StrEnum", "enum.IntEnum", "enum.Flag", "enum.IntFlag"})
PROTOCOL_BASES = frozenset({"typing.Protocol", "typing_extensions.Protocol"})
# Python's own exception classes: a class that inherits one is an exception.
BUILTIN_EXCEPTIONS = (
    "ArithmeticError",
    "AssertionError",
    "AttributeError",
    "BaseException",
    "BaseExceptionGroup",
    "BlockingIOError",
    "BrokenPipeError",
    "BufferError",
    "BytesWarning",
    "ChildProcessError",
    "ConnectionAbortedError",
    "ConnectionError",
    "ConnectionRefusedError",
    "ConnectionResetError",
    "DeprecationWarning",
    "EOFError",
    "EncodingWarning",
    "EnvironmentError",
    "Exception",
    "ExceptionGroup",
    "FileExistsError",
    "FileNotFoundError",
    "FloatingPointError",
    "FutureWarning",
    "GeneratorExit",
    "IOError",
    "ImportError",
    "ImportWarning",
    "IndentationError",
    "IndexError",
    "InterruptedError",
    "IsADirectoryError",
    "KeyError",
    "KeyboardInterrupt",
    "LookupError",
    "MemoryError",
    "ModuleNotFoundError",
    "NameError",
    "NotADirectoryError",
    "NotImplementedError",
    "OSError",
    "OverflowError",
    "PendingDeprecationWarning",
    "PermissionError",
    "ProcessLookupError",
    "PythonFinalizationError",
    "RecursionError",
    "ReferenceError",
    "ResourceWarning",
    "RuntimeError",
    "RuntimeWarning",
    "StopAsyncIteration",
    "StopIteration",
    "SyntaxError",
    "SyntaxWarning",
    "SystemError",
    "SystemExit",
    "TabError",
    "TimeoutError",
    "TypeError",
    "UnboundLocalError",
    "UnicodeDecodeError",
    "UnicodeEncodeError",
    "UnicodeError",
    "UnicodeTranslateError",
    "UnicodeWarning",
    "UserWarning",
    "ValueError",
    "Warning",
    "ZeroDivisionError",
)
EXCEPTION_BASES = frozenset(f"builtins.{name}" for name in BUILTIN_EXCEPTIONS)
# The core shapes a class has when its lineage holds one of their bases.
SHAPE_BASES = {"abc": ABC_BASES, "enum": ENUM_BASES, "protocol": PROTOCOL_BASES, "exception": EXCEPTION_BASES}


def core_shape_bases() -> dict[str, list[str]]:
    """The base classes and metaclasses that make each core shape, for the native evaluator's
    plan: these lists are the only copy, so both engines recognize the same classes."""
    out: dict[str, list[str]] = {}
    for name, bases in SHAPE_BASES.items():
        out[name] = sorted(bases)
        if name == "abc":
            out["abc_metas"] = sorted(ABC_METAS)
    return out


# Why an export rule cannot tell about a symbol, by code; `{detail}` is the export's name or the
# slot. The native evaluator reports the code (`export.rs`).
UNKNOWN_WHY = {
    "not-member": "cannot tell whether it is in export {detail}: a base did not resolve",
    "unknown-type": "a type hoglint reads as unknown",
    "no-facts": "no facts for this symbol",
    "bases": "its bases did not all resolve",
    "no-annotation": "{detail} has no annotation",
}


class ExportPrimitive(RuleType):
    PRIMITIVE = MODE = "export"
    NATIVE_MODE = "export"
    CONFIRM = "whole"
    READS = frozenset({"exports", "contexts", "from", "leave_slots_to", "on_unknown", "unknown_types", "on_unresolved"})

    def __init__(self, ctx: RuleContext, spec: RuleSpec) -> None:
        self.ctx = ctx
        self.spec = spec
        self.run = RuleRun(spec)
        self.facts = ctx.labeler.facts
        self.dataclass_kinds = ConfigInputs.of(ctx.config).core.dataclass_kinds
        self.index = ctx.labeler.index
        self.reported: set[tuple[str, str, str]] = set()
        self.visited: set[tuple[str, str, str]] = set()
        self.shapes: dict[str, tuple[frozenset[str], bool]] = {}
        # `allow_where` and `walk_where` per (shape, symbol): many export modules name one type.
        self.allowed: dict[tuple[str, str], bool] = {}
        self.walked: dict[tuple[str, str], bool] = {}
        self.members: dict[tuple[str, bool, str], list[tuple[Edge, str, str]]] = {}
        self.exposes_by_name: dict[str, dict[str, list[int]]] = {}
        self.made: dict[int, Edge] = {}
        self.include = frozenset(spec.contexts_include)
        self.exclude = frozenset(NOT_HANDED_CONTEXTS if spec.contexts_exclude is None else spec.contexts_exclude)
        self.slot_owners = self.signature_scopes()
        self.slots_seen: dict[str, bool] = {}

    @staticmethod
    def message(module: str, trail: Sequence[str], label: str) -> str:
        """A finding's message: the words of both engines."""
        return f"{module} exports {' -> '.join(trail)}: {label}"

    @staticmethod
    def unknown_item(
        rule: str, module: str, symbol: str, trail: Sequence[str], why: str, detail: str
    ) -> IncompleteItem:
        """What a symbol the rule cannot tell about leaves incomplete; `why` is a code of
        `UNKNOWN_WHY`, and an annotation's `detail` is its slot."""
        if why == "no-annotation":
            detail = detail or "a slot"
        reason = UNKNOWN_WHY[why].format(detail=detail)
        return IncompleteItem(rule, f"{module}|{symbol}", f"{' -> '.join(trail)}: {reason}", (module,))

    @staticmethod
    def unresolved_item(rule: str, module: str, surface: str) -> IncompleteItem:
        """What an import that did not resolve leaves incomplete."""
        return IncompleteItem(rule, f"{module}|{surface}", f"{surface!r} did not resolve", (module,))

    @property
    def exposes(self) -> Mapping[str, list[tuple[str, int]]]:
        return self.index.exposed_handles

    @property
    def top_level(self) -> Mapping[str, list[str]]:
        return self.index.top_level

    def selected_exports(self) -> list[ExportSpec]:
        names = self.spec.exports or tuple(self.ctx.config.exports)
        return [self.ctx.config.exports[name] for name in names]

    def signature_scopes(self) -> list[Node | None]:
        """Where each signature rule of `leave_slots_to` reads rows: its `from` and the parts of
        its `select` that read only the source, None when it reads them in every module."""
        out: list[Node | None] = []
        for name in self.spec.leave_slots_to:
            rule = self.ctx.config.rules.get(name)
            if rule is None or rule.primitive != "signature":
                continue
            if "exposes" not in rule_kinds(rule, self.ctx.config.views):
                continue
            out.append(source_part((rule.from_, rule.select)))
        return out

    def signature_sees(self, module: str) -> bool:
        """Whether a signature rule sees the slots of a module's public callables. A module a
        rule cannot tell about is not seen, so its slots are judged here."""
        seen = self.slots_seen.get(module)
        if seen is None:
            resolve = self.ctx.module_env(module).resolve
            seen = any(node is None or evaluate(node, resolve) is Truth.TRUE for node in self.slot_owners)
            self.slots_seen[module] = seen
        return seen

    def plan(self) -> dict[str, Any]:
        """The exports it checks by place, the contexts it reads and leaves out, and how it
        reports what it cannot tell."""
        spec = self.spec
        places = list(self.ctx.config.exports)
        checked = {
            "exports": [places.index(export.name) for export in self.selected_exports()],
            "include": context_bits(self.include),
            "exclude": context_bits(self.exclude),
            "unknown_types": list(spec.unknown_types),
            "on_unknown": spec.on_unknown,
            "on_unresolved": spec.on_unresolved,
            "start": selector_tree(spec.from_),
            "slot_owners": [None if node is None else tree(node) for node in self.slot_owners],
        }
        return {"id": spec.id, "mode": self.NATIVE_MODE, "identity": identity_paths(spec.identity), "export": checked}

    def covered_modules(self) -> dict[str, list[tuple[str, str]]]:
        """(module, path) of every module each export covers, in module order: one pass over the
        modules for every export, and only paths that start with an export's `within` are asked."""
        exports = self.ctx.config.exports.values()
        prefixes = tuple(compile_label_glob(f"{spec.within}/**").prefix if spec.within else "" for spec in exports)
        out: dict[str, list[tuple[str, str]]] = {}
        for module, path in self.ctx.labeler.index.sorted_modules:
            if not path.startswith(prefixes):
                continue
            for name in self.ctx.labeler.path_exports(path):
                out.setdefault(name, []).append((module, path))
        return out

    def judges(self, module: str) -> bool:
        """Whether the rule judges what a module exports: `from`, read of the module alone. A
        module it cannot tell about is judged."""
        if self.spec.from_ is None:
            return True
        return self.spec.from_.evaluate(self.ctx.module_env(module).resolve) is not Truth.FALSE

    def evaluate(self) -> RuleRun:
        found = self.ctx.export_run(self)
        if found is not None:
            return found
        labeler = self.ctx.labeler
        covered = self.covered_modules()
        for export in self.selected_exports():
            policy = self.ctx.config.shapes[export.shape]
            for module, path in covered.get(export.name, []):
                if not self.judges(module):
                    continue
                for symbol, edge in self.exported_symbols(module):
                    member = labeler.export_member((export.name, path, symbol))
                    if member is Truth.FALSE:
                        continue
                    if member is Truth.UNKNOWN:
                        self.unknown(symbol, module, policy, (symbol,), edge, "not-member", export.name)
                        continue
                    self.check(symbol, module, policy, edge, (symbol,))
        return self.run

    def exported_symbols(self, module: str) -> list[tuple[str, Edge | None]]:
        """The module's public top-level definitions, then the imports it hands out
        (`Graph.hand_outs`): an import it keeps to itself is no part of what it exports."""
        out: list[tuple[str, Edge | None]] = [(symbol, None) for symbol in self.top_level.get(module, [])]
        for edge in self.ctx.labeler.handed_imports(module):
            if not context_ok(edge, self.include, self.exclude):
                continue
            if edge.resolution is Resolution.UNRESOLVED or not edge.targets:
                self.unresolved(edge, module)
                continue
            for target in edge.targets:
                if target.symbol:
                    out.append((str(target), edge))
        return out

    # ── shapes ──

    def recognize(self, symbol: str) -> tuple[frozenset[str], bool]:
        """Shape names of a symbol, and whether hoglint saw enough facts to be sure."""
        found = self.shapes.get(symbol)
        if found is None:
            found = self.shapes[symbol] = self.find_shapes(symbol)
        return found

    def find_shapes(self, symbol: str) -> tuple[frozenset[str], bool]:
        names: set[str] = set()
        kind, _ = self.ctx.labeler.kind(symbol, first_party=self.facts.has(symbol))
        if kind.state is State.KNOWN:
            names.add(str(kind.data))
        if not self.facts.has(symbol):
            return frozenset(names), kind.state is not State.UNKNOWN
        ancestors, complete = self.facts.ancestors(symbol)
        lineage = ancestors | {symbol}
        decl = self.facts.decl(symbol)
        if decl == "function":
            names.add("function")
        if decl == "assignment":
            names.add("constant")
        if decl == "class":
            names.add("class")
        if self.facts.is_dataclass(symbol, self.dataclass_kinds):
            names.add("dataclass")
            if self.facts.is_frozen(symbol, self.dataclass_kinds):
                names.add("frozen-dataclass")
        names.update(name for name, bases in SHAPE_BASES.items() if lineage & bases)
        if self.facts.get(symbol, "metaclass") in ABC_METAS:
            names.add("abc")
        return frozenset(names), complete and kind.state is not State.UNKNOWN

    def symbol_truth(self, selector: Selector, symbol: str) -> Truth:
        """A `[shapes]` entity selector, read of a symbol in the module that defines it."""
        labeler = self.ctx.labeler
        return selector.evaluate(labeler.entity_resolver(labeler.module_of_symbol(symbol), symbol))

    def allowed_as_is(self, symbol: str, policy: ShapePolicy) -> bool:
        """Whether the policy's `allow_where` holds for a symbol: it is handed out as it stands."""
        if policy.allow_where is None:
            return False
        key = (policy.name, symbol)
        found = self.allowed.get(key)
        if found is None:
            found = self.allowed[key] = self.symbol_truth(policy.allow_where, symbol) is Truth.TRUE
        return found

    def walks(self, symbol: str, shapes: frozenset[str], policy: ShapePolicy) -> bool:
        """Whether what an allowed class's members name is judged: always the fields of a data
        shape; in a contract the members of any other allowed class, unless `walk_where` is false.
        Where it cannot be told the class is walked, as `allow_where` excuses only where it holds."""
        dataclass_like = bool(shapes & {"dataclass", "frozen-dataclass"})
        if dataclass_like and shapes & set(policy.data):
            return True
        if not (policy.signatures or dataclass_like):
            return False
        return self.walk_holds(symbol, policy)

    def walk_holds(self, symbol: str, policy: ShapePolicy) -> bool:
        """Whether `walk_where` holds for a class, or cannot be told."""
        if policy.walk_where is None:
            return True
        key = (policy.name, symbol)
        found = self.walked.get(key)
        if found is None:
            found = self.walked[key] = self.symbol_truth(policy.walk_where, symbol) is not Truth.FALSE
        return found

    def ancestor_walked(self, ancestor: str, policy: ShapePolicy) -> bool:
        """Whether the member rows of an ancestor of a walked class are read: always the fields of
        a data shape, else where `walk_where` holds for the ancestor, so what a product class
        inherits from a class core defines is core's contract."""
        shapes, _ = self.recognize(ancestor)
        if shapes & {"dataclass", "frozen-dataclass"} and shapes & set(policy.data):
            return True
        return self.walk_holds(ancestor, policy)

    def behavior_ok(self, symbol: str, policy: ShapePolicy) -> bool:
        behavior = policy.behavior
        if behavior is None:
            return False
        ancestors, _ = self.facts.ancestors(symbol)
        for candidate in sorted(ancestors | {symbol}):
            shapes, _ = self.recognize(candidate)
            if behavior.kind not in shapes:
                continue
            if behavior.where is None:
                return True
            if self.symbol_truth(behavior.where, candidate) is Truth.TRUE:
                return True
        return False

    # ── recursion ──

    def check(
        self,
        symbol: str,
        module: str,
        policy: ShapePolicy,
        edge: Edge | None,
        trail: tuple[str, ...],
        shape_only: bool = False,
    ) -> None:
        """Findings are keyed by (symbol, module), so a symbol already checked for this module and
        policy adds nothing the second time. A check of the shape alone (`shape_only`) that finds
        a denied kind or an unknown type marks nothing: a full check may still report it."""
        visit = (module, policy.name, symbol)
        if visit in self.visited:
            return
        if self.allowed_as_is(symbol, policy):
            self.visited.add(visit)
            return
        shapes, complete = self.recognize(symbol)
        denied = sorted(shapes & set(policy.deny))
        if shape_only and (denied or symbol in self.spec.unknown_types):
            return
        self.visited.add(visit)
        if denied:
            self.finding(symbol, module, f"export-denied({denied[0]})", trail, edge)
            return
        if symbol in self.spec.unknown_types:
            self.unknown(symbol, module, policy, trail, edge, "unknown-type")
            return
        if not self.facts.has(symbol):
            if not complete:
                self.unknown(symbol, module, policy, trail, edge, "no-facts")
            return
        if "class" in shapes:
            allowed = bool(shapes & {*policy.data, *policy.allow}) or self.behavior_ok(symbol, policy)
            if not allowed:
                if not complete:
                    self.unknown(symbol, module, policy, trail, edge, "bases")
                    return
                self.finding(symbol, module, "export-shape(class)", trail, edge)
                return
            if self.walks(symbol, shapes, policy):
                self.check_members(symbol, module, policy, trail, edge)
            return
        if "function" in shapes:
            self.check_members(symbol, module, policy, trail, edge)

    def exposes_under(self, module: str) -> dict[str, list[int]]:
        """A module's exposes edges, as handles, under every qualname their callable lies in: `A`
        holds the edges of `A`, `A.f` and `A.B.g`, in the module's order."""
        found = self.exposes_by_name.get(module)
        if found is None:
            found = self.exposes_by_name[module] = {}
            for callable_name, handle in self.exposes.get(module, []):
                parts = callable_name.split(".")
                for end in range(1, len(parts) + 1):
                    found.setdefault(".".join(parts[:end]), []).append(handle)
        return found

    def edges(self, handles: list[int]) -> list[Edge]:
        """The edges at `handles`, each made once: only member edges a check reads become Edges."""
        missing = list(dict.fromkeys(handle for handle in handles if handle not in self.made))
        for handle, edge in zip(missing, self.index.edges_at(missing)):
            self.made[handle] = edge
        return [self.made[handle] for handle in handles]

    def member_edges(self, symbol: str, include_bases: bool, policy: ShapePolicy) -> list[tuple[Edge, str, str]]:
        """Exposes edges of a callable, or of a class's fields and methods (and those of the bases
        the policy walks), each with the module and qualname of the class or callable it belongs to."""
        key = (symbol, include_bases, policy.name)
        found = self.members.get(key)
        if found is None:
            places = self.member_handles(symbol, include_bases, policy)
            edges = self.edges([handle for handle, _, _ in places])
            found = self.members[key] = [(edge, owner, qualname) for edge, (_, owner, qualname) in zip(edges, places)]
        return found

    def member_handles(self, symbol: str, include_bases: bool, policy: ShapePolicy) -> list[tuple[int, str, str]]:
        lineage = [symbol]
        if include_bases:
            ancestors, _ = self.facts.ancestors(symbol)
            lineage.extend(ancestor for ancestor in sorted(ancestors) if self.ancestor_walked(ancestor, policy))
        out: list[tuple[int, str, str]] = []
        for target in lineage:
            owner = self.ctx.labeler.module_of_symbol(target)
            if owner is None or owner == target:
                continue
            qualname = target[len(owner) + 1 :]
            out.extend((handle, owner, qualname) for handle in self.exposes_under(owner).get(qualname, ()))
        return out

    def holds_copy(self, module: str, callable_name: str, slot: str) -> bool:
        """Whether a module holds an exposes row of a callable's slot: the copy the producer
        writes where a surface hands out a function or a data shape's fields."""
        handles = self.exposes_under(module).get(callable_name, [])
        return any(edge.attr("callable") == callable_name and edge.attr("slot") == slot for edge in self.edges(handles))

    def leaves_slot(self, row: Edge, owner: str, qualname: str, module: str, bound: str) -> bool:
        """Whether a slot is left to a signature rule: a public callable's slot whose row that rule
        reads. The row's own module is one it holds for, or the exporting module holds the copy of
        the slot under the name it binds."""
        if not SignaturePrimitive.sees_callable(row):
            return False
        if self.signature_sees(owner):
            return True
        if not bound or not self.signature_sees(module):
            return False
        return self.holds_copy(module, bound + row.attr("callable")[len(qualname) :], row.attr("slot"))

    def check_members(
        self, symbol: str, module: str, policy: ShapePolicy, trail: tuple[str, ...], edge: Edge | None
    ) -> None:
        """The types in the slots of a member, at any depth. The rule judges only the shape of a
        class where a signature rule of `leave_slots_to` reads the slot's row; `edge` is the import
        the module hands the symbol out by, if any, which names the copy of a slot."""
        bound = edge.attr("binds") if edge is not None and len(trail) == 1 else ""
        include_bases = self.facts.decl(symbol) == "class"
        for row, owner, qualname in self.member_edges(symbol, include_bases, policy):
            if row.attr("alias") == "expanded":
                continue
            shape_only = self.leaves_slot(row, owner, qualname, module, bound)
            if row.attr("annotation") == "missing":
                if not shape_only:
                    self.unknown(symbol, module, policy, trail, row, "no-annotation", row.attr("slot"))
                continue
            if row.resolution is Resolution.UNRESOLVED:
                if not shape_only:
                    self.unresolved(row, module)
                continue
            names = [str(target) for target in row.targets] or [row.surface]
            for name in names:
                if name and name not in trail:
                    self.check(name, module, policy, row, (*trail, name), shape_only)

    # ── output ──

    def resolver(self, symbol: str, module: str, label: str) -> Resolver:
        labeler = self.ctx.labeler

        def resolve(ref: Ref) -> Value:
            root, name = (ref.path[0], ref.path[-1]) if len(ref.path) == 2 else ("", ref.path[-1])
            if root == "edge":
                return Value.known(label if name == "label" else "export") if name in ("label", "kind") else NONE
            if root == "dst":
                if name == "symbol":
                    return Value.known(symbol)
                return labeler.entity_resolver(labeler.module_of_symbol(symbol), symbol)(Ref((name,)))
            if name in ("module", "symbol", "subject"):
                return Value.known(module)
            return labeler.entity_resolver(module, None)(Ref((name,)))

        return resolve

    def finding(
        self,
        symbol: str,
        module: str,
        label: str,
        trail: tuple[str, ...],
        edge: Edge | None,
        ledgerable: bool = True,
        unknown: bool = False,
    ) -> None:
        key = (symbol, module, label)
        if key in self.reported:
            return
        self.reported.add(key)
        finding = self.ctx.finding(
            self.spec,
            project(self.spec.identity, self.resolver(symbol, module, label)),
            self.message(module, trail, label),
            module,
            (module, self.ctx.labeler.module_of_symbol(symbol) or symbol),
            (edge,) if edge is not None else (),
            ledgerable=ledgerable,
            unknown=unknown,
        )
        self.run.findings.append(finding)

    def unknown(
        self,
        symbol: str,
        module: str,
        policy: ShapePolicy,
        trail: tuple[str, ...],
        edge: Edge | None,
        why: str,
        detail: str = "",
    ) -> None:
        mode = "incomplete" if policy.unknown == "incomplete" else self.spec.on_unknown
        if mode == "incomplete":
            self.run.incomplete.append(self.unknown_item(self.spec.id, module, symbol, trail, why, detail))
            return
        self.finding(symbol, module, "export-unknown", trail, edge, ledgerable=mode == "finding", unknown=True)

    def unresolved(self, edge: Edge, module: str) -> None:
        if self.spec.on_unresolved == "incomplete":
            self.run.incomplete.append(self.unresolved_item(self.spec.id, module, edge.surface))
            return
        ledgerable = self.spec.on_unresolved == "finding"
        self.finding(edge.surface, module, f"unresolved-{edge.kind}", (edge.surface,), edge, ledgerable, unknown=True)
