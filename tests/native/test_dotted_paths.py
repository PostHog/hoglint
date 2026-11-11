"""dotted-path edges from string literals, in both engines: a string that resolves is a path edge
anywhere; one that resolves to nothing is an edge only where the code holds a path."""

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
path_callees = ["import_string"]

[fields]
package = { type = "id" }

[[assign]]
match = "products/{product}/**"
set = { package = "{product}" }
"""
BASE = {
    "hoglint.toml": CONFIG,
    "products/__init__.py": "",
    "products/shop/__init__.py": "",
    "products/shop/backend/__init__.py": "",
    "products/shop/backend/logic.py": "def charge():\n    pass\n",
}
# (the statement, the dotted-path edge it leaves: (surface, resolution), or None)
CASES = {
    "a name that resolves": (
        'name = "products.shop.backend.logic.charge"\n',
        ("products.shop.backend.logic.charge", "resolved"),
    ),
    "a logger name": ('log = getLogger("products.shop.backend.audit")\n', None),
    "a check id": ('CHECK = "products.shop.E001"\n', None),
    "a host": ('HOST = "products.shop.example.com"\n', None),
    "a patch target": (
        'patch("products.shop.backend.gone.charge")\n',
        ("products.shop.backend.gone.charge", "unresolved"),
    ),
    "a path callee's argument": (
        'import_string("products.shop.backend.gone.Thing")\n',
        ("products.shop.backend.gone.Thing", "unresolved"),
    ),
    "a path callee's second argument": ('import_string(None, "products.shop.backend.gone.Thing")\n', None),
}


@pytest.mark.parametrize("case", CASES)
def test_a_string_that_names_nothing_is_a_path_only_where_the_code_holds_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    statement, expected = CASES[case]
    out = dumps(tmp_path, {**BASE, "products/shop/backend/jobs.py": statement}, monkeypatch)
    same_in_both_engines(out)
    edges = [
        (row[6], row[8])
        for row in read_lines(out["native"] / "edges.jsonl")
        if row[0] == "dotted-path" and row[1] == "products/shop/backend/jobs.py"
    ]
    assert edges == ([expected] if expected else [])


COVERAGE_CONFIG = """
schema = 1

[discovery]
source_roots = ["."]
first_party = ["core", "products"]

[fields]
package = { type = "id" }

[[assign]]
match = "products/{product}/**"
set = { package = "{product}" }

[[assign]]
match = "core/**"
set = { package = "core" }

[rules.wiring-watched]
primitive = "coverage"
covers = ["edge.kind == 'dotted-path' and edge.context == 'test' and src.package != dst.package: ENDPOINT"]
inputs = { adapter = "turbo", task = "contract-check" }
identity = "entity"
ledger = false
"""
# A test outside the product patches `charge` where the facade binds it: the patch replaces the
# facade's name, so the facade module is the one to watch, not the file that defines `charge`.
PATCHED = {
    "products/__init__.py": "",
    "products/shop/__init__.py": "",
    "products/shop/package.json": '{"name": "shop", "scripts": {"contract-check": "true"}}',
    "products/shop/turbo.json": '{"tasks": {"contract-check": {"inputs": ["backend/facade/**"]}}}',
    "products/shop/backend/__init__.py": "",
    "products/shop/backend/logic.py": "def charge():\n    pass\n",
    "products/shop/backend/facade/__init__.py": "",
    "products/shop/backend/facade/api.py": "from products.shop.backend.logic import charge as charge\n",
    "core/__init__.py": "",
    "core/tests/__init__.py": "",
    "core/tests/test_shop.py": 'from unittest.mock import patch\n\npatch("products.shop.backend.facade.api.charge")\n',
}


@pytest.mark.parametrize(
    "endpoint, unwatched",
    [("surface", []), ("dst", [["products/shop/backend/logic.py"]])],
)
def test_a_patch_target_is_watched_where_the_string_points(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, endpoint: str, unwatched: list[list[str]]
) -> None:
    files = {**PATCHED, "hoglint.toml": COVERAGE_CONFIG.replace("ENDPOINT", endpoint)}
    out = dumps(tmp_path, files, monkeypatch)
    same_in_both_engines(out)
    assert [row[1] for row in read_lines(out["native"] / "findings.jsonl")] == unwatched
