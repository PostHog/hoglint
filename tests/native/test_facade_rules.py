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
    "posthog/settings.py": "INSTALLED_APPS = ['posthog', 'products.shop.backend']\n",
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
