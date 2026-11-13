"""What the export primitive reads of a surface module: the shape of each class it hands out, in
both engines."""

from __future__ import annotations

from pathlib import Path

import pytest

from hoglint.native.parity import read_lines

from native.test_contract_slots import both_engines, findings_of

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
    out = both_engines(tmp_path, BASE, {"products/shop/backend/facade/contracts.py": IMPORTS + body}, monkeypatch)
    expected = [[f"{CONTRACTS}.Order", CONTRACTS, "export-shape(class)"]] if flagged else []
    assert findings_of(out["native"], "export-shape") == expected


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
API = f"{SHOP}.facade.api"


def test_a_surface_module_exports_only_what_it_hands_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = both_engines(tmp_path, BASE, SURFACE, monkeypatch)
    findings = sorted([row[0], row[1]] for row in read_lines(out["native"] / "findings.jsonl"))
    assert findings == [
        ["export-shape", [f"{SHOP}.facade.contracts.Own", f"{SHOP}.facade.contracts", "export-shape(class)"]],
        ["export-shape", [f"{SHOP}.logic.Late", f"{SHOP}.facade.lazy", "export-shape(class)"]],
        ["export-shape", [f"{SHOP}.logic.Receipt", f"{SHOP}.facade.api", "export-shape(class)"]],
        ["export-shape", [f"{SHOP}.logic.Spare", f"{SHOP}.facade.contracts", "export-shape(class)"]],
        ["export-shape", [f"{SHOP}.logic.Ticket", f"{SHOP}.facade.api", "export-shape(class)"]],
        ["public-surface", [f"{SHOP}.facade.api.Order", "products.other.use"]],
        ["public-surface", [f"{SHOP}.facade.contracts.Base", "products.other.use"]],
    ]


ALIAS_CONFIG = (
    CONFIG.replace('[shapes.contract]\ndata = ["frozen-dataclass"]\nunknown = "error"\n', "", 1)
    + """
[kinds]
model = { subclass_of = ["products.shop.backend.models.Model"] }

[shapes.contract]
data = ["frozen-dataclass"]
deny = ["model"]
unknown = "error"
signatures = true

[rules.facade-signature]
primitive = "signature"
deny = "dst.kind == 'model'"
on_unknown = "finding"
identity = ["src.module", "edge.callable", "edge.slot", "dst.symbol"]
ledger = false
"""
)
MODELS = "class Model:\n    pass\n\n\nclass Order(Model):\n    pass\n"
DEEP = "".join(f"A{at} = list[A{at + 1}]\n" for at in range(40)) + "A40 = int\n"


# A type alias whose expansion core wrote is explained by it: the rules judge the names it expands
# to, never the alias's own name. An alias too deep to follow stays unknown, by the marker that
# stops the expansion: one row for the name.
def slot_finding(name: str) -> list[object]:
    return ["facade-signature", [API, "charge", "param:amount", name]]


ORDER_IN_DICT = [slot_finding("builtins.dict"), slot_finding(f"{SHOP}.models.Order")]
ALIAS_CASES = {
    "a local alias": ("Totals = dict[str, Order]\n\n\ndef charge(amount: Totals) -> None:\n    pass\n", ORDER_IN_DICT),
    "an alias of an alias": (
        "Totals = dict[str, Order]\nRows = list[Totals]\n\n\ndef charge(amount: Rows) -> None:\n    pass\n",
        ORDER_IN_DICT,
    ),
    "an alias too deep to follow": (
        DEEP + "\n\ndef charge(amount: A0) -> None:\n    pass\n",
        [slot_finding("A32")],
    ),
}


@pytest.mark.parametrize("case", ALIAS_CASES)
def test_an_expanded_alias_is_judged_by_what_it_expands_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    body, expected = ALIAS_CASES[case]
    files = {
        "hoglint.toml": ALIAS_CONFIG,
        "products/shop/backend/models.py": MODELS,
        "products/shop/backend/facade/api.py": "from products.shop.backend.models import Order\n\n" + body,
    }
    out = both_engines(tmp_path, BASE, files, monkeypatch)
    findings = sorted([row[0], row[1]] for row in read_lines(out["native"] / "findings.jsonl"))
    assert [row for row in findings if row[0] == "facade-signature"] == sorted(expected)


BARE_DICT_CONFIG = (
    CONFIG.replace('unknown = "error"\n', 'unknown = "error"\nsignatures = true\n', 1)
    + """
[rules.bare-dict]
primitive = "edge"
kinds = ["exposes"]
select = "edge.surface == 'builtins.dict'"
deny = "edge.subscripted == none"
identity = ["src.module", "edge.callable", "edge.slot"]
ledger = false
"""
)
# A slot row says whether its generic type has arguments (`subscripted`), so a rule can tell a
# bare `dict` from `dict[str, str]`, in the slot itself or in the alias it names.
SUBSCRIPT_CASES = {
    "a bare dict": ("dict", True),
    "a dict with arguments": ("dict[str, str]", False),
    "a bare dict inside a list": ("list[dict]", True),
    "an optional dict with arguments": ("Optional[dict[str, int]]", False),
    "an alias of a dict with arguments": ("Totals", False),
    "an alias of a bare dict": ("Loose", True),
}


