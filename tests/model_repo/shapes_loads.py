"""Computed imports in a registration point (CASES.md `WA15`): a module a registration point loads
by a name it computes, as data for `test_watched_shapes.py`. Each case is a registration point of
its own (`ee/hogai/core/agent_modes/presets/<case>.py`, a preset glob), so no case borrows another
case's edges and a problem names its case. `watched`: the loader takes an element of a list or
tuple of string literals its module binds once and the reading reads exactly, so the answer stays
complete and the module the list names is watched. `problem`: any other computed name; the module
may be any product's, so the whole answer is incomplete (t30, Nov 19)."""

from __future__ import annotations

from typing import Any

PRESETS = "ee/hogai/core/agent_modes/presets"
PACKAGES = {
    "ee/hogai/core/__init__.py": "# core\n",
    "ee/hogai/core/agent_modes/__init__.py": "# agent modes\n",
    f"{PRESETS}/__init__.py": "# presets\n",
}
CHECK = "def detect(team_id):\n    return team_id\n"


def case(p: str, body: str, outcome: str, note: str, other: dict[str, str] | None = None) -> dict[str, Any]:
    """A product with one health check module, and a registration point of its own that loads it:
    `PATH` in `body` is the module's dotted path, `OTHER` another module of the product."""
    path = f'"products.{p}.backend.temporal.health_checks.check"'
    other_path = f'"products.{p}.backend.temporal.health_checks.other"'
    text = body.replace("PATH", path).replace("OTHER", other_path)
    core = {**PACKAGES, f"{PRESETS}/{p}.py": text}
    for extra, extra_text in (other or {}).items():
        core[extra] = extra_text.replace("PATH", path)
    return {
        "files": {
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": CHECK,
            "backend/temporal/health_checks/other.py": CHECK,
        },
        "core": core,
        "probes": ["backend/temporal/health_checks/check.py"],
        "outcome": outcome,
        "note": note,
    }


LIVE = """import importlib
import threading

MODULES = [
    PATH,
]

_loaded = False
_lock = threading.Lock()


def ensure_loaded() -> None:
    global _loaded
    with _lock:
        if _loaded:
            return
        for module_path in MODULES:
            importlib.import_module(module_path)
        _loaded = True
"""

