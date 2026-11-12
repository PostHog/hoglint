"""The PostHog preset's facade and contract rules on small product trees, in both engines."""

from __future__ import annotations

from pathlib import Path

import pytest

from hoglint import facts
from hoglint.core import analysis as core_analysis
from hoglint.native import analysis as native_analysis
from hoglint.native.parity import read_lines

from native.test_contract_slots import both_engines

PRESET = Path(__file__).resolve().parents[2] / "presets" / "posthog.toml"
ROOT_CONFIG = """
schema = 1
extends = ["hoglint:django", "tools/hoglint/presets/posthog.toml"]

[discovery]
source_roots = ["."]
first_party = ["posthog", "products"]
external = ["django", "rest_framework"]

[django]
settings = ["posthog.settings"]
"""

SHOP = "products.shop.backend"
# Without Django installed, only its defining module names the model base for the Django pack.
MODEL = "from django.db import models\n\n\nclass {name}(models.Model):\n    pass\n"
# The third-party code the preset reads, as a stand-in site-packages.
SITE_PACKAGES = {
    "django/__init__.py": "",
    "django/db/__init__.py": "",
    "django/db/models/__init__.py": "from django.db.models.base import Model\n",
    "django/db/models/base.py": "class Model:\n    pass\n",
    "rest_framework/__init__.py": "",
    "rest_framework/serializers.py": "class Serializer:\n    pass\n",
}
BASE = {
    "hoglint.toml": ROOT_CONFIG,
    "tools/hoglint/presets/posthog.toml": PRESET.read_text(),
    "posthog/__init__.py": "",
    "posthog/settings.py": "INSTALLED_APPS = ['posthog', 'products.shop.backend', 'products.billing.backend']\n",
    "products/__init__.py": "",
    "products/shop/__init__.py": "",
    "products/shop/backend/__init__.py": "",
    "products/shop/backend/models.py": MODEL.format(name="Order"),
    "products/shop/backend/logic.py": "def charge() -> int:\n    return 1\n",
    "products/shop/backend/temporal/__init__.py": "",
    "products/shop/backend/temporal/workflows.py": "def run_charge() -> int:\n    return 1\n",
    "products/shop/backend/facade/__init__.py": "",
}


@pytest.fixture(autouse=True)
def site_packages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    site = tmp_path / "site-packages"
    for path, text in SITE_PACKAGES.items():
        (site / path).parent.mkdir(parents=True, exist_ok=True)
        (site / path).write_text(text)
    for module in (facts, core_analysis, native_analysis):
        monkeypatch.setattr(module, "site_packages", lambda: site)


def findings(out: Path, rule: str) -> list[list[str]]:
    return sorted(row[1] for row in read_lines(out / "findings.jsonl") if row[0] == rule)


BODIES = (
    "def build() -> int:\n    return 1\n\n\n"
    "def _helper() -> int:\n    return 2\n\n\n"
    "def stub() -> None:\n    ...\n\n\n"
    'def documented() -> None:\n    """Only a docstring."""\n\n\n'
    "class Runner:\n    def go(self) -> int:\n        return 1\n\n\n"
    "def __getattr__(name: str) -> object:\n    return name\n"
)
HANDS_OUT_WIRING = "from products.shop.backend.temporal.workflows import run_charge\n\n__all__ = ['run_charge']\n\n\n"
HANDS_OUT_LOGIC = "from products.shop.backend.logic import charge\n\n__all__ = ['charge']\n\n\n"


def bodies_of(module: str) -> list[list[str]]:
    return [[f"{module}.{name}", module] for name in ("build", "_helper", "Runner")]


# A capability submodule (a doctrine stem at the top of the facade, or a module that hands out
# wiring or model classes) holds no body; `api*.py`, contracts, enums and testing never are one.
LOGIC_CASES = {
    "a doctrine stem": ("tasks.py", BODIES, bodies_of(f"{SHOP}.facade.tasks")),
    "a doctrine stem below the top": ("jobs/tasks.py", BODIES, []),
    "a module that hands out wiring": ("hooks.py", HANDS_OUT_WIRING + BODIES, bodies_of(f"{SHOP}.facade.hooks")),
    "a module that hands out logic": ("hooks.py", HANDS_OUT_LOGIC + BODIES, []),
    "api that hands out wiring": ("api_jobs.py", HANDS_OUT_WIRING + BODIES, []),
    "testing that hands out wiring": ("testing.py", HANDS_OUT_WIRING + BODIES, []),
    "a nested api that hands out wiring": (
        "jobs/api.py",
        HANDS_OUT_WIRING + BODIES,
        bodies_of(f"{SHOP}.facade.jobs.api"),
    ),
    "a plain api module": ("api.py", BODIES, []),
}


