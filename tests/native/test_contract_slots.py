"""The signature slots core exposes in a contract surface, and what the signature and export rules
read from them, in both engines."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from hoglint.core.producers import DeclarationProducer
from hoglint.native.parity import read_lines

from native.test_parity import dumps, same_in_both_engines

CONFIG = """
schema = 1

[discovery]
source_roots = ["."]
first_party = ["products"]
dataclass_decorators = ["dataclasses.dataclass", "products.shop.backend.kit.frozen"]

[fields]
package = { type = "id" }

[[assign]]
match = "products/{product}/**"
set = { package = "{product}" }

[shapes.contract]
data = ["dataclass", "frozen-dataclass"]
unknown = "error"
signatures = true

[shapes.loose]
allow = ["class", "function"]

[exports.facade]
within = "products/{product}"
paths = ["backend/facade/**"]
shape = "contract"

[exports.routes]
within = "products/{product}"
paths = ["backend/routes.py"]
shape = "loose"

[rules.facade-signature]
primitive = "signature"
on_unknown = "finding"
identity = ["src.module", "edge.callable", "edge.slot", "dst.symbol"]
ledger = false

[rules.export-shape]
primitive = "export"
exports = ["facade"]
identity = "module-symbol"
ledger = false
"""

# The files every repo below shares: a product with a facade package.
BASE = {
    "hoglint.toml": CONFIG,
    "products/__init__.py": "",
    "products/shop/__init__.py": "",
    "products/shop/backend/__init__.py": "",
    "products/shop/backend/kit.py": "def frozen(cls):\n    return cls\n",
    "products/shop/backend/facade/__init__.py": "",
}
CHARGE = "def charge(amount: int) -> str:\n    return ''\n"

REPO = {
    **BASE,
    "products/shop/backend/logic.py": (
        "from typing import ClassVar\n\n"
        "from products.shop.backend.kit import frozen\n\n\n"
        "Totals = dict[str, int]\n\n\n"
        "def charge(cls: type, amount: Totals, note) -> dict:\n    return {}\n\n\n"
        "def refund(order_id):\n    pass\n\n\n"
        "@frozen\nclass Receipt:\n    total: int\n    _audit: dict\n    kind: ClassVar[str]\n\n\n"
        "@frozen\nclass Base:\n    id: int\n\n\n"
        "class Failure(Exception):\n    receipt: Receipt\n    count: int\n    label: 'Receipt'\n    kind: ClassVar[Receipt]\n"
    ),
    "products/shop/backend/routes.py": "def register(router):\n    pass\n",
    "products/shop/backend/facade/api.py": (
        "from products.shop.backend.logic import Receipt\n"
        "from products.shop.backend.logic import charge as charge\n"
        "from products.shop.backend.logic import refund\n\n"
        "__all__ = []\n\n\n"
        "def close(order_id, *, flag: bool) -> None:\n    refund(order_id)\n\n\n"
        "def _helper(value):\n    pass\n\n\n"
        "class Orders:\n    def find(self, key: str):\n        pass\n"
    ),
    "products/shop/backend/facade/contracts.py": (
        "from dataclasses import dataclass\n\n"
        "from products.shop.backend.kit import frozen\n"
        "from products.shop.backend.logic import Base, Receipt\n\n\n"
        "@frozen\nclass Order(Base):\n    total: int\n    _audit: dict\n\n\n"
        "@dataclass\nclass Line:\n    meta: dict\n\n\n"
        "class Failed(Exception):\n    code: int\n    order: 'Order'\n"
    ),
}

# (module, callable, slot, type name, annotation, handed): every `exposes` row core writes. A
# dataclass a surface hands out has its fields at the surface wherever it is defined; `Receipt`
# imported only to use it in `api.py` is not handed out. An annotated attribute of a class that is
# no dataclass is a slot of the class, written as a method's slots are: every name in a surface,
# a name that is no plain builtin elsewhere.
EXPECTED_SLOTS = [
    ("products.shop.backend.facade.api", "close", "param:order_id", "", "missing", ""),
    ("products.shop.backend.facade.api", "close", "param:flag", "builtins.bool", "", ""),
    ("products.shop.backend.facade.api", "close", "return", "builtins.None", "", ""),
    ("products.shop.backend.facade.api", "Orders.find", "param:key", "builtins.str", "", ""),
    ("products.shop.backend.facade.api", "charge", "param:cls", "builtins.type", "", "alias"),
    ("products.shop.backend.facade.api", "charge", "param:amount", "products.shop.backend.logic.Totals", "", "alias"),
    ("products.shop.backend.facade.api", "charge", "param:amount", "builtins.dict", "", "alias"),
    ("products.shop.backend.facade.api", "charge", "param:amount", "builtins.str", "", "alias"),
    ("products.shop.backend.facade.api", "charge", "param:amount", "builtins.int", "", "alias"),
    ("products.shop.backend.facade.api", "charge", "param:note", "", "missing", "alias"),
    ("products.shop.backend.facade.api", "charge", "return", "builtins.dict", "", "alias"),
    ("products.shop.backend.facade.contracts", "Order.__init__", "field:id", "builtins.int", "", ""),
    ("products.shop.backend.facade.contracts", "Order.__init__", "field:total", "builtins.int", "", ""),
    ("products.shop.backend.facade.contracts", "Line.__init__", "field:meta", "builtins.dict", "", ""),
    ("products.shop.backend.facade.contracts", "Order.__init__", "field:_audit", "builtins.dict", "", ""),
    ("products.shop.backend.facade.contracts", "Receipt.__init__", "field:total", "builtins.int", "", "no-functions"),
    ("products.shop.backend.facade.contracts", "Receipt.__init__", "field:_audit", "builtins.dict", "", "no-functions"),
    ("products.shop.backend.logic", "charge", "param:amount", "products.shop.backend.logic.Totals", "", ""),
    ("products.shop.backend.logic", "Receipt.__init__", "field:kind", "typing.ClassVar", "", ""),
    ("products.shop.backend.logic", "Failure", "attr:receipt", "products.shop.backend.logic.Receipt", "", ""),
    ("products.shop.backend.logic", "Failure", "attr:label", "products.shop.backend.logic.Receipt", "", ""),
    ("products.shop.backend.logic", "Failure", "attr:kind", "typing.ClassVar", "", ""),
    ("products.shop.backend.logic", "Failure", "attr:kind", "products.shop.backend.logic.Receipt", "", ""),
    ("products.shop.backend.facade.contracts", "Failed", "attr:code", "builtins.int", "", ""),
    (
        "products.shop.backend.facade.contracts",
        "Failed",
        "attr:order",
        "products.shop.backend.facade.contracts.Order",
        "",
        "",
    ),
]


@pytest.fixture(scope="module")
def out(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    with pytest.MonkeyPatch.context() as monkeypatch:
        return dumps(tmp_path_factory.mktemp("contract"), REPO, monkeypatch)


def test_both_engines_agree_on_a_contract_surface(out: dict[str, Path]) -> None:
    same_in_both_engines(out)


@pytest.mark.parametrize("engine", ["python", "native"])
def test_a_contract_exposes_every_slot_of_what_it_hands_out(out: dict[str, Path], engine: str) -> None:
    slots = []
    for row in read_lines(out[engine] / "edges.jsonl"):
        if row[0] != "exposes":
            continue
        attrs = dict(row[-1])
        slots.append(
            (row[2], attrs["callable"], attrs["slot"], row[6], attrs.get("annotation", ""), attrs.get("handed", ""))
        )
    assert sorted(slots) == sorted(EXPECTED_SLOTS)


@pytest.mark.parametrize(
    "rule, identity",
    [
        ("facade-signature", ["products.shop.backend.facade.api", "close", "param:order_id", "?"]),
        ("facade-signature", ["products.shop.backend.facade.api", "charge", "param:note", "?"]),
        ("facade-signature", ["products.shop.backend.facade.api", "charge", "return", "builtins.dict"]),
        (
            "export-shape",
            ["products.shop.backend.facade.api.close", "products.shop.backend.facade.api", "export-unknown"],
        ),
        ("export-shape", ["builtins.dict", "products.shop.backend.facade.contracts", "export-unknown"]),
    ],
)
def test_the_rules_read_an_unannotated_slot_and_a_builtin_as_unknown(
    out: dict[str, Path], rule: str, identity: list[str]
) -> None:
    findings = [[row[0], row[1]] for row in read_lines(out["native"] / "findings.jsonl")]
    assert [rule, identity] in findings


FACADE = "products.shop.backend.facade.api"
HANDS_OUT_CHARGE = "from products.shop.backend.logic import charge as charge\n\n\ndef ping() -> None:\n    pass\n"
LOGIC = "products/shop/backend/logic"


def slot_rows(out: Path, module: str) -> list[tuple[str, str, str, str, str]]:
    """(callable, slot, type name, resolution, handed) of every `exposes` row at `module`."""
    rows = []
    for row in read_lines(out / "edges.jsonl"):
        if row[0] != "exposes" or row[2] != module:
            continue
        attrs = dict(row[-1])
        rows.append((attrs["callable"], attrs["slot"], row[6], row[8], attrs.get("handed", "")))
    return sorted(rows)


def both_engines(
    tmp_path: Path, base: dict[str, str], files: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> dict[str, Path]:
    """The dumps of both engines over `base` and `files`, which must agree and fail no file."""
    out = dumps(tmp_path, {**base, **files}, monkeypatch)
    same_in_both_engines(out)
    assert read_lines(out["native"] / "failed.jsonl") == []
    return out


def findings_of(out: Path, rule: str) -> list[list[str]]:
    """The identities of a rule's findings in a dump, sorted."""
    return sorted(row[1] for row in read_lines(out / "findings.jsonl") if row[0] == rule)


