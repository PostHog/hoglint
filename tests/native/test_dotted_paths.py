"""dotted-path edges from string literals: a string is a path edge only where the code holds a
path. In a sink (a patch target, a path callee's first argument) it is one whether or not it
resolves; in a path place (a loader's first argument, an element of a path callee's argument
vector, a lazy map's value, any string of a file that hands a computed path to such a callee, a
path module in tests/core) only when it names a module."""

from __future__ import annotations

from pathlib import Path

import pytest

from hoglint.native.parity import read_lines

from native.test_parity import dump_repo

CONFIG = """
schema = 1

[discovery]
source_roots = ["."]
first_party = ["products"]
path_callees = ["import_string", "subprocess.Popen", "patch.dict"]

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
# (the file, its text, the dotted-path edge it leaves: (surface, resolution), or None)
JOBS = "products/shop/backend/jobs.py"
CASES = {
    "a name that resolves, held as text": (JOBS, 'name = "products.shop.backend.logic.charge"\n', None),
    "a name that resolves, compared": (JOBS, 'def f(x):\n    return x == "products.shop.backend.logic"\n', None),
    "a logger name": (JOBS, 'log = getLogger("products.shop.backend.audit")\n', None),
    "a check id": (JOBS, 'CHECK = "products.shop.E001"\n', None),
    "a host": (JOBS, 'HOST = "products.shop.example.com"\n', None),
    "a patch target": (
        JOBS,
        'patch("products.shop.backend.gone.charge")\n',
        ("products.shop.backend.gone.charge", "unresolved"),
    ),
    "a path callee's argument": (
        JOBS,
        'import_string("products.shop.backend.gone.Thing")\n',
        ("products.shop.backend.gone.Thing", "unresolved"),
    ),
    "a path callee's second argument": (JOBS, 'import_string(None, "products.shop.backend.gone.Thing")\n', None),
    "an argument vector": (
        JOBS,
        'subprocess.Popen(["python", "-m", "products.shop.backend.logic"])\n',
        ("products.shop.backend.logic", "resolved"),
    ),
    "an argument vector's element that names nothing": (JOBS, 'subprocess.Popen(["python", "manage.py"])\n', None),
    "a loader's argument": (
        JOBS,
        'import_module("products.shop.backend.logic")\n',
        ("products.shop.backend.logic", "resolved"),
    ),
    "a loader's argument that names nothing": (JOBS, 'import_module("products.shop.backend.gone")\n', None),
    "a module-level name passed as an object": (
        JOBS,
        'import os\n\nNAME = "products.shop.backend.logic"\n\n\ndef run():\n    patch.dict(os.environ, {})\n',
        None,
    ),
    "a lazy map's value": (
        JOBS,
        '_LAZY = {"charge": "products.shop.backend.logic"}\n\n\ndef __getattr__(name):\n    raise AttributeError(name)\n',
        ("products.shop.backend.logic", "resolved"),
    ),
    "a module-level name loaded by a computed name": (
        JOBS,
        'TASK = "products.shop.backend.logic.charge"\n\n\ndef run():\n    import_string(TASK)\n',
        ("products.shop.backend.logic.charge", "resolved"),
    ),
    "a module-level name, no computed load": (
        JOBS,
        'TASK = "products.shop.backend.logic.charge"\n\n\ndef run():\n    import_string("products.shop.backend.logic")\n',
        ("products.shop.backend.logic", "resolved"),
    ),
}


@pytest.mark.parametrize("case", CASES)
def test_a_string_is_a_path_only_where_the_code_holds_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    path, text, expected = CASES[case]
    out = dump_repo(tmp_path, {**BASE, path: text}, monkeypatch)
    edges = [(row[6], row[8]) for row in read_lines(out / "edges.jsonl") if row[0] == "dotted-path" and row[1] == path]
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
    out = dump_repo(tmp_path, files, monkeypatch)
    assert [row[1] for row in read_lines(out / "findings.jsonl")] == unwatched
