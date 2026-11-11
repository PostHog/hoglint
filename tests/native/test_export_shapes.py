"""What the export primitive reads of a surface module: the shape of each class it hands out, in
both engines."""

from __future__ import annotations

from pathlib import Path

import pytest

from hoglint.native.parity import read_lines

from native.test_parity import dumps, same_in_both_engines

CONFIG = """
schema = 1

[discovery]
source_roots = ["."]
first_party = ["products"]
dataclass_decorators = [
  "dataclasses.dataclass", "products.shop.backend.kit.frozen", "products.shop.backend.kit.model",
]
frozen_dataclass_decorators = ["products.shop.backend.kit.frozen"]

[fields]
package = { type = "id" }

[[assign]]
match = "products/{product}/**"
set = { package = "{product}" }

[shapes.contract]
data = ["frozen-dataclass"]
unknown = "error"

[exports.facade]
within = "products/{product}"
paths = ["backend/facade/**"]
shape = "contract"

[rules.export-shape]
primitive = "export"
identity = "module-symbol"
ledger = false
"""

BASE = {
    "hoglint.toml": CONFIG,
    "products/__init__.py": "",
    "products/shop/__init__.py": "",
    "products/shop/backend/__init__.py": "",
    "products/shop/backend/kit.py": (
        "def frozen(cls):\n    return cls\n\n\n"
        "def model(cls=None, *, frozen=False):\n    return cls\n\n\n"
        "def other(cls=None, *, frozen=False):\n    return cls\n"
    ),
    "products/shop/backend/facade/__init__.py": "",
}
CONTRACTS = "products.shop.backend.facade.contracts"


def export_shape_findings(out: Path) -> list[list[str]]:
    return sorted(row[1] for row in read_lines(out / "findings.jsonl") if row[0] == "export-shape")


def both_engines(tmp_path: Path, files: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    out = dumps(tmp_path, {**BASE, **files}, monkeypatch)
    same_in_both_engines(out)
    return out


IMPORTS = (
    "import dataclasses\n\n"
    "from products.shop.backend.kit import frozen, model, other\n\n\n"
    "def build() -> None:\n    pass\n\n\n"
)
# A class is a frozen dataclass when a decorator of `dataclass_decorators` passes `frozen=True`, or
# when a decorator of `frozen_dataclass_decorators` makes it; any other class is a plain class.
FROZEN_CASES = {
    "stdlib with frozen=True": ("@dataclasses.dataclass(frozen=True)\nclass Order:\n    id: int\n", False),
    "a decorator that always freezes": ("@frozen\nclass Order:\n    id: int\n", False),
    "another dataclass decorator with frozen=True": ("@model(frozen=True)\nclass Order:\n    id: int\n", False),
    "a dataclass decorator without frozen": ("@model\nclass Order:\n    id: int\n", True),
    "frozen=True on a decorator that makes no dataclass": (
        "@other(frozen=True)\n@model\nclass Order:\n    id: int\n",
        True,
    ),
}


@pytest.mark.parametrize("case", FROZEN_CASES)
def test_a_frozen_dataclass_is_read_from_the_dataclass_decorators(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    body, flagged = FROZEN_CASES[case]
    out = both_engines(tmp_path, {"products/shop/backend/facade/contracts.py": IMPORTS + body}, monkeypatch)
    expected = [[f"{CONTRACTS}.Order", CONTRACTS, "export-shape(class)"]] if flagged else []
    assert export_shape_findings(out["native"]) == expected


SURFACE_CONFIG = (
    CONFIG
    + """
[rules.public-surface]
primitive = "entrypoint"
kinds = ["import"]
select = "src.package != dst.package"
identity = ["dst.symbol", "src.module"]
ledger = false
"""
)
LOGIC = (
    "class Order:\n    pass\n\n\nclass Receipt:\n    pass\n\n\nclass Ticket:\n    pass\n\n\n"
    "class Base:\n    pass\n\n\nclass Spare:\n    pass\n\n\nclass Late:\n    pass\n"
)
# A surface module hands out what `__all__` lists, what it imports as its own name, its lazy names,
# and in a module with no function every import its own code does not use; its own public
# definitions besides. Any other import is the module's private business.
SURFACE = {
    "hoglint.toml": SURFACE_CONFIG,
    "products/shop/backend/logic.py": LOGIC,
    "products/shop/backend/facade/api.py": (
        "from products.shop.backend.logic import Order, Receipt\n"
        "from products.shop.backend.logic import Ticket as Ticket\n\n"
        '__all__ = ["Receipt", "place"]\n\n\n'
        "def place() -> None:\n    Order()\n"
    ),
    "products/shop/backend/facade/contracts.py": (
        "from products.shop.backend.logic import Base, Spare\n\n\nclass Own(Base):\n    pass\n"
    ),
    "products/shop/backend/facade/lazy.py": (
        '_LAZY = {"Late": "products.shop.backend.logic"}\n\n\ndef __getattr__(name):\n    raise AttributeError(name)\n'
    ),
    "products/other/__init__.py": "",
    "products/other/use.py": (
        "from products.shop.backend.facade.api import Order, Receipt, Ticket, place\n"
        "from products.shop.backend.facade.contracts import Base, Own, Spare\n"
        "from products.shop.backend.facade.lazy import Late\n"
    ),
}
SHOP = "products.shop.backend"


def test_a_surface_module_exports_only_what_it_hands_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = both_engines(tmp_path, SURFACE, monkeypatch)
    findings = sorted([row[0], row[1]] for row in read_lines(out["native"] / "findings.jsonl"))
    assert findings == [
        ["export-shape", [f"{SHOP}.facade.contracts.Own", f"{SHOP}.facade.contracts", "export-shape(class)"]],
        ["export-shape", [f"{SHOP}.logic.Receipt", f"{SHOP}.facade.api", "export-shape(class)"]],
        ["export-shape", [f"{SHOP}.logic.Spare", f"{SHOP}.facade.contracts", "export-shape(class)"]],
        ["export-shape", [f"{SHOP}.logic.Ticket", f"{SHOP}.facade.api", "export-shape(class)"]],
        ["public-surface", [f"{SHOP}.facade.api.Order", "products.other.use"]],
        ["public-surface", [f"{SHOP}.facade.contracts.Base", "products.other.use"]],
    ]
