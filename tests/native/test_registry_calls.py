"""A call of a declared registry loader must name one of its registries: a name no `[registries]`
entry of that loader declares, or one the link cannot read, is a `config:undeclared-registry`
incomplete item, never a silent drop of the modules it loads."""

from __future__ import annotations

from pathlib import Path

import pytest

from hoglint.native.parity import read_lines

from native.test_parity import dump_repo

CONFIG = """
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

[registries.routes]
loader    = "core.loading.load_modules"
module    = "products/{product}/backend/routes.py"
attribute = "ROUTES"

[registries.highlights]
loader    = "core.loading.load_modules"
module    = "products/{product}/backend/facade/highlights.py"
attribute = "HIGHLIGHTS"

[registries.pages]
loader    = "core.pages.discover"
module    = "products/{product}/backend/pages.py"
attribute = "PAGES"
"""
BASE = {
    "hoglint.toml": CONFIG,
    "core/__init__.py": "",
    "core/loading.py": "import importlib\n\n\ndef load_modules(name):\n    return importlib.import_module(name)\n",
    "core/pages.py": "def discover():\n    return []\n",
    "products/__init__.py": "",
    "products/shop/__init__.py": "",
    "products/shop/backend/__init__.py": "",
    "products/shop/backend/routes.py": "ROUTES = []\n",
}
USER = "core/user.py"
IMPORT = "from core.loading import load_modules\n\n"
# (the user's text, the items it leaves: (key, kind))
CASES = {
    "a declared name": (IMPORT + 'load_modules("routes")\n', []),
    "a declared dotted name": (IMPORT + 'load_modules("facade.highlights")\n', []),
    "a declared name through a module constant": (IMPORT + 'NAME = "routes"\n\nload_modules(NAME)\n', []),
    "a declared name over a literal tuple": (
        IMPORT + 'for name in ("routes", "facade.highlights"):\n    load_modules(name)\n',
        [],
    ),
    "the one registry of a loader called with no name": ("from core.pages import discover\n\ndiscover()\n", []),
    "an undeclared name": (
        IMPORT + 'load_modules("activity_log_config")\n',
        [(f"{USER}|core.loading.load_modules|activity_log_config", "undeclared-registry")],
    ),
    # A written limit: a name is matched against the end of a registry's files, so this one reads as
    # declared although the loader would load `backend/highlights.py`.
    "a name that is only the end of a declared one": (
        IMPORT + 'load_modules("highlights")\n',
        [],
    ),
    "a name longer than any declared one": (
        IMPORT + 'load_modules("backend.facade.highlights.more")\n',
        [(f"{USER}|core.loading.load_modules|backend.facade.highlights.more", "undeclared-registry")],
    ),
    "an undeclared name through a module constant": (
        IMPORT + 'NAME = "entitlement_reactors"\n\nload_modules(NAME)\n',
        [(f"{USER}|core.loading.load_modules|entitlement_reactors", "undeclared-registry")],
    ),
    "a module constant bound twice": (
        IMPORT + 'NAME = "routes"\nNAME = "other"\n\nload_modules(NAME)\n',
        [(f"{USER}|core.loading.load_modules|name:NAME", "not-modelled")],
    ),
    "a parameter": (
        IMPORT + "def load(name):\n    return load_modules(name)\n",
        [(f"{USER}|core.loading.load_modules|name:name", "not-modelled")],
    ),
    "an f-string": (
        IMPORT + 'KIND = "routes"\nload_modules(f"{KIND}")\n',
        [(f"{USER}|core.loading.load_modules|expr:JoinedStr", "not-modelled")],
    ),
    "a function of the same last name elsewhere": ("def load_modules(name):\n    pass\n\n\nload_modules('x')\n", []),
}


@pytest.mark.parametrize("case", CASES)
def test_a_registry_loader_call_names_a_declared_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    text, expected = CASES[case]
    out = dump_repo(tmp_path, {**BASE, USER: text}, monkeypatch)
    rows = [row for row in read_lines(out / "incomplete.jsonl") if row[0] == "config:undeclared-registry"]
    assert [(row[1], row[5]) for row in rows] == expected


def test_the_words_name_the_call_site_the_loader_the_name_and_the_entry_it_needs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = IMPORT + 'load_modules("activity_log_config")\n'
    out = dump_repo(tmp_path, {**BASE, USER: text}, monkeypatch)
    [row] = [row for row in read_lines(out / "incomplete.jsonl") if row[0] == "config:undeclared-registry"]
    assert row[2] == (
        "core/user.py:3 calls core.loading.load_modules('activity_log_config'), and no [registries.<name>] entry "
        "of that loader declares its module: the modules it loads are unknown until a [registries.<name>] entry "
        "names them"
    )
