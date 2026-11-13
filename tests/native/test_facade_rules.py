"""The PostHog preset's facade and contract rules on small product trees, in both engines."""

from __future__ import annotations

from pathlib import Path

import pytest

from hoglint import facts
from hoglint.core import analysis as core_analysis
from hoglint.native import analysis as native_analysis
from hoglint.native.parity import read_lines
from hoglint.rules import exports
from rules.support import PRESET

from native.test_contract_slots import both_engines, findings_of

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
    "django/core/__init__.py": "",
    "django/core/management/__init__.py": "",
    "django/core/management/base.py": "class BaseCommand:\n    pass\n",
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
    assert findings_of(out["native"], "facade-logic") == sorted(expected)


HANDS_OUT_QUERIES = "from products.shop.backend import hogql_queries\n\n__all__ = ['hogql_queries']\n\n\n"
QUERIES = "products/shop/backend/hogql_queries"
# (the files of a directory without `__init__.py` a facade module hands out, the findings, the
# incomplete items). The directory has the zone its files read; where they disagree, unknown.
NAMESPACE_CASES = {
    "a namespace directory under wiring": (
        {f"{QUERIES}/trends.py": "def run() -> int:\n    return 1\n"},
        bodies_of(f"{SHOP}.facade.hooks"),
        [],
    ),
    "a namespace directory whose files disagree on zone": (
        {
            f"{QUERIES}/trends.py": "def run() -> int:\n    return 1\n",
            f"{QUERIES}/test_trends.py": "def test_run() -> None:\n    pass\n",
        },
        [],
        [f"{SHOP}.facade.hooks|declares|{SHOP}.facade.hooks.{name}" for name in ("Runner", "_helper", "build")],
    ),
}


@pytest.mark.parametrize("case", NAMESPACE_CASES)
def test_facade_logic_reads_the_zone_of_a_handed_namespace_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    directory, expected, incomplete = NAMESPACE_CASES[case]
    files = {"products/shop/backend/facade/hooks.py": HANDS_OUT_QUERIES + BODIES, **directory}
    out = both_engines(tmp_path, BASE, files, monkeypatch)
    assert findings_of(out["native"], "facade-logic") == sorted(expected)
    rows = read_lines(out["native"] / "incomplete.jsonl")
    assert sorted(row[1] for row in rows if row[0] == "facade-logic") == incomplete