# A handed-out def is read from the file whose binding the module table holds, never from a stub
# twin or a module of the same name that the table does not read.
TWIN_CASES = {
    "stub twin with fewer defs": {
        f"{LOGIC}.py": "def helper(x: bytes) -> None:\n    pass\n\n\n" + CHARGE,
        f"{LOGIC}.pyi": "def charge(amount: int) -> str: ...\n",
    },
    "stub twin in another order": {
        f"{LOGIC}.py": "def refund(x: float) -> None:\n    pass\n\n\n" + CHARGE,
        f"{LOGIC}.pyi": "def charge(amount: int) -> str: ...\ndef refund(x: float) -> None: ...\n",
    },
    "package of the same name": {
        f"{LOGIC}.py": "def refund(x: float) -> None:\n    pass\n\n\n" + CHARGE,
        f"{LOGIC}/__init__.py": "def charge(total: bytes) -> bytes:\n    return b''\n\n\ndef other() -> None:\n    pass\n",
    },
}


@pytest.mark.parametrize("case", TWIN_CASES)
def test_a_handed_out_def_is_read_from_the_file_the_module_table_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    files = {**TWIN_CASES[case], "products/shop/backend/facade/api.py": HANDS_OUT_CHARGE}
    out = both_engines(tmp_path, BASE, files, monkeypatch)
    handed = [row for row in slot_rows(out["native"], FACADE) if row[4]]
    assert handed == [
        ("charge", "param:amount", "builtins.int", "external", "alias"),
        ("charge", "return", "builtins.str", "external", "alias"),
    ]