LOADS: dict[str, dict[str, Any]] = {
    # The live shape and the forms that read it exactly.
    "ld_live": case("ld_live", LIVE, "watched", "the live health check registry: a loop in a function"),
    "ld_tuple": case(
        "ld_tuple",
        "import importlib\n\nMODULES = (PATH, OTHER)\n\n\ndef load():\n    for path in MODULES:\n        importlib.import_module(path)\n",
        "watched",
        "a tuple",
    ),
    "ld_annotated": case(
        "ld_annotated",
        "import importlib\n\nMODULES: list[str] = [PATH]\n\n\ndef load():\n    for path in MODULES:\n        importlib.import_module(path)\n",
        "watched",
        "an annotated list",
    ),
    "ld_module_loop": case(
        "ld_module_loop",
        "import importlib\n\nMODULES = [PATH]\nfor path in MODULES:\n    importlib.import_module(path)\n",
        "watched",
        "a loop at module level",
    ),
    "ld_comprehension": case(
        "ld_comprehension",
        "import importlib\n\nMODULES = [PATH]\nLOADED = [importlib.import_module(path) for path in MODULES]\n",
        "watched",
        "a comprehension over the list",
    ),
    "ld_subscript": case(
        "ld_subscript",
        "import importlib\n\nMODULES = [PATH]\n\n\ndef first():\n    return importlib.import_module(MODULES[0])\n",
        "watched",
        "an element by subscript (MODULES[0])",
    ),
    "ld_dunder": case(
        "ld_dunder",
        "MODULES = [PATH]\n\n\ndef load():\n    for path in MODULES:\n        __import__(path)\n",
        "watched",
        "__import__ in the loop",
    ),
    "ld_keyword": case(
        "ld_keyword",
        "import importlib\n\nMODULES = [PATH]\n\n\ndef load():\n    for path in MODULES:\n        importlib.import_module(name=path)\n",
        "watched",
        "import_module(name=path)",
    ),
    "ld_from_import": case(
        "ld_from_import",
        "from importlib import import_module\n\nMODULES = [PATH]\n\n\ndef load():\n    for path in MODULES:\n        import_module(path)\n",
        "watched",
        "from importlib import import_module",
    ),
    "ld_literal_loop": case(
        "ld_literal_loop",
        "import importlib\n\n\ndef load():\n    for path in (PATH,):\n        importlib.import_module(path)\n",
        "watched",
        "a loop over a literal in its header (the walk loads each string by name)",
    ),
    "ld_tuple_used": case(
        "ld_tuple_used",
        "import importlib\n\n\ndef register(modules):\n    return modules\n\n\nMODULES = (PATH,)\nregister(MODULES)\n\n\n"
        "def load():\n    for path in MODULES:\n        importlib.import_module(path)\n",
        "watched",
        "a tuple handed to a call (a tuple cannot change)",
    ),
    "ld_two_lists": case(
        "ld_two_lists",
        "import importlib\n\nFIRST = [PATH]\nSECOND = [OTHER]\n\n\ndef load():\n    for path in FIRST:\n        importlib.import_module(path)\n"
        "    for path in SECOND:\n        importlib.import_module(path)\n",
        "watched",
        "two lists, two loops",
    ),
    # Any other computed name: a problem of the answer.
    "ld_var": case(
        "ld_var",
        "import importlib\n\nMODULE = PATH\n\n\ndef load():\n    importlib.import_module(MODULE)\n",
        "problem",
        "import_module(NAME), NAME a constant",
    ),
    "ld_fstring": case(
        "ld_fstring",
        'import importlib\n\nNAME = "check"\n\n\ndef load():\n    importlib.import_module(f"products.ld_fstring.backend.temporal.health_checks.{NAME}")\n',
        "problem",
        "an f-string",
    ),
    "ld_sysmod_loop": case(
        "ld_sysmod_loop",
        "import sys\n\nMODULES = [PATH]\n\n\ndef load():\n    return [sys.modules[path] for path in MODULES]\n",
        "problem",
        "sys.modules[path] over the list (no edge names a key of sys.modules)",
    ),
    "ld_append": case(
        "ld_append",
        "import importlib\n\nMODULES = [PATH]\nMODULES.append(OTHER)\n\n\ndef load():\n    for path in MODULES:\n        importlib.import_module(path)\n",
        "problem",
        "MODULES.append(...) at module level",
    ),
    "ld_item_store": case(
        "ld_item_store",
        "import importlib\n\nMODULES = [PATH]\nMODULES[0] = OTHER\n\n\ndef load():\n    for path in MODULES:\n        importlib.import_module(path)\n",
        "problem",
        "MODULES[0] = ... at module level",
    ),
    "ld_list_escape": case(
        "ld_list_escape",
        "import importlib\n\n\ndef register(modules):\n    modules.append('x')\n\n\nMODULES = [PATH]\nregister(MODULES)\n\n\n"
        "def load():\n    for path in MODULES:\n        importlib.import_module(path)\n",
        "problem",
        "the list handed to a call that may change it",
    ),
    "ld_twice": case(
        "ld_twice",
        "import importlib\n\nMODULES = [PATH]\nMODULES = [OTHER]\n\n\ndef load():\n    for path in MODULES:\n        importlib.import_module(path)\n",
        "problem",
        "the list bound twice",
    ),
    "ld_block": case(
        "ld_block",
        "import importlib\nimport os\n\nif os.environ.get('X'):\n    MODULES = [PATH]\nelse:\n    MODULES = []\n\n\n"
        "def load():\n    for path in MODULES:\n        importlib.import_module(path)\n",
        "problem",
        "the list bound in a block",
    ),
    "ld_item_name": case(
        "ld_item_name",
        "import importlib\n\nCHECK = PATH\nMODULES = [CHECK]\n\n\ndef load():\n    for path in MODULES:\n        importlib.import_module(path)\n",
        "problem",
        "an item that is a name, not a string literal",
    ),
    "ld_shadow": case(
        "ld_shadow",
        "import importlib\n\nMODULES = [PATH]\n\n\ndef load():\n    MODULES = [OTHER]\n    for path in MODULES:\n        importlib.import_module(path)\n",
        "problem",
        "a function's own list of the same name",
    ),
    "ld_call_iter": case(
        "ld_call_iter",
        "import importlib\n\n\ndef modules():\n    return [PATH]\n\n\ndef load():\n    for path in modules():\n        importlib.import_module(path)\n",
        "problem",
        "a loop over a call's result",
    ),
    "ld_rebound_variable": case(
        "ld_rebound_variable",
        "import importlib\n\nMODULES = [PATH]\n\n\ndef load():\n    for path in MODULES:\n        path = path + '_v2'\n        importlib.import_module(path)\n",
        "problem",
        "the loop body binds the variable again",
    ),
    "ld_nested": case(
        "ld_nested",
        "import importlib\n\nGROUPS = [[PATH]]\n\n\ndef load():\n    for group in GROUPS:\n        for path in group:\n            importlib.import_module(path)\n",
        "problem",
        "a loop over another loop's variable",
    ),
    "ld_literal_comprehension": case(
        "ld_literal_comprehension",
        "import importlib\n\n\ndef load():\n    return [importlib.import_module(path) for path in (PATH,)]\n",
        "problem",
        "a comprehension over a literal in its header (no module-level list; write a loop or name the list)",
    ),
    "ld_attribute_iter": case(
        "ld_attribute_iter",
        "import importlib\n\n\nclass Registry:\n    MODULES = [PATH]\n\n\ndef load():\n    for path in Registry.MODULES:\n        importlib.import_module(path)\n",
        "problem",
        "a loop over a class attribute",
    ),
    "ld_dict": case(
        "ld_dict",
        "import importlib\n\nMODULES = {PATH: 1}\n\n\ndef load():\n    for path in MODULES:\n        importlib.import_module(path)\n",
        "problem",
        "a loop over a dict's keys",
    ),
    "ld_imported_list": case(
        "ld_imported_list",
        "import importlib\n\nfrom posthog.ld_imported_list_paths import MODULES\n\n\ndef load():\n    for path in MODULES:\n        importlib.import_module(path)\n",
        "problem",
        "a list another module binds (its strings name no edge)",
        other={"posthog/ld_imported_list_paths.py": "MODULES = [PATH]\n"},
    ),
    "ld_alias": case(
        "ld_alias",
        "from importlib import import_module as load_module\n\nMODULES = [PATH]\n\n\ndef load():\n    for path in MODULES:\n        load_module(path)\n",
        "watched",
        "the loader under another name (an import alias) is the loader (round 6)",
    ),
    "ld_loader_bound": case(
        "ld_loader_bound",
        "import importlib\n\nMODULES = [PATH]\nLOAD = importlib.import_module\n\n\ndef load():\n    for path in MODULES:\n        LOAD(path)\n",
        "problem",
        "the loader bound to a name",
    ),
    "ld_find_spec": case(
        "ld_find_spec",
        "import importlib.util\n\nSPEC = importlib.util.find_spec(PATH)\n",
        "problem",
        "importlib.util.find_spec, which the link does not read",
    ),
    "ld_run_module": case(
        "ld_run_module",
        "import runpy\n\n\ndef run():\n    runpy.run_module(PATH)\n",
        "problem",
        "runpy.run_module, which the link does not read",
    ),
    "ld_sysmod_literal": case(
        "ld_sysmod_literal",
        "import sys\n\n\ndef check():\n    return sys.modules[PATH]\n",
        "problem",
        "sys.modules[...] by a literal key (no edge names a key of sys.modules)",
    ),
    "ld_global_rebind": case(
        "ld_global_rebind",
        "import importlib\n\nMODULES = [PATH]\n\n\ndef reset():\n    global MODULES\n    MODULES = []\n\n\n"
        "def load():\n    for path in MODULES:\n        importlib.import_module(path)\n",
        "problem",
        "a function rebinds the list (global)",
    ),
}