@pytest.mark.parametrize("case", LOGIC_CASES)
def test_facade_logic_names_each_body_of_a_capability_submodule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    name, body, expected = LOGIC_CASES[case]
    files = {f"products/shop/backend/facade/{name}": body}
    if "/" in name:
        files[f"products/shop/backend/facade/{name.split('/')[0]}/__init__.py"] = ""
    out = both_engines(tmp_path, BASE, files, monkeypatch)
    assert findings(out["native"], "facade-logic") == sorted(expected)


CORE_MODELS = {
    "posthog/models/__init__.py": "",
    "posthog/models/team/__init__.py": "",
    "posthog/models/team/team.py": MODEL.format(name="Team"),
    "posthog/models/activity.py": MODEL.format(name="ActivityLog"),
    "posthog/models/organization.py": MODEL.format(name="OrganizationMembership"),
}
API = f"{SHOP}.facade.api"


def test_facade_signature_lets_the_named_core_models_cross(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    api = (
        "from posthog.models.activity import ActivityLog\n"
        "from posthog.models.organization import OrganizationMembership\n"
        "from posthog.models.team.team import Team\n"
        "from products.shop.backend.models import Order\n\n\n"
        "def for_team(team: Team) -> int:\n    return 1\n\n\n"
        "def logged(entry: ActivityLog) -> int:\n    return 1\n\n\n"
        "def member(membership: OrganizationMembership) -> int:\n    return 1\n\n\n"
        "def place(order: Order) -> int:\n    return 1\n"
    )
    files = {**CORE_MODELS, "products/shop/backend/facade/api.py": api}
    out = both_engines(tmp_path, BASE, files, monkeypatch)
    assert findings(out["native"], "facade-signature") == [
        [API, "logged", "param:entry", "posthog.models.activity.ActivityLog"],
        [API, "place", "param:order", f"{SHOP}.models.Order"],
    ]


# A mapping given type arguments is typed; `Any` stays unknown, except as the value type of a
# mapping in a parameter or a field (an opaque payload). `object` stays unknown: it would hide a model.
UNKNOWN_CASES = {
    "a bare dict": ("amount: dict", "None", ["builtins.dict"]),
    "a dict with arguments": ("amount: dict[str, str]", "None", []),
    "a payload parameter": ("amount: dict[str, Any]", "None", []),
    "a payload mapping": ("amount: Mapping[str, Any]", "None", []),
    "a payload returned": ("amount: int", "dict[str, Any]", ["typing.Any"]),
    "a bare Any": ("amount: Any", "None", ["typing.Any"]),
    "an optional Any": ("amount: Optional[Any]", "None", ["typing.Any"]),
    "Any in a list": ("amount: list[Any]", "None", ["typing.Any"]),
    "a callback that returns Any": ("amount: Callable[..., Any]", "None", ["typing.Any"]),
    "a list of bare dicts": ("amount: list[dict]", "None", ["builtins.dict"]),
    "object": ("amount: object", "None", ["builtins.object"]),
    "an alias of a payload and a list of Any": ("amount: Payload", "None", ["typing.Any"]),
    "an alias of a typed and a bare dict": ("amount: Bag", "None", ["builtins.dict"]),
}
# Aliases name each type once per slot and position, so a second mention that is an argument of
# another generic, or not subscripted, must still be its own row.
ALIASES = "Payload = dict[str, Any] | list[Any]\nBag = dict[str, str] | dict\n\n\n"


@pytest.mark.parametrize("case", UNKNOWN_CASES)
def test_facade_signature_reads_typed_mappings_and_payloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    param, returns, expected = UNKNOWN_CASES[case]
    api = (
        "from collections.abc import Callable, Mapping\nfrom typing import Any, Optional\n\n"
        f"{ALIASES}def charge({param}) -> {returns}:\n    pass\n"
    )
    out = both_engines(tmp_path, BASE, {"products/shop/backend/facade/api.py": api}, monkeypatch)
    assert [row[3] for row in findings(out["native"], "facade-signature")] == expected


def test_a_payload_field_of_a_contract_is_typed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    contracts = (
        "import dataclasses\nfrom typing import Any\n\n\n"
        "@dataclasses.dataclass(frozen=True)\nclass Event:\n    properties: dict[str, Any]\n    extra: Any\n"
    )
    out = both_engines(tmp_path, BASE, {"products/shop/backend/facade/contracts.py": contracts}, monkeypatch)
    assert findings(out["native"], "facade-signature") == [
        [f"{SHOP}.facade.contracts", "Event.__init__", "field:extra", "typing.Any"],
    ]


HOGQL = {
    "posthog/hogql/__init__.py": "",
    "posthog/hogql/base.py": (
        "import dataclasses\n\n\nclass Context:\n    pass\n\n\nclass AST:\n    pass\n\n\n"
        "@dataclasses.dataclass\nclass Expr(AST):\n    context: Context\n"
    ),
}


# A contract may hand out a hook's Protocol and core's query syntax tree. Core's own types are
# core's contract: a product's facade hands them out as they stand, without judging their fields.
def test_a_contract_hands_out_protocols_and_core_syntax_trees(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    contracts = (
        "from typing import Protocol\n\nfrom posthog.hogql.base import Expr\n\n\n"
        "class Chooser(Protocol):\n    def __call__(self, name: str) -> str: ...\n\n\n"
        "def where(expr: Expr) -> Expr:\n    return expr\n"
    )
    files = {**HOGQL, "products/shop/backend/facade/contracts.py": contracts}
    out = both_engines(tmp_path, BASE, files, monkeypatch)
    assert findings(out["native"], "export-shape") == []


def test_export_shape_allows_errors_and_pydantic_contracts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    contracts = (
        "from pydantic import BaseModel\n\n\n"
        "class ShopError(Exception):\n    pass\n\n\n"
        "class OrderMissing(ShopError):\n    pass\n\n\n"
        "class Payload(BaseModel):\n    name: str\n\n\n"
        "class Loose:\n    pass\n"
    )
    out = both_engines(tmp_path, BASE, {"products/shop/backend/facade/contracts.py": contracts}, monkeypatch)
    assert findings(out["native"], "export-shape") == [
        [f"{SHOP}.facade.contracts.Loose", f"{SHOP}.facade.contracts", "export-shape(class)"],
    ]


def test_export_shape_leaves_a_slot_to_the_signature_rule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    logic = (
        "import dataclasses\n\nfrom products.shop.backend.models import Order\n\n\n"
        "class Spec:\n    pass\n\n\n"
        "@dataclasses.dataclass(frozen=True)\nclass Wrapper:\n    order: Order\n"
    )
    api = (
        "from typing import Any\n\n"
        "from products.shop.backend.logic import Spec, Wrapper\n"
        "from products.shop.backend.models import Order\n\n\n"
        "def place(order: Order, extra: Any) -> int:\n    return 1\n\n\n"
        "def build(spec: Spec) -> int:\n    return 1\n\n\n"
        "def wrap(wrapper: Wrapper) -> int:\n    return 1\n"
    )
    files = {"products/shop/backend/logic.py": logic, "products/shop/backend/facade/api.py": api}
    out = both_engines(tmp_path, BASE, files, monkeypatch)
    assert findings(out["native"], "export-shape") == [
        [f"{SHOP}.logic.Spec", API, "export-shape(class)"],
        [f"{SHOP}.models.Order", API, "export-denied(model)"],
    ]
    assert [row[1:3] for row in findings(out["native"], "facade-signature")] == [
        ["place", "param:extra"],
        ["place", "param:order"],
    ]


def test_the_fixture_module_is_outside_the_contract_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    testing = (
        "from typing import Any\n\nfrom rest_framework import serializers\n\n"
        "from products.shop.backend.models import Order\n\n\n"
        "def make_order(**fields: Any) -> Order:\n    return Order(**fields)\n\n\n"
        "class OrderFactory:\n    def build(self) -> Order:\n        return Order()\n"
    )
    out = both_engines(tmp_path, BASE, {"products/shop/backend/facade/testing.py": testing}, monkeypatch)
    for rule in ("export-shape", "facade-signature", "facade-logic", "facade-no-drf"):
        assert findings(out["native"], rule) == [], rule


# Importing a contract module must run no Django: a module-level import, direct or through other
# modules, reaches it; an import inside a function body and one under `if TYPE_CHECKING:` do not.
LOGIC_IMPORTING_DJANGO = "def build() -> None:\n    from django.db import models\n"
CONTRACT_IMPORT_CASES = {
    "a module-level import": ("from django.db import models\n", ["django.db.models"]),
    "an import in a function": ("def build() -> None:\n    from django.db import models\n", []),
    "a type-only import": (
        "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    from django.db import models\n",
        [],
    ),
    "a chain through module-level imports": ("import products.shop.backend.models\n", ["django.db.models"]),
    "a chain through a function-local import": ("import products.shop.backend.helpers\n", []),
}


@pytest.mark.parametrize("case", CONTRACT_IMPORT_CASES)
def test_contracts_no_django_reads_what_runs_on_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    body, reached = CONTRACT_IMPORT_CASES[case]
    files = {
        "products/shop/backend/helpers.py": LOGIC_IMPORTING_DJANGO,
        "products/shop/backend/facade/contracts.py": body,
    }
    out = both_engines(tmp_path, BASE, files, monkeypatch)
    assert findings(out["native"], "contracts-no-django") == [[f"{SHOP}.facade.contracts", dst] for dst in reached]


# The core models on the list may cross a contract at any depth: in a slot of a facade function
# and in a field of a data class that slot names. Any other model is a finding at both depths.
def test_the_core_model_list_holds_at_any_depth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    logic = (
        "import dataclasses\n\n"
        "from posthog.models.team.team import Team\n"
        "from products.shop.backend.models import Order\n\n\n"
        "@dataclasses.dataclass(frozen=True)\nclass Scope:\n    team: Team\n    order: Order\n"
    )
    api = (
        "from posthog.models.team.team import Team\n"
        "from products.shop.backend.logic import Scope\n\n\n"
        "def for_team(team: Team, scope: Scope) -> int:\n    return 1\n"
    )
    files = {**CORE_MODELS, "products/shop/backend/logic.py": logic, "products/shop/backend/facade/api.py": api}
    out = both_engines(tmp_path, BASE, files, monkeypatch)
    assert findings(out["native"], "export-shape") == [[f"{SHOP}.models.Order", API, "export-denied(model)"]]
    assert findings(out["native"], "facade-signature") == []


# An allowed contract type is allowed as a class; what its fields and methods name is still judged.
ALLOWED_WITH_A_MODEL = (
    "from typing import Protocol\n\nfrom pydantic import BaseModel\n\n"
    "from products.billing.backend.models import Invoice\n\n\n"
    "class Payload(BaseModel):\n    invoice: Invoice\n\n\n"
    "class BillingError(Exception):\n    def __init__(self, invoice: Invoice) -> None:\n        self.invoice = invoice\n\n\n"
    "class Chooser(Protocol):\n    def __call__(self, invoice: Invoice) -> None: ...\n"
)


def test_an_allowed_contract_type_is_still_walked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = {
        "products/billing/__init__.py": "",
        "products/billing/backend/__init__.py": "",
        "products/billing/backend/models.py": MODEL.format(name="Invoice"),
        "products/shop/backend/facade/contracts.py": ALLOWED_WITH_A_MODEL,
    }
    out = both_engines(tmp_path, BASE, files, monkeypatch)
    invoice = "products.billing.backend.models.Invoice"
    contracts = f"{SHOP}.facade.contracts"
    assert findings(out["native"], "facade-signature") == [
        [contracts, "BillingError.__init__", "param:invoice", invoice],
        [contracts, "Chooser.__call__", "param:invoice", invoice],
        [contracts, "Payload.__init__", "field:invoice", invoice],
    ]
    assert findings(out["native"], "export-shape") == []