MODELS = {"products/shop/backend/models.py": "class OrderModel:\n    pass\n"}
ORDERS_USES_ORDER_MAP = "from ..types import OrderMap\n\n\ndef orders() -> OrderMap:\n    return {}\n"
ORDER_MAP = ("orders", "return", "products.shop.backend.types.OrderMap", "resolved", "")
ORDER_MODEL = ("orders", "return", "products.shop.backend.models.OrderModel", "resolved", "")
DICT, STR, INT, LIST = (
    ("orders", "return", f"builtins.{name}", "external", "") for name in ("dict", "str", "int", "list")
)


def test_an_alias_chain_deeper_than_the_limit_leaves_an_unresolved_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chain = "".join(f"A{at} = list[A{at + 1}]\n" for at in range(40)) + "A40 = int\n"
    files = {
        "products/shop/backend/types.py": chain + "OrderMap = A0\n",
        "products/shop/backend/facade/api.py": ORDERS_USES_ORDER_MAP,
    }
    out = both_engines(tmp_path, BASE, files, monkeypatch)
    rows = slot_rows(out["native"], FACADE)
    assert [row for row in rows if row[3] == "unresolved"] == [("orders", "return", "A32", "unresolved", "")]
    assert ("orders", "return", "products.shop.backend.types.A31", "resolved", "") in rows
    assert ("orders", "return", "products.shop.backend.types.A33", "resolved", "") not in rows


