"""The reach primitive on small trees, in both engines: what a search enters past a denied
target, with and without `through_denied`."""

from __future__ import annotations

from pathlib import Path

import pytest

from native.test_contract_slots import both_engines, findings_of

CONFIG = """
schema = 1

[discovery]
source_roots = ["."]
first_party = ["core", "products"]

[fields]
tier = {{ type = "enum", values = ["core", "product"] }}

[[assign]]
match = "products/**"
set = {{ tier = "product" }}

[[assign]]
match = "core/**"
set = {{ tier = "core" }}

[rules.request-path]
primitive = "reach"
view      = "file-imports"
from      = "src.module == 'core.start'"
deny      = "dst.tier == 'product'"
contexts  = {{ exclude = ["type_only", "test", "function_local"] }}
identity  = ["src.module", "dst.module"]
ledger    = false
{through}
"""

BASE = {
    "core/__init__.py": "",
    "core/middle.py": "from products.shop.facade import api\n",
    "products/__init__.py": "",
    "products/shop/__init__.py": "",
    "products/shop/facade/__init__.py": "",
    "products/shop/facade/api.py": "from products.shop.logic import decide\n",
    "products/shop/logic.py": "from products.shop.rules import RULES\n\n\ndef decide():\n    pass\n",
    "products/shop/rules.py": "RULES = []\n",
}

# The start names the facade once, or once more through a core module the search enters after it.
STARTS = {
    "one import": "from products.shop.facade import api\n",
    "a second import of the same name": "from core.middle import api as again\nfrom products.shop.facade import api\n",
}
FACADE = ["core.start", "products.shop.facade.api"]
PAST_THE_FACADE = [["core.start", "products.shop.logic"], ["core.start", "products.shop.rules"]]


def reach_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, start: str, through: str) -> list[list[str]]:
    files = {"hoglint.toml": CONFIG.format(through=through), "core/start.py": STARTS[start]}
    out = both_engines(tmp_path, BASE, files, monkeypatch)
    return findings_of(out["native"], "request-path")


@pytest.mark.parametrize("start", sorted(STARTS))
def test_a_reach_never_enters_a_denied_module_whatever_the_order_of_imports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, start: str
) -> None:
    assert reach_rows(tmp_path, monkeypatch, start, "") == [FACADE]


@pytest.mark.parametrize("start", sorted(STARTS))
def test_a_reach_through_denied_targets_reports_every_denied_module_past_the_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, start: str
) -> None:
    assert reach_rows(tmp_path, monkeypatch, start, "through_denied = true") == sorted([FACADE, *PAST_THE_FACADE])
