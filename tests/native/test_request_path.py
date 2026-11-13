"""The preset's request path on small trees: every way a settings module can put a
product class into a setting ends with the product file required to be watched, or with an
incomplete item, never clean. Only a key on the exclude list is not followed."""

from __future__ import annotations

import json
import tomllib
from functools import cache
from pathlib import Path

import pytest

from hoglint.native.parity import read_lines
from hoglint.packs.django.pack import MODELS_PRODUCER, SYMBOLS_PRODUCER
from hoglint.packs.django.relations import RELATIONS_PRODUCER
from hoglint.packs.django.signals import SIGNALS_PRODUCER
from hoglint.packs.django.uses import GET_MODEL_PRODUCER, USES_PRODUCER

from native.test_contract_slots import findings_of
from native.test_evaluator import rule_table
from native.test_parity import dump_repo

MODEL_PRODUCERS = {
    MODELS_PRODUCER,
    RELATIONS_PRODUCER,
    USES_PRODUCER,
    GET_MODEL_PRODUCER,
    SIGNALS_PRODUCER,
    SYMBOLS_PRODUCER,
}
PRESET = Path(__file__).resolve().parents[2] / "presets" / "posthog.toml"

CONFIG = """
schema = 1
extends = ["hoglint:django"]

[discovery]
source_roots = ["."]
first_party = ["proj", "products"]

[django]
settings = ["proj.settings", "proj.settings.*"]

[fields]
tier = { type = "enum", values = ["core", "product"] }
settings = { type = "bool" }
request_seed = { type = "bool" }

[[assign]]
match = "products/**"
set = { tier = "product" }

[[assign]]
match = "proj/**"
set = { tier = "core" }

[[assign]]
match = "proj/settings/**"
set = { settings = true }

[rules.wiring-watched]
primitive = "coverage"
covers = ["rules.request-path.dst.path"]
inputs = { adapter = "turbo", task = "contract-check" }
identity = "entity"
growth_key = ["subject"]
"""

PRODUCT = {
    "turbo.json": json.dumps({"tasks": {"contract-check": {}}}),
    "proj/__init__.py": "",
    "products/__init__.py": "",
    "products/shop/__init__.py": "",
    "products/shop/package.json": json.dumps({"name": "shop", "scripts": {"contract-check": "true"}}),
    "products/shop/turbo.json": json.dumps({"tasks": {"contract-check": {"inputs": ["facade/**"]}}}),
    "products/shop/gate.py": "class Gate:\n    pass\n",
}