def charge_by(clause: str) -> list[tuple[str, str, str, str, str]]:
    return [
        ("charge", "param:amount", "builtins.int", "external", clause),
        ("charge", "return", "builtins.str", "external", clause),
    ]


CHARGE_LOGIC = {f"{LOGIC}.py": CHARGE}
IMPORTS_CHARGE = "from products.shop.backend.logic import charge\n\n\ndef ping() -> None:\n    pass\n\n\n"
PING = [("ping", "return", "builtins.None", "external", "")]
ALL_UNKNOWN = [("__all__", "names", "__all__", "unresolved", "all")]
# Every `__all__` form whose names a reader can list hands them out; one built by code or set
# under a condition makes what the module hands out unknown, and an unknown is never clean.
DUNDER_ALL_CASES = {
    "annotated": ('__all__: list[str] = ["charge"]\n', charge_by("all")),
    "extended with a literal": ('__all__ = ["ping"]\n__all__ += ["charge"]\n', charge_by("all")),
    "a sum of literals": ('__all__ = ["ping"] + ("charge",)\n', charge_by("all")),
    "built by a comprehension": ('__all__ = [name for name in ("charge",)]\n', ALL_UNKNOWN),
    "set under a condition": ('if ping:\n    __all__ = ["charge"]\n', ALL_UNKNOWN),
    "extended with a name": ('__all__ = ["ping"]\n__all__ += NAMES\n', ALL_UNKNOWN),
    "an item that is no string": ('__all__ = ["ping", charge.__name__]\n', ALL_UNKNOWN),
    "changed by a method": ('__all__ = ["ping"]\n__all__.extend(["charge"])\n', ALL_UNKNOWN),
    "sorted keys of a module-level dict": ('_LAZY = {"charge": "logic"}\n__all__ = sorted(_LAZY)\n', charge_by("all")),
    "list of a module-level tuple": ('_NAMES = ("charge", "ping")\n__all__ = list(_NAMES)\n', charge_by("all")),
    "a starred module-level set": ('_NAMES = {"charge"}\n__all__ = [*_NAMES]\n', charge_by("all")),
    "a starred sorted name beside literals": (
        '_LAZY = {"charge": "logic"}\n__all__ = [*sorted(_LAZY), "ping"]\n',
        charge_by("all"),
    ),
    "sorted over a starred name in a tuple": (
        '_LAZY = {"charge": "logic"}\n__all__ = sorted((*_LAZY, "ping"))\n',
        charge_by("all"),
    ),
    "a name assigned twice": ('_LAZY = {"charge": "logic"}\n_LAZY = {}\n__all__ = sorted(_LAZY)\n', ALL_UNKNOWN),
    "a name assigned under a condition": (
        'if ping:\n    _LAZY = {"charge": "logic"}\n__all__ = sorted(_LAZY)\n',
        ALL_UNKNOWN,
    ),
    "a name holding no strings": ("_LAZY = {1: 2}\n__all__ = sorted(_LAZY)\n", ALL_UNKNOWN),
    "a call of anything else": ('_LAZY = {"charge": "logic"}\n__all__ = names(_LAZY)\n', ALL_UNKNOWN),
}


@pytest.mark.parametrize("case", DUNDER_ALL_CASES)
def test_every_readable_dunder_all_hands_out_and_an_unreadable_one_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    dunder_all, expected = DUNDER_ALL_CASES[case]
    files = {**CHARGE_LOGIC, "products/shop/backend/facade/api.py": IMPORTS_CHARGE + dunder_all}
    out = both_engines(tmp_path, BASE, files, monkeypatch)
    assert slot_rows(out["native"], FACADE) == sorted(PING + expected)
    findings = [row[1] for row in read_lines(out["native"] / "findings.jsonl") if row[0] == "facade-signature"]
    assert ([FACADE, "__all__", "names", "__all__"] in findings) == (expected == ALL_UNKNOWN)