CORE_MODELS = {
    "posthog/models/__init__.py": "",
    "posthog/models/team/__init__.py": "",
    "posthog/models/team/team.py": MODEL.format(name="Team"),
    "posthog/models/activity.py": MODEL.format(name="ActivityLog"),
    "posthog/models/organization.py": MODEL.format(name="OrganizationMembership"),
}
# A product whose model another product's facade names.
BILLING = {
    "products/billing/__init__.py": "",
    "products/billing/backend/__init__.py": "",
    "products/billing/backend/models.py": MODEL.format(name="Invoice"),
}
INVOICE = "products.billing.backend.models.Invoice"
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
    assert findings_of(out["native"], "facade-signature") == [
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


# One function per case in one run: a slot's rows depend on that slot alone.
def test_facade_signature_reads_typed_mappings_and_payloads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    callables = {f"charge_{at}": case for at, case in enumerate(UNKNOWN_CASES)}
    functions = "".join(
        f"def {name}({UNKNOWN_CASES[case][0]}) -> {UNKNOWN_CASES[case][1]}:\n    pass\n\n\n"
        for name, case in callables.items()
    )
    api = f"from collections.abc import Callable, Mapping\nfrom typing import Any, Optional\n\n{ALIASES}{functions}"
    out = both_engines(tmp_path, BASE, {"products/shop/backend/facade/api.py": api}, monkeypatch)
    found: dict[str, list[str]] = {case: [] for case in UNKNOWN_CASES}
    for row in findings_of(out["native"], "facade-signature"):
        found[callables[row[1]]].append(row[3])
    assert found == {case: expected for case, (_, _, expected) in UNKNOWN_CASES.items()}


def test_a_payload_field_of_a_contract_is_typed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    contracts = (
        "import dataclasses\nfrom typing import Any\n\n\n"
        "@dataclasses.dataclass(frozen=True)\nclass Event:\n    properties: dict[str, Any]\n    extra: Any\n"
    )
    out = both_engines(tmp_path, BASE, {"products/shop/backend/facade/contracts.py": contracts}, monkeypatch)
    assert findings_of(out["native"], "facade-signature") == [
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
    assert findings_of(out["native"], "export-shape") == []


def test_export_shape_allows_errors_and_pydantic_contracts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    contracts = (
        "from pydantic import BaseModel\n\n\n"
        "class ShopError(Exception):\n    pass\n\n\n"
        "class OrderMissing(ShopError):\n    pass\n\n\n"
        "class Payload(BaseModel):\n    name: str\n\n\n"
        "class Loose:\n    pass\n"
    )
    out = both_engines(tmp_path, BASE, {"products/shop/backend/facade/contracts.py": contracts}, monkeypatch)
    assert findings_of(out["native"], "export-shape") == [
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
    assert findings_of(out["native"], "export-shape") == [
        [f"{SHOP}.logic.Spec", API, "export-shape(class)"],
        [f"{SHOP}.models.Order", API, "export-denied(model)"],
    ]
    assert [row[1:3] for row in findings_of(out["native"], "facade-signature")] == [
        ["place", "param:extra"],
        ["place", "param:order"],
    ]


COMMANDS = f"{SHOP}.management.commands"
COMMAND = f"{COMMANDS}.charge.Command"
COMMAND_FILES = {
    "products/shop/backend/management/__init__.py": "",
    "products/shop/backend/management/commands/__init__.py": "",
    "products/shop/backend/management/commands/charge.py": (
        "from django.core.management.base import BaseCommand\n\n\nclass Command(BaseCommand):\n    pass\n"
    ),
    "products/shop/backend/logic.py": (
        f"import dataclasses\n\nfrom {COMMANDS}.charge import Command\n\n\n"
        "@dataclasses.dataclass(frozen=True)\nclass Job:\n    command: Command\n"
    ),
}
IMPORTS_COMMAND = f"from {COMMANDS}.charge import Command\nfrom {SHOP}.logic import Job\n\n\n"
# A command class in a slot the signature rule sees is that rule's; in a field one level down it is
# the export rule's. Each case is one finding of exactly one rule.
COMMAND_SLOT_CASES = {
    "a parameter": (
        "def run(command: Command) -> None:\n    pass\n",
        {"facade-signature": [[API, "run", "param:command", COMMAND]], "export-shape": []},
    ),
    "a return value": (
        "def make() -> Command:\n    return Command()\n",
        {"facade-signature": [[API, "make", "return", COMMAND]], "export-shape": []},
    ),
    "a field of a contract": (
        "def schedule(job: Job) -> None:\n    pass\n",
        {"facade-signature": [], "export-shape": [[COMMAND, API, "export-denied(command)"]]},
    ),
}


@pytest.mark.parametrize("case", COMMAND_SLOT_CASES)
def test_a_command_class_in_a_facade_slot_is_one_rules_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    body, expected = COMMAND_SLOT_CASES[case]
    files = {**COMMAND_FILES, "products/shop/backend/facade/api.py": IMPORTS_COMMAND + body}
    out = both_engines(tmp_path, BASE, files, monkeypatch)
    assert {rule: findings_of(out["native"], rule) for rule in expected} == expected


TAKES_AN_ORDER = (
    "from typing import Any\n\n"
    "from products.shop.backend.models import Order\n"
    "from products.shop.backend.nowhere import Missing\n\n\n"
    "def place(order: Order, extra: Any, other: Missing) -> None:\n    pass\n"
)
PRIVATE_METHOD = (
    "import dataclasses\n\nfrom products.shop.backend.models import Order\n\n\n"
    "@dataclasses.dataclass(frozen=True)\nclass Receipt:\n    total: int\n\n"
    "    def _load(self, order: Order) -> None:\n        pass\n"
)
ROUTES = f"{SHOP}.routes"
SHOP_DIR = "products/shop/backend"
ERRORS = f"{SHOP}.facade.errors"
ORDER = f"{SHOP}.models.Order"
HANDS_OUT_ERRORS = "from products.shop.backend.errors import {names}\n\n__all__ = [{listed}]\n"
IMPORTS_ORDER = (
    "import dataclasses\nfrom typing import Protocol\n\nfrom pydantic import BaseModel\n\n"
    "from products.shop.backend.models import Order\n\n\n"
)


def handed_out(body: str, *names: str) -> dict[str, str]:
    """Classes or functions defined outside the facade, and a facade module that hands them out."""
    listed = ", ".join(f"'{name}'" for name in names)
    return {
        f"{SHOP_DIR}/errors.py": IMPORTS_ORDER + body,
        f"{SHOP_DIR}/facade/errors.py": HANDS_OUT_ERRORS.format(names=", ".join(names), listed=listed),
    }


# A signature rule the export rule does not name in `leave_slots_to` takes no slot from it.
UNNAMED_SIGNATURE_RULE = """
[rules.routes-signature]
primitive  = "signature"
select     = "src.module == 'products.shop.backend.routes'"
deny       = "dst.kind == 'model'"
on_unknown = "finding"
identity   = "facade-slot"
ledger     = false
"""
DENIED_AT_ERRORS = {"export-shape": [[ORDER, ERRORS, "export-denied(model)"]], "facade-signature": []}
# A slot is left to the signature rule only where that rule reads the slot's row: in a facade
# module, in a public callable, or in the copy a facade holds of a function or a data shape's fields
# it hands out. A registration surface, a private method and a member of a class handed out from
# outside the facade are judged by the export rule.
SLOT_OWNER_CASES = {
    "a facade function": ({f"{SHOP_DIR}/facade/api.py": TAKES_AN_ORDER}, {"export-shape": []}),
    "a registration function": (
        {f"{SHOP_DIR}/routes.py": TAKES_AN_ORDER},
        {
            "export-shape": [
                [ORDER, ROUTES, "export-denied(model)"],
                [f"{SHOP}.nowhere.Missing", ROUTES, "unresolved-exposes"],
                ["typing.Any", ROUTES, "export-unknown"],
            ]
        },
    ),
    "a registration function an unnamed signature rule reads": (
        {
            "hoglint.toml": ROOT_CONFIG + UNNAMED_SIGNATURE_RULE,
            f"{SHOP_DIR}/routes.py": TAKES_AN_ORDER,
        },
        {
            "export-shape": [
                [ORDER, ROUTES, "export-denied(model)"],
                [f"{SHOP}.nowhere.Missing", ROUTES, "unresolved-exposes"],
                ["typing.Any", ROUTES, "export-unknown"],
            ],
            "routes-signature": [
                [ROUTES, "place", "param:extra", "typing.Any"],
                [ROUTES, "place", "param:order", ORDER],
                [ROUTES, "place", "param:other", f"{SHOP}.nowhere.Missing"],
            ],
        },
    ),
    "a private method of a contract": (
        {f"{SHOP_DIR}/facade/contracts.py": PRIVATE_METHOD},
        {"export-shape": [[ORDER, f"{SHOP}.facade.contracts", "export-denied(model)"]]},
    ),
    "a method of a handed-out exception": (
        handed_out("class Rejected(Exception):\n    def order(self) -> Order:\n        raise ValueError\n", "Rejected"),
        DENIED_AT_ERRORS,
    ),
    "a method of a handed-out pydantic model": (
        handed_out(
            "class Row(BaseModel):\n    title: str\n\n    def order(self) -> Order:\n        raise ValueError\n", "Row"
        ),
        DENIED_AT_ERRORS,
    ),
    "a method of a handed-out Protocol": (
        handed_out("class Sink(Protocol):\n    def push(self, order: Order) -> None: ...\n", "Sink"),
        DENIED_AT_ERRORS,
    ),
    "an __init__ parameter of a handed-out exception": (
        handed_out(
            "class Rejected(Exception):\n    def __init__(self, order: Order) -> None:\n        self.order = order\n",
            "Rejected",
        ),
        DENIED_AT_ERRORS,
    ),
    "a property of a handed-out exception": (
        handed_out(
            "class Rejected(Exception):\n    @property\n    def order(self) -> Order:\n        raise ValueError\n",
            "Rejected",
        ),
        DENIED_AT_ERRORS,
    ),
    "an attribute of a handed-out exception": (
        handed_out("class Rejected(Exception):\n    order: Order\n", "Rejected"),
        DENIED_AT_ERRORS,
    ),
    "a function handed out from outside": (
        handed_out("def place(order: Order) -> None:\n    pass\n", "place"),
        {"export-shape": [], "facade-signature": [[ERRORS, "place", "param:order", ORDER]]},
    ),
    "a field of a handed-out dataclass": (
        handed_out("@dataclasses.dataclass(frozen=True)\nclass Receipt:\n    order: Order\n", "Receipt"),
        {"export-shape": [], "facade-signature": [[ERRORS, "Receipt.__init__", "field:order", ORDER]]},
    ),
    "a method inherited from a class core defines": (
        {
            "posthog/errors.py": IMPORTS_ORDER
            + "class CoreError(Exception):\n    def order(self) -> Order:\n        raise ValueError\n",
            **handed_out(
                "from posthog.errors import CoreError\n\n\nclass Rejected(CoreError):\n    pass\n", "Rejected"
            ),
        },
        {"export-shape": [], "facade-signature": []},
    ),
    "a method inherited from a base in the same product": (
        {
            f"{SHOP_DIR}/bases.py": IMPORTS_ORDER
            + "class ShopError(Exception):\n    def order(self) -> Order:\n        raise ValueError\n",
            **handed_out(f"from {SHOP}.bases import ShopError\n\n\nclass Rejected(ShopError):\n    pass\n", "Rejected"),
        },
        DENIED_AT_ERRORS,
    ),
    "a base that does not resolve": (
        handed_out(
            f"from {SHOP}.nowhere import Missing\n\n\nclass Rejected(Missing):\n    def order(self) -> Order:\n        raise ValueError\n",
            "Rejected",
        ),
        {"export-shape": [[f"{SHOP}.errors.Rejected", ERRORS, "export-unknown"]], "facade-signature": []},
    ),
    "an opaque payload parameter of a handed-out exception": (
        handed_out(
            "from typing import Any\n\n\nclass Rejected(Exception):\n"
            "    def __init__(self, extra: dict[str, Any]) -> None:\n        self.extra = extra\n",
            "Rejected",
        ),
        {"export-shape": [], "facade-signature": []},
    ),
    "an opaque payload returned by a method of a handed-out exception": (
        handed_out(
            "from typing import Any\n\n\nclass Rejected(Exception):\n"
            "    def extra(self) -> dict[str, Any]:\n        return {}\n",
            "Rejected",
        ),
        {"export-shape": [["typing.Any", ERRORS, "export-unknown"]], "facade-signature": []},
    ),
    "a bare Any parameter of a handed-out exception": (
        handed_out(
            "from typing import Any\n\n\nclass Rejected(Exception):\n"
            "    def __init__(self, extra: Any) -> None:\n        self.extra = extra\n",
            "Rejected",
        ),
        {"export-shape": [["typing.Any", ERRORS, "export-unknown"]], "facade-signature": []},
    ),
    "a model as a mapping's argument on a handed-out exception": (
        handed_out(
            "class Rejected(Exception):\n"
            "    def __init__(self, orders: dict[str, Order]) -> None:\n        self.orders = orders\n",
            "Rejected",
        ),
        DENIED_AT_ERRORS,
    ),
    "an opaque payload field of a class defined outside a surface": (
        {
            f"{SHOP_DIR}/logic.py": "import dataclasses\nfrom typing import Any\n\n\n"
            "@dataclasses.dataclass(frozen=True)\nclass Info:\n    payload: dict[str, Any]\n",
            f"{SHOP_DIR}/facade/api.py": f"from {SHOP}.logic import Info\n\n\ndef get() -> Info:\n    raise ValueError\n",
        },
        {"export-shape": [], "facade-signature": []},
    ),
    "an opaque payload only one of two named rules leaves out": (
        {
            "hoglint.toml": ROOT_CONFIG
            + UNNAMED_SIGNATURE_RULE
            + '\n[rules.export-shape]\nleave_slots_to = { add = ["routes-signature"] }\n',
            **handed_out(
                "from typing import Any\n\n\nclass Rejected(Exception):\n"
                "    def __init__(self, extra: dict[str, Any]) -> None:\n        self.extra = extra\n",
                "Rejected",
            ),
        },
        {"export-shape": [["typing.Any", ERRORS, "export-unknown"]], "facade-signature": []},
    ),
    "a field of a contract class a facade function returns": (
        {
            f"{SHOP_DIR}/facade/contracts.py": IMPORTS_ORDER
            + "@dataclasses.dataclass(frozen=True)\nclass Info:\n    order: Order\n",
            f"{SHOP_DIR}/facade/api.py": f"from {SHOP}.facade.contracts import Info\n\n\ndef get() -> Info:\n    raise ValueError\n",
        },
        {
            "export-shape": [],
            "facade-signature": [[f"{SHOP}.facade.contracts", "Info.__init__", "field:order", ORDER]],
        },
    ),
    "an inherited field of a contract dataclass the facade defines": (
        {
            f"{SHOP_DIR}/logic.py": IMPORTS_ORDER
            + "@dataclasses.dataclass(frozen=True)\nclass Base:\n    order: Order\n",
            f"{SHOP_DIR}/facade/contracts.py": f"import dataclasses\n\nfrom {SHOP}.logic import Base\n\n\n"
            "@dataclasses.dataclass(frozen=True)\nclass Info(Base):\n    pass\n",
        },
        {
            "export-shape": [],
            "facade-signature": [[f"{SHOP}.facade.contracts", "Info.__init__", "field:order", ORDER]],
        },
    ),
    "an inherited field of a contract dataclass the facade package hands out": (
        {
            f"{SHOP_DIR}/logic.py": IMPORTS_ORDER
            + "@dataclasses.dataclass(frozen=True)\nclass Base:\n    order: Order\n",
            f"{SHOP_DIR}/facade/contracts.py": f"import dataclasses\n\nfrom {SHOP}.logic import Base\n\n\n"
            "@dataclasses.dataclass(frozen=True)\nclass Info(Base):\n    pass\n",
            f"{SHOP_DIR}/facade/__init__.py": f"from {SHOP}.facade.contracts import Info\n\n__all__ = ['Info']\n",
        },
        {
            "export-shape": [],
            "facade-signature": [[f"{SHOP}.facade.contracts", "Info.__init__", "field:order", ORDER]],
        },
    ),
    "a dataclass handed out under a private name": (
        {
            f"{SHOP_DIR}/logic.py": IMPORTS_ORDER
            + "@dataclasses.dataclass(frozen=True)\nclass Receipt:\n    order: Order\n",
            f"{SHOP_DIR}/facade/api.py": f"from {SHOP}.logic import Receipt as _Receipt\n\n__all__ = ['_Receipt']\n",
        },
        {"export-shape": [[ORDER, API, "export-denied(model)"]], "facade-signature": []},
    ),
    "a private function handed out under a public name": (
        {
            f"{SHOP_DIR}/logic.py": IMPORTS_ORDER + "def _place(order: Order) -> None:\n    pass\n",
            f"{SHOP_DIR}/facade/api.py": f"from {SHOP}.logic import _place as place\n\n__all__ = ['place']\n",
        },
        {"export-shape": [], "facade-signature": [[API, "place", "param:order", ORDER]]},
    ),
    "a class a facade function names and the module hands out": (
        {
            f"{SHOP_DIR}/logic.py": IMPORTS_ORDER
            + "@dataclasses.dataclass(frozen=True)\nclass Info:\n    order: Order\n",
            f"{SHOP_DIR}/facade/api.py": f"from {SHOP}.logic import Info\n\n__all__ = ['get', 'Info']\n\n\n"
            "def get() -> Info:\n    raise ValueError\n",
        },
        {"export-shape": [], "facade-signature": [[API, "Info.__init__", "field:order", ORDER]]},
    ),
    "a class a handed-out function names, handed out after it": (
        {
            f"{SHOP_DIR}/logic.py": IMPORTS_ORDER
            + "@dataclasses.dataclass(frozen=True)\nclass Info:\n    order: Order\n\n\n"
            "def get() -> Info:\n    raise ValueError\n",
            f"{SHOP_DIR}/facade/api.py": f"from {SHOP}.logic import get\nfrom {SHOP}.logic import Info\n\n__all__ = ['get', 'Info']\n",
        },
        {"export-shape": [], "facade-signature": [[API, "Info.__init__", "field:order", ORDER]]},
    ),
    "a class a handed-out function names, handed out before it": (
        {
            f"{SHOP_DIR}/logic.py": IMPORTS_ORDER
            + "@dataclasses.dataclass(frozen=True)\nclass Info:\n    order: Order\n\n\n"
            "def get() -> Info:\n    raise ValueError\n",
            f"{SHOP_DIR}/facade/api.py": f"from {SHOP}.logic import Info\nfrom {SHOP}.logic import get\n\n__all__ = ['get', 'Info']\n",
        },
        {"export-shape": [], "facade-signature": [[API, "Info.__init__", "field:order", ORDER]]},
    ),
    "a field inherited from a pydantic model core defines": (
        {
            "posthog/rows.py": IMPORTS_ORDER + "class CoreRow(BaseModel):\n    order: Order\n",
            f"{SHOP_DIR}/logic.py": "from posthog.rows import CoreRow\n\n\nclass Row(CoreRow):\n    title: str\n",
            f"{SHOP_DIR}/facade/api.py": f"from {SHOP}.logic import Row\n\n\ndef get() -> Row:\n    raise ValueError\n",
        },
        {"export-shape": [[ORDER, API, "export-denied(model)"]], "facade-signature": []},
    ),
    "a contract class a registration function returns, with a core model on the list": (
        {
            **CORE_MODELS,
            f"{SHOP_DIR}/facade/contracts.py": "import dataclasses\n\nfrom posthog.models.team.team import Team\n\n\n"
            "@dataclasses.dataclass(frozen=True)\nclass Info:\n    team: Team\n",
            f"{SHOP_DIR}/routes.py": f"from {SHOP}.facade.contracts import Info\n\n\ndef register() -> Info:\n    raise ValueError\n",
        },
        {
            "export-shape": [["posthog.models.team.team.Team", ROUTES, "export-denied(model)"]],
            "facade-signature": [],
        },
    ),
    "a method of a handed-out class that is a finding": (
        handed_out("class Loader:\n    def load(self) -> Order:\n        raise ValueError\n", "Loader"),
        {"export-shape": [[f"{SHOP}.errors.Loader", ERRORS, "export-shape(class)"]], "facade-signature": []},
    ),
}


@pytest.mark.parametrize("case", SLOT_OWNER_CASES)
def test_export_shape_judges_a_slot_no_signature_rule_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    files, expected = SLOT_OWNER_CASES[case]
    out = both_engines(tmp_path, BASE, files, monkeypatch)
    assert {rule: findings_of(out["native"], rule) for rule in expected} == expected


def test_the_fixture_module_is_outside_the_contract_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    testing = (
        "from typing import Any\n\nfrom rest_framework import serializers\n\n"
        "from products.shop.backend.models import Order\n\n\n"
        "def make_order(**fields: Any) -> Order:\n    return Order(**fields)\n\n\n"
        "class OrderFactory:\n    def build(self) -> Order:\n        return Order()\n"
    )
    out = both_engines(tmp_path, BASE, {"products/shop/backend/facade/testing.py": testing}, monkeypatch)
    for rule in ("export-shape", "facade-signature", "facade-logic", "facade-no-drf"):
        assert findings_of(out["native"], rule) == [], rule


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
    assert findings_of(out["native"], "contracts-no-django") == [[f"{SHOP}.facade.contracts", dst] for dst in reached]


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
    assert findings_of(out["native"], "export-shape") == [[f"{SHOP}.models.Order", API, "export-denied(model)"]]
    assert findings_of(out["native"], "facade-signature") == []


# An allowed contract type is allowed as a class; what its fields and methods name is still judged.
ALLOWED_WITH_A_MODEL = (
    "from typing import Protocol\n\nfrom pydantic import BaseModel\n\n"
    "from products.billing.backend.models import Invoice\n\n\n"
    "class Payload(BaseModel):\n    invoice: Invoice\n\n\n"
    "class BillingError(Exception):\n    def __init__(self, invoice: Invoice) -> None:\n        self.invoice = invoice\n\n\n"
    "class Chooser(Protocol):\n    def __call__(self, invoice: Invoice) -> None: ...\n\n\n"
    "class InvoiceMissing(Exception):\n    invoice: Invoice\n\n\n"
    "class Holder(Protocol):\n    invoice: Invoice\n"
)


def test_an_allowed_contract_type_is_still_walked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = {**BILLING, "products/shop/backend/facade/contracts.py": ALLOWED_WITH_A_MODEL}
    out = both_engines(tmp_path, BASE, files, monkeypatch)
    contracts = f"{SHOP}.facade.contracts"
    assert findings_of(out["native"], "facade-signature") == [
        [contracts, "BillingError.__init__", "param:invoice", INVOICE],
        [contracts, "Chooser.__call__", "param:invoice", INVOICE],
        [contracts, "Holder", "attr:invoice", INVOICE],
        [contracts, "InvoiceMissing", "attr:invoice", INVOICE],
        [contracts, "Payload.__init__", "field:invoice", INVOICE],
    ]
    assert findings_of(out["native"], "export-shape") == []


# An allowed class is walked where `walk_where` holds and where it cannot be told, as `allow_where`
# excuses only where it holds: a class in a file no assignment claims has an unknown tier.
WALKED_CASES = {
    "a product's class": ("products/shop/backend/kit.py", "products.shop.backend.kit"),
    "a class whose tier is unknown": ("scripts/kit.py", "scripts.kit"),
}


@pytest.mark.parametrize("case", WALKED_CASES)
def test_an_allowed_class_whose_walk_cannot_be_told_is_walked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    path, module = WALKED_CASES[case]
    kit = (
        "from pydantic import BaseModel\n\nfrom products.billing.backend.models import Invoice\n\n\n"
        "class Payload(BaseModel):\n    invoice: Invoice\n"
    )
    files = {
        "hoglint.toml": ROOT_CONFIG.replace(
            'first_party = ["posthog", "products"]', 'first_party = ["posthog", "products", "scripts"]'
        ),
        "scripts/__init__.py": "",
        **BILLING,
        path: kit,
        "products/shop/backend/facade/api.py": f"from {module} import Payload\n\n\ndef get() -> Payload:\n    return Payload()\n",
    }
    out = both_engines(tmp_path, BASE, files, monkeypatch)
    assert findings_of(out["native"], "export-shape") == [[INVOICE, API, "export-denied(model)"]]


# The core shapes' base classes have one list, in Python: the native engine reads it from the plan.
# A class that inherits a base the list leaves out is a plain class in both engines.
@pytest.mark.parametrize("shape", ["exception", "protocol"])
def test_both_engines_read_one_list_of_core_shape_bases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    monkeypatch.setitem(exports.SHAPE_BASES, shape, frozenset())
    contracts = (
        "from typing import Protocol\n\n\n"
        "class ShopError(Exception):\n    pass\n\n\n"
        "class Chooser(Protocol):\n    def __call__(self, name: str) -> str: ...\n"
    )
    out = both_engines(tmp_path, BASE, {"products/shop/backend/facade/contracts.py": contracts}, monkeypatch)
    left_out = "ShopError" if shape == "exception" else "Chooser"
    module = f"{SHOP}.facade.contracts"
    assert findings_of(out["native"], "export-shape") == [[f"{module}.{left_out}", module, "export-shape(class)"]]
