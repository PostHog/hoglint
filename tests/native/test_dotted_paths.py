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
