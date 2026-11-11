"""The signature slots core exposes in a contract surface, and what the signature and export rules
read from them, in both engines."""

from __future__ import annotations

from pathlib import Path

import pytest

from hoglint.native.parity import compare_dumps, read_lines

from native.test_parity import dumps

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

REPO = {
    "hoglint.toml": CONFIG,
    "products/__init__.py": "",
    "products/shop/__init__.py": "",
    "products/shop/backend/__init__.py": "",
    "products/shop/backend/kit.py": "def frozen(cls):\n    return cls\n",
    "products/shop/backend/logic.py": (
        "from typing import ClassVar\n\n"
        "from products.shop.backend.kit import frozen\n\n\n"
        "Totals = dict[str, int]\n\n\n"
        "def charge(cls: type, amount: Totals, note) -> dict:\n    return {}\n\n\n"
        "def refund(order_id):\n    pass\n\n\n"
        "@frozen\nclass Receipt:\n    total: int\n    _audit: dict\n    kind: ClassVar[str]\n\n\n"
        "@frozen\nclass Base:\n    id: int\n"
    ),
    "products/shop/backend/routes.py": "def register(router):\n    pass\n",
    "products/shop/backend/facade/__init__.py": "",
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
        "@dataclass\nclass Line:\n    meta: dict\n"
    ),
}

# (module, callable, slot, type name, annotation, handed): every `exposes` row core writes. A
# dataclass a surface hands out has its fields at the surface wherever it is defined; `Receipt`
# imported only to use it in `api.py` is not handed out.
EXPECTED_SLOTS = [
    ("products.shop.backend.facade.api", "close", "param:order_id", "", "missing", ""),
    ("products.shop.backend.facade.api", "close", "param:flag", "builtins.bool", "", ""),
    ("products.shop.backend.facade.api", "close", "return", "builtins.None", "", ""),
    ("products.shop.backend.facade.api", "Orders.find", "param:key", "builtins.str", "", ""),
    ("products.shop.backend.facade.api", "charge", "param:amount", "products.shop.backend.logic.Totals", "", "alias"),
    ("products.shop.backend.facade.api", "charge", "param:amount", "builtins.dict", "", "alias"),
    ("products.shop.backend.facade.api", "charge", "param:amount", "builtins.str", "", "alias"),
    ("products.shop.backend.facade.api", "charge", "param:amount", "builtins.int", "", "alias"),
    ("products.shop.backend.facade.api", "charge", "param:note", "", "missing", "alias"),
    ("products.shop.backend.facade.api", "charge", "return", "builtins.dict", "", "alias"),
    ("products.shop.backend.facade.contracts", "Order.__init__", "field:total", "builtins.int", "", ""),
    ("products.shop.backend.facade.contracts", "Line.__init__", "field:meta", "builtins.dict", "", ""),
    ("products.shop.backend.facade.contracts", "Order.__init__", "field:_audit", "builtins.dict", "", ""),
    ("products.shop.backend.facade.contracts", "Receipt.__init__", "field:total", "builtins.int", "", "no-functions"),
    ("products.shop.backend.facade.contracts", "Receipt.__init__", "field:_audit", "builtins.dict", "", "no-functions"),
    ("products.shop.backend.facade.contracts", "Base.__init__", "field:id", "builtins.int", "", "no-functions"),
    ("products.shop.backend.logic", "charge", "param:amount", "products.shop.backend.logic.Totals", "", ""),
    ("products.shop.backend.logic", "Receipt.__init__", "field:kind", "typing.ClassVar", "", ""),
]


