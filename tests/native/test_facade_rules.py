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
HANDS_OUT_A_MODEL = "from products.shop.backend import models\n\nThing = models.Order\n\n__all__ = ['Thing']\n\n\n"


def bodies_of(module: str) -> list[list[str]]:
    return [[f"{module}.{name}", module] for name in ("build", "_helper", "Runner")]


# A capability submodule (a doctrine stem at the top of the facade, or a module that hands out
# wiring or model classes) holds no body; `api*.py`, contracts, enums and testing never are one.
LOGIC_CASES = {
    "a doctrine stem": ("tasks.py", BODIES, bodies_of(f"{SHOP}.facade.tasks")),
    "a doctrine stem below the top": ("jobs/tasks.py", BODIES, []),
    "a module that hands out wiring": ("hooks.py", HANDS_OUT_WIRING + BODIES, bodies_of(f"{SHOP}.facade.hooks")),
    "a module that hands out logic": ("hooks.py", HANDS_OUT_LOGIC + BODIES, []),
    "a module that hands out a model by assigning it again": (
        "hooks.py",
        HANDS_OUT_A_MODEL + BODIES,
        bodies_of(f"{SHOP}.facade.hooks"),
    ),
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
# A command class in a slot the signature rule reads is that rule's; in a field of `Job`, whose row in
# the product's logic no signature rule reads, it is the export rule's. Each case is one finding of
# exactly one rule.
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
# A signature rule that reads only rows in a test context: it reads no production slot.
TEST_ONLY_SIGNATURE_RULE = """
[rules.test-signature]
primitive  = "signature"
select     = "src.zone == 'facade'"
contexts   = { include = ["test"] }
deny       = "dst.kind == 'model'"
identity   = "facade-slot"
ledger     = false
"""
CONTRACTS = f"{SHOP}.facade.contracts"
CLEAN = {"export-shape": [], "facade-signature": []}
DENIED_AT_ERRORS = {"export-shape": [[ORDER, ERRORS, "export-denied(model)"]], "facade-signature": []}
ANY_AT_ERRORS = {"export-shape": [["typing.Any", ERRORS, "export-unknown"]], "facade-signature": []}
JUDGED_AT_ROUTES = [
    [ORDER, ROUTES, "export-denied(model)"],
    [f"{SHOP}.nowhere.Missing", ROUTES, "unresolved-exposes"],
    ["typing.Any", ROUTES, "export-unknown"],
]
# A frozen dataclass whose one field is a model, by its name.
ORDER_DATACLASS = "@dataclasses.dataclass(frozen=True)\nclass {name}:\n    order: Order\n"
INFO_FIELD = [API, "Info.__init__", "field:order", ORDER]
INFO_FIELD_AT_CONTRACTS = [CONTRACTS, "Info.__init__", "field:order", ORDER]
# An exception whose `__init__` takes an opaque payload.
OPAQUE_INIT = (
    "from typing import Any\n\n\nclass Rejected(Exception):\n"
    "    def __init__(self, extra: dict[str, Any]) -> None:\n        self.extra = extra\n"
)
# The class and function of the hand-out order cases, defined in the product's logic.
INFO_AND_GET = IMPORTS_ORDER + ORDER_DATACLASS.format(name="Info") + "\n\ndef get() -> Info:\n    raise ValueError\n"
# A contract dataclass the facade defines, with a field it inherits from the product's logic.
INHERITED_FIELD = {
    f"{SHOP_DIR}/logic.py": IMPORTS_ORDER + ORDER_DATACLASS.format(name="Base"),
    f"{SHOP_DIR}/facade/contracts.py": f"import dataclasses\n\nfrom {SHOP}.logic import Base\n\n\n"
    "@dataclasses.dataclass(frozen=True)\nclass Info(Base):\n    pass\n",
}


def two_names(module: str, name: str, *bound: str) -> str:
    """A facade file that hands one symbol of `module` out under each name of `bound`, in order."""
    imports = "".join(f"from {module} import {name} as {each}\n" for each in bound)
    return imports + f"\n__all__ = {list(bound)!r}\n"


def returns_in_rejected(annotation: str) -> str:
    """An exception whose one method returns `annotation`."""
    return f"\n\nclass Rejected(Exception):\n    def item(self) -> {annotation}:\n        raise ValueError\n"


# Names bound at the top of a module that the producer does not expand into types, each as `Ref`.
UNEXPANDED_NAMES = {
    "a NewType": "from typing import NewType\n\nRef = NewType('Ref', Order)\n",
    "a TypeVar": "from typing import TypeVar\n\nRef = TypeVar('Ref')\n",
    "a functional TypedDict": "from typing import TypedDict\n\nRef = TypedDict('Ref', {'order': Order})\n",
    "a constant": "Ref = 3\n",
}
MEMBER_TAKES_REF = "\n\nclass Rejected(Exception):\n    def item(self) -> Ref:\n        raise ValueError\n"
# An allowed class, and an attribute that names a class-body alias of the same spelling.
KINDS = {
    f"{SHOP_DIR}/kinds.py": "import dataclasses\n\n\n@dataclasses.dataclass(frozen=True)\nclass Fine:\n    total: int\n"
}
FINE_HIDDEN_IN_ATTRIBUTE = (
    f"from {SHOP}.kinds import Fine\n\n\nclass Rejected(Exception):\n    Fine = Order\n    order: Fine\n"
)
# A frozen dataclass with one field and one `ClassVar` that names a model.
CLASS_VAR_DATACLASS = "@dataclasses.dataclass(frozen=True)\nclass Record:\n    total: int\n    kind: ClassVar[Order]\n"
# A chain of aliases one deeper than the producer follows.
TOO_DEEP_ALIASES = "".join(f"A{depth} = A{depth + 1} | None\n" for depth in range(34)) + "A34 = Order\n"
# Parts of an annotation that are no name, subscript, `|`, tuple, list, string or star.
UNREAD_FORMS = {
    "an operator other than |": "list[int] | (Order & int)",
    "a conditional expression": "(Order if TYPE_CHECKING else int)",
    "a call": "list[type(Order)]",
    "a boolean operator": "Order or None",
}


def facade_function(signature: str, imports: str = "from typing import TYPE_CHECKING\n") -> str:
    """A facade module whose one function `get` has `signature`."""
    return f"{imports}\nfrom {SHOP}.models import Order\n\n\ndef get{signature}:\n    raise ValueError\n"


# A pydantic model core defines whose field is a model.
CORE_MODEL_ROW = "class Record(BaseModel):\n    order: Order\n"
# A class-body alias whose name a module-level alias also binds.
SHADOWED_ALIAS = "Ref = int\n\n\nclass Rejected(Exception):\n    Ref = Order\n\n    def item(self) -> Ref:\n        raise ValueError\n"
REF_UNKNOWN_AT_ERRORS = {"export-shape": [[f"{SHOP}.errors.Ref", ERRORS, "export-unknown"]], "facade-signature": []}
# A type alias bound once per branch stands for every binding's types; one binding that is no
# type alias leaves the name unknown.
BRANCH_ALIASES = {
    "under TYPE_CHECKING, int otherwise": (
        "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    Ref = Order\nelse:\n    Ref = int\n",
        DENIED_AT_ERRORS,
    ),
    "in the arms of a try": ("try:\n    Ref = Order\nexcept ImportError:\n    Ref = int\n", DENIED_AT_ERRORS),
    "under TYPE_CHECKING only": (
        "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    Ref = Order\n",
        DENIED_AT_ERRORS,
    ),
    "under TYPE_CHECKING, Any otherwise": (
        "from typing import TYPE_CHECKING, Any\n\nif TYPE_CHECKING:\n    Ref = Order\nelse:\n    Ref = Any\n",
        {
            "export-shape": [[ORDER, ERRORS, "export-denied(model)"], ["typing.Any", ERRORS, "export-unknown"]],
            "facade-signature": [],
        },
    ),
    "under TYPE_CHECKING, a NewType otherwise": (
        "from typing import TYPE_CHECKING, NewType\n\nif TYPE_CHECKING:\n    Ref = Order\nelse:\n    Ref = NewType('Ref', int)\n",
        REF_UNKNOWN_AT_ERRORS,
    ),
}


MAKES_A_BASE = "def make() -> type:\n    return object\n\n\n"
# Bases that resolve to something that is no class: a call's result, a function, a variable.
UNKNOWN_BASES = {
    "an allowed class with a call base": "class Rejected(Exception, make()):\n    pass\n",
    "a class whose one base is a call": "class Rejected(make()):\n    pass\n",
    "an allowed class with a subscript of a call as base": "class Rejected(Exception, make()[int]):\n    pass\n",
    "an allowed class with a third-party call base": (
        "from collections import namedtuple\n\n\nclass Rejected(Exception, namedtuple('Pair', 'a b')):\n    pass\n"
    ),
    "an allowed class whose call base hides a model method": (
        "class Mixin:\n    def item(self) -> Order:\n        raise ValueError\n\n\n"
        "def mixin() -> type:\n    return Mixin\n\n\nclass Rejected(Exception, mixin()):\n    pass\n"
    ),
    "an allowed class with an ancestor with a call base": (
        "class Middle(make()):\n    pass\n\n\nclass Rejected(Exception, Middle):\n    pass\n"
    ),
    "an allowed class with a function as base": "class Rejected(Exception, make):\n    pass\n",
    "an allowed class with a base bound to a call's result": "Base = make()\n\n\nclass Rejected(Exception, Base):\n    pass\n",
    "an allowed class with a subscript of a variable as base": (
        "BASES = (ValueError,)\n\n\nclass Rejected(Exception, BASES[0]):\n    pass\n"
    ),
}
REJECTED_UNKNOWN = {"export-shape": [[f"{SHOP}.errors.Rejected", ERRORS, "export-unknown"]], "facade-signature": []}
SHOP_ERRORS = (
    "from typing import TYPE_CHECKING, Generic, TypeVar\n\nT = TypeVar('T')\n\n\n"
    "class ShopError(Exception, Generic[T]):\n    def order(self) -> Order:\n        raise ValueError\n\n\n"
    "class Mixin:\n    pass\n\n\n"
)
# A base bound to a name: the name stands for every class its bindings name (a subscript read
# through), and is unknown when one binding names no class.
BOUND_BASES = {
    "a base bound to a class under TYPE_CHECKING and to object otherwise": (
        "if TYPE_CHECKING:\n    _Base = Mixin\nelse:\n    _Base = object\n\n\n"
        "class Rejected(Exception, _Base):\n    pass\n",
        CLEAN,
    ),
    "a base bound to two classes of different shapes": (
        "if TYPE_CHECKING:\n    _Base = ShopError\nelse:\n    _Base = Mixin\n\n\nclass Rejected(_Base):\n    pass\n",
        DENIED_AT_ERRORS,
    ),
    "a base bound to a class in one branch and to a call in the other": (
        "def make() -> type:\n    return Mixin\n\n\n"
        "if TYPE_CHECKING:\n    _Base = Mixin\nelse:\n    _Base = make()\n\n\n"
        "class Rejected(Exception, _Base):\n    pass\n",
        REJECTED_UNKNOWN,
    ),
    "a base bound to a subscript of a class": (
        "Bound = ShopError[int]\n\n\nclass Rejected(Bound):\n    pass\n",
        DENIED_AT_ERRORS,
    ),
    "a base bound to a name bound to a subscript of a class": (
        "Inner = ShopError[int]\nBound = Inner\n\n\nclass Rejected(Bound):\n    pass\n",
        DENIED_AT_ERRORS,
    ),
    "a base bound in a cycle": (
        "First = Second\nSecond = First\n\n\nclass Rejected(Exception, First):\n    pass\n",
        REJECTED_UNKNOWN,
    ),
    "a base bound to a class in each arm of a try": (
        "try:\n    _Base = Mixin\nexcept ImportError:\n    _Base = object\n\n\n"
        "class Rejected(Exception, _Base):\n    pass\n",
        CLEAN,
    ),
}


def returns(module: str, name: str) -> str:
    """A facade file whose one function returns a class it imports from `module`."""
    return f"from {module} import {name}\n\n\ndef get() -> {name}:\n    raise ValueError\n"


# A contract dataclass of the product's logic, handed out by one file and returned by another.
INFO_IN_LOGIC = {f"{SHOP_DIR}/logic.py": IMPORTS_ORDER + ORDER_DATACLASS.format(name="Info")}
HANDS_OUT_INFO = f"from {SHOP}.logic import Info\n\n__all__ = ['Info']\n"
RETURNS_INFO = returns(f"{SHOP}.logic", "Info")
INFO_AT_CONTRACTS_ONLY = {"export-shape": [], "facade-signature": [INFO_FIELD_AT_CONTRACTS]}
INFO_DENIED_AT_API = {"export-shape": [[ORDER, API, "export-denied(model)"]], "facade-signature": []}


# A slot is left to the signature rule only where that rule reads the slot's row: in a facade
# module, in a public callable, or in the copy a facade holds of a function or a data shape's fields
# it hands out. A registration surface, a private method and a member of a class handed out from
# outside the facade are judged by the export rule.
SLOT_OWNER_CASES: dict[str, tuple[dict[str, str], dict[str, list[list[str]]]]] = {
    "a facade function": ({f"{SHOP_DIR}/facade/api.py": TAKES_AN_ORDER}, {"export-shape": []}),
    "a registration function": ({f"{SHOP_DIR}/routes.py": TAKES_AN_ORDER}, {"export-shape": JUDGED_AT_ROUTES}),
    "a registration function an unnamed signature rule reads": (
        {
            "hoglint.toml": ROOT_CONFIG + UNNAMED_SIGNATURE_RULE,
            f"{SHOP_DIR}/routes.py": TAKES_AN_ORDER,
        },
        {
            "export-shape": JUDGED_AT_ROUTES,
            "routes-signature": [
                [ROUTES, "place", "param:extra", "typing.Any"],
                [ROUTES, "place", "param:order", ORDER],
                [ROUTES, "place", "param:other", f"{SHOP}.nowhere.Missing"],
            ],
        },
    ),
    "a private method of a contract": (
        {f"{SHOP_DIR}/facade/contracts.py": PRIVATE_METHOD},
        {"export-shape": [[ORDER, CONTRACTS, "export-denied(model)"]]},
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
        handed_out(ORDER_DATACLASS.format(name="Receipt"), "Receipt"),
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
        CLEAN,
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
    "an opaque payload parameter of a handed-out exception": (handed_out(OPAQUE_INIT, "Rejected"), CLEAN),
    "an opaque payload returned by a method of a handed-out exception": (
        handed_out(
            "from typing import Any\n\n\nclass Rejected(Exception):\n"
            "    def extra(self) -> dict[str, Any]:\n        return {}\n",
            "Rejected",
        ),
        ANY_AT_ERRORS,
    ),
    "a bare Any parameter of a handed-out exception": (
        handed_out(
            "from typing import Any\n\n\nclass Rejected(Exception):\n"
            "    def __init__(self, extra: Any) -> None:\n        self.extra = extra\n",
            "Rejected",
        ),
        ANY_AT_ERRORS,
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
            f"{SHOP_DIR}/facade/api.py": returns(f"{SHOP}.logic", "Info"),
        },
        CLEAN,
    ),
    "an opaque payload only one of two named rules leaves out": (
        {
            "hoglint.toml": ROOT_CONFIG
            + UNNAMED_SIGNATURE_RULE
            + '\n[rules.export-shape]\nleave_slots_to = { add = ["routes-signature"] }\n',
            **handed_out(OPAQUE_INIT, "Rejected"),
        },
        ANY_AT_ERRORS,
    ),
    "a field of a contract class a facade function returns": (
        {
            f"{SHOP_DIR}/facade/contracts.py": IMPORTS_ORDER + ORDER_DATACLASS.format(name="Info"),
            f"{SHOP_DIR}/facade/api.py": returns(CONTRACTS, "Info"),
        },
        {"export-shape": [], "facade-signature": [INFO_FIELD_AT_CONTRACTS]},
    ),
    "an inherited field of a contract dataclass the facade defines": (
        INHERITED_FIELD,
        {"export-shape": [], "facade-signature": [INFO_FIELD_AT_CONTRACTS]},
    ),
    "an inherited field of a contract dataclass the facade package hands out": (
        {**INHERITED_FIELD, f"{SHOP_DIR}/facade/__init__.py": f"from {CONTRACTS} import Info\n\n__all__ = ['Info']\n"},
        {"export-shape": [], "facade-signature": [INFO_FIELD_AT_CONTRACTS]},
    ),
    "a dataclass handed out under a private name": (
        {
            f"{SHOP_DIR}/logic.py": IMPORTS_ORDER + ORDER_DATACLASS.format(name="Receipt"),
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
            f"{SHOP_DIR}/logic.py": IMPORTS_ORDER + ORDER_DATACLASS.format(name="Info"),
            f"{SHOP_DIR}/facade/api.py": f"from {SHOP}.logic import Info\n\n__all__ = ['get', 'Info']\n\n\n"
            "def get() -> Info:\n    raise ValueError\n",
        },
        {"export-shape": [], "facade-signature": [INFO_FIELD]},
    ),
    "a class a handed-out function names, handed out after it": (
        {
            f"{SHOP_DIR}/logic.py": INFO_AND_GET,
            f"{SHOP_DIR}/facade/api.py": f"from {SHOP}.logic import get\nfrom {SHOP}.logic import Info\n\n__all__ = ['get', 'Info']\n",
        },
        {"export-shape": [], "facade-signature": [INFO_FIELD]},
    ),
    "a class a handed-out function names, handed out before it": (
        {
            f"{SHOP_DIR}/logic.py": INFO_AND_GET,
            f"{SHOP_DIR}/facade/api.py": f"from {SHOP}.logic import Info\nfrom {SHOP}.logic import get\n\n__all__ = ['get', 'Info']\n",
        },
        {"export-shape": [], "facade-signature": [INFO_FIELD]},
    ),
    "a field inherited from a pydantic model core defines": (
        {
            "posthog/rows.py": IMPORTS_ORDER + "class CoreRow(BaseModel):\n    order: Order\n",
            f"{SHOP_DIR}/logic.py": "from posthog.rows import CoreRow\n\n\nclass Row(CoreRow):\n    title: str\n",
            f"{SHOP_DIR}/facade/api.py": returns(f"{SHOP}.logic", "Row"),
        },
        {"export-shape": [[ORDER, API, "export-denied(model)"]], "facade-signature": []},
    ),
    "a contract class a registration function returns, with a core model on the list": (
        {
            **CORE_MODELS,
            f"{SHOP_DIR}/facade/contracts.py": "import dataclasses\n\nfrom posthog.models.team.team import Team\n\n\n"
            "@dataclasses.dataclass(frozen=True)\nclass Info:\n    team: Team\n",
            f"{SHOP_DIR}/routes.py": f"from {CONTRACTS} import Info\n\n\ndef register() -> Info:\n    raise ValueError\n",
        },
        {
            "export-shape": [["posthog.models.team.team.Team", ROUTES, "export-denied(model)"]],
            "facade-signature": [],
        },
    ),
    "a facade function a named rule reads in other contexts only": (
        {
            "hoglint.toml": ROOT_CONFIG
            + TEST_ONLY_SIGNATURE_RULE
            + '\n[rules.export-shape]\nleave_slots_to = { add = ["test-signature"], remove = ["facade-signature"] }\n',
            f"{SHOP_DIR}/facade/api.py": IMPORTS_ORDER + "def place(order: Order) -> None:\n    pass\n",
        },
        {"export-shape": [[ORDER, API, "export-denied(model)"]], "test-signature": []},
    ),
    "an allowed class with a base that does not resolve": (
        handed_out(
            f"from {SHOP}.nowhere import Missing\n\n\nclass Rejected(Exception, Missing):\n    pass\n", "Rejected"
        ),
        {"export-shape": [[f"{SHOP}.errors.Rejected", ERRORS, "export-unknown"]], "facade-signature": []},
    ),
    **{case: (handed_out(MAKES_A_BASE + body, "Rejected"), REJECTED_UNKNOWN) for case, body in UNKNOWN_BASES.items()},
    **{case: (handed_out(SHOP_ERRORS + body, "Rejected"), expected) for case, (body, expected) in BOUND_BASES.items()},
    "an allowed class with a base that names a first-party class through an alias": (
        handed_out(
            "class ShopError(Exception):\n    pass\n\n\nBase = ShopError\n\n\nclass Rejected(Base):\n    pass\n",
            "Rejected",
        ),
        CLEAN,
    ),
    "a model whose base is an abstract model the product defines": (
        {
            f"{SHOP_DIR}/models.py": "from django.db import models\n\n\nclass Stamped(models.Model):\n"
            "    class Meta:\n        abstract = True\n\n\nclass Order(Stamped):\n    pass\n",
            f"{SHOP_DIR}/facade/errors.py": f"from {SHOP}.models import Order\n\n__all__ = ['Order']\n",
        },
        DENIED_AT_ERRORS,
    ),
    "an allowed class with a generic base": (
        handed_out(
            "from typing import Generic, TypeVar\n\nT = TypeVar('T')\n\n\nclass Rejected(Exception, Generic[T]):\n    pass\n",
            "Rejected",
        ),
        CLEAN,
    ),
    "a contract class with a call base": (
        {f"{SHOP_DIR}/facade/contracts.py": MAKES_A_BASE + "class Rejected(Exception, make()):\n    pass\n"},
        {"export-shape": [[f"{CONTRACTS}.Rejected", CONTRACTS, "export-unknown"]], "facade-signature": []},
    ),
    "a handed-out dataclass with a call base": (
        handed_out(MAKES_A_BASE + "@dataclasses.dataclass(frozen=True)\nclass Info(make()):\n    total: int\n", "Info"),
        {
            "export-shape": [[f"{SHOP}.errors.Info", ERRORS, "export-unknown"]],
            "facade-signature": [[ERRORS, "Info.__init__", "fields", f"{SHOP}.errors.make"]],
        },
    ),
    "a contract dataclass with a call base": (
        {
            f"{SHOP_DIR}/facade/contracts.py": "import dataclasses\n\n\n"
            + MAKES_A_BASE
            + "@dataclasses.dataclass(frozen=True)\nclass Info(make()):\n    total: int\n"
        },
        {
            "export-shape": [[f"{CONTRACTS}.Info", CONTRACTS, "export-unknown"]],
            "facade-signature": [[CONTRACTS, "Info.__init__", "fields", f"{CONTRACTS}.make"]],
        },
    ),
    "a facade function slot that names a class with a call base": (
        {
            f"{SHOP_DIR}/errors.py": MAKES_A_BASE + "class Rejected(Exception, make()):\n    pass\n",
            f"{SHOP_DIR}/facade/api.py": f"from {SHOP}.errors import Rejected\n\n\ndef get(x: Rejected) -> None:\n    pass\n",
        },
        {
            "export-shape": [[f"{SHOP}.errors.Rejected", API, "export-unknown"]],
            "facade-signature": [[API, "get", "param:x", f"{SHOP}.errors.Rejected"]],
        },
    ),
    "a type alias in a method of a handed-out exception": (
        handed_out(
            "Orders = list[Order]\n\n\nclass Rejected(Exception):\n    def orders(self) -> Orders:\n        raise ValueError\n",
            "Rejected",
        ),
        DENIED_AT_ERRORS,
    ),
    "a union-of-literals alias in a method of a handed-out exception": (
        handed_out(
            "from typing import Literal\n\nKind = Literal['open', 'closed']\n\n\nclass Rejected(Exception):\n"
            "    def kind(self, default: Kind) -> Kind:\n        raise ValueError\n",
            "Rejected",
        ),
        CLEAN,
    ),
    "an imported alias in a method of a handed-out exception": (
        {
            f"{SHOP_DIR}/types.py": IMPORTS_ORDER + "Orders = list[Order]\n",
            **handed_out(
                f"from {SHOP}.types import Orders\n\n\nclass Rejected(Exception):\n"
                "    def orders(self) -> Orders:\n        raise ValueError\n",
                "Rejected",
            ),
        },
        DENIED_AT_ERRORS,
    ),
    "an opaque payload alias in a parameter of a handed-out exception": (
        handed_out(
            "from typing import Any\n\nPayload = dict[str, Any]\n\n\nclass Rejected(Exception):\n"
            "    def __init__(self, extra: Payload) -> None:\n        self.extra = extra\n",
            "Rejected",
        ),
        CLEAN,
    ),
    "an opaque payload alias returned by a method of a handed-out exception": (
        handed_out(
            "from typing import Any\n\nPayload = dict[str, Any]\n\n\nclass Rejected(Exception):\n"
            "    def extra(self) -> Payload:\n        return {}\n",
            "Rejected",
        ),
        ANY_AT_ERRORS,
    ),
    "a TypeVar in a method of a handed-out exception": (
        handed_out(
            "from typing import TypeVar\n\nT = TypeVar('T')\n\n\nclass Rejected(Exception):\n"
            "    def take(self, value: T) -> None:\n        pass\n",
            "Rejected",
        ),
        {"export-shape": [[f"{SHOP}.errors.T", ERRORS, "export-unknown"]], "facade-signature": []},
    ),
    "a TypeVar in a facade function": (
        {
            f"{SHOP_DIR}/facade/api.py": "from typing import TypeVar\n\nT = TypeVar('T')\n\n\ndef take(value: T) -> None:\n    pass\n"
        },
        {"export-shape": [], "facade-signature": [[API, "take", "param:value", f"{API}.T"]]},
    ),
    "an alias in a private method of a contract class": (
        {
            f"{SHOP_DIR}/facade/contracts.py": IMPORTS_ORDER + "Orders = list[Order]\n\n\nclass Rejected(Exception):\n"
            "    def _load(self) -> Orders:\n        raise ValueError\n"
        },
        {"export-shape": [[ORDER, CONTRACTS, "export-denied(model)"]], "facade-signature": []},
    ),
    # A symbol handed out under two names: the public copy is the signature rule's, in any order.
    "a dataclass handed out under a private name, then a public one": (
        {
            f"{SHOP_DIR}/logic.py": IMPORTS_ORDER + ORDER_DATACLASS.format(name="Receipt"),
            f"{SHOP_DIR}/facade/api.py": two_names(f"{SHOP}.logic", "Receipt", "_Receipt", "Receipt"),
        },
        {"export-shape": [], "facade-signature": [[API, "Receipt.__init__", "field:order", ORDER]]},
    ),
    "a dataclass handed out under a public name, then a private one": (
        {
            f"{SHOP_DIR}/logic.py": IMPORTS_ORDER + ORDER_DATACLASS.format(name="Receipt"),
            f"{SHOP_DIR}/facade/api.py": two_names(f"{SHOP}.logic", "Receipt", "Receipt", "_Receipt"),
        },
        {"export-shape": [], "facade-signature": [[API, "Receipt.__init__", "field:order", ORDER]]},
    ),
    "a function handed out under a private name, then a public one": (
        {
            f"{SHOP_DIR}/logic.py": IMPORTS_ORDER + "def place(order: Order) -> None:\n    pass\n",
            f"{SHOP_DIR}/facade/api.py": two_names(f"{SHOP}.logic", "place", "_place", "place"),
        },
        {"export-shape": [], "facade-signature": [[API, "place", "param:order", ORDER]]},
    ),
    "a function handed out under a public name, then a private one": (
        {
            f"{SHOP_DIR}/logic.py": IMPORTS_ORDER + "def place(order: Order) -> None:\n    pass\n",
            f"{SHOP_DIR}/facade/api.py": two_names(f"{SHOP}.logic", "place", "place", "_place"),
        },
        {"export-shape": [], "facade-signature": [[API, "place", "param:order", ORDER]]},
    ),
    "a dataclass handed out under three names": (
        {
            f"{SHOP_DIR}/logic.py": IMPORTS_ORDER + ORDER_DATACLASS.format(name="Receipt"),
            f"{SHOP_DIR}/facade/api.py": two_names(f"{SHOP}.logic", "Receipt", "_Receipt", "Bill", "Receipt"),
        },
        {
            "export-shape": [],
            "facade-signature": [
                [API, "Bill.__init__", "field:order", ORDER],
                [API, "Receipt.__init__", "field:order", ORDER],
            ],
        },
    ),
    "a dataclass handed out under two public names": (
        {
            f"{SHOP_DIR}/logic.py": IMPORTS_ORDER + ORDER_DATACLASS.format(name="Receipt"),
            f"{SHOP_DIR}/facade/api.py": two_names(f"{SHOP}.logic", "Receipt", "Bill", "Receipt"),
        },
        {
            "export-shape": [],
            "facade-signature": [
                [API, "Bill.__init__", "field:order", ORDER],
                [API, "Receipt.__init__", "field:order", ORDER],
            ],
        },
    ),
    **{
        f"a model alias bound {case}, in a method of a handed-out exception": (
            handed_out(body + MEMBER_TAKES_REF, "Rejected"),
            expected,
        )
        for case, (body, expected) in BRANCH_ALIASES.items()
    },
    "a model alias bound under TYPE_CHECKING, int otherwise, in a facade function": (
        {
            f"{SHOP_DIR}/facade/api.py": IMPORTS_ORDER
            + BRANCH_ALIASES["under TYPE_CHECKING, int otherwise"][0]
            + "\n\ndef get() -> Ref:\n    raise ValueError\n"
        },
        {"export-shape": [], "facade-signature": [[API, "get", "return", ORDER]]},
    ),
    # A name the producer does not expand, named by a facade function's slot and by a member slot.
    **{
        f"{kind} a facade function names first": (
            {
                f"{SHOP_DIR}/errors.py": IMPORTS_ORDER + UNEXPANDED_NAMES[kind] + MEMBER_TAKES_REF,
                f"{SHOP_DIR}/facade/api.py": f"from {SHOP}.errors import Ref, Rejected\n\n__all__ = ['get', 'Rejected']\n\n\n"
                "def get(x: Ref) -> None:\n    pass\n",
            },
            {
                "export-shape": [[f"{SHOP}.errors.Ref", API, "export-unknown"]],
                "facade-signature": [[API, "get", "param:x", f"{SHOP}.errors.Ref"]],
            },
        )
        for kind in UNEXPANDED_NAMES
    },
    "a NewType a handed-out function names second": (
        {
            f"{SHOP_DIR}/errors.py": IMPORTS_ORDER
            + UNEXPANDED_NAMES["a NewType"]
            + MEMBER_TAKES_REF
            + "\n\ndef get(x: Ref) -> None:\n    pass\n",
            f"{SHOP_DIR}/facade/api.py": f"from {SHOP}.errors import Rejected\nfrom {SHOP}.errors import get\n\n"
            "__all__ = ['Rejected', 'get']\n",
        },
        {
            "export-shape": [[f"{SHOP}.errors.Ref", API, "export-unknown"]],
            "facade-signature": [[API, "get", "param:x", f"{SHOP}.errors.Ref"]],
        },
    ),
    # A name the module hands out itself, next to the class whose member names it, in both orders.
    **{
        f"{kind} handed out {order}": (
            {
                f"{SHOP_DIR}/errors.py": IMPORTS_ORDER + UNEXPANDED_NAMES[kind] + MEMBER_TAKES_REF,
                f"{SHOP_DIR}/facade/errors.py": HANDS_OUT_ERRORS.format(
                    names=", ".join(names), listed=", ".join(f"'{name}'" for name in names)
                ),
            },
            REF_UNKNOWN_AT_ERRORS,
        )
        for kind in UNEXPANDED_NAMES
        for order, names in (("before the class", ("Ref", "Rejected")), ("after the class", ("Rejected", "Ref")))
    },
    "a TypeVar and its generic class, handed out by a module with no function": (
        {
            f"{SHOP_DIR}/errors.py": "from typing import Generic, TypeVar\n\nT = TypeVar('T')\n\n\n"
            "class Rejected(Exception, Generic[T]):\n    def item(self) -> T:\n        raise ValueError\n",
            f"{SHOP_DIR}/facade/errors.py": f"from {SHOP}.errors import T, Rejected\n",
        },
        {"export-shape": [[f"{SHOP}.errors.T", ERRORS, "export-unknown"]], "facade-signature": []},
    ),
    # A name the producer does not expand, next to or inside an alias it does expand.
    "a NewType inside an alias's value": (
        handed_out(UNEXPANDED_NAMES["a NewType"] + "Maybe = Ref | None\n" + returns_in_rejected("Maybe"), "Rejected"),
        REF_UNKNOWN_AT_ERRORS,
    ),
    "a NewType next to an alias in one slot": (
        handed_out(UNEXPANDED_NAMES["a NewType"] + "Ids = list[int]\n" + returns_in_rejected("Ids | Ref"), "Rejected"),
        REF_UNKNOWN_AT_ERRORS,
    ),
    "a functional TypedDict inside an alias's value": (
        handed_out(
            UNEXPANDED_NAMES["a functional TypedDict"] + "Many = list[Ref]\n" + returns_in_rejected("Many"), "Rejected"
        ),
        REF_UNKNOWN_AT_ERRORS,
    ),
    "a TypeVar inside an alias of an alias": (
        handed_out(
            UNEXPANDED_NAMES["a TypeVar"] + "Inner = list[Ref]\nOuter = Inner | None\n" + returns_in_rejected("Outer"),
            "Rejected",
        ),
        REF_UNKNOWN_AT_ERRORS,
    ),
    "a recursive alias": (
        handed_out("type Json = str | dict[str, Json]\n" + returns_in_rejected("Json"), "Rejected"),
        CLEAN,
    ),
    # An alias in a class body: the producer does not expand it, so its target is unknown.
    "a class-body alias returned by a method of a handed-out exception": (
        handed_out(
            "class Rejected(Exception):\n    Ref = Order\n\n    def item(self) -> Ref:\n        raise ValueError\n",
            "Rejected",
        ),
        {"export-shape": [[f"{SHOP}.errors.Rejected.Ref", ERRORS, "export-unknown"]], "facade-signature": []},
    ),
    "a class-body generic alias returned by a method of a handed-out exception": (
        handed_out(
            "class Rejected(Exception):\n    Refs = list[Order]\n\n    def items(self) -> Refs:\n        raise ValueError\n",
            "Rejected",
        ),
        {"export-shape": [[f"{SHOP}.errors.Rejected.Refs", ERRORS, "export-unknown"]], "facade-signature": []},
    ),
    "a class-body alias in a parameter of a handed-out exception": (
        handed_out(
            "class Rejected(Exception):\n    Ref = Order\n\n    def take(self, item: Ref) -> None:\n        pass\n",
            "Rejected",
        ),
        {"export-shape": [[f"{SHOP}.errors.Rejected.Ref", ERRORS, "export-unknown"]], "facade-signature": []},
    ),
    "a class-body alias in a nested class of a handed-out exception": (
        handed_out(
            "class Rejected(Exception):\n    class Inner:\n        Ref = Order\n\n"
            "        def item(self) -> Ref:\n            raise ValueError\n",
            "Rejected",
        ),
        {"export-shape": [[f"{SHOP}.errors.Rejected.Inner.Ref", ERRORS, "export-unknown"]], "facade-signature": []},
    ),
    # A module-level alias of the same name does not stand for the class-body alias.
    "a class-body alias next to a module-level alias of the same name, handed out": (
        handed_out(SHADOWED_ALIAS, "Rejected"),
        {"export-shape": [[f"{SHOP}.errors.Rejected.Ref", ERRORS, "export-unknown"]], "facade-signature": []},
    ),
    "a class-body alias next to a module-level alias of the same name, in a contract": (
        {f"{SHOP_DIR}/facade/contracts.py": IMPORTS_ORDER + SHADOWED_ALIAS},
        {"export-shape": [], "facade-signature": [[CONTRACTS, "Rejected.item", "return", f"{CONTRACTS}.Rejected.Ref"]]},
    ),
    "a class-body alias in a contract": (
        {f"{SHOP_DIR}/facade/contracts.py": IMPORTS_ORDER + SHADOWED_ALIAS.replace("Ref = int\n\n\n", "")},
        {"export-shape": [], "facade-signature": [[CONTRACTS, "Rejected.item", "return", f"{CONTRACTS}.Rejected.Ref"]]},
    ),
    # A class-body annotation sees the names of the class body first, then the module.
    "a class-body alias in an attribute of a handed-out exception, hiding an allowed class": (
        {**KINDS, **handed_out(FINE_HIDDEN_IN_ATTRIBUTE, "Rejected")},
        {"export-shape": [[f"{SHOP}.errors.Rejected.Fine", ERRORS, "export-unknown"]], "facade-signature": []},
    ),
    "a class-body alias in an attribute of a contract class, hiding an allowed class": (
        {**KINDS, f"{SHOP_DIR}/facade/contracts.py": IMPORTS_ORDER + FINE_HIDDEN_IN_ATTRIBUTE},
        {"export-shape": [], "facade-signature": [[CONTRACTS, "Rejected", "attr:order", f"{CONTRACTS}.Rejected.Fine"]]},
    ),
    "a class-body alias in a field of a handed-out dataclass, hiding a module-level alias": (
        handed_out(
            "Ref = int\n\n\n@dataclasses.dataclass(frozen=True)\nclass Info:\n    Ref = Order\n    order: Ref\n", "Info"
        ),
        {"export-shape": [], "facade-signature": [[ERRORS, "Info.__init__", "field:order", f"{SHOP}.errors.Info.Ref"]]},
    ),
    "a field named like the module its annotation names": (
        {
            f"{SHOP_DIR}/facade/contracts.py": "import dataclasses\nimport uuid\n\n\n"
            "@dataclasses.dataclass(frozen=True)\nclass Creator:\n    uuid: uuid.UUID\n"
        },
        CLEAN,
    ),
    "a pydantic contract's field names its nested model": (
        {
            f"{SHOP_DIR}/facade/contracts.py": "from pydantic import BaseModel\n\n\n"
            "class Row(BaseModel):\n    class Part(BaseModel):\n        title: str\n\n    part: Part\n"
        },
        CLEAN,
    ),
    "a contract dataclass's field names its nested enum": (
        {
            f"{SHOP_DIR}/facade/contracts.py": "import dataclasses\nfrom enum import Enum\n\n\n"
            "@dataclasses.dataclass(frozen=True)\nclass Row:\n    class Kind(Enum):\n        A = 'a'\n\n    kind: Kind\n"
        },
        CLEAN,
    ),
    "a handed-out exception's attribute names its nested exception": (
        handed_out(
            "class Rejected(Exception):\n    class Inner(Exception):\n        pass\n\n    inner: Inner\n", "Rejected"
        ),
        CLEAN,
    ),
    # A class core defines is core's contract also when it is a data shape: only its fields are read.
    "a method of a frozen dataclass core defines": (
        {
            "posthog/records.py": IMPORTS_ORDER
            + "@dataclasses.dataclass(frozen=True)\nclass CoreRecord:\n    count: int\n\n"
            "    def load(self) -> Order:\n        raise ValueError\n",
            f"{SHOP_DIR}/facade/api.py": "from posthog.records import CoreRecord\n\n__all__ = ['CoreRecord']\n",
        },
        CLEAN,
    ),
    # One name gives one row: an alias left too deep to follow, a name both bare and in an alias.
    "an alias too deep to follow, in a method of a handed-out exception": (
        handed_out(TOO_DEEP_ALIASES + returns_in_rejected("A0"), "Rejected"),
        {"export-shape": [["A32", ERRORS, "unresolved-exposes"]], "facade-signature": []},
    ),
    "a NewType bare and through an alias in one slot of a facade function": (
        {
            f"{SHOP_DIR}/facade/api.py": IMPORTS_ORDER
            + UNEXPANDED_NAMES["a NewType"]
            + "Maybe = Ref | None\n\n\ndef get() -> Maybe | Ref:\n    raise ValueError\n"
        },
        {"export-shape": [], "facade-signature": [[API, "get", "return", f"{API}.Ref"]]},
    ),
    # A starred part is read through; a part of another form makes the slot unknown.
    "a starred tuple of a model in a facade function's return": (
        {f"{SHOP_DIR}/facade/api.py": facade_function("() -> tuple[int, *tuple[Order, ...]]")},
        {"export-shape": [], "facade-signature": [[API, "get", "return", ORDER]]},
    ),
    "a starred tuple of a model typing *args of a facade function": (
        {f"{SHOP_DIR}/facade/api.py": facade_function("(*args: *tuple[Order, int]) -> None")},
        {"export-shape": [], "facade-signature": [[API, "get", "param:args", ORDER]]},
    ),
    **{
        f"{form} in a facade function's return": (
            {f"{SHOP_DIR}/facade/api.py": facade_function(f"() -> {annotation}")},
            {"export-shape": [], "facade-signature": [[API, "get", "return", "?"]]},
        )
        for form, annotation in UNREAD_FORMS.items()
    },
    **{
        f"{form} in a method of a handed-out exception": (
            handed_out("from typing import TYPE_CHECKING\n" + returns_in_rejected(annotation), "Rejected"),
            {"export-shape": [[f"{SHOP}.errors.Rejected", ERRORS, "export-unknown"]], "facade-signature": []},
        )
        for form, annotation in UNREAD_FORMS.items()
    },
    "an alias whose value has an operator other than |, in a method of a handed-out exception": (
        handed_out("Odd = list[Order & int]\n" + returns_in_rejected("Odd"), "Rejected"),
        {"export-shape": [[f"{SHOP}.errors.Rejected", ERRORS, "export-unknown"]], "facade-signature": []},
    ),
    "the metadata of Annotated, an ellipsis and a Literal's values are no types": (
        {
            f"{SHOP_DIR}/facade/api.py": facade_function(
                "(a: Annotated[int, Field(gt=0)], b: tuple[int, ...], c: Callable[..., int]) -> Literal['x', 1]",
                "from collections.abc import Callable\nfrom typing import Annotated, Literal\n\n"
                "from pydantic import Field\n",
            )
        },
        CLEAN,
    ),
    # The fields of a dataclass-like class core defines are read where a slot names it; its methods are not.
    "a field of a pydantic model core defines, returned by a facade function": (
        {
            "posthog/records.py": IMPORTS_ORDER + CORE_MODEL_ROW,
            f"{SHOP_DIR}/facade/api.py": returns("posthog.records", "Record"),
        },
        {"export-shape": [[ORDER, API, "export-denied(model)"]], "facade-signature": []},
    ),
    "a field of a pydantic model core defines, returned by a method of a handed-out exception": (
        {
            "posthog/records.py": IMPORTS_ORDER + CORE_MODEL_ROW,
            **handed_out("from posthog.records import Record\n" + returns_in_rejected("Record"), "Rejected"),
        },
        DENIED_AT_ERRORS,
    ),
    "a field of a pydantic model core defines, in a field of a contract dataclass": (
        {
            "posthog/records.py": IMPORTS_ORDER + CORE_MODEL_ROW,
            f"{SHOP_DIR}/facade/contracts.py": "import dataclasses\n\nfrom posthog.records import Record\n\n\n"
            "@dataclasses.dataclass(frozen=True)\nclass Info:\n    record: Record\n",
        },
        {"export-shape": [[ORDER, CONTRACTS, "export-denied(model)"]], "facade-signature": []},
    ),
    "a method of a pydantic model core defines, returned by a facade function": (
        {
            "posthog/records.py": IMPORTS_ORDER
            + "class Record(BaseModel):\n    total: int\n\n    def load(self) -> Order:\n        raise ValueError\n",
            f"{SHOP_DIR}/facade/api.py": returns("posthog.records", "Record"),
        },
        CLEAN,
    ),
    # A `ClassVar` is no constructor slot: it is an attribute, which `walk_where` decides.
    "a ClassVar of a frozen dataclass core defines, handed out": (
        {
            "posthog/records.py": IMPORTS_ORDER + "from typing import ClassVar\n\n\n" + CLASS_VAR_DATACLASS,
            f"{SHOP_DIR}/facade/api.py": "from posthog.records import Record\n\n__all__ = ['Record']\n",
        },
        CLEAN,
    ),
    "a ClassVar of an exception core defines, handed out": (
        {
            "posthog/records.py": IMPORTS_ORDER
            + "from typing import ClassVar\n\n\nclass Record(Exception):\n    kind: ClassVar[Order]\n",
            f"{SHOP_DIR}/facade/api.py": "from posthog.records import Record\n\n__all__ = ['Record']\n",
        },
        CLEAN,
    ),
    "a ClassVar a contract dataclass inherits from a frozen dataclass core defines": (
        {
            "posthog/records.py": IMPORTS_ORDER + "from typing import ClassVar\n\n\n" + CLASS_VAR_DATACLASS,
            f"{SHOP_DIR}/facade/contracts.py": "import dataclasses\n\nfrom posthog.records import Record\n\n\n"
            "@dataclasses.dataclass(frozen=True)\nclass Mine(Record):\n    pass\n",
        },
        CLEAN,
    ),
    "a ClassVar of a frozen dataclass the product defines, handed out": (
        handed_out("from typing import ClassVar\n\n\n" + CLASS_VAR_DATACLASS, "Record"),
        DENIED_AT_ERRORS,
    ),
    "a ClassVar of a contract dataclass": (
        {f"{SHOP_DIR}/facade/contracts.py": IMPORTS_ORDER + "from typing import ClassVar\n\n\n" + CLASS_VAR_DATACLASS},
        {"export-shape": [], "facade-signature": [[CONTRACTS, "Record", "attr:kind", ORDER]]},
    ),
    "a method of a handed-out class that is a finding": (
        handed_out("class Loader:\n    def load(self) -> Order:\n        raise ValueError\n", "Loader"),
        {"export-shape": [[f"{SHOP}.errors.Loader", ERRORS, "export-shape(class)"]], "facade-signature": []},
    ),
    # One facade file hands a class out, another names it in a slot: the copy is the signature
    # rule's when a file of the same export and root holds it under a public name.
    "a class one facade file hands out and another returns": (
        {**INFO_IN_LOGIC, f"{SHOP_DIR}/facade/contracts.py": HANDS_OUT_INFO, f"{SHOP_DIR}/facade/api.py": RETURNS_INFO},
        INFO_AT_CONTRACTS_ONLY,
    ),
    "a class one facade file hands out and another imports from it and returns": (
        {
            **INFO_IN_LOGIC,
            f"{SHOP_DIR}/facade/contracts.py": HANDS_OUT_INFO,
            f"{SHOP_DIR}/facade/api.py": returns(CONTRACTS, "Info"),
        },
        INFO_AT_CONTRACTS_ONLY,
    ),
    "a class one facade file hands out under another name and another returns": (
        {
            **INFO_IN_LOGIC,
            f"{SHOP_DIR}/facade/contracts.py": f"from {SHOP}.logic import Info as Details\n\n__all__ = ['Details']\n",
            f"{SHOP_DIR}/facade/api.py": RETURNS_INFO,
        },
        {"export-shape": [], "facade-signature": [[CONTRACTS, "Details.__init__", "field:order", ORDER]]},
    ),
    "a class one facade file hands out by an import it keeps public, next to a function": (
        {
            **INFO_IN_LOGIC,
            f"{SHOP_DIR}/facade/contracts.py": f"from {SHOP}.logic import Info as Info\n\n\n"
            "def noop() -> None:\n    pass\n",
            f"{SHOP_DIR}/facade/api.py": RETURNS_INFO,
        },
        INFO_AT_CONTRACTS_ONLY,
    ),
    "a class one facade file hands out, in a field of a class another returns": (
        {
            f"{SHOP_DIR}/logic.py": IMPORTS_ORDER
            + ORDER_DATACLASS.format(name="Info")
            + "\n\n@dataclasses.dataclass(frozen=True)\nclass Outer:\n    info: Info\n",
            f"{SHOP_DIR}/facade/contracts.py": HANDS_OUT_INFO,
            f"{SHOP_DIR}/facade/api.py": returns(f"{SHOP}.logic", "Outer"),
        },
        INFO_AT_CONTRACTS_ONLY,
    ),
    "a class two facade files hand out and a third returns": (
        {
            **INFO_IN_LOGIC,
            f"{SHOP_DIR}/facade/contracts.py": HANDS_OUT_INFO,
            f"{SHOP_DIR}/facade/__init__.py": f"from {CONTRACTS} import Info\n\n__all__ = ['Info']\n",
            f"{SHOP_DIR}/facade/api.py": RETURNS_INFO,
        },
        {
            "export-shape": [],
            "facade-signature": [[f"{SHOP}.facade", "Info.__init__", "field:order", ORDER], INFO_FIELD_AT_CONTRACTS],
        },
    ),
    "a class one facade file hands out with a bare dict field and another returns": (
        {
            f"{SHOP_DIR}/logic.py": IMPORTS_ORDER
            + "@dataclasses.dataclass(frozen=True)\nclass Info:\n    extra: dict\n    order: Order\n",
            f"{SHOP_DIR}/facade/contracts.py": HANDS_OUT_INFO,
            f"{SHOP_DIR}/facade/api.py": RETURNS_INFO,
        },
        {
            "export-shape": [],
            "facade-signature": [[CONTRACTS, "Info.__init__", "field:extra", "builtins.dict"], INFO_FIELD_AT_CONTRACTS],
        },
    ),
    # The copy the signature rule does not read, or that no file of the same export and root
    # holds, leaves the slot to the export rule.
    "a class one facade file hands out under a private name and another returns": (
        {
            **INFO_IN_LOGIC,
            f"{SHOP_DIR}/facade/contracts.py": f"from {SHOP}.logic import Info as _Info\n\n__all__ = ['_Info']\n",
            f"{SHOP_DIR}/facade/api.py": RETURNS_INFO,
        },
        {
            "export-shape": [[ORDER, API, "export-denied(model)"], [ORDER, CONTRACTS, "export-denied(model)"]],
            "facade-signature": [],
        },
    ),
    "a class the facade's fixture file hands out and a facade file returns": (
        {**INFO_IN_LOGIC, f"{SHOP_DIR}/facade/testing.py": HANDS_OUT_INFO, f"{SHOP_DIR}/facade/api.py": RETURNS_INFO},
        INFO_DENIED_AT_API,
    ),
    "a class another product's facade hands out and this product's facade returns": (
        {
            **INFO_IN_LOGIC,
            "products/other/__init__.py": "",
            "products/other/backend/__init__.py": "",
            "products/other/backend/facade/__init__.py": "",
            "products/other/backend/facade/contracts.py": HANDS_OUT_INFO,
            f"{SHOP_DIR}/facade/api.py": RETURNS_INFO,
        },
        {
            "export-shape": [[ORDER, API, "export-denied(model)"]],
            "facade-signature": [["products.other.backend.facade.contracts", "Info.__init__", "field:order", ORDER]],
        },
    ),
    "a class a registration file hands out and a facade file returns": (
        {**INFO_IN_LOGIC, f"{SHOP_DIR}/routes.py": HANDS_OUT_INFO, f"{SHOP_DIR}/facade/api.py": RETURNS_INFO},
        {
            "export-shape": [[ORDER, API, "export-denied(model)"], [ORDER, ROUTES, "export-denied(model)"]],
            "facade-signature": [],
        },
    ),
    "an exception one facade file hands out and another names": (
        {
            f"{SHOP_DIR}/logic.py": IMPORTS_ORDER + "class Rejected(Exception):\n    def item(self) -> Order:\n"
            "        raise ValueError\n",
            f"{SHOP_DIR}/facade/errors.py": f"from {SHOP}.logic import Rejected\n\n__all__ = ['Rejected']\n",
            f"{SHOP_DIR}/facade/api.py": f"from {SHOP}.logic import Rejected\n\n\ndef get(e: Rejected) -> None:\n    pass\n",
        },
        {
            "export-shape": [[ORDER, API, "export-denied(model)"], [ORDER, ERRORS, "export-denied(model)"]],
            "facade-signature": [],
        },
    ),
}


@pytest.mark.parametrize("case", SLOT_OWNER_CASES)
def test_export_shape_judges_a_slot_no_signature_rule_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    files, expected = SLOT_OWNER_CASES[case]
    out = both_engines(tmp_path, BASE, files, monkeypatch)
    assert {rule: findings_of(out["native"], rule) for rule in expected} == expected


# A model, a frozen dataclass, an exception whose method returns the model, a function and a plain
# class, behind a `__future__` import.
STAR_SOURCE = (
    "from __future__ import annotations\n\nimport dataclasses\n\nfrom products.shop.backend.models import Order\n\n\n"
    + ORDER_DATACLASS.format(name="Receipt")
    + "\n\nclass Rejected(Exception):\n    def item(self) -> Order:\n        raise ValueError\n\n\n"
    "def place(order: Order) -> None:\n    pass\n\n\nclass Loose:\n    pass\n"
)
LISTS_FIVE = "\n\n__all__ = ['Order', 'Receipt', 'Rejected', 'place', 'Loose']\n"
STAR = "from products.shop.backend.errors import *\n"
FIVE_ROWS = {
    "export-shape": [[f"{SHOP}.errors.Loose", ERRORS, "export-shape(class)"], [ORDER, ERRORS, "export-denied(model)"]],
    "facade-signature": [[ERRORS, "Receipt.__init__", "field:order", ORDER], [ERRORS, "place", "param:order", ORDER]],
}
UNREAD_STAR = f"{SHOP}.errors.*"
# A star import hands out what the same names, imported by name, hand out: each name it binds is an
# import edge of its own. A star the engine cannot read is an unresolved import.
STAR_CASES = {
    "a star import of a source with __all__": (
        {f"{SHOP_DIR}/errors.py": STAR_SOURCE + LISTS_FIVE, f"{SHOP_DIR}/facade/errors.py": STAR},
        FIVE_ROWS,
    ),
    "the same five names imported by name": (
        {
            f"{SHOP_DIR}/errors.py": STAR_SOURCE + LISTS_FIVE,
            f"{SHOP_DIR}/facade/errors.py": STAR.replace("*", "Order, Receipt, Rejected, place, Loose"),
        },
        FIVE_ROWS,
    ),
    "a star import of a source with no __all__": (
        {f"{SHOP_DIR}/errors.py": STAR_SOURCE, f"{SHOP_DIR}/facade/errors.py": STAR},
        FIVE_ROWS,
    ),
    "a star import of a module that star-imports": (
        {
            f"{SHOP_DIR}/errors.py": STAR_SOURCE + LISTS_FIVE,
            f"{SHOP_DIR}/again.py": STAR,
            f"{SHOP_DIR}/facade/errors.py": STAR.replace("errors", "again"),
        },
        FIVE_ROWS,
    ),
    "a star import whose names the facade lists": (
        {
            f"{SHOP_DIR}/errors.py": STAR_SOURCE + LISTS_FIVE,
            f"{SHOP_DIR}/facade/errors.py": STAR
            + "\n__all__ = ['Order', 'Rejected', 'noop']\n\n\ndef noop() -> None:\n    pass\n",
        },
        DENIED_AT_ERRORS,
    ),
    "a star import of a source whose __all__ cannot be read": (
        {
            f"{SHOP_DIR}/errors.py": STAR_SOURCE + "\n\n__all__ = sorted(globals())\n",
            f"{SHOP_DIR}/facade/errors.py": STAR,
        },
        {
            "export-shape": [[UNREAD_STAR, ERRORS, "unresolved-import"]],
            "facade-signature": [[ERRORS, "*", "signature", UNREAD_STAR]],
        },
    ),
    "a star import whose source lists a name it does not bind": (
        {
            f"{SHOP_DIR}/errors.py": STAR_SOURCE + "\n\n__all__ = ['Rejected', 'Ghost']\n",
            f"{SHOP_DIR}/facade/errors.py": STAR,
        },
        {
            "export-shape": [
                [f"{SHOP}.errors.Ghost", ERRORS, "unresolved-import"],
                [ORDER, ERRORS, "export-denied(model)"],
            ],
            "facade-signature": [[ERRORS, "Ghost", "signature", f"{SHOP}.errors.Ghost"]],
        },
    ),
    "a star import whose source lists a private class": (
        {
            f"{SHOP_DIR}/errors.py": IMPORTS_ORDER
            + "class _Hidden(Exception):\n    def item(self) -> Order:\n        raise ValueError\n\n\n__all__ = ['_Hidden']\n",
            f"{SHOP_DIR}/facade/errors.py": STAR,
        },
        DENIED_AT_ERRORS,
    ),
    "a star import of a third-party module": (
        {f"{SHOP_DIR}/facade/errors.py": "from rest_framework.serializers import *\n"},
        {"export-shape": [], "facade-signature": []},
    ),
    "a star import of a module that does not exist": (
        {f"{SHOP_DIR}/facade/errors.py": STAR.replace("errors", "nowhere")},
        {
            "export-shape": [[f"{SHOP}.nowhere.*", ERRORS, "unresolved-import"]],
            "facade-signature": [[ERRORS, "*", "signature", f"{SHOP}.nowhere.*"]],
        },
    ),
    "a star import in a registration surface": (
        {f"{SHOP_DIR}/errors.py": STAR_SOURCE + LISTS_FIVE, f"{SHOP_DIR}/routes.py": STAR},
        {"export-shape": [[ORDER, ROUTES, "export-denied(model)"]]},
    ),
    "a star import in a registration surface of a source whose __all__ cannot be read": (
        {f"{SHOP_DIR}/errors.py": STAR_SOURCE + "\n\n__all__ = sorted(globals())\n", f"{SHOP_DIR}/routes.py": STAR},
        {"export-shape": [[UNREAD_STAR, ROUTES, "unresolved-import"]]},
    ),
}


API_FILE = f"{SHOP_DIR}/facade/api.py"
IMPORTS_MODELS = f"from {SHOP} import models\n\n"
NOOP = "\n\ndef noop() -> None:\n    pass\n"
LOOSE_AT_API = {"export-shape": [[f"{SHOP}.errors.Loose", API, "export-shape(class)"]], "facade-signature": []}
DENIED_AT_API = {"export-shape": [[ORDER, API, "export-denied(model)"]], "facade-signature": []}
CLEAN_AT_API = {"export-shape": [], "facade-signature": []}


def unknown_at_api(*names: str) -> dict[str, list[list[str]]]:
    """`export-unknown` for each name the facade api module lists and binds by nothing hoglint reads."""
    return {"export-shape": [[f"{API}.{name}", API, "export-unknown"] for name in names], "facade-signature": []}


def rebinds(value: str, *, imports: str = IMPORTS_MODELS, name: str = "Thing", tail: str = "") -> str:
    """A facade module that binds `name` again to `value` and lists it in `__all__`."""
    return f"{imports}{name} = {value}\n{tail}\n__all__ = ['{name}']\n"


# A public name a top-level assignment binds to a class or a function is handed out as the import
# of that name would be, listed in `__all__` or not; a private one only when `__all__` lists it.
ASSIGNED_CASES = {
    "a model by its attribute chain": ({API_FILE: rebinds("models.Order")}, DENIED_AT_API),
    "a model by its name": (
        {API_FILE: rebinds("Order", imports=f"from {SHOP}.models import Order\n\n")},
        DENIED_AT_API,
    ),
    "a model imported under the name (control)": (
        {API_FILE: f"from {SHOP}.models import Order as Thing\n\n__all__ = ['Thing']\n"},
        DENIED_AT_API,
    ),
    "a plain class": (
        {
            f"{SHOP_DIR}/errors.py": "class Loose:\n    pass\n",
            API_FILE: rebinds("Loose", imports=f"from {SHOP}.errors import Loose\n\n"),
        },
        LOOSE_AT_API,
    ),
    "an exception whose method returns a model": (
        {
            f"{SHOP_DIR}/errors.py": IMPORTS_ORDER
            + "class Rejected(Exception):\n    def item(self) -> Order:\n        raise ValueError\n",
            API_FILE: rebinds("Rejected", imports=f"from {SHOP}.errors import Rejected\n\n"),
        },
        DENIED_AT_API,
    ),
    "a function": (
        {
            f"{SHOP_DIR}/errors.py": IMPORTS_ORDER + "def place(order: Order) -> None:\n    pass\n",
            API_FILE: rebinds("place", imports=f"from {SHOP}.errors import place\n\n"),
        },
        {"export-shape": [], "facade-signature": [[API, "Thing", "param:order", ORDER]]},
    ),
    "a frozen dataclass": (
        {
            f"{SHOP_DIR}/errors.py": IMPORTS_ORDER + ORDER_DATACLASS.format(name="Receipt"),
            API_FILE: rebinds("Receipt", imports=f"from {SHOP}.errors import Receipt\n\n"),
        },
        {"export-shape": [], "facade-signature": [[API, "Thing.__init__", "field:order", ORDER]]},
    ),
    "a class nested in a class": (
        {
            f"{SHOP_DIR}/errors.py": "class Holder:\n    class Inner:\n        pass\n",
            API_FILE: rebinds("Holder.Inner", imports=f"from {SHOP}.errors import Holder\n\n"),
        },
        {"export-shape": [[f"{SHOP}.errors.Holder.Inner", API, "export-shape(class)"]], "facade-signature": []},
    ),
    "an alias of an alias in a logic module": (
        {
            f"{SHOP_DIR}/errors.py": IMPORTS_ORDER + "Ref = Order\n",
            API_FILE: rebinds("Ref", imports=f"from {SHOP}.errors import Ref\n\n"),
        },
        DENIED_AT_API,
    ),
    "no __all__ in a module with no function": ({API_FILE: IMPORTS_MODELS + "Thing = models.Order\n"}, DENIED_AT_API),
    "a public name not listed in a module with a function": (
        {API_FILE: IMPORTS_MODELS + "Thing = models.Order\n" + NOOP + "\n\n__all__ = ['noop']\n"},
        DENIED_AT_API,
    ),
    "a private name not listed in a module with a function": (
        {API_FILE: IMPORTS_MODELS + "_Thing = models.Order\n" + NOOP + "\n\n__all__ = ['noop']\n"},
        CLEAN_AT_API,
    ),
    "a private name listed": ({API_FILE: rebinds("models.Order", name="_Thing")}, DENIED_AT_API),
    "the last of two assignments": ({API_FILE: rebinds("3", tail="Thing = models.Order\n")}, DENIED_AT_API),
    "a registration surface": (
        {f"{SHOP_DIR}/routes.py": rebinds("models.Order")},
        {"export-shape": [[ORDER, ROUTES, "export-denied(model)"]]},
    ),
    # Written limits: none of these is judged as a name handed out; listed, each is unknown.
    "a member past a class": ({API_FILE: rebinds("models.Order.objects")}, unknown_at_api("Thing")),
    "a call": ({API_FILE: rebinds("models.Order()")}, unknown_at_api("Thing")),
    "two targets": (
        {API_FILE: IMPORTS_MODELS + "A = B = models.Order\n\n__all__ = ['A', 'B']\n"},
        unknown_at_api("A", "B"),
    ),
    "a tuple target": (
        {API_FILE: IMPORTS_MODELS + "A, B = models.Order, models.Order\n\n__all__ = ['A', 'B']\n"},
        unknown_at_api("A", "B"),
    ),
    "the first of two assignments": ({API_FILE: rebinds("models.Order", tail="Thing = 3\n")}, CLEAN_AT_API),
    "a generic alias": ({API_FILE: rebinds("list[models.Order]")}, unknown_at_api("Thing")),
    "a public name not listed, bound by a call": (
        {API_FILE: IMPORTS_MODELS + "Thing = models.Order()\n" + NOOP + "\n\n__all__ = ['noop']\n"},
        CLEAN_AT_API,
    ),
}

# Every name a readable `__all__` lists is a def, an edge, a literal constant, or unknown.
LISTED_CASES = {
    "literal constants": (
        {
            API_FILE: "LIMIT = 3\nNAME = 'shop'\nNOTHING = None\nFLAG = True\nRATE = 1.5\n"
            "PAIR = (1, 'a', None, False)\nNAMES = ['a', 'b']\nMAP = {'a': 1}\n\n"
            "__all__ = ['LIMIT', 'NAME', 'NOTHING', 'FLAG', 'RATE', 'PAIR', 'NAMES', 'MAP']\n"
        },
        CLEAN_AT_API,
    ),
    "a list of classes": ({API_FILE: rebinds("[models.Order]", name="MODELS")}, unknown_at_api("MODELS")),
    "a dict whose value is a class": (
        {API_FILE: rebinds("{'order': models.Order}", name="KINDS")},
        unknown_at_api("KINDS"),
    ),
    "a name the module does not bind": ({API_FILE: "__all__ = ['Ghost']\n"}, unknown_at_api("Ghost")),
    "a module-level name of another module": (
        {API_FILE: rebinds("models.LIMIT", name="LIMIT")},
        unknown_at_api("LIMIT"),
    ),
    # A written limit: a negative number is an operation, not a literal.
    "a negative number": ({API_FILE: "LIMIT = -1\n\n__all__ = ['LIMIT']\n"}, unknown_at_api("LIMIT")),
    "a private function listed": (
        {
            API_FILE: f"from {SHOP}.models import Order\n\n\ndef _get(order: Order) -> None:\n    pass\n\n\n__all__ = ['_get']\n"
        },
        DENIED_AT_API,
    ),
    "a private function listed, with a slot that has no annotation": (
        {API_FILE: "def _get(order) -> None:\n    pass\n\n\n__all__ = ['_get']\n"},
        unknown_at_api("_get"),
    ),
    "a private function not listed": (
        {
            API_FILE: f"from {SHOP}.models import Order\n\n\ndef _get(order: Order) -> None:\n    pass\n"
            + NOOP
            + "\n\n__all__ = ['noop']\n"
        },
        CLEAN_AT_API,
    ),
    "a private class listed": (
        {API_FILE: "class _Box:\n    pass\n\n\n__all__ = ['_Box']\n"},
        {"export-shape": [[f"{API}._Box", API, "export-shape(class)"]], "facade-signature": []},
    ),
    "an import, a def and a class": (
        {
            API_FILE: IMPORTS_MODELS + "def get() -> None:\n    pass\n\n\nclass Box:\n    pass\n\n\n"
            "__all__ = ['models', 'get', 'Box']\n"
        },
        {"export-shape": [[f"{API}.Box", API, "export-shape(class)"]], "facade-signature": []},
    ),
}


@pytest.mark.parametrize("case", LISTED_CASES)
def test_every_name_a_readable_dunder_all_lists_is_accounted_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    files, expected = LISTED_CASES[case]
    out = both_engines(tmp_path, BASE, files, monkeypatch)
    assert {rule: findings_of(out["native"], rule) for rule in expected} == {
        rule: sorted(rows) for rule, rows in expected.items()
    }


@pytest.mark.parametrize("case", ASSIGNED_CASES)
def test_a_name_assigned_again_is_handed_out_as_its_import_would_be(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    files, expected = ASSIGNED_CASES[case]
    out = both_engines(tmp_path, BASE, files, monkeypatch)
    assert {rule: findings_of(out["native"], rule) for rule in expected} == {
        rule: sorted(rows) for rule, rows in expected.items()
    }


@pytest.mark.parametrize("case", STAR_CASES)
def test_a_star_import_hands_out_what_its_names_imported_by_name_do(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    files, expected = STAR_CASES[case]
    out = both_engines(tmp_path, BASE, files, monkeypatch)
    assert {rule: findings_of(out["native"], rule) for rule in expected} == {
        rule: sorted(rows) for rule, rows in expected.items()
    }


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
