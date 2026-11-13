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
a class handed out from outside a facade, is judged in full, with one slot policy: the parts of the
named rules' selections that do not read only the source. A type the row names that every named
rule's selection leaves out (an opaque payload) is not judged as unknown here either.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from hoglint.config.model import ExportSpec, RuleSpec, ShapePolicy
from hoglint.globs import compile_label_glob
from hoglint.labels import NOT_HANDED_CONTEXTS
from hoglint.ledger.envelope import ConfigInputs
from hoglint.model import Edge, Resolution, context_bits
from hoglint.rules.base import EdgeEnv, IncompleteItem, RuleContext, RuleRun, RuleType, context_ok, project, rule_kinds
from hoglint.rules.edges import is_private
from hoglint.rules.plan import identity_paths, selector_tree, tree
from hoglint.selectors import NONE, Node, Ref, Resolver, Selector, State, Truth, Value, evaluate, rest_part, source_part

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
    "alias": "a name hoglint does not expand into types",
}


@dataclass(frozen=True, slots=True)
class SlotRead:
    """How a member's slot reached a symbol: left to a named rule, which judges it (only the
    class's shape is judged here), or judged here. An exported symbol itself is reached by no slot
    (None)."""

    left: bool


@dataclass(frozen=True, slots=True)
class SlotRule:
    """A signature rule of `leave_slots_to`: where it reads rows (the parts of its selection that
    read only the source; None for every module), which slots it judges (the other parts; None for
    every slot), the endpoint it reads a row's destination by, and the contexts it reads and
    leaves out."""

    name: str
    scope: Node | None
    rest: Node | None
    endpoint: str
    include: frozenset[str]
    exclude: frozenset[str]


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
        self.members: dict[tuple[str, str], list[tuple[Edge, str, str]]] = {}
        self.exposes_by_name: dict[str, dict[str, list[int]]] = {}
        self.made: dict[int, Edge] = {}
        self.include = frozenset(spec.contexts_include)
        self.exclude = frozenset(NOT_HANDED_CONTEXTS if spec.contexts_exclude is None else spec.contexts_exclude)
        self.slot_rules = self.named_slot_rules()
        # The named rules whose source part holds for a module (`holding_rules`), per module.
        self.holding: dict[str, tuple[SlotRule, ...]] = {}
        self.slot_policy_answers: dict[int, bool] = {}
        self.copy_index: dict[str, dict[str, list[int]]] = {}
        # A callable's rows by slot, so a class's fields are not scanned once per field.
        self.copy_rows: dict[tuple[str, str], dict[str, list[Edge]]] = {}
        # (module, public name) of every hand-out of a symbol by the files of the export and root of
        # the module in hand, read before the walk, so a slot finds a copy any of them holds.
        self.handing: dict[str, list[tuple[str, str]]] = {}

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

    def named_slot_rules(self) -> list[SlotRule]:
        """The signature rules of `leave_slots_to` that read exposes rows, each with its selection
        split into where it reads rows and which slots it judges. The config check refuses a name
        that is no signature rule."""
        out: list[SlotRule] = []
        for name in self.spec.leave_slots_to:
            rule = self.ctx.config.rules[name]
            if "exposes" not in rule_kinds(rule, self.ctx.config.views):
                continue
            selection = (rule.from_, rule.select)
            include, exclude = frozenset(rule.contexts_include), frozenset(rule.contexts_exclude or ())
            out.append(SlotRule(name, source_part(selection), rest_part(selection), rule.endpoint, include, exclude))
        return out

    def holding_rules(self, module: str) -> tuple[SlotRule, ...]:
        """The signature rules of `leave_slots_to` whose source part holds for a module. A module a
        rule cannot tell about is not held, so its slots are judged here."""
        found = self.holding.get(module)
        if found is None:
            resolve = self.ctx.module_env(module).resolve
            found = self.holding[module] = tuple(
                rule for rule in self.slot_rules if rule.scope is None or evaluate(rule.scope, resolve) is Truth.TRUE
            )
        return found

    def signature_sees(self, module: str, row: Edge) -> bool:
        """Whether a signature rule reads a row in a module: its source part holds for the module
        and its contexts take the row's."""
        return any(context_ok(row, rule.include, rule.exclude) for rule in self.holding_rules(module))

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
            "slot_owners": [None if rule.scope is None else tree(rule.scope) for rule in self.slot_rules],
            "slot_policies": [
                {
                    "rest": None if rule.rest is None else tree(rule.rest),
                    "endpoint": rule.endpoint,
                    "include": context_bits(rule.include),
                    "exclude": context_bits(rule.exclude),
                }
                for rule in self.slot_rules
            ],
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
            modules = covered.get(export.name, [])
            handed_by_root = self.hand_out_index(export, modules)
            for module, path in modules:
                if not self.judges(module):
                    continue
                self.handing = handed_by_root.get(export.files.covered_root(path) or "", {})
                exported = self.exported_symbols(module)
                for symbol, edge in exported:
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

    def hand_out_index(
        self, export: ExportSpec, modules: list[tuple[str, str]]
    ) -> dict[str, dict[str, list[tuple[str, str]]]]:
        """Per captured root of an export, (module, name) of every public name a file of that root
        binds a handed-out import's symbol to, in module order: the places the producer may hold a
        copy of the symbol's slots. Every file counts, also one the rule does not judge."""
        out: dict[str, dict[str, list[tuple[str, str]]]] = {}
        for module, path in modules:
            by_symbol = out.setdefault(export.files.covered_root(path) or "", {})
            for edge in self.ctx.labeler.handed_imports(module):
                name = edge.attr("binds")
                if not name or is_private(name) or not context_ok(edge, self.include, self.exclude):
                    continue
                if edge.resolution is Resolution.UNRESOLVED:
                    continue
                for target in edge.targets:
                    if not target.symbol:
                        continue
                    places = by_symbol.setdefault(str(target), [])
                    if (module, name) not in places:
                        places.append((module, name))
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

    @staticmethod
    def constructor_slot(row: Edge) -> bool:
        """Whether a row is a field of a dataclass-like class's generated `__init__`, or the marker
        of a base whose fields are unknown: the producer writes them only for such a class."""
        slot = row.attr("slot")
        return slot.startswith("field:") or slot == "fields"

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
        slot: SlotRead | None = None,
    ) -> None:
        """Findings are keyed by (symbol, module), so a symbol already checked for this module and
        policy adds nothing the second time. A check of a slot left to a named rule marks only a
        class or a function, whose walk a check of a slot judged here would repeat: a later full
        check may still report anything else. Nor does an unknown type in a slot judged here
        (`edge` is its row) that every named rule's policy leaves out mark it. An assignment there
        that the producer did not expand is unknown."""
        visit = (module, policy.name, symbol)
        if visit in self.visited:
            return
        if self.allowed_as_is(symbol, policy):
            self.visited.add(visit)
            return
        shapes, complete = self.recognize(symbol)
        denied = sorted(shapes & set(policy.deny))
        unknown_type = symbol in self.spec.unknown_types
        if slot is not None and slot.left and (denied or unknown_type):
            return
        judged = slot is not None and not slot.left
        if judged and edge is not None and not denied and unknown_type and self.slot_policy_leaves_out(edge):
            return
        if judged or slot is None or shapes & {"class", "function"}:
            self.visited.add(visit)
        if denied:
            self.finding(symbol, module, f"export-denied({denied[0]})", trail, edge)
            return
        if unknown_type:
            self.unknown(symbol, module, policy, trail, edge, "unknown-type")
            return
        unexpanded = self.unexpanded_assignment(symbol, shapes, slot)
        if not self.facts.has(symbol):
            if not complete:
                self.unknown(symbol, module, policy, trail, edge, "no-facts")
            elif unexpanded:
                self.unknown(symbol, module, policy, trail, edge, "alias")
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
                if not complete:
                    self.unknown(symbol, module, policy, trail, edge, "bases")
                self.check_members(symbol, module, policy, trail)
            return
        if "function" in shapes:
            self.check_members(symbol, module, policy, trail)
            return
        if unexpanded:
            self.unknown(symbol, module, policy, trail, edge, "alias")

    def unexpanded_assignment(self, symbol: str, shapes: frozenset[str], slot: SlotRead | None) -> bool:
        """Whether a slot judged here names an assignment the producer did not expand: a symbol a
        pack reads as a constant, or a name with no facts (no class, no def) that the producer
        resolved in a graph module, at its top or in a class body."""
        if slot is None or slot.left:
            return False
        if self.facts.has(symbol):
            return "constant" in shapes
        return self.place_of(symbol) is not None

    def place_of(self, symbol: str) -> tuple[str, str] | None:
        """The graph module a symbol lies in and its qualname there; None for a module itself or a
        name no graph module holds."""
        owner = self.ctx.labeler.module_of_symbol(symbol)
        if owner is None or owner == symbol:
            return None
        return owner, symbol[len(owner) + 1 :]

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
        if missing:
            for handle, edge in zip(missing, self.index.edges_at(missing)):
                self.made[handle] = edge
        return [self.made[handle] for handle in handles]

    def member_edges(self, symbol: str, policy: ShapePolicy) -> list[tuple[Edge, str, str]]:
        """The rows of a callable, or of a class's fields and methods (and those of the bases the
        policy walks), that are judged: each with the module and qualname of the class or callable
        it belongs to. The marker row of an expanded alias is left out: the rows of its expansion
        stand for it, and each of them is judged by its own target."""
        key = (symbol, policy.name)
        found = self.members.get(key)
        if found is None:
            places = self.member_handles(symbol, policy)
            edges = self.edges([handle for handle, _, _, _ in places])
            found = self.members[key] = [
                (edge, owner, qualname)
                for edge, (_, owner, qualname, walked) in zip(edges, places)
                if (walked or self.constructor_slot(edge)) and edge.attr("alias") != "expanded"
            ]
        return found

    def member_handles(self, symbol: str, policy: ShapePolicy) -> list[tuple[int, str, str, bool]]:
        """(handle, module, qualname, walked) of each row of the symbol and, for a class, of its
        ancestors. Of a class `walk_where` is false for, the handed-out class or an ancestor, only
        the constructor fields are read: they are slots of the class, while its methods and
        attributes are core's contract."""
        is_class = self.facts.decl(symbol) == "class"
        lineage = [(symbol, not is_class or self.walk_holds(symbol, policy))]
        if is_class:
            ancestors, _ = self.facts.ancestors(symbol)
            lineage.extend((ancestor, self.walk_holds(ancestor, policy)) for ancestor in sorted(ancestors))
        out: list[tuple[int, str, str, bool]] = []
        for target, walked in lineage:
            place = self.place_of(target)
            if place is None:
                continue
            owner, qualname = place
            out.extend((handle, owner, qualname, walked) for handle in self.exposes_under(owner).get(qualname, ()))
        return out

    def copies(self, module: str, callable_name: str, slot: str) -> list[Edge]:
        """A module's exposes rows of a callable's slot: the copy the producer writes where a
        surface hands out a function or a data shape's fields."""
        by_slot = self.copy_rows.get((module, callable_name))
        if by_slot is None:
            by_callable = self.copy_index.get(module)
            if by_callable is None:
                by_callable = self.copy_index[module] = {}
                for name, handle in self.exposes.get(module, []):
                    by_callable.setdefault(name, []).append(handle)
            by_slot = self.copy_rows[(module, callable_name)] = {}
            for edge in self.edges(by_callable.get(callable_name, [])):
                if edge.attr("callable") == callable_name:
                    by_slot.setdefault(edge.attr("slot"), []).append(edge)
        return by_slot.get(slot, [])

    def copy_places(self, symbol: str, policy: ShapePolicy) -> list[tuple[str, str]]:
        """(module, name) of each place the producer may hold a copy of the walked symbol's slots,
        among the surfaces of the walk's shape policy: each file of the export and root of the
        module in hand under each public name it hands the symbol out by, and the module that
        defines the symbol under its own name (a contract dataclass's fields along its MRO). The
        same for every row of one visit."""
        out = list(self.handing.get(symbol, []))
        place = self.place_of(symbol)
        if place is not None:
            out.append(place)
        return [(place, name) for place, name in out if self.under_policy(place, policy)]

    def under_policy(self, module: str, policy: ShapePolicy) -> bool:
        """Whether an export of the shape policy covers a module."""
        path = self.ctx.labeler.path_of(module)
        names = self.ctx.labeler.path_exports(path) if path is not None else frozenset()
        return any(self.ctx.config.exports[name].shape == policy.name for name in names)

    def reads_under(self, module: str, policy: ShapePolicy, row: Edge) -> bool:
        """Whether a signature rule reads a row in a module for this walk: it reads the row there,
        and the module is a surface of the shape policy the walk runs under, so the rule judges
        the slot as this policy would."""
        return self.under_policy(module, policy) and self.signature_sees(module, row)

    def leaves_slot(
        self, row: Edge, owner: str, qualname: str, places: list[tuple[str, str]], policy: ShapePolicy
    ) -> bool:
        """Whether a slot is left to a signature rule: a public callable's slot whose row that rule
        reads. The row's own module is one it reads for this walk, or one of the visit's `places`
        holds the copy of the slot; "public" is asked of the callable of the row the rule reads."""
        if not self.slot_rules:
            return False
        if not is_private(row.attr("callable")) and self.reads_under(owner, policy, row):
            return True
        rest = row.attr("callable")[len(qualname) :]
        for place, name in places:
            copy = name + rest
            if is_private(copy):
                continue
            if any(self.signature_sees(place, found) for found in self.copies(place, copy, row.attr("slot"))):
                return True
        return False

    def slot_policy_leaves_out(self, row: Edge) -> bool:
        """Whether every named signature rule's slot policy is false for a row: the type it
        mentions is no part of what a slot may not hold (an opaque payload), so it is not judged
        as an unknown type here either. Unknown counts as true. Worked out once per row."""
        found = self.slot_policy_answers.get(row.handle)
        if found is None:
            found = self.slot_policy_answers[row.handle] = self.find_slot_policy_leaves_out(row)
        return found

    def find_slot_policy_leaves_out(self, row: Edge) -> bool:
        if not self.slot_rules:
            return False
        src = self.ctx.src_endpoint(row.site)
        for rule in self.slot_rules:
            if rule.rest is None:
                return False
            dsts = self.ctx.dst_endpoints(row, rule.endpoint)
            if not dsts:
                return False
            for dst in dsts:
                if evaluate(rule.rest, EdgeEnv(self.ctx, row, src, dst).resolve) is not Truth.FALSE:
                    return False
        return True

    def check_members(self, symbol: str, module: str, policy: ShapePolicy, trail: tuple[str, ...]) -> None:
        """The types in the slots of a member, at any depth. The rule judges only the shape of a
        class where a signature rule of `leave_slots_to` reads the slot's row; a name a file of the
        same export and root hands the symbol out by names the copy of a slot."""
        places = self.copy_places(symbol, policy)
        for row, owner, qualname in self.member_edges(symbol, policy):
            shape_only = self.leaves_slot(row, owner, qualname, places, policy)
            if row.attr("annotation") == "missing":
                if not shape_only:
                    self.unknown(symbol, module, policy, trail, row, "no-annotation", row.attr("slot"))
                continue
            if row.resolution is Resolution.UNRESOLVED:
                if not shape_only:
                    self.unresolved(row, module)
                continue
            read = SlotRead(left=shape_only)
            names = [str(target) for target in row.targets] or [row.surface]
            for name in names:
                if name and name not in trail:
                    self.check(name, module, policy, row, (*trail, name), read)

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