@pytest.mark.parametrize("case", SUBSCRIPT_CASES)
def test_a_rule_tells_a_bare_generic_from_one_with_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    annotation, flagged = SUBSCRIPT_CASES[case]
    api = (
        "from typing import Optional\n\nTotals = dict[str, int]\nLoose = dict\n\n\n"
        f"def charge(amount: {annotation}) -> None:\n    pass\n"
    )
    files = {"hoglint.toml": BARE_DICT_CONFIG, "products/shop/backend/facade/api.py": api}
    out = both_engines(tmp_path, BASE, files, monkeypatch)
    findings = [row[1] for row in read_lines(out["native"] / "findings.jsonl") if row[0] == "bare-dict"]
    assert findings == ([[API, "charge", "param:amount"]] if flagged else [])


# A module-level import hands out its name in whatever block it runs; one under `if TYPE_CHECKING:`
# never runs, so it hands out nothing.
IMPORT_BLOCK_CASES = {
    "under try": (
        "try:\n    from products.shop.backend.logic import Order\nexcept ImportError:\n    Order = None\n",
        True,
    ),
    "under a plain if": ("import sys\n\nif sys.platform:\n    from products.shop.backend.logic import Order\n", True),
    "under if TYPE_CHECKING": (
        "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    from products.shop.backend.logic import Order\n",
        False,
    ),
}


@pytest.mark.parametrize("case", IMPORT_BLOCK_CASES)
def test_a_module_level_import_in_a_block_is_handed_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    imports, flagged = IMPORT_BLOCK_CASES[case]
    api = imports + '\n__all__ = ["Order"]\n\n\ndef place() -> None:\n    pass\n'
    files = {"products/shop/backend/logic.py": LOGIC, "products/shop/backend/facade/api.py": api}
    out = both_engines(tmp_path, BASE, files, monkeypatch)
    expected = [[f"{SHOP}.logic.Order", API, "export-shape(class)"]] if flagged else []
    assert findings_of(out["native"], "export-shape") == expected


# A lazy name that does not resolve is named by the full path its map gives it, the base its values
# are joined to included, whether its module is missing or only its name. Read without the base, a
# missing module under a head no repo file has would read as third-party, with no row at all.
UNRESOLVED_LAZY_CASES = {
    "its module is missing": ("sub.gone", {}, f"{SHOP}.sub.gone.Gone"),
    "its module is missing, and a repo package has the value's head": (
        "sub.gone",
        {"sub/__init__.py": ""},
        f"{SHOP}.sub.gone.Gone",
    ),
    "its name is missing": ("logic", {}, f"{SHOP}.logic.Gone"),
}


@pytest.mark.parametrize("case", UNRESOLVED_LAZY_CASES)
def test_an_unresolved_lazy_name_is_named_by_its_full_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    value, extra, path = UNRESOLVED_LAZY_CASES[case]
    lazy = (
        f'_BASE = "{SHOP}."\n_LAZY = {{"Order": "logic", "Gone": "{value}"}}\n\n\n'
        "def __getattr__(name):\n    return _BASE + _LAZY[name]\n"
    )
    files = {"products/shop/backend/logic.py": LOGIC, "products/shop/backend/facade/lazy.py": lazy, **extra}
    out = both_engines(tmp_path, BASE, files, monkeypatch)
    unresolved = [row for row in findings_of(out["native"], "export-shape") if row[-1] == "unresolved-import"]
    assert unresolved == [[path, f"{SHOP}.facade.lazy", "unresolved-import"]]


def test_a_bare_string_names_one_dataclass_decorator_in_both_engines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kit = "products.shop.backend.kit"
    config = CONFIG.replace(
        f'dataclass_decorators = [\n  "dataclasses.dataclass", "{kit}.frozen", "{kit}.model",\n]\n'
        f'frozen_dataclass_decorators = ["{kit}.frozen"]\n',
        f'dataclass_decorators = "{kit}.frozen"\nfrozen_dataclass_decorators = "{kit}.frozen"\n',
    )
    assert config != CONFIG
    contracts = IMPORTS + "@frozen\nclass Order:\n    id: int\n"
    files = {"hoglint.toml": config, "products/shop/backend/facade/contracts.py": contracts}
    out = both_engines(tmp_path, BASE, files, monkeypatch)
    assert findings_of(out["native"], "export-shape") == []