# Each form puts `products.shop.gate.Gate` into a request setting; the settings package's files.
FORMS = {
    "list(BASE)": {"proj/settings/__init__.py": '_BASE = ["products.shop.gate.Gate"]\nMIDDLEWARE = list(_BASE)\n'},
    "tuple(BASE)": {"proj/settings/__init__.py": '_BASE = ["products.shop.gate.Gate"]\nMIDDLEWARE = tuple(_BASE)\n'},
    "a conditional": {
        "proj/settings/__init__.py": 'DEBUG = True\n_ON = ["products.shop.gate.Gate"]\n_OFF = []\n'
        "MIDDLEWARE = _ON if DEBUG else _OFF\n"
    },
    "a comprehension": {
        "proj/settings/__init__.py": '_BASE = ["products.shop.gate.Gate"]\nMIDDLEWARE = [entry for entry in _BASE]\n'
    },
    "an attribute of another module": {
        "proj/settings/base.py": 'EXTRA = ["products.shop.gate.Gate"]\n',
        "proj/settings/__init__.py": "from proj.settings import base\n\nMIDDLEWARE = [*base.EXTRA]\n",
    },
    ".extend(BASE)": {
        "proj/settings/__init__.py": '_BASE = ["products.shop.gate.Gate"]\nMIDDLEWARE = []\nMIDDLEWARE.extend(_BASE)\n'
    },
    "a subscript target set from a name": {
        "proj/settings/__init__.py": '_PERMS = ["products.shop.gate.Gate"]\nREST_FRAMEWORK = {}\n'
        'REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"] = _PERMS\n'
    },
    "a computed key": {
        "proj/settings/__init__.py": 'KEY = "DEFAULT_PERMISSION_CLASSES"\nREST_FRAMEWORK = {}\n'
        'REST_FRAMEWORK[KEY] = ["products.shop.gate.Gate"]\n'
    },
    "dict(KEY=[...])": {
        "proj/settings/__init__.py": 'REST_FRAMEWORK = dict(DEFAULT_AUTHENTICATION_CLASSES=["products.shop.gate.Gate"])\n'
    },
    ".update(...)": {
        "proj/settings/__init__.py": "REST_FRAMEWORK = {}\n"
        'REST_FRAMEWORK.update({"DEFAULT_AUTHENTICATION_CLASSES": ["products.shop.gate.Gate"]})\n'
    },
    ".setdefault(...)": {
        "proj/settings/__init__.py": "REST_FRAMEWORK = {}\n"
        'REST_FRAMEWORK.setdefault("DEFAULT_THROTTLE_CLASSES", ["products.shop.gate.Gate"])\n'
    },
    "a string in a function body": {
        "proj/settings/__init__.py": 'def _middleware():\n    return ["products.shop.gate.Gate"]\n\n\nMIDDLEWARE = _middleware()\n'
    },
    "a name that flows into one other name": {
        "proj/settings/__init__.py": 'MIDDLEWARE = ["products.shop.gate.Gate"]\nDEBUG_MIDDLEWARE = MIDDLEWARE + []\n'
    },
    "a name that flows into two names": {
        "proj/settings/__init__.py": '_BASE = ["products.shop.gate.Gate"]\nMIDDLEWARE = [*_BASE]\nOTHER = [*_BASE]\n'
    },
}

# The setting of these cannot be told: the reach cannot tell whether to follow the string.
CANNOT_TELL = {"a name that flows into two names"}

# Keys the preset does not follow: the product file is not on the request path.
EXCLUDED = {
    "an app": {"proj/settings/__init__.py": 'INSTALLED_APPS = ["products.shop.gate.Gate"]\n'},
    "a logger key": {
        "proj/settings/__init__.py": 'LOGGING = {"filters": {"gate": {"()": "products.shop.gate.Gate"}}}\n'
    },
}


@cache
def request_path_rule() -> str:
    """The preset's rule, with the conditions its selectors name."""
    preset = tomllib.loads(PRESET.read_text())
    rule = preset["rules"]["request-path"]
    named = [name for name in preset["conditions"] if name in rule["select"]]
    conditions = "".join(f"{name} = {json.dumps(preset['conditions'][name])}\n" for name in named)
    return f"\n[conditions]\n{conditions}" + rule_table("request-path", rule)


def watched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, str]) -> tuple[list[str], list[str]]:
    """The files wiring-watched requires, and the keys of its incomplete items."""
    tree = {**PRODUCT, **files, "hoglint.toml": CONFIG + request_path_rule()}
    out = dump_repo(tmp_path, tree, monkeypatch)
    # The tree holds no model, so only the model producers fail.
    assert {row[0] for row in read_lines(out / "failed.jsonl")} <= MODEL_PRODUCERS
    required = [row[0] for row in findings_of(out, "wiring-watched")]
    incomplete = sorted(row[1] for row in read_lines(out / "incomplete.jsonl") if row[0] == "wiring-watched")
    return required, incomplete


@pytest.mark.parametrize("form", sorted(FORMS))
def test_a_product_class_a_settings_form_puts_on_the_request_path_is_required_or_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, form: str
) -> None:
    required, incomplete = watched(tmp_path, monkeypatch, FORMS[form])
    if form in CANNOT_TELL:
        assert (required, incomplete) == ([], ["rules.request-path|proj.settings|dotted-path|products.shop.gate.Gate"])
    else:
        assert (required, incomplete) == (["products/shop/gate.py"], [])


@pytest.mark.parametrize("form", sorted(EXCLUDED))
def test_a_key_on_the_exclude_list_is_not_followed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, form: str) -> None:
    assert watched(tmp_path, monkeypatch, EXCLUDED[form]) == ([], [])