def test_only_a_method_leaves_out_its_first_parameter_whatever_its_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = {
        f"{LOGIC}.py": "def frozen(cls: type) -> type:\n    return cls\n",
        "products/shop/backend/facade/api.py": (
            "from ..logic import frozen as frozen\n\n\n"
            "def build(cls: int, data: str) -> None:\n    pass\n\n\n"
            "class Orders:\n"
            "    def meta(mcs, key: str) -> None:\n        pass\n\n"
            "    @classmethod\n    def load(klass, key: str) -> None:\n        pass\n\n"
            "    @staticmethod\n    def make(self: int) -> None:\n        pass\n"
        ),
    }
    out = both_engines(tmp_path, BASE, files, monkeypatch)
    assert slot_rows(out["native"], FACADE) == sorted(
        [
            ("frozen", "param:cls", "builtins.type", "external", "alias"),
            ("frozen", "return", "builtins.type", "external", "alias"),
            ("build", "param:cls", "builtins.int", "external", ""),
            ("build", "param:data", "builtins.str", "external", ""),
            ("build", "return", "builtins.None", "external", ""),
            ("Orders.meta", "param:key", "builtins.str", "external", ""),
            ("Orders.meta", "return", "builtins.None", "external", ""),
            ("Orders.load", "param:key", "builtins.str", "external", ""),
            ("Orders.load", "return", "builtins.None", "external", ""),
            ("Orders.make", "param:self", "builtins.int", "external", ""),
            ("Orders.make", "return", "builtins.None", "external", ""),
        ]
    )


def test_a_surface_hands_out_nothing_its_own_export_already_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = {
        "products/shop/backend/facade/__init__.py": "from .api import charge\n",
        "products/shop/backend/facade/api.py": CHARGE,
        "products/other/__init__.py": "",
        "products/other/backend/__init__.py": "",
        "products/other/backend/facade/__init__.py": "",
        "products/other/backend/facade/contracts.py": "from products.shop.backend.facade.api import charge\n",
    }
    out = both_engines(tmp_path, BASE, files, monkeypatch)
    assert slot_rows(out["native"], "products.shop.backend.facade") == []
    assert slot_rows(out["native"], FACADE) == charge_by("")
    assert slot_rows(out["native"], "products.other.backend.facade.contracts") == charge_by("no-functions")


@pytest.mark.parametrize("depth", [4, 30])
def test_an_alias_chain_that_doubles_at_every_step_gives_each_type_name_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, depth: int
) -> None:
    chain = "".join(f"A{at} = tuple[A{at + 1}, A{at + 1}]\n" for at in range(depth)) + f"A{depth} = int\n"
    files = {
        "products/shop/backend/types.py": chain,
        "products/shop/backend/facade/api.py": "from ..types import A0\n\n\ndef orders(x: list[A0]) -> None:\n    pass\n",
    }
    out = both_engines(tmp_path, BASE, files, monkeypatch)
    names = [row[2] for row in slot_rows(out["native"], FACADE) if row[1] == "param:x"]
    chained = [f"products.shop.backend.types.A{at}" for at in range(depth + 1)]
    assert sorted(names) == sorted(["builtins.list", "builtins.tuple", "builtins.int", *chained])


def test_a_module_with_no_function_hands_out_only_the_imports_it_does_not_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = {
        f"{LOGIC}.py": (
            "from .kit import frozen\n\n\n" + CHARGE + "\n\n"
            "def refund(x: float) -> None:\n    pass\n\n\n"
            "def ping_target() -> None:\n    pass\n\n\n"
            "def untouched(y: bytes) -> None:\n    pass\n\n\n"
            "def rebound(z: int) -> None:\n    pass\n\n\n"
            "@frozen\nclass Base:\n    id: int\n\n\n"
            "@frozen\nclass Receipt:\n    total: int\n"
        ),
        "products/shop/backend/facade/api.py": (
            "from ..kit import frozen\n"
            "from ..logic import Base, Receipt, charge, rebound, refund, untouched\n"
            "from ..logic import ping_target as ping_target\n\n"
            '__all__ = ["refund"]\n\n'
            "other = rebound\n\n\n"
            "@frozen\nclass Order(Base):\n    note: 'Receipt'\n\n"
            "    def total(self) -> None:\n        charge(1)\n        refund(2)\n        ping_target()\n"
        ),
    }
    out = both_engines(tmp_path, BASE, files, monkeypatch)
    handed = [row for row in slot_rows(out["native"], FACADE) if row[4]]
    assert handed == sorted(
        [
            ("refund", "param:x", "builtins.float", "external", "all"),
            ("refund", "return", "builtins.None", "external", "all"),
            ("ping_target", "return", "builtins.None", "external", "alias"),
            ("untouched", "param:y", "builtins.bytes", "external", "no-functions"),
            ("untouched", "return", "builtins.None", "external", "no-functions"),
        ]
    )


