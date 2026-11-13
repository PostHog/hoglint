"""Who reports a type slot of a contract surface, and how many times, on small product trees.

Each case is a tree (a base plus a few files) and a verdict: how many findings each rule gives,
`export-shape` (ES) and `facade-signature` (FS), and a third signature rule where a case adds one.
No case may fail a producer. The first part is the slot table of
docs/design/09-contract-surfaces.md, cell by cell; the second part is the forms that attack it;
the third part is the decided verdicts of holes that are not built yet, run as expected failures.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import pytest

from hoglint import facts
from hoglint.core import analysis as core_analysis
from hoglint.native import analysis as native_analysis
from hoglint.native.parity import read_lines
from rules.support import PRESET

from native.test_contract_slots import clean_dump
from native.test_facade_rules import MODEL, ROOT_CONFIG, SITE_PACKAGES

D = "products/shop/backend"
S = "products.shop.backend"
# Core classes a product class can inherit from or a facade can hand out as they stand.
CORE = (
    "import dataclasses\nfrom typing import Protocol\n\nfrom pydantic import BaseModel\n\n"
    "from products.shop.backend.models import Order\n\n\n"
    "class CoreError(Exception):\n    def core_order(self) -> Order:\n        raise ValueError\n\n"
    "    def shared(self) -> Order:\n        raise ValueError\n\n\n"
    "class CoreRow(BaseModel):\n    core_field: Order\n\n    def core_method(self) -> Order:\n        raise ValueError\n\n\n"
    "@dataclasses.dataclass\nclass CorePlainData:\n    plain_field: Order\n\n\n"
    "class CoreSink(Protocol):\n    def push(self, order: Order) -> None: ...\n"
)
# `Plain` has no allowed shape; `Fine` is a frozen dataclass of builtins.
KINDS = (
    "import dataclasses\n\n\nclass Plain:\n    pass\n\n\n"
    "@dataclasses.dataclass(frozen=True)\nclass Fine:\n    total: int\n"
)
BASE = {
    "hoglint.toml": ROOT_CONFIG,
    "tools/hoglint/presets/posthog.toml": PRESET.read_text(),
    "posthog/__init__.py": "",
    "posthog/settings.py": "INSTALLED_APPS = ['posthog', 'products.shop.backend']\n",
    "posthog/corelib.py": CORE,
    "posthog/models/__init__.py": "",
    "posthog/models/team/__init__.py": "",
    "posthog/models/team/team.py": MODEL.format(name="Team"),
    "products/__init__.py": "",
    "products/shop/__init__.py": "",
    f"{D}/__init__.py": "",
    f"{D}/models.py": MODEL.format(name="Order"),
    f"{D}/kinds.py": KINDS,
    f"{D}/facade/__init__.py": "",
}

EXPORT = "export-shape"
SIGNATURE = "facade-signature"
SECOND = "second-signature"
RULES = (EXPORT, SIGNATURE, SECOND)
# A verdict: the number of findings per rule; a rule left out gives none.
Verdict = dict[str, int]
NONE: Verdict = {}
ES: Verdict = {EXPORT: 1}
FS: Verdict = {SIGNATURE: 1}
BOTH: Verdict = {EXPORT: 1, SIGNATURE: 1}
Files = dict[str, str]


@pytest.fixture(autouse=True)
def site_packages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    site = tmp_path / "site-packages"
    for path, text in SITE_PACKAGES.items():
        (site / path).parent.mkdir(parents=True, exist_ok=True)
        (site / path).write_text(text)
    for module in (facts, core_analysis, native_analysis):
        monkeypatch.setattr(module, "site_packages", lambda: site)


MISSING = "from products.shop.backend.nowhere import Missing\n"
TYPING = ("TYPE_CHECKING", "Any", "ClassVar", "NewType", "Protocol", "TypedDict", "TypeVar")


def uses(body: str, name: str) -> bool:
    return re.search(rf"\b{name}\b", body) is not None


def src(body: str, missing: bool = False, extra: str = "") -> str:
    """A module with only the imports its body uses: a facade module with no function hands out
    every name it imports, so an unused import would be a hand-out of its own."""
    lines = ["from __future__ import annotations", ""]
    if uses(body, "dataclasses"):
        lines.append("import dataclasses")
    if uses(body, "cached_property"):
        lines.append("from functools import cached_property")
    typing = [name for name in TYPING if uses(body, name)]
    if typing:
        lines.append(f"from typing import {', '.join(typing)}")
    if uses(body, "BaseModel"):
        lines.append("from pydantic import BaseModel")
    kinds = [name for name in ("Fine", "Plain") if uses(body, name)]
    if kinds:
        lines.append(f"from products.shop.backend.kinds import {', '.join(kinds)}")
    if uses(body, "Order"):
        lines.append("from products.shop.backend.models import Order")
    return "\n".join(lines) + "\n" + (MISSING if missing else "") + extra + "\n\n" + body


def hand(module: str, *names: str) -> str:
    """A facade module that hands out names of a product module by `__all__`."""
    listed = ", ".join(repr(name.split(" as ")[-1]) for name in names)
    return f"from {S}.{module} import {', '.join(names)}\n\n__all__ = [{listed}]\n"


# ── Part 1: the slot table. A column is where the slot sits, a row is what the slot holds. ──
SLOTS = {
    "a model": ": Order",
    "Any": ": Any",
    "a bare dict": ": dict",
    "no annotation": "",
    "a name that does not resolve": ": Missing",
    "a class of no allowed shape": ": Plain",
    "an allowed class": ": Fine",
}
# In a slot the signature rule reads, it reports everything but a class, which the export rule judges.
SIGNATURE_COLUMN = {
    "a model": FS,
    "Any": FS,
    "a bare dict": FS,
    "no annotation": FS,
    "a name that does not resolve": FS,
    "a class of no allowed shape": ES,
    "an allowed class": NONE,
}
# A member of a class defined outside a surface file is the export rule's. A bare builtin and a
# missing annotation there have no row: the limit the design page names.
EXPORT_COLUMN = {**SIGNATURE_COLUMN, "a model": ES, "Any": ES, "a bare dict": NONE, "no annotation": NONE}
EXPORT_COLUMN["a name that does not resolve"] = ES


def rows(column: dict[str, Verdict], *names: str) -> dict[str, Verdict]:
    return {name: column[name] for name in names}


# The first column of each rule holds every row. The other columns keep one row per reason the
# verdict has there: the rows they leave out give the same verdict for the same reason (an allowed
# class is clean in every slot).
SIGNATURE_SHORT = rows(SIGNATURE_COLUMN, "a model", "a class of no allowed shape")
# A field or an attribute without an annotation is no slot.
EXPORT_FIELD = rows(EXPORT_COLUMN, "a model", "Any", "a bare dict", "a class of no allowed shape")
EXPORT_METHOD = {**EXPORT_FIELD, "no annotation": NONE}
# The class is judged at both facade modules: the one that defines it and the one that returns it.
CONTRACT_RETURNED = {**SIGNATURE_SHORT, "a class of no allowed shape": {EXPORT: 2}}


def facade_function(slot: str, missing: bool) -> Files:
    return {f"{D}/facade/api.py": src(f"def get(x{slot}) -> None:\n    pass\n", missing)}


def handed_out_function(slot: str, missing: bool) -> Files:
    return {
        f"{D}/errors.py": src(f"def place(x{slot}) -> None:\n    pass\n", missing),
        f"{D}/facade/errors.py": hand("errors", "place"),
    }


def facade_class_method(slot: str, missing: bool) -> Files:
    body = f"class Rejected(Exception):\n    def item(self, x{slot}) -> None:\n        pass\n"
    return {f"{D}/facade/contracts.py": src(body, missing)}


def facade_dataclass_field(slot: str, missing: bool) -> Files:
    body = f"@dataclasses.dataclass(frozen=True)\nclass Info:\n    x{slot}\n"
    return {f"{D}/facade/contracts.py": src(body, missing)}


def handed_out_dataclass_field(slot: str, missing: bool) -> Files:
    body = f"@dataclasses.dataclass(frozen=True)\nclass Info:\n    x{slot}\n"
    return {f"{D}/errors.py": src(body, missing), f"{D}/facade/errors.py": hand("errors", "Info")}


def handed_out_exception_method(slot: str, missing: bool) -> Files:
    body = f"class Rejected(Exception):\n    def item(self, x{slot}) -> None:\n        pass\n"
    return {f"{D}/errors.py": src(body, missing), f"{D}/facade/errors.py": hand("errors", "Rejected")}


def handed_out_dataclass_method(slot: str, missing: bool) -> Files:
    body = (
        "@dataclasses.dataclass(frozen=True)\nclass Info:\n    total: int\n\n"
        f"    def item(self, x{slot}) -> None:\n        pass\n"
    )
    return {f"{D}/errors.py": src(body, missing), f"{D}/facade/errors.py": hand("errors", "Info")}


def facade_exception_attribute(slot: str, missing: bool) -> Files:
    return {f"{D}/facade/contracts.py": src(f"class Rejected(Exception):\n    x{slot}\n", missing)}


def handed_out_exception_attribute(slot: str, missing: bool) -> Files:
    body = f"class Rejected(Exception):\n    x{slot}\n"
    return {f"{D}/errors.py": src(body, missing), f"{D}/facade/errors.py": hand("errors", "Rejected")}


def logic_dataclass_returned(slot: str, missing: bool) -> Files:
    body = f"@dataclasses.dataclass(frozen=True)\nclass Info:\n    x{slot}\n"
    return {
        f"{D}/errors.py": src(body, missing),
        f"{D}/facade/api.py": f"from {S}.errors import Info\n\n\ndef get() -> Info:\n    raise ValueError\n",
    }


def contract_dataclass_returned(slot: str, missing: bool) -> Files:
    body = f"@dataclasses.dataclass(frozen=True)\nclass Info:\n    x{slot}\n"
    return {
        f"{D}/facade/contracts.py": src(body, missing),
        f"{D}/facade/api.py": f"from {S}.facade.contracts import Info\n\n\ndef get() -> Info:\n    raise ValueError\n",
    }


# (the tree of a slot, the verdict of each row the column holds).
COLUMNS: dict[str, tuple[Callable[[str, bool], Files], dict[str, Verdict]]] = {
    "a facade function": (facade_function, SIGNATURE_COLUMN),
    "a function handed out": (handed_out_function, SIGNATURE_SHORT),
    "a method of a facade class": (facade_class_method, SIGNATURE_SHORT),
    "a field of a facade dataclass": (facade_dataclass_field, SIGNATURE_SHORT),
    "a field of a handed-out dataclass": (handed_out_dataclass_field, SIGNATURE_SHORT),
    "a method of a handed-out exception": (handed_out_exception_method, EXPORT_COLUMN),
    "a method of a handed-out frozen dataclass": (handed_out_dataclass_method, EXPORT_METHOD),
    "an attribute of a facade exception": (facade_exception_attribute, SIGNATURE_SHORT),
    "an attribute of a handed-out exception": (handed_out_exception_attribute, EXPORT_FIELD),
    "a field of a logic dataclass a facade function returns": (logic_dataclass_returned, EXPORT_FIELD),
    "a field of a contract dataclass a facade function returns": (contract_dataclass_returned, CONTRACT_RETURNED),
}


def table_cases() -> dict[str, tuple[Files, Verdict]]:
    cases = {}
    for column, (tree, verdicts) in COLUMNS.items():
        for row, verdict in verdicts.items():
            files = tree(SLOTS[row], row == "a name that does not resolve")
            cases[f"{row} in {column}"] = (files, verdict)
    return cases


# ── Part 2: the forms that attack the table. ──
ORDER_DC = "@dataclasses.dataclass(frozen=True)\nclass Receipt:\n    order: Order\n"
EXC_METHOD = "class Rejected(Exception):\n    def item(self) -> Order:\n        raise ValueError\n"
INFO = ORDER_DC.replace("Receipt", "Info")
TV = "T = TypeVar('T')\n\n\n"
NEW_TYPE = "Ref = NewType('Ref', Order)\n"
FROM_CORE = "from posthog.corelib import {name}\n"
PRODUCT_BASE = (
    "class ProductBase(Exception):\n    def product_order(self) -> Order:\n        raise ValueError\n\n"
    "    def shared(self) -> Order:\n        raise ValueError\n"
)
SECOND_RULE = """
[rules.second-signature]
primitive  = "signature"
select     = "src.zone == 'facade'"
deny       = "dst.kind == 'model'"
on_unknown = "finding"
identity   = "facade-slot"
ledger     = false
"""
DEFINING_MODULE_RULE = SECOND_RULE.replace("src.zone == 'facade'", "src.module == 'products.shop.backend.errors'")
LAZY = (
    "import importlib\n\n_LAZY = {{'{name}': 'products.shop.backend.errors'}}\n\n__all__ = sorted(_LAZY)\n\n\n"
    "def __getattr__(name: str) -> object:\n    return getattr(importlib.import_module(_LAZY[name]), name)\n"
)
REEXPORTED = {
    f"{D}/again.py": f"from {S}.errors import Rejected, Receipt\n\n__all__ = ['Rejected', 'Receipt']\n",
    f"{D}/facade/errors.py": f"from {S}.again import Rejected, Receipt\n\n__all__ = ['Rejected', 'Receipt']\n",
}
BASE_DC = (
    "@dataclasses.dataclass(frozen=True)\nclass Base:\n    order: Order\n\n\n"
    "@dataclasses.dataclass(frozen=True)\nclass Info(Base):\n    total: int = 0\n"
)
BARE_FORMS = (
    "class Rejected(Exception):\n"
    "    def __init__(self, a: dict, b: object, c: list[dict], d: dict | None, e: dict[str, object], f: Any, g) -> None:\n"
    "        pass\n\n"
    "    def give(self) -> dict:\n        raise ValueError\n\n    def other(self):\n        raise ValueError\n"
)
ANNOTATION_FORMS = (
    "class A1(Exception):\n    def item(self) -> Order | None:\n        raise ValueError\n\n\n"
    "class A2(Exception):\n    def item(self) -> Annotated[Order, 'x']:\n        raise ValueError\n\n\n"
    "class A3(Exception):\n    def item(self) -> Callable[[Order], None]:\n        raise ValueError\n\n\n"
    "class A4(Exception):\n    def item(self) -> type[Order]:\n        raise ValueError\n\n\n"
    "class A5(Exception):\n    def item(self) -> list['Order']:\n        raise ValueError\n\n\n"
    "class A6(Exception):\n    def item(self) -> tuple[Order, ...]:\n        raise ValueError\n"
)


def get_returns(module: str, name: str = "Info") -> str:
    return f"from {S}.{module} import {name}\n\n\ndef get() -> {name}:\n    raise ValueError\n"


ATTACKS: dict[str, tuple[Files, Verdict]] = {
    # Hand-outs under other names and in other ways.
    "a dataclass under an alias": (
        {f"{D}/errors.py": src(ORDER_DC), f"{D}/facade/errors.py": hand("errors", "Receipt as Bill")},
        FS,
    ),
    "an exception under an alias": (
        {f"{D}/errors.py": src(EXC_METHOD), f"{D}/facade/errors.py": hand("errors", "Rejected as Refused")},
        ES,
    ),
    "a dataclass under a private name": (
        {f"{D}/errors.py": src(ORDER_DC), f"{D}/facade/errors.py": hand("errors", "Receipt as _Receipt")},
        ES,
    ),
    "a dataclass under two public names": (
        {f"{D}/errors.py": src(ORDER_DC), f"{D}/facade/errors.py": hand("errors", "Receipt", "Receipt as Bill")},
        {SIGNATURE: 2},
    ),
    "a dataclass under two names, the private one first": (
        {
            f"{D}/errors.py": src(ORDER_DC),
            f"{D}/facade/errors.py": hand("errors", "Receipt as _Receipt", "Receipt as Receipt"),
        },
        FS,
    ),
    "a dataclass under two names, the private one second": (
        {
            f"{D}/errors.py": src(ORDER_DC),
            f"{D}/facade/errors.py": hand("errors", "Receipt as Receipt", "Receipt as _Receipt"),
        },
        FS,
    ),
    "a function under two names, the private one first": (
        {
            f"{D}/errors.py": src("def place(order: Order) -> None:\n    pass\n"),
            f"{D}/facade/errors.py": hand("errors", "place as _place", "place as place"),
        },
        FS,
    ),
    "a private function under a public name": (
        {
            f"{D}/errors.py": src("def _place(order: Order) -> None:\n    pass\n"),
            f"{D}/facade/errors.py": hand("errors", "_place as place"),
        },
        FS,
    ),
    "a function under a private name": (
        {
            f"{D}/errors.py": src("def place(order: Order) -> None:\n    pass\n"),
            f"{D}/facade/errors.py": hand("errors", "place as _place"),
        },
        ES,
    ),
    "a lazy hand-out of a dataclass": (
        {f"{D}/errors.py": src(ORDER_DC), f"{D}/facade/errors.py": LAZY.format(name="Receipt")},
        FS,
    ),
    "a lazy hand-out of an exception": (
        {f"{D}/errors.py": src(EXC_METHOD), f"{D}/facade/errors.py": LAZY.format(name="Rejected")},
        ES,
    ),
    "a hand-out by a module with no function and no __all__": (
        {
            f"{D}/errors.py": src(ORDER_DC + "\n\n" + EXC_METHOD),
            f"{D}/facade/errors.py": f"from {S}.errors import Receipt, Rejected\n",
        },
        BOTH,
    ),
    "an x as x hand-out in a module with a function": (
        {
            f"{D}/errors.py": src(EXC_METHOD),
            f"{D}/facade/errors.py": f"from {S}.errors import Rejected as Rejected\n\n\ndef noop() -> None:\n    pass\n",
        },
        ES,
    ),
    "the facade package hands out an exception of its contracts module": (
        {
            f"{D}/facade/contracts.py": src(EXC_METHOD),
            f"{D}/facade/__init__.py": f"from {S}.facade.contracts import Rejected\n\n__all__ = ['Rejected']\n",
        },
        FS,
    ),
    "an exception two re-exports away": (
        {
            f"{D}/errors.py": src(
                EXC_METHOD + "\n\n@dataclasses.dataclass(frozen=True)\nclass Receipt:\n    total: int\n"
            ),
            **REEXPORTED,
        },
        ES,
    ),
    "a named import of a class of no allowed shape": (
        {f"{D}/errors.py": "class Loose:\n    pass\n", f"{D}/facade/errors.py": hand("errors", "Loose")},
        ES,
    ),
    # Nested classes.
    "a nested class in a handed-out exception": (
        {
            f"{D}/errors.py": src(
                "class Rejected(Exception):\n    class Detail:\n        order: Order\n\n"
                "        def item(self) -> Order:\n            raise ValueError\n\n"
                "    def detail(self) -> Rejected.Detail:\n        raise ValueError\n"
            ),
            f"{D}/facade/errors.py": hand("errors", "Rejected"),
        },
        {EXPORT: 2},
    ),
    "a nested class in a handed-out exception that no slot names": (
        {
            f"{D}/errors.py": src(
                "class Rejected(Exception):\n    class Detail:\n        order: Order\n\n"
                "        def item(self) -> Order:\n            raise ValueError\n"
            ),
            f"{D}/facade/errors.py": hand("errors", "Rejected"),
        },
        ES,
    ),
    "a nested class in a facade exception": (
        {
            f"{D}/facade/contracts.py": src(
                "class Rejected(Exception):\n    class Detail:\n        order: Order\n        loose: dict\n\n"
                "        def item(self, x, y: dict) -> Order:\n            raise ValueError\n"
            )
        },
        {SIGNATURE: 2},
    ),
    # Inheritance: a core class's members are core's contract.
    "a method through two ancestors, core first": (
        {
            f"{D}/errors.py": src(
                PRODUCT_BASE + "\n\nclass Rejected(CoreError, ProductBase):\n    pass\n",
                extra=FROM_CORE.format(name="CoreError"),
            ),
            f"{D}/facade/errors.py": hand("errors", "Rejected"),
        },
        ES,
    ),
    "a method inherited from core only": (
        {
            f"{D}/errors.py": src("class Rejected(CoreError):\n    pass\n", extra=FROM_CORE.format(name="CoreError")),
            f"{D}/facade/errors.py": hand("errors", "Rejected"),
        },
        NONE,
    ),
    "a method inherited from a product base only": (
        {
            f"{D}/errors.py": src(PRODUCT_BASE + "\n\nclass Rejected(ProductBase):\n    pass\n"),
            f"{D}/facade/errors.py": hand("errors", "Rejected"),
        },
        ES,
    ),
    "a core method a product class overrides": (
        {
            f"{D}/errors.py": src(
                "class Rejected(CoreError):\n    def core_order(self) -> Order:\n        raise ValueError\n",
                extra=FROM_CORE.format(name="CoreError"),
            ),
            f"{D}/facade/errors.py": hand("errors", "Rejected"),
        },
        ES,
    ),
    "a field from a core pydantic model, the class in logic and handed out": (
        {
            f"{D}/errors.py": src("class Row(CoreRow):\n    title: str\n", extra=FROM_CORE.format(name="CoreRow")),
            f"{D}/facade/errors.py": hand("errors", "Row"),
        },
        FS,
    ),
    "a field from a core pydantic model, the class in logic and named in a slot": (
        {
            f"{D}/errors.py": src("class Row(CoreRow):\n    title: str\n", extra=FROM_CORE.format(name="CoreRow")),
            f"{D}/facade/api.py": get_returns("errors", "Row"),
        },
        ES,
    ),
    "a field from a core plain dataclass, by a frozen product dataclass in logic": (
        {
            f"{D}/errors.py": src(
                "@dataclasses.dataclass(frozen=True)\nclass Row(CorePlainData):\n    title: str = ''\n",
                extra=FROM_CORE.format(name="CorePlainData"),
            ),
            f"{D}/facade/errors.py": hand("errors", "Row"),
        },
        FS,
    ),
    "a field from a product base dataclass, handed out": (
        {f"{D}/errors.py": src(BASE_DC), f"{D}/facade/errors.py": hand("errors", "Info")},
        FS,
    ),
    "a facade dataclass with a field from a logic base, returned by a facade function": (
        {
            f"{D}/errors.py": src("@dataclasses.dataclass(frozen=True)\nclass Base:\n    order: Order\n"),
            f"{D}/facade/contracts.py": src(
                "@dataclasses.dataclass(frozen=True)\nclass Info(Base):\n    total: int = 0\n",
                extra=f"from {S}.errors import Base\n",
            ),
            f"{D}/facade/api.py": get_returns("facade.contracts"),
        },
        FS,
    ),
    "a core Protocol handed out as it stands": (
        {f"{D}/facade/errors.py": "from posthog.corelib import CoreSink\n\n__all__ = ['CoreSink']\n"},
        NONE,
    ),
    "a core pydantic model handed out as it stands": (
        {f"{D}/facade/errors.py": "from posthog.corelib import CoreRow\n\n__all__ = ['CoreRow']\n"},
        FS,
    ),
    "a core frozen dataclass with a model method, handed out as it stands": (
        {
            "posthog/coredata.py": src(
                "@dataclasses.dataclass(frozen=True)\nclass CoreInfo:\n    total: int\n\n"
                "    def order(self) -> Order:\n        raise ValueError\n"
            ),
            f"{D}/facade/errors.py": "from posthog.coredata import CoreInfo\n\n__all__ = ['CoreInfo']\n",
        },
        NONE,
    ),
    "a product subclass of a core ABC with a model method, handed out": (
        {
            "posthog/abcs.py": "import abc\n\n\nclass CoreRunner(abc.ABC):\n    @abc.abstractmethod\n    def run(self) -> int: ...\n",
            f"{D}/errors.py": src(
                "class Runner(CoreRunner):\n    def run(self) -> int:\n        return 1\n\n"
                "    def load(self) -> Order:\n        raise ValueError\n",
                extra="from posthog.abcs import CoreRunner\n",
            ),
            f"{D}/facade/errors.py": hand("errors", "Runner"),
        },
        ES,
    ),
    # Members of every kind.
    "a written __init__ on a handed-out dataclass": (
        {
            f"{D}/errors.py": src(
                "@dataclasses.dataclass(frozen=True)\nclass Receipt:\n    total: int\n\n"
                "    def __init__(self, order: Order) -> None:\n        pass\n"
            ),
            f"{D}/facade/errors.py": hand("errors", "Receipt"),
        },
        ES,
    ),
    "a property and two cached properties of a handed-out exception": (
        {
            f"{D}/errors.py": src(
                "class Rejected(Exception):\n    @property\n    def one(self) -> Order:\n        raise ValueError\n\n"
                "    @cached_property\n    def two(self) -> Team:\n        raise ValueError\n\n"
                "    @cached_property\n    def three(self) -> Plain:\n        raise ValueError\n",
                extra="from posthog.models.team.team import Team\n",
            ),
            f"{D}/facade/errors.py": hand("errors", "Rejected"),
        },
        {EXPORT: 2},
    ),
    "a callback Protocol with a model, handed out": (
        {
            f"{D}/errors.py": src("class Hook(Protocol):\n    def __call__(self, order: Order) -> None: ...\n"),
            f"{D}/facade/errors.py": hand("errors", "Hook"),
        },
        ES,
    ),
    "a Protocol attribute and a pydantic method, handed out": (
        {
            f"{D}/errors.py": src(
                "class Holder(Protocol):\n    item: Order\n\n\n"
                "class Row(BaseModel):\n    title: str\n\n    def order(self) -> Order:\n        raise ValueError\n"
            ),
            f"{D}/facade/errors.py": hand("errors", "Holder"),
            f"{D}/facade/rows.py": hand("errors", "Row"),
        },
        {EXPORT: 2},
    ),
    "a generic exception with a TypeVar method, handed out": (
        {
            f"{D}/errors.py": src(
                TV + "class Rejected(Exception, Generic[T]):\n    def item(self) -> T:\n        raise ValueError\n",
                extra="from typing import Generic\n",
            ),
            f"{D}/facade/errors.py": hand("errors", "Rejected"),
        },
        ES,
    ),
    "an overloaded method with a model, handed out": (
        {
            f"{D}/errors.py": src(
                "class Rejected(Exception):\n    @overload\n    def item(self, x: int) -> int: ...\n"
                "    @overload\n    def item(self, x: Order) -> Order: ...\n    def item(self, x):\n        return x\n",
                extra="from typing import overload\n",
            ),
            f"{D}/facade/errors.py": hand("errors", "Rejected"),
        },
        ES,
    ),
    "a method annotated with a string that names a model by its dotted path": (
        {
            f"{D}/errors.py": "class Rejected(Exception):\n    def item(self) -> 'products.shop.backend.models.Order':\n"
            "        raise ValueError\n",
            f"{D}/facade/errors.py": hand("errors", "Rejected"),
        },
        ES,
    ),
    "a dotted annotation models.Order, handed out": (
        {
            f"{D}/errors.py": f"from {S} import models\n\n\nclass Rejected(Exception):\n"
            "    def item(self) -> models.Order:\n        raise ValueError\n",
            f"{D}/facade/errors.py": hand("errors", "Rejected"),
        },
        ES,
    ),
    "six annotation forms, each in an exception handed out by its own module": (
        {
            f"{D}/errors.py": src(
                ANNOTATION_FORMS, extra="from collections.abc import Callable\nfrom typing import Annotated\n"
            ),
            **{f"{D}/facade/a{i}.py": hand("errors", f"A{i}") for i in range(1, 7)},
        },
        {EXPORT: 6},
    ),
    "an exception defined in a function and returned by a handed-out factory": (
        {
            f"{D}/errors.py": src(
                "def make() -> type[Exception]:\n    class Local(Exception):\n        def item(self) -> Order:\n"
                "            raise ValueError\n\n    return Local\n"
            ),
            f"{D}/facade/errors.py": hand("errors", "make"),
        },
        NONE,
    ),
    "a non-frozen dataclass with a model field, handed out": (
        {
            f"{D}/errors.py": src("@dataclasses.dataclass\nclass Loose:\n    order: Order\n"),
            f"{D}/facade/errors.py": hand("errors", "Loose"),
        },
        BOTH,
    ),
    "the bare dict and object forms, handed out": (
        {f"{D}/errors.py": src(BARE_FORMS), f"{D}/facade/errors.py": hand("errors", "Rejected")},
        ES,
    ),
    "the bare dict and object forms, in the facade": ({f"{D}/facade/contracts.py": src(BARE_FORMS)}, {SIGNATURE: 8}),
    # Trails: a class a slot reaches.
    "two trails, the first a slot left to the signature rule": (
        {
            f"{D}/errors.py": src(
                ORDER_DC
                + "\n\nclass Rejected(Exception):\n    def receipt(self) -> Receipt:\n        raise ValueError\n"
            ),
            f"{D}/facade/api.py": f"from {S}.errors import Receipt, Rejected\n\n\ndef get() -> Receipt:\n"
            "    raise ValueError\n\n\n__all__ = ['get', 'Rejected']\n",
        },
        ES,
    ),
    "a handed-out function whose slot names a logic exception with a model method": (
        {
            f"{D}/errors.py": src(EXC_METHOD + "\n\ndef place(e: Rejected) -> None:\n    pass\n"),
            f"{D}/facade/errors.py": hand("errors", "place"),
        },
        ES,
    ),
    "a method of a handed-out exception names a logic dataclass with a model field": (
        {
            f"{D}/errors.py": src(
                ORDER_DC
                + "\n\nclass Rejected(Exception):\n    def receipt(self) -> Receipt:\n        raise ValueError\n"
            ),
            f"{D}/facade/errors.py": hand("errors", "Rejected"),
        },
        ES,
    ),
    "a field of a handed-out dataclass names a logic dataclass with a model field": (
        {
            f"{D}/errors.py": src(
                ORDER_DC + "\n\n@dataclasses.dataclass(frozen=True)\nclass Outer:\n    inner: Receipt\n"
            ),
            f"{D}/facade/errors.py": hand("errors", "Outer"),
        },
        ES,
    ),
    "a method of a handed-out exception names a contract dataclass with a model field": (
        {
            f"{D}/facade/contracts.py": src(ORDER_DC),
            f"{D}/errors.py": src(
                "class Rejected(Exception):\n    def receipt(self) -> Receipt:\n        raise ValueError\n",
                extra=f"from {S}.facade.contracts import Receipt\n",
            ),
            f"{D}/facade/errors.py": hand("errors", "Rejected"),
        },
        FS,
    ),
    "a model in a left slot and in a member slot of a handed-out exception": (
        {
            f"{D}/errors.py": src(EXC_METHOD),
            f"{D}/facade/api.py": f"from {S}.errors import Rejected\nfrom {S}.models import Order\n\n\n"
            "def get() -> Order:\n    raise ValueError\n\n\n__all__ = ['get', 'Rejected']\n",
        },
        BOTH,
    ),
    "a TypeVar in a left slot and in a member slot of a handed-out exception": (
        {
            f"{D}/errors.py": src(
                TV + "class Rejected(Exception):\n    def item(self) -> T:\n        raise ValueError\n"
            ),
            f"{D}/facade/api.py": f"from {S}.errors import Rejected, T\n\n\ndef get(x: T) -> None:\n    pass\n\n\n"
            "__all__ = ['get', 'Rejected']\n",
        },
        BOTH,
    ),
    "a NewType of a model in a left slot and in a member slot": (
        {
            f"{D}/errors.py": src(
                NEW_TYPE + "\n\nclass Rejected(Exception):\n    def item(self) -> Ref:\n        raise ValueError\n"
            ),
            f"{D}/facade/api.py": f"from {S}.errors import Ref, Rejected\n\n\ndef get(x: Ref) -> None:\n    pass\n\n\n"
            "__all__ = ['get', 'Rejected']\n",
        },
        BOTH,
    ),
    "a NewType of a model in a private facade function and in a member slot": (
        {
            f"{D}/errors.py": src(
                NEW_TYPE + "\n\nclass Rejected(Exception):\n    def item(self) -> Ref:\n        raise ValueError\n"
            ),
            f"{D}/facade/api.py": f"from {S}.errors import Ref, Rejected\n\n\ndef _get(x: Ref) -> None:\n    pass\n\n\n"
            "def get() -> None:\n    pass\n\n\n__all__ = ['get', 'Rejected']\n",
        },
        ES,
    ),
    "a class named in a slot and handed out, the hand-out after": (
        {
            f"{D}/errors.py": src(INFO + "\n\ndef get() -> Info:\n    raise ValueError\n"),
            f"{D}/facade/api.py": hand("errors", "get", "Info"),
        },
        FS,
    ),
    "a class named in a slot and handed out, the hand-out before": (
        {
            f"{D}/errors.py": src(INFO + "\n\ndef get() -> Info:\n    raise ValueError\n"),
            f"{D}/facade/api.py": hand("errors", "Info", "get"),
        },
        FS,
    ),
    "a class a facade function names and the same module hands out": (
        {
            f"{D}/errors.py": src(INFO),
            f"{D}/facade/api.py": get_returns("errors") + "\n\n__all__ = ['get', 'Info']\n",
        },
        FS,
    ),
    "a facade function slot names a class whose base does not resolve": (
        {
            f"{D}/errors.py": MISSING + "\n\nclass Weird(Missing):\n    pass\n",
            f"{D}/facade/api.py": f"from {S}.errors import Weird\n\n\ndef get(x: Weird) -> None:\n    pass\n",
        },
        BOTH,
    ),
    # Registration surfaces and rules a case adds.
    "a registration surface returns a contract class": (
        {
            f"{D}/facade/contracts.py": src(INFO),
            f"{D}/routes.py": get_returns("facade.contracts").replace("get", "register"),
        },
        BOTH,
    ),
    "a registration surface returns a contract class with a core model on the list": (
        {
            f"{D}/facade/contracts.py": src(
                "@dataclasses.dataclass(frozen=True)\nclass Info:\n    team: Team\n",
                extra="from posthog.models.team.team import Team\n",
            ),
            f"{D}/routes.py": get_returns("facade.contracts").replace("get", "register"),
        },
        ES,
    ),
    "an unnamed second signature rule for the facade module, a facade function": (
        {
            "hoglint.toml": ROOT_CONFIG + SECOND_RULE,
            f"{D}/facade/api.py": src("def get(x: Order) -> None:\n    pass\n"),
        },
        {SIGNATURE: 1, SECOND: 1},
    ),
    "an unnamed second signature rule for the facade module, a handed-out exception": (
        {
            "hoglint.toml": ROOT_CONFIG + SECOND_RULE,
            f"{D}/errors.py": src(EXC_METHOD),
            f"{D}/facade/errors.py": hand("errors", "Rejected"),
        },
        ES,
    ),
    "an unnamed second signature rule for the defining module": (
        {
            "hoglint.toml": ROOT_CONFIG + DEFINING_MODULE_RULE,
            f"{D}/errors.py": src(EXC_METHOD),
            f"{D}/facade/errors.py": hand("errors", "Rejected"),
        },
        {EXPORT: 1, SECOND: 1},
    ),
}

# The same member forms in a class handed out from logic (the export rule's) and in a class the
# facade defines (the signature rule's).
MEMBER_FORMS: dict[str, tuple[str, Verdict, Verdict]] = {
    "an attribute as a string annotation": (
        src("class Rejected(Exception):\n    order: 'Order'\n").replace("from __future__ import annotations\n\n", ""),
        ES,
        FS,
    ),
    "an attribute whose type is imported under if TYPE_CHECKING": (
        "from __future__ import annotations\n\nfrom typing import TYPE_CHECKING\n\n"
        "if TYPE_CHECKING:\n    from products.shop.backend.models import Order\n\n\n"
        "class Rejected(Exception):\n    order: Order\n",
        ES,
        FS,
    ),
    "a private attribute": (src("class Rejected(Exception):\n    _order: Order\n"), ES, FS),
    "a private method": (
        src("class Rejected(Exception):\n    def _item(self) -> Order:\n        raise ValueError\n"),
        ES,
        ES,
    ),
    "an annotated receiver": (
        src("class Rejected(Exception):\n    def item(self: Order) -> None:\n        pass\n"),
        ES,
        FS,
    ),
    "an unannotated dunder that is not __init__": (
        src("class Rejected(Exception):\n    def __call__(self, x, y: dict) -> None:\n        pass\n"),
        NONE,
        NONE,
    ),
    "*args and **kwargs typed with a model": (
        src("class Rejected(Exception):\n    def item(self, *orders: Order, **named: Order) -> None:\n        pass\n"),
        ES,
        {SIGNATURE: 2},
    ),
    "a staticmethod and a classmethod": (
        src(
            "class Rejected(Exception):\n    @staticmethod\n    def one(order: Order) -> None:\n        pass\n\n"
            "    @classmethod\n    def two(cls, team: Team) -> Plain:\n        raise ValueError\n",
            extra="from posthog.models.team.team import Team\n",
        ),
        {EXPORT: 2},
        BOTH,
    ),
    "a class-body alias used by a method": (
        src("class Rejected(Exception):\n    Ref = Order\n\n    def item(self) -> Ref:\n        raise ValueError\n"),
        ES,
        FS,
    ),
    "an alias to a model list": (
        src(
            "Orders = list[Order]\n\n\nclass Rejected(Exception):\n    def item(self) -> Orders:\n        raise ValueError\n"
        ),
        ES,
        FS,
    ),
    "an alias cycle": (
        src(
            "Loop = list['Loop'] | Order\n\n\nclass Rejected(Exception):\n    def item(self) -> Loop:\n        raise ValueError\n"
        ),
        ES,
        FS,
    ),
    "a NewType of a model": (
        src(NEW_TYPE + "\n\nclass Rejected(Exception):\n    def item(self) -> Ref:\n        raise ValueError\n"),
        ES,
        FS,
    ),
    "a class-form TypedDict with a model": (
        src(
            "class Props(TypedDict):\n    order: Order\n\n\nclass Rejected(Exception):\n    def item(self) -> Props:\n        raise ValueError\n"
        ),
        ES,
        BOTH,
    ),
    "a typed mapping whose argument is a model": (
        src("class Rejected(Exception):\n    def item(self, orders: dict[str, Order]) -> None:\n        pass\n"),
        ES,
        FS,
    ),
    "an opaque payload parameter": (
        src("class Rejected(Exception):\n    def item(self, extra: dict[str, Any]) -> None:\n        pass\n"),
        NONE,
        NONE,
    ),
    "an opaque payload returned": (
        src("class Rejected(Exception):\n    def item(self) -> dict[str, Any]:\n        raise ValueError\n"),
        ES,
        FS,
    ),
    "an alias of an opaque payload, as a parameter and returned": (
        src(
            "Payload = dict[str, Any]\n\n\nclass Rejected(Exception):\n    def take(self, extra: Payload) -> None:\n"
            "        pass\n\n    def give(self) -> Payload:\n        raise ValueError\n"
        ),
        ES,
        FS,
    ),
    "a typed mapping with an object value": (
        src("class Rejected(Exception):\n    def item(self, extra: dict[str, object]) -> None:\n        pass\n"),
        NONE,
        FS,
    ),
    "a base that does not resolve next to one that does": (
        src(
            "class Rejected(Exception, Missing):\n    def item(self) -> Order:\n        raise ValueError\n",
            missing=True,
        ),
        {EXPORT: 2},
        BOTH,
    ),
    # A name the producer does not expand, next to or inside an alias it does expand.
    "a slot that mixes an alias with a NewType of a model": (
        src(
            "Ids = list[int]\n"
            + NEW_TYPE
            + "\n\nclass Rejected(Exception):\n    def item(self) -> Ids | Ref:\n        raise ValueError\n"
        ),
        ES,
        FS,
    ),
    "an alias whose value holds a NewType of a model": (
        src(
            NEW_TYPE
            + "Maybe = Ref | None\n\n\nclass Rejected(Exception):\n    def item(self) -> Maybe:\n        raise ValueError\n"
        ),
        ES,
        FS,
    ),
}
TYPES_WITH_ALIASES = {
    f"{D}/types.py": src("Orders = list[Order]\nMore = dict[str, Orders]\n"),
}
# An alias another module defines, in a class of logic a facade module hands out.
IMPORTED_ALIAS_FORMS = {
    "an imported alias": src(
        "class Rejected(Exception):\n    def item(self) -> Orders:\n        raise ValueError\n",
        extra=f"from {S}.types import Orders\n",
    ),
    "an alias of an alias": src(
        "class Rejected(Exception):\n    def item(self) -> More:\n        raise ValueError\n",
        extra=f"from {S}.types import More\n",
    ),
}
DEEP_ALIAS = "A0 = Order\n" + "".join(f"A{i} = list[A{i - 1}]\n" for i in range(1, 41))


def member_cases() -> dict[str, tuple[Files, Verdict]]:
    """Each member form in a class of logic a facade module hands out, and in a facade class where
    the verdict there is not one signature finding: that one repeats the facade class column."""
    handed_out = {f"{D}/facade/errors.py": hand("errors", "Rejected")}
    cases: dict[str, tuple[Files, Verdict]] = {}
    for form, (body, in_logic, in_facade) in MEMBER_FORMS.items():
        cases[f"{form}, handed out"] = ({f"{D}/errors.py": body, **handed_out}, in_logic)
        if in_facade != FS:
            cases[f"{form}, in the facade"] = ({f"{D}/facade/contracts.py": body}, in_facade)
    for form, body in IMPORTED_ALIAS_FORMS.items():
        cases[f"{form}, handed out"] = ({**TYPES_WITH_ALIASES, f"{D}/errors.py": body, **handed_out}, ES)
    deep = src(
        "class Rejected(Exception):\n    def item(self) -> A40:\n        raise ValueError\n",
        extra=f"from {S}.types import A40\n",
    )
    deep_types = {f"{D}/types.py": src(DEEP_ALIAS)}
    # Too deep to expand: one row for the name, the marker where the walk stops.
    cases["an alias too deep to expand, handed out"] = (
        {**deep_types, f"{D}/errors.py": deep, **handed_out},
        ES,
    )
    cases["an alias too deep to expand, in the facade"] = (
        {**deep_types, f"{D}/facade/contracts.py": deep},
        FS,
    )
    # An attribute with a value and no annotation is no slot.
    cases["an attribute with a value and no annotation, handed out"] = (
        {f"{D}/errors.py": src("class Rejected(Exception):\n    order = Order\n"), **handed_out},
        NONE,
    )
    return cases


# A class-body alias in another kind of member.
CLASS_BODY_ALIAS = "@dataclasses.dataclass(frozen=True)\nclass Info:\n    Ref = Order\n    order: Ref\n"
CLASS_BODY_CASES: dict[str, tuple[Files, Verdict]] = {
    "a class-body alias used by an attribute, handed out": (
        {
            f"{D}/errors.py": src("class Rejected(Exception):\n    Ref = Order\n    order: Ref\n"),
            f"{D}/facade/errors.py": hand("errors", "Rejected"),
        },
        ES,
    ),
    "a class-body alias used by a field of a handed-out dataclass": (
        {f"{D}/errors.py": src(CLASS_BODY_ALIAS), f"{D}/facade/errors.py": hand("errors", "Info")},
        FS,
    ),
    "a class-body alias used by a field of a logic dataclass a facade function returns": (
        {f"{D}/errors.py": src(CLASS_BODY_ALIAS), f"{D}/facade/api.py": get_returns("errors")},
        ES,
    ),
}


# ── Part 3: holes whose verdict is decided and not built. Each case asserts the decided verdict and
# runs as an expected failure until the change lands; the reason says what is missing. ──
PLACE = "def place(order: Order) -> None:\n    pass\n"
EVERYTHING = ORDER_DC + "\n\n" + EXC_METHOD + "\n\n" + PLACE + "\n\nclass Loose:\n    pass\n"
ALL_FIVE = "\n\n__all__ = ['Order', 'Receipt', 'Rejected', 'place', 'Loose']\n"
STAR = f"from {S}.errors import *\n"
MAKE = "def make() -> type:\n    return object\n\n\n"
IMPORTS_MODELS = f"from {S} import models\n"
GET_FROM_LOGIC = get_returns("errors")
# The named import of the five names: the rows a star import of them must give.
NAMED_FIVE: Verdict = {EXPORT: 2, SIGNATURE: 2}

HOLES: dict[str, tuple[Files, Verdict, str]] = {}

# A star import: the same rows as the named import of each name it binds.
STAR_CASES: dict[str, tuple[Files, Verdict]] = {
    # The engine reads the installed distribution's source, so the star hands out the names a named
    # import would. Where the distribution is not installed, the star is an unresolved import.
    "a star import of a third-party module": (
        {f"{D}/facade/errors.py": "from rest_framework.serializers import *\n"},
        NONE,
    ),
    "a star import of five kinds, the facade without a function": (
        {f"{D}/errors.py": src(EVERYTHING + ALL_FIVE), f"{D}/facade/errors.py": STAR},
        NAMED_FIVE,
    ),
    "a star import of a source without __all__": (
        {f"{D}/errors.py": src(EVERYTHING), f"{D}/facade/errors.py": STAR},
        NAMED_FIVE,
    ),
    "a star import, the facade lists two of its names and has a function": (
        {
            f"{D}/errors.py": src(EVERYTHING + ALL_FIVE),
            f"{D}/facade/errors.py": STAR
            + "\n__all__ = ['Order', 'Rejected', 'noop']\n\n\ndef noop() -> None:\n    pass\n",
        },
        ES,
    ),
    "a star import of a module that star-imports": (
        {
            f"{D}/errors.py": src(EVERYTHING + ALL_FIVE),
            f"{D}/again.py": STAR,
            f"{D}/facade/errors.py": f"from {S}.again import *\n",
        },
        NAMED_FIVE,
    ),
    "a star import of a source that lists a private exception": (
        {
            f"{D}/errors.py": src(
                "class _Hidden(Exception):\n    def item(self) -> Order:\n        raise ValueError\n\n\n__all__ = ['_Hidden']\n"
            ),
            f"{D}/facade/errors.py": STAR,
        },
        ES,
    ),
    "a star import of a source that imports a model": (
        {f"{D}/errors.py": f"from {S}.models import Order\n", f"{D}/facade/errors.py": STAR},
        ES,
    ),
    "a star import of a source that lists a constant and an alias of a model": (
        {
            f"{D}/errors.py": src("LIMIT = 3\nRef = Order\n\n__all__ = ['LIMIT', 'Ref']\n"),
            f"{D}/facade/errors.py": STAR,
        },
        ES,
    ),
    "a star import whose source __all__ cannot be read": (
        {f"{D}/errors.py": src(EVERYTHING + "\n\n__all__ = sorted(globals())\n"), f"{D}/facade/errors.py": STAR},
        BOTH,
    ),
    "a star import in a registration surface": (
        {f"{D}/errors.py": src(EVERYTHING + ALL_FIVE), f"{D}/routes.py": STAR},
        ES,
    ),
}

# A top-level assignment of a public or listed name: a hand-out under that name.
ASSIGNED_CASES: dict[str, tuple[Files, Verdict]] = {
    "Thing = models.Order, listed": (
        {f"{D}/facade/api.py": IMPORTS_MODELS + "\nThing = models.Order\n\n__all__ = ['Thing']\n"},
        ES,
    ),
    "Thing = Loose, a class of no allowed shape, listed": (
        {
            f"{D}/errors.py": "class Loose:\n    pass\n",
            f"{D}/facade/api.py": f"from {S}.errors import Loose\n\nThing = Loose\n\n__all__ = ['Thing']\n",
        },
        ES,
    ),
    "Thing = place, a function, listed": (
        {
            f"{D}/errors.py": src(PLACE),
            f"{D}/facade/api.py": f"from {S}.errors import place\n\nThing = place\n\n__all__ = ['Thing']\n",
        },
        FS,
    ),
    "Thing = Receipt, a dataclass, listed": (
        {
            f"{D}/errors.py": src(ORDER_DC),
            f"{D}/facade/api.py": f"from {S}.errors import Receipt\n\nThing = Receipt\n\n__all__ = ['Thing']\n",
        },
        FS,
    ),
    "Thing = models.Order, no __all__, the module has no function": (
        {f"{D}/facade/api.py": IMPORTS_MODELS + "\nThing = models.Order\n"},
        ES,
    ),
    "Thing = models.Order, public and not listed, the module has a function": (
        {
            f"{D}/facade/api.py": IMPORTS_MODELS
            + "\nThing = models.Order\n\n\ndef noop() -> None:\n    pass\n\n\n__all__ = ['noop']\n"
        },
        ES,
    ),
    "Thing = Order in a registration surface, listed": (
        {f"{D}/routes.py": f"from {S}.models import Order\n\nThing = Order\n\n__all__ = ['Thing']\n"},
        ES,
    ),
}

# A base that is no class makes the lineage unknown; the members of the known lineage are still judged.
BASE_CASES: dict[str, tuple[Files, Verdict]] = {
    "a call base, handed out": (
        {
            f"{D}/errors.py": src(
                MAKE + "class Rejected(Exception, make()):\n    def item(self) -> int:\n        return 1\n"
            ),
            f"{D}/facade/errors.py": hand("errors", "Rejected"),
        },
        ES,
    ),
    "a call base in the facade": (
        {
            f"{D}/facade/contracts.py": src(
                MAKE + "class Rejected(Exception, make()):\n    def item(self) -> int:\n        return 1\n"
            )
        },
        ES,
    ),
    "a frozen dataclass with a call base, handed out": (
        {
            f"{D}/errors.py": src(MAKE + "@dataclasses.dataclass(frozen=True)\nclass Info(make()):\n    total: int\n"),
            f"{D}/facade/errors.py": hand("errors", "Info"),
        },
        BOTH,
    ),
    "a call base that hides a class with a model method": (
        {
            f"{D}/errors.py": src(
                "class Mixin:\n    def item(self) -> Order:\n        raise ValueError\n\n\n"
                "def make() -> type:\n    return Mixin\n\n\nclass Rejected(Exception, make()):\n    pass\n"
            ),
            f"{D}/facade/errors.py": hand("errors", "Rejected"),
        },
        ES,
    ),
    "an ancestor with a call base": (
        {
            f"{D}/errors.py": src(
                MAKE + "class Middle(make()):\n    pass\n\n\nclass Rejected(Exception, Middle):\n    pass\n"
            ),
            f"{D}/facade/errors.py": hand("errors", "Rejected"),
        },
        ES,
    ),
    "a facade function slot names a class with a call base": (
        {
            f"{D}/errors.py": src(MAKE + "class Rejected(Exception, make()):\n    pass\n"),
            f"{D}/facade/api.py": f"from {S}.errors import Rejected\n\n\ndef get(x: Rejected) -> None:\n    pass\n",
        },
        BOTH,
    ),
    "a subscript base that is no generic": (
        {
            f"{D}/errors.py": "BASES = (ValueError,)\n\n\nclass Rejected(Exception, BASES[0]):\n    pass\n",
            f"{D}/facade/errors.py": hand("errors", "Rejected"),
        },
        ES,
    ),
    "a base bound to a variable by a call": (
        {
            f"{D}/errors.py": src(MAKE + "Base = make()\n\n\nclass Rejected(Exception, Base):\n    pass\n"),
            f"{D}/facade/errors.py": hand("errors", "Rejected"),
        },
        ES,
    ),
}

# A class one facade module hands out and another names in a slot: the copy's rule reports it, once.
COPY_CASES: dict[str, tuple[Files, Verdict]] = {
    "handed out by facade.contracts, returned by facade.api": (
        {
            f"{D}/errors.py": src(INFO),
            f"{D}/facade/contracts.py": hand("errors", "Info"),
            f"{D}/facade/api.py": GET_FROM_LOGIC,
        },
        FS,
    ),
    "handed out as Info as Details by facade.contracts, returned by facade.api": (
        {
            f"{D}/errors.py": src(INFO),
            f"{D}/facade/contracts.py": hand("errors", "Info as Details"),
            f"{D}/facade/api.py": GET_FROM_LOGIC,
        },
        FS,
    ),
    "handed out by facade.contracts, a field of a logic dataclass facade.api returns": (
        {
            f"{D}/errors.py": src(INFO + "\n\n@dataclasses.dataclass(frozen=True)\nclass Outer:\n    info: Info\n"),
            f"{D}/facade/contracts.py": hand("errors", "Info"),
            f"{D}/facade/api.py": get_returns("errors", "Outer"),
        },
        FS,
    ),
    "handed out by facade.contracts and the facade package, returned by facade.api": (
        {
            f"{D}/errors.py": src(INFO),
            f"{D}/facade/contracts.py": hand("errors", "Info"),
            f"{D}/facade/__init__.py": f"from {S}.facade.contracts import Info\n\n__all__ = ['Info']\n",
            f"{D}/facade/api.py": GET_FROM_LOGIC,
        },
        {SIGNATURE: 2},
    ),
    "a dataclass with a bare dict field, handed out by facade.contracts, returned by facade.api": (
        {
            f"{D}/errors.py": src(
                "@dataclasses.dataclass(frozen=True)\nclass Info:\n    extra: dict\n    order: Order\n"
            ),
            f"{D}/facade/contracts.py": hand("errors", "Info"),
            f"{D}/facade/api.py": GET_FROM_LOGIC,
        },
        {SIGNATURE: 2},
    ),
}

# The neighbors of the holes that give their verdict today and keep it after the change.
NEIGHBORS: dict[str, tuple[Files, Verdict]] = {
    "a star import in a module with a function and no __all__ hands out nothing": (
        {
            f"{D}/errors.py": src(EVERYTHING + ALL_FIVE),
            f"{D}/facade/errors.py": STAR + "\n\ndef noop() -> None:\n    pass\n",
        },
        NONE,
    ),
    "a star import of a module that does not exist": ({f"{D}/facade/errors.py": f"from {S}.nowhere import *\n"}, BOTH),
    "a star import under TYPE_CHECKING": (
        {
            f"{D}/errors.py": src(EVERYTHING + ALL_FIVE),
            f"{D}/facade/errors.py": f"from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    from {S}.errors import *\n",
        },
        NONE,
    ),
    "the five kinds by a named import": (
        {
            f"{D}/errors.py": src(EVERYTHING + ALL_FIVE),
            f"{D}/facade/errors.py": hand("errors", "Order", "Receipt", "Rejected", "place", "Loose"),
        },
        NAMED_FIVE,
    ),
    "a model by import-as, listed": (
        {f"{D}/facade/api.py": f"from {S}.models import Order as Thing\n\n__all__ = ['Thing']\n"},
        ES,
    ),
    "a constant, listed": ({f"{D}/facade/api.py": "Thing = 3\n\n__all__ = ['Thing']\n"}, NONE),
    # A handed-out type alias is judged by its expansion, as a slot that names it is.
    "a generic alias of a model, listed": (
        {f"{D}/facade/api.py": f"from {S}.models import Order\n\nThing = list[Order]\n\n__all__ = ['Thing']\n"},
        FS,
    ),
    # A member past a class is no hand-out; listed, the name is unknown.
    "a manager past a model, listed": (
        {f"{D}/facade/api.py": IMPORTS_MODELS + "\nThing = models.Order.objects\n\n__all__ = ['Thing']\n"},
        ES,
    ),
    "a generic base stays clean": (
        {
            f"{D}/errors.py": src(
                TV + "class Rejected(Exception, Generic[T]):\n    pass\n", extra="from typing import Generic\n"
            ),
            f"{D}/facade/errors.py": hand("errors", "Rejected"),
        },
        NONE,
    ),
    # A private copy is no copy the signature rule reads: both modules judge in full.
    "handed out under a private name by facade.contracts, returned by facade.api": (
        {
            f"{D}/errors.py": src(INFO),
            f"{D}/facade/contracts.py": hand("errors", "Info as _Info"),
            f"{D}/facade/api.py": GET_FROM_LOGIC,
        },
        {EXPORT: 2},
    ),
    # Each product keeps its own row.
    "handed out by another product's facade, returned by this product's facade.api": (
        {
            "products/other/__init__.py": "",
            "products/other/backend/__init__.py": "",
            "products/other/backend/facade/__init__.py": "",
            "products/other/backend/facade/contracts.py": hand("errors", "Info"),
            f"{D}/errors.py": src(INFO),
            f"{D}/facade/api.py": GET_FROM_LOGIC,
        },
        BOTH,
    ),
    "an exception handed out by facade.errors and named by facade.api": (
        {
            f"{D}/errors.py": src(EXC_METHOD),
            f"{D}/facade/errors.py": hand("errors", "Rejected"),
            f"{D}/facade/api.py": f"from {S}.errors import Rejected\n\n\ndef get(e: Rejected) -> None:\n    pass\n",
        },
        {EXPORT: 2},
    ),
}

CASES = {
    **table_cases(),
    **ATTACKS,
    **member_cases(),
    **CLASS_BODY_CASES,
    **STAR_CASES,
    **ASSIGNED_CASES,
    **BASE_CASES,
    **COPY_CASES,
    **NEIGHBORS,
}


def case_params() -> list[object]:
    params: list[object] = [pytest.param(files, verdict, id=name) for name, (files, verdict) in CASES.items()]
    for name, (files, verdict, reason) in HOLES.items():
        params.append(pytest.param(files, verdict, id=name, marks=pytest.mark.xfail(strict=True, reason=reason)))
    return params


def reported(out: Path) -> Verdict:
    """How many findings each rule of `RULES` gives."""
    counts = Counter(row[0] for row in read_lines(out / "findings.jsonl") if row[0] in RULES)
    return dict(counts)


@pytest.mark.parametrize("files, verdict", case_params())
def test_who_reports_a_slot_and_how_many_times(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: Files, verdict: Verdict
) -> None:
    out = clean_dump(tmp_path, BASE, files, monkeypatch)
    assert reported(out) == verdict
