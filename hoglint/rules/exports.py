"""The export primitive: does an export hand out a denied shape?

The check is recursive. An exported class must have an allowed shape, and the types in its
fields, its bases' fields and its methods' signatures must too. An exported function's
signature types are checked the same way. A module exports its public definitions and the
imports it hands out; a re-export is judged at its origin, so a shim module hides nothing.
Shape names are core recognizers plus the config's [kinds].

Membership is the labeler's: an export's paths, narrowed by `subclass_of`. The audience is not
read here: it limits who may use an export, not what the export hands out, so every member is
checked whatever its audience. `from` leaves out the export modules it is false for.

The rule judges only the shape of a class named in a slot of a member where a signature rule
it names in `slots_reported_by` reads that slot's row: a denied kind, an unknown type, a
missing annotation or an unresolved name there is the signature rule's to report. A signature rule
reads the rows of a public callable in a module its `from` and the source-only parts of its
`select` hold for; the other parts of its selection judge the slot's type. So a slot is left to it
where the row's own module is such a module, or where the exporting module is one and holds the
producer's copy of the slot. This holds at every depth: the fields of a class a slot names are left
to the signature rule where it reads their rows. A slot no signature rule reads, such as a method of
a class handed out from outside a facade, is judged in full, with one slot policy: the parts of the
named rules' selections that do not read only the source. A type the row names that every named
rule's selection leaves out (an opaque payload) is not judged as unknown here either.

The native evaluator walks it (`src/link/rules/export.rs`). This module holds the rule's metadata,
the plan the evaluator reads, and the wording of the rows it returns.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from hoglint.config.model import ExportSpec, RuleSpec
from hoglint.labels import NOT_HANDED_CONTEXTS
from hoglint.ledger.errors import EngineError
from hoglint.model import context_bits
from hoglint.rules.base import IncompleteItem, RuleContext, RuleRun, RuleType, rule_kinds
from hoglint.rules.plan import identity_paths, selector_tree, tree
from hoglint.selectors import Node, rest_part, source_part

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
    plan: these lists are the only copy."""
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
    "unread-annotation": "{detail} has a part of its annotation hoglint cannot read",
    "alias": "a name hoglint does not expand into types",
    "listed": "__all__ lists it, but hoglint cannot tell what it binds",
    "value": "a module-level value that is no literal constant",
}


@dataclass(frozen=True, slots=True)
class SlotRule:
    """A signature rule of `slots_reported_by`: where it reads rows (the parts of its selection that
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
    """The export rule as data: its metadata, the plan the native evaluator walks
    (`src/link/rules/export.rs`), and the wording of the rows the evaluator returns."""

    PRIMITIVE = MODE = "export"
    NATIVE_MODE = "export"
    READS = frozenset({"exports", "contexts", "from", "slots_reported_by", "on_unknown", "on_unresolved"})

    def __init__(self, ctx: RuleContext, spec: RuleSpec) -> None:
        self.ctx = ctx
        self.spec = spec
        self.run = RuleRun(spec)
        self.include = frozenset(spec.contexts_include)
        self.exclude = frozenset(NOT_HANDED_CONTEXTS if spec.contexts_exclude is None else spec.contexts_exclude)
        self.slot_rules = self.named_slot_rules()

    @staticmethod
    def message(module: str, trail: Sequence[str], label: str) -> str:
        """A finding's message, worded from the evaluator's row."""
        return f"{module} exports {' -> '.join(trail)}: {label}"

    @staticmethod
    def unknown_item(
        rule: str, module: str, symbol: str, trail: Sequence[str], why: str, detail: str
    ) -> IncompleteItem:
        """What a symbol the rule cannot tell about leaves incomplete; `why` is a code of
        `UNKNOWN_WHY`, and an annotation's `detail` is its slot."""
        if why in ("no-annotation", "unread-annotation"):
            detail = detail or "a slot"
        reason = UNKNOWN_WHY[why].format(detail=detail)
        return IncompleteItem(rule, f"{module}|{symbol}", f"{' -> '.join(trail)}: {reason}", (module,))

    @staticmethod
    def unresolved_item(rule: str, module: str, surface: str) -> IncompleteItem:
        """What an import that did not resolve leaves incomplete."""
        return IncompleteItem(rule, f"{module}|{surface}", f"{surface!r} did not resolve", (module,))

    def selected_exports(self) -> list[ExportSpec]:
        names = self.spec.exports or tuple(self.ctx.config.exports)
        return [self.ctx.config.exports[name] for name in names]

    def named_slot_rules(self) -> list[SlotRule]:
        """The signature rules of `slots_reported_by` that read exposes rows, each with its selection
        split into where it reads rows and which slots it judges. The config check refuses a name
        that is no signature rule."""
        out: list[SlotRule] = []
        for name in self.spec.slots_reported_by:
            rule = self.ctx.config.rules[name]
            if "exposes" not in rule_kinds(rule, self.ctx.config.views):
                continue
            selection = (rule.from_, rule.select)
            include, exclude = frozenset(rule.contexts_include), frozenset(rule.contexts_exclude or ())
            out.append(SlotRule(name, source_part(selection), rest_part(selection), rule.endpoint, include, exclude))
        return out

    def plan(self) -> dict[str, Any]:
        """The exports it checks by place, the contexts it reads and leaves out, and how it
        reports what it cannot tell."""
        spec = self.spec
        places = list(self.ctx.config.exports)
        checked = {
            "exports": [places.index(export.name) for export in self.selected_exports()],
            "include": context_bits(self.include),
            "exclude": context_bits(self.exclude),
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

    def evaluate(self) -> RuleRun:
        """The evaluator's check of the rule. Only the native evaluator walks what an export
        hands out; a context without it cannot check the rule."""
        found = self.ctx.export_run(self)
        if found is None:
            raise EngineError(f"rule {self.spec.id}: the export rule runs in the native evaluator only")
        return found