TOTALS_IN_PY = "Totals = dict[str, int]\n\n\ndef charge(amount: Totals) -> None:\n    pass\n"
# The slots of `charge` in a file whose `Totals` is the module table's (`dict[str, int]`).
CHARGE_WITH_TOTALS = [
    ("charge", "param:amount", f"{FACADE}.Totals", "resolved", ""),
    ("charge", "param:amount", "builtins.dict", "external", ""),
    ("charge", "param:amount", "builtins.int", "external", ""),
    ("charge", "param:amount", "builtins.str", "external", ""),
    ("charge", "return", "builtins.None", "external", ""),
]
# A type alias is read from the file whose binding the module table holds: the `.py` for a name it
# binds, the stub for a name only the stub binds; a package of the same name is never read.
ALIAS_FILE_CASES = {
    "stub twin": (
        {
            "products/shop/backend/facade/api.py": TOTALS_IN_PY,
            "products/shop/backend/facade/api.pyi": (
                "Totals = dict[str, bytes]\nStubOnly = list[float]\n\n\n"
                "def charge(amount: Totals) -> None: ...\ndef total(x: StubOnly) -> None: ...\n"
            ),
        },
        [
            *CHARGE_WITH_TOTALS,
            *CHARGE_WITH_TOTALS,
            ("total", "param:x", f"{FACADE}.StubOnly", "resolved", ""),
            ("total", "param:x", "builtins.float", "external", ""),
            ("total", "param:x", "builtins.list", "external", ""),
            ("total", "return", "builtins.None", "external", ""),
        ],
    ),
    "package of the same name": (
        {
            "products/shop/backend/facade/api.py": TOTALS_IN_PY,
            "products/shop/backend/facade/api/__init__.py": TOTALS_IN_PY.replace("int", "bytes", 1),
        },
        [*CHARGE_WITH_TOTALS, *CHARGE_WITH_TOTALS],
    ),
}


# An alias assigned in another first-party module is followed there, through the link's own
# resolution, and the names in it are looked up where it is assigned.
def orders_with(files: dict[str, str]) -> dict[str, str]:
    """A facade whose `orders` returns `OrderMap`, from the given types modules."""
    return {**MODELS, **files, "products/shop/backend/facade/api.py": ORDERS_USES_ORDER_MAP}


IMPORTED_ALIAS_CASES = {
    "imported": (
        orders_with(
            {
                "products/shop/backend/types.py": (
                    "from products.shop.backend.models import OrderModel\n\nOrderMap = dict[str, OrderModel]\n"
                ),
            }
        ),
        [ORDER_MAP, DICT, STR, ORDER_MODEL],
    ),
    "re-exported, then imported again in the alias's module": (
        orders_with(
            {
                "products/shop/backend/types.py": "from products.shop.backend.more import OrderMap as OrderMap\n",
                "products/shop/backend/more.py": (
                    "from products.shop.backend.models import OrderModel\nfrom .ids import Id\n\n"
                    "OrderMap = dict[Id, OrderModel]\n"
                ),
                "products/shop/backend/ids.py": "Id = tuple[int, str]\n",
            }
        ),
        [
            ("orders", "return", "products.shop.backend.types.OrderMap", "resolved", ""),
            DICT,
            ("orders", "return", "products.shop.backend.ids.Id", "resolved", ""),
            ("orders", "return", "builtins.tuple", "external", ""),
            INT,
            STR,
            ORDER_MODEL,
        ],
    ),
    "a cycle": (
        orders_with(
            {
                "products/shop/backend/types.py": "from .other import Other\n\nOrderMap = list[Other]\n",
                "products/shop/backend/other.py": "from .types import OrderMap\n\nOther = list[OrderMap]\n",
            }
        ),
        [
            ORDER_MAP,
            ORDER_MAP,
            LIST,
            LIST,
            ("orders", "return", "products.shop.backend.other.Other", "resolved", ""),
        ],
    ),
}