@pytest.fixture(scope="module")
def out(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    with pytest.MonkeyPatch.context() as monkeypatch:
        return dumps(tmp_path_factory.mktemp("contract"), REPO, monkeypatch)


def test_both_engines_agree_on_a_contract_surface(out: dict[str, Path]) -> None:
    assert [diff.render() for diff in compare_dumps(out["python"], out["native"]) if not diff.equal] == []


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


# The files every case below shares: a product with a facade package.
BASE = {
    "hoglint.toml": CONFIG,
    "products/__init__.py": "",
    "products/shop/__init__.py": "",
    "products/shop/backend/__init__.py": "",
    "products/shop/backend/kit.py": "def frozen(cls):\n    return cls\n",
    "products/shop/backend/facade/__init__.py": "",
}
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


def both_engines(tmp_path: Path, files: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    out = dumps(tmp_path, {**BASE, **files}, monkeypatch)
    assert [diff.render() for diff in compare_dumps(out["python"], out["native"]) if not diff.equal] == []
    assert read_lines(out["native"] / "failed.jsonl") == []
    return out


# A handed-out def is read from the file whose binding the module table holds, never from a stub
# twin or a module of the same name that the table does not read.
TWIN_CASES = {
    "stub twin with fewer defs": {
        f"{LOGIC}.py": "def helper(x: bytes) -> None:\n    pass\n\n\ndef charge(amount: int) -> str:\n    return ''\n",
        f"{LOGIC}.pyi": "def charge(amount: int) -> str: ...\n",
    },
    "stub twin in another order": {
        f"{LOGIC}.py": "def refund(x: float) -> None:\n    pass\n\n\ndef charge(amount: int) -> str:\n    return ''\n",
        f"{LOGIC}.pyi": "def charge(amount: int) -> str: ...\ndef refund(x: float) -> None: ...\n",
    },
    "package of the same name": {
        f"{LOGIC}.py": "def refund(x: float) -> None:\n    pass\n\n\ndef charge(amount: int) -> str:\n    return ''\n",
        f"{LOGIC}/__init__.py": "def charge(total: bytes) -> bytes:\n    return b''\n\n\ndef other() -> None:\n    pass\n",
    },
}


@pytest.mark.parametrize("case", TWIN_CASES)
def test_a_handed_out_def_is_read_from_the_file_the_module_table_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    files = {**TWIN_CASES[case], "products/shop/backend/facade/api.py": HANDS_OUT_CHARGE}
    out = both_engines(tmp_path, files, monkeypatch)
    handed = [row for row in slot_rows(out["native"], FACADE) if row[4]]
    assert handed == [
        ("charge", "param:amount", "builtins.int", "external", "alias"),
        ("charge", "return", "builtins.str", "external", "alias"),
    ]


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


MODELS = {"products/shop/backend/models.py": "class OrderModel:\n    pass\n"}
ORDERS_USES_ORDER_MAP = "from ..types import OrderMap\n\n\ndef orders() -> OrderMap:\n    return {}\n"
ORDER_MAP = ("orders", "return", "products.shop.backend.types.OrderMap", "resolved", "")
ORDER_MODEL = ("orders", "return", "products.shop.backend.models.OrderModel", "resolved", "")
DICT, STR, INT, LIST = (
    ("orders", "return", f"builtins.{name}", "external", "") for name in ("dict", "str", "int", "list")
)
# An alias assigned in another first-party module is followed there, through the link's own
# resolution, and the names in it are looked up where it is assigned.
IMPORTED_ALIAS_CASES = {
    "imported": (
        {
            "products/shop/backend/types.py": (
                "from products.shop.backend.models import OrderModel\n\nOrderMap = dict[str, OrderModel]\n"
            ),
        },
        [ORDER_MAP, DICT, STR, ORDER_MODEL],
    ),
    "re-exported, then imported again in the alias's module": (
        {
            "products/shop/backend/types.py": "from products.shop.backend.more import OrderMap as OrderMap\n",
            "products/shop/backend/more.py": (
                "from products.shop.backend.models import OrderModel\nfrom .ids import Id\n\n"
                "OrderMap = dict[Id, OrderModel]\n"
            ),
            "products/shop/backend/ids.py": "Id = tuple[int, str]\n",
        },
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
        {
            "products/shop/backend/types.py": "from .other import Other\n\nOrderMap = list[Other]\n",
            "products/shop/backend/other.py": "from .types import OrderMap\n\nOther = list[OrderMap]\n",
        },
        [
            ORDER_MAP,
            ORDER_MAP,
            LIST,
            LIST,
            ("orders", "return", "products.shop.backend.other.Other", "resolved", ""),
        ],
    ),
}


@pytest.mark.parametrize("case", IMPORTED_ALIAS_CASES)
def test_an_imported_alias_is_expanded_where_it_is_assigned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    files, expected = IMPORTED_ALIAS_CASES[case]
    files = {**MODELS, **files, "products/shop/backend/facade/api.py": ORDERS_USES_ORDER_MAP}
    out = both_engines(tmp_path, files, monkeypatch)
    assert slot_rows(out["native"], FACADE) == sorted(expected)


def test_an_alias_chain_deeper_than_the_limit_leaves_an_unresolved_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chain = "".join(f"A{at} = list[A{at + 1}]\n" for at in range(40)) + "A40 = int\n"
    files = {
        "products/shop/backend/types.py": chain + "OrderMap = A0\n",
        "products/shop/backend/facade/api.py": ORDERS_USES_ORDER_MAP,
    }
    out = both_engines(tmp_path, files, monkeypatch)
    rows = slot_rows(out["native"], FACADE)
    assert [row for row in rows if row[3] == "unresolved"] == [("orders", "return", "A32", "unresolved", "")]
    assert ("orders", "return", "products.shop.backend.types.A31", "resolved", "") in rows
    assert ("orders", "return", "products.shop.backend.types.A33", "resolved", "") not in rows


CHARGE_LOGIC = {f"{LOGIC}.py": "def charge(amount: int) -> str:\n    return ''\n"}
IMPORTS_CHARGE = "from products.shop.backend.logic import charge\n\n\ndef ping() -> None:\n    pass\n\n\n"
PING = [("ping", "return", "builtins.None", "external", "")]
CHARGE_BY_ALL = [
    ("charge", "param:amount", "builtins.int", "external", "all"),
    ("charge", "return", "builtins.str", "external", "all"),
]
ALL_UNKNOWN = [("__all__", "names", "__all__", "unresolved", "all")]
# Every `__all__` form whose names a reader can list hands them out; one built by code or set
# under a condition makes what the module hands out unknown, and an unknown is never clean.
DUNDER_ALL_CASES = {
    "annotated": ('__all__: list[str] = ["charge"]\n', CHARGE_BY_ALL),
    "extended with a literal": ('__all__ = ["ping"]\n__all__ += ["charge"]\n', CHARGE_BY_ALL),
    "a sum of literals": ('__all__ = ["ping"] + ("charge",)\n', CHARGE_BY_ALL),
    "built by a comprehension": ('__all__ = [name for name in ("charge",)]\n', ALL_UNKNOWN),
    "set under a condition": ('if ping:\n    __all__ = ["charge"]\n', ALL_UNKNOWN),
    "extended with a name": ('__all__ = ["ping"]\n__all__ += NAMES\n', ALL_UNKNOWN),
    "an item that is no string": ('__all__ = ["ping", charge.__name__]\n', ALL_UNKNOWN),
    "changed by a method": ('__all__ = ["ping"]\n__all__.extend(["charge"])\n', ALL_UNKNOWN),
}


@pytest.mark.parametrize("case", DUNDER_ALL_CASES)
def test_every_readable_dunder_all_hands_out_and_an_unreadable_one_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    dunder_all, expected = DUNDER_ALL_CASES[case]
    files = {**CHARGE_LOGIC, "products/shop/backend/facade/api.py": IMPORTS_CHARGE + dunder_all}
    out = both_engines(tmp_path, files, monkeypatch)
    assert slot_rows(out["native"], FACADE) == sorted(PING + expected)
    findings = [row[1] for row in read_lines(out["native"] / "findings.jsonl") if row[0] == "facade-signature"]
    assert ([FACADE, "__all__", "names", "__all__"] in findings) == (expected == ALL_UNKNOWN)


@pytest.mark.parametrize("case", ALIAS_FILE_CASES)
def test_an_alias_is_read_from_the_file_the_module_table_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    files, expected = ALIAS_FILE_CASES[case]
    out = both_engines(tmp_path, files, monkeypatch)
    assert slot_rows(out["native"], FACADE) == sorted(expected)
