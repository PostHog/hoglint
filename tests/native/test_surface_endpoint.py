"""The `surface` endpoint of a name that is a directory without `__init__.py`: it reads the labels
the directory's files give it, as a destination does, never those of the graph module above it. Where the files directly inside disagree, it cannot tell."""

from __future__ import annotations

from pathlib import Path

import pytest

from hoglint.native.parity import read_lines

from native.test_contract_slots import clean_dump

CONFIG = """
schema = 1

[discovery]
source_roots = ["."]
first_party = ["products"]

[fields]
zone = { type = "zone", required_on = ["**"] }

[zones]
within = "products/{product}"
default = "internal"
presentation = { paths = ["backend/presentation/**"] }
tests = { paths = ["backend/**/tests/**"] }
wiring = { paths = ["backend/mixed/jobs.py"] }

[rules.presentation-uses-facade]
primitive = "edge"
view = "zones"
from = "src.zone == 'presentation'"
endpoint = "surface"
deny = "dst.zone not in ['presentation', 'tests']"
identity = "module-pair"
ledger = false
"""

TREE = {
    "hoglint.toml": CONFIG,
    "products/__init__.py": "",
    "products/shop/__init__.py": "",
    "products/shop/backend/__init__.py": "",
    "products/shop/backend/presentation/__init__.py": "",
    "products/shop/backend/checks/tests/test_views.py": "def test_view() -> None:\n    pass\n",
    "products/shop/backend/mixed/jobs.py": "def run() -> None:\n    pass\n",
    "products/shop/backend/mixed/other.py": "def run() -> None:\n    pass\n",
}

VIEWS = "products/shop/backend/presentation/views.py"
# (the import, the findings as (identity, unknown))
CASES = {
    "a directory whose files are all allowed": ("from products.shop.backend.checks import tests", []),
    "a directory whose files disagree": (
        "from products.shop.backend import mixed",
        [(["products.shop.backend.presentation.views", "products.shop.backend.mixed"], True)],
    ),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_a_surface_that_names_a_directory_without_init_reads_the_directory_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    line, findings = CASES[case]
    out = clean_dump(tmp_path, TREE, {VIEWS: f"{line}\n"}, monkeypatch)
    assert [(row[1], row[-1]) for row in read_lines(out / "findings.jsonl")] == findings