def unfollowed(bound: str, target: str, clause: str) -> tuple[str, str, str, str, str]:
    return (bound, "signature", f"products.shop.backend.{target}", "unresolved", clause)


PING_DEF = "\n\ndef ping() -> None:\n    pass\n"
# A handed-out name the engine cannot follow to a def is an unresolved row, never no row; a
# constant or a module needs none. A star import hands out the public names of its source.
UNFOLLOWED_CASES = {
    "a file discovery excludes": (
        {
            "hoglint.toml": CONFIG.replace(
                'first_party = ["products"]',
                'first_party = ["products"]\nexclude = ["products/shop/backend/hidden.py"]',
            ),
            "products/shop/backend/hidden.py": CHARGE,
            "products/shop/backend/facade/api.py": "from ..hidden import charge as charge\n" + PING_DEF,
        },
        [*PING, unfollowed("charge", "hidden.charge", "alias")],
    ),
    "a re-export cycle": (
        {
            "products/shop/backend/a.py": "from .b import charge as charge\n",
            "products/shop/backend/b.py": "from .a import charge as charge\n",
            "products/shop/backend/facade/api.py": "from ..a import charge as charge\n" + PING_DEF,
        },
        [*PING, unfollowed("charge", "a.charge", "alias")],
    ),
    "a chain through an external step": (
        {
            "products/shop/backend/ext.py": "from requests import Session\n",
            "products/shop/backend/facade/api.py": "from ..ext import Session as Session\n" + PING_DEF,
        },
        [*PING, unfollowed("Session", "ext.Session", "alias")],
    ),
    "a constant and a module": (
        {
            "products/shop/backend/consts.py": "LIMIT = 3\n",
            "products/shop/backend/facade/api.py": (
                "from .. import consts as consts\nfrom ..consts import LIMIT as LIMIT\n" + PING_DEF
            ),
        },
        PING,
    ),
    "a name only a stub defines": (
        {
            f"{LOGIC}.py": "def __getattr__(name):\n    raise AttributeError(name)\n",
            f"{LOGIC}.pyi": "def charge(amount: int) -> str: ...\n",
            "products/shop/backend/facade/api.py": "from ..logic import charge as charge\n" + PING_DEF,
        },
        [*PING, *charge_by("alias")],
    ),
    "a star import listed in __all__": (
        {
            f"{LOGIC}.py": (
                '__all__ = ["charge", "refund"]\n\n\ndef charge(amount: int) -> str:\n    return ""\n\n\n'
                "def refund(x: float) -> None:\n    pass\n"
            ),
            "products/shop/backend/facade/api.py": 'from ..logic import *\n\n__all__ = ["charge", "ping"]\n' + PING_DEF,
        },
        [*PING, *charge_by("all")],
    ),
    "a star import in a module with no function": (
        {
            f"{LOGIC}.py": CHARGE + "\n\ndef _helper(x: float) -> None:\n    pass\n",
            "products/shop/backend/facade/api.py": "from ..logic import *\n",
        },
        charge_by("no-functions"),
    ),
    "a star import of a source whose __all__ is unreadable": (
        {
            f"{LOGIC}.py": "__all__ = sorted(NAMES)\n\n\n" + CHARGE,
            "products/shop/backend/facade/api.py": "from ..logic import *\n",
        },
        [unfollowed("*", "logic.*", "no-functions")],
    ),
}


BASES = (
    "from dataclasses import dataclass\n\n\n"
    "@dataclass\nclass Base:\n    id: int\n    name: str\n\n\n"
    "class Mixin:\n    note: str\n"
)
CHILD = "@dataclass\nclass Child(Mixin, Base):\n    name: bytes\n    extra: float\n"


def child_fields(callable_name: str, handed: str) -> list[tuple[str, str, str, str, str]]:
    return [
        (callable_name, "field:id", "builtins.int", "external", handed),
        (callable_name, "field:name", "builtins.bytes", "external", handed),
        (callable_name, "field:extra", "builtins.float", "external", handed),
    ]


# A dataclass's `__init__` takes the fields of every dataclass along its MRO, base first, a
# redefinition replacing the earlier one; a base that does not resolve leaves its fields unknown.
INHERITED_FIELD_CASES = {
    "the surface's own class": (
        {"products/shop/backend/facade/api.py": BASES + "\n\n" + CHILD},
        [
            ("Base.__init__", "field:id", "builtins.int", "external", ""),
            ("Base.__init__", "field:name", "builtins.str", "external", ""),
            ("Mixin", "attr:note", "builtins.str", "external", ""),
            *child_fields("Child.__init__", ""),
        ],
    ),
    "a class the surface hands out": (
        {
            f"{LOGIC}.py": BASES + "\n\n" + CHILD,
            "products/shop/backend/facade/api.py": "from ..logic import Child as Child\n",
        },
        child_fields("Child.__init__", "no-functions"),
    ),
    "a base that does not resolve": (
        {
            "products/shop/backend/facade/api.py": (
                "from dataclasses import dataclass\n\nfrom ..nowhere import Missing\n\n\n"
                "@dataclass\nclass Child(Missing):\n    extra: float\n"
            ),
        },
        [
            ("Child.__init__", "fields", "products.shop.backend.nowhere.Missing", "unresolved", ""),
            ("Child.__init__", "field:extra", "builtins.float", "external", ""),
        ],
    ),
}
# Every case above: the files beside BASE, and every `exposes` row at the facade.
SLOT_CASES = {**ALIAS_FILE_CASES, **IMPORTED_ALIAS_CASES, **UNFOLLOWED_CASES, **INHERITED_FIELD_CASES}


@pytest.mark.parametrize("case", SLOT_CASES)
def test_a_facade_exposes_the_rows_each_case_expects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    files, expected = SLOT_CASES[case]
    out = both_engines(tmp_path, BASE, files, monkeypatch)
    assert slot_rows(out["native"], FACADE) == sorted(expected)


# Each class's dataclass base is worked out once: a plain base class that many classes, plain and
# dataclass-like, inherit is not walked again for each of them. A class with no field of its own,
# outside a contract, has no slot the answer could add, so its lineage is not walked for it.
BASE_CLASSES = (
    "class Model:\n    pass\n\n\n"
    "class Plain:\n    note: str\n\n\n"
    "class Payload(Plain, Model):\n    total: int\n\n\n"
    "class Other(Plain):\n    pass\n"
)


def test_each_class_walks_its_lineage_for_a_dataclass_base_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    walked: list[tuple[int, str, int]] = []
    find = DeclarationProducer.find_dataclass_base

    def counted(self: DeclarationProducer, context: Any, scope: Any, idx: int) -> bool:
        walked.append((id(self), scope.path, idx))
        return find(self, context, scope, idx)

    monkeypatch.setattr(DeclarationProducer, "find_dataclass_base", counted)
    config = CONFIG.replace(
        "dataclass_decorators =", 'dataclass_bases = ["products.shop.backend.logic.Model"]\ndataclass_decorators ='
    )
    files = {
        "hoglint.toml": config,
        "products/shop/backend/logic.py": BASE_CLASSES,
        "products/shop/backend/facade/api.py": "from ..logic import Payload\n",
    }
    out = both_engines(tmp_path, BASE, files, monkeypatch)
    assert ("Payload.__init__", "field:total", "builtins.int", "external", "no-functions") in slot_rows(
        out["native"], FACADE
    )
    assert walked and max(Counter(walked).values()) == 1
    other = BASE_CLASSES.split("\n\n\n").index("class Other(Plain):\n    pass\n")
    assert ("products/shop/backend/logic.py", other) not in {(path, idx) for _, path, idx in walked}


def test_the_slot_rows_of_a_lazy_name_sit_at_the_line_of_its_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lazy = '"""Lazy names."""\n\n_LAZY = {"charge": "products.shop.backend.logic"}\n\n\ndef __getattr__(name):\n    raise AttributeError(name)\n'
    files = {f"{LOGIC}.py": CHARGE, "products/shop/backend/facade/api.py": lazy}
    out = both_engines(tmp_path, BASE, files, monkeypatch)
    places = {
        (row[4], row[5])
        for row in read_lines(out["native"] / "edges.jsonl")
        if row[0] == "exposes" and row[2] == FACADE and dict(row[-1]).get("handed") == "lazy"
    }
    assert places == {(3, 0)}
