"""Round 9b's cases (CASES.md `WA21`, t30's ruling on open point 1): a module reached by a value
the reading cannot name. The facade binds WORKFLOWS and builds a record of it
(`BINDING = Rec(WORKFLOWS)`); another file of the product, G, which the facade imports first,
reaches the facade by `importlib.import_module`, `__import__`, `sys.modules[...]` with a computed
name, or the `__globals__` of a function no name gives, and adds BFlow in plain Python:

- a change through the value, inline or through a name: a list mutation or a record replacement by
  a literal member, `setattr`, a namespace write: unread, for each list or record the member may be;
- a computed member, a namespace `update`, or the value handed on at module level or in a default:
  every read that may depend on the module is unread (each such row runs alone, `alone`);
- the value handed on in a function body (called at import time), a returned attribute, a saved
  `functools.partial`: limit 9's sentence for function-body handoffs, waiting on the owner (`kept`);
- harmless twins: `is`, `__path__`, an unrelated member, G's own module three ways, a cache store
  under a key that names no first-party module (`watched`);
- the same computed import in a registration point: a problem of the answer."""

from __future__ import annotations

from typing import Any

from model_repo.shapes_round7_cases import case

FACADE = (
    "from products.{p}.backend.temporal.rec import Rec\n\n\n"
    "def _hook():\n    return 1\n\n\n"
    "import products.{p}.backend.g  # noqa: E402,F401\n\n"
    "BINDING = Rec(WORKFLOWS)\n"
)
REC = "class Rec:\n    def __init__(self, workflows):\n        self.workflows = workflows\n"
G_HEAD = (
    "from products.{p}.backend.temporal.a import AFlow\n"
    "from products.{p}.backend.temporal.b import BFlow\n\n\n"
    "class _Evil:\n    def __init__(self, workflows):\n        workflows.append(BFlow)\n\n\n"
    "def _patch(m):\n    m.WORKFLOWS.append(BFlow)\n\n\n"
)
NAME = '_n = "products.{p}.backend.facade" + ".temporal"\n'

# Each form: what G needs, and the expression that reaches the facade.
MODULES = {
    "im": ("import importlib\n\n" + NAME, "importlib.import_module(_n)"),
    "dunder": (NAME, '__import__(_n, fromlist=["WORKFLOWS"])'),
    "sysmod": ("import sys\n\n" + NAME, "sys.modules[_n]"),
}
GLOBALS_HEAD = (
    "def _pick():\n    import products.{p}.backend.facade.temporal as t\n\n    return t._hook\n\n\n_fn = _pick()\n"
)


def row(p: str, g: str, outcome: str, note: str, **extra: Any) -> dict[str, Any]:
    """The facade with its record, G with `g` after its head."""
    files = {"backend/temporal/rec.py": REC, "backend/g.py": G_HEAD + g}
    out = case(p, FACADE, outcome, note, files=files)
    out.update(extra)
    return out


CASES: dict[str, dict[str, Any]] = {}
for form, (head, expr) in MODULES.items():
    changes = {
        "list_inline": f"{head}{expr}.WORKFLOWS.append(BFlow)\n",
        "list_alias": f"{head}_m = {expr}\n_m.WORKFLOWS.append(BFlow)\n",
        "record_inline": f"{head}{expr}.Rec = _Evil\n",
        "record_alias": f"{head}_m = {expr}\n_m.Rec = _Evil\n",
        "setattr": f'{head}_m = {expr}\nsetattr(_m, "Rec", _Evil)\n',
        "namespace": f'{head}_m = {expr}\nvars(_m)["Rec"] = _Evil\n',
    }
    for kind, g in changes.items():
        p = f"r9b_{form}_{kind}"
        CASES[p] = row(p, g, "unread", f"{form}: a change through a computed module value, {kind}")
    p = f"r9b_{form}_member"
    g = f'{head}_m = {expr}\n_k = "WORK" + "FLOWS"\ngetattr(_m, _k).append(BFlow)\n'
    CASES[p] = row(p, g, "unread", f"{form}: a computed member", alone=True)
GLOBALS = {
    "list_inline": '_fn.__globals__["WORKFLOWS"].append(BFlow)\n',
    "list_alias": '_g = _fn.__globals__\n_g["WORKFLOWS"].append(BFlow)\n',
    "record_inline": '_fn.__globals__["Rec"] = _Evil\n',
    "record_alias": '_g = _fn.__globals__\n_g["Rec"] = _Evil\n',
}
for kind, g in GLOBALS.items():
    p = f"r9b_globals_{kind}"
    CASES[p] = row(p, GLOBALS_HEAD + g, "unread", f"__globals__: a change through it, {kind}")
for kind, g in {
    "member": '_g = _fn.__globals__\n_k = "WORK" + "FLOWS"\n_g[_k].append(BFlow)\n',
    "update": "_g = _fn.__globals__\n_g.update(Rec=_Evil)\n",
}.items():
    p = f"r9b_globals_{kind}"
    CASES[p] = row(p, GLOBALS_HEAD + g, "unread", f"__globals__: {kind}", alone=True)

IM = MODULES["im"][0]
# Handed on: at module level and in a default it is every dependent read; in a function body,
# the limit.
CASES["r9b_patch_module"] = row(
    "r9b_patch_module", IM + "_patch(importlib.import_module(_n))\n", "unread", "patch(m) at module level", alone=True
)
CASES["r9b_patch_default"] = row(
    "r9b_patch_default",
    IM + "\n\ndef _d(x=_patch(importlib.import_module(_n))):\n    return x\n",
    "unread",
    "patch(m) in a default",
    alone=True,
)
LIMIT = "limit 9 (round 9b): a function body hands a computed module value on; waits on the owner"
CASES["r9b_patch_function"] = row(
    "r9b_patch_function",
    IM + "\n\ndef _run():\n    _patch(importlib.import_module(_n))\n\n\n_run()\n",
    "kept",
    "patch(m) in a function called at import time",
    why=LIMIT,
)
CASES["r9b_returned_attribute"] = row(
    "r9b_returned_attribute",
    IM + '\n\ndef _get():\n    return getattr(importlib.import_module(_n), "WORKFLOWS")\n\n\n_get().append(BFlow)\n',
    "kept",
    "a returned attribute of a computed module value",
    why=LIMIT,
)
CASES["r9b_saved_partial"] = row(
    "r9b_saved_partial",
    "import functools\n"
    + IM
    + "\n\ndef _save():\n    global _P\n    _P = functools.partial(_patch, importlib.import_module(_n))\n\n\n_save()\n_P()\n",
    "kept",
    "a saved functools.partial of a computed module value",
    why=LIMIT,
)
# A change through a module a literal names counts for that module only.
CASES["r9b_literal_list"] = row(
    "r9b_literal_list",
    'import importlib\n\nimportlib.import_module("products.{p}.backend.facade.temporal").WORKFLOWS.append(BFlow)\n',
    "unread",
    "a literal module: its list changed",
)
# Harmless twins.
TWINS = {
    "is": IM + "_m = importlib.import_module(_n)\n_same = _m is None\n",
    "path": 'import importlib\n\n_m = importlib.import_module("products.{p}.backend" + ".facade")\n_p = _m.__path__\n',
    "unrelated_member": IM + "_m = importlib.import_module(_n)\n_m.OTHER = 1\n",
    "own_globals": 'globals()["X"] = 1\n',
    "own_vars": 'vars()["Y"] = 1\n',
    "own_sysmod": "import sys\n\nsys.modules[__name__].Z = 1\n",
    "cache_store": 'import sys\n\nsys.modules["_posthog_scopes_standalone"] = object()\n',
}
for kind, g in TWINS.items():
    p = f"r9b_ok_{kind}"
    CASES[p] = row(p, g, "watched", f"harmless: {kind}")
# The same computed import in a registration point: a problem of the whole answer.
CASES["r9b_registration_point"] = {
    **case("r9b_registration_point", "", "problem", "a computed import in a registration point"),
    "core": {
        "ee/__init__.py": "# core\n",
        "ee/hogai/__init__.py": "# core\n",
        "ee/hogai/core/__init__.py": "# core\n",
        "ee/hogai/core/agent_modes/__init__.py": "# agent modes\n",
        "ee/hogai/core/agent_modes/presets/__init__.py": "# presets\n",
        "ee/hogai/core/agent_modes/presets/r9b_registration_point.py": (
            "import importlib\n\n"
            '_n = "products.r9b_registration_point.backend.facade" + ".temporal"\n'
            "_m = importlib.import_module(_n)\n"
        ),
    },
    "reason": "presets.r9b_registration_point",
}

# The second opinion's fixed rows (t30's ruling on the round 9b stop, part 2): visible changes
# count in every scope; building a fresh module changes only that object until it is published.
PUBLISH = (
    "import sys\nimport types\n\n" + NAME + "\n\ndef _outer():\n    def _reg(key):\n"
    "        mod = types.ModuleType(key)\n        mod.WORKFLOWS = [AFlow, BFlow]\n        mod.Rec = _Evil\n"
    "        sys.modules[key] = mod\n\n    _reg(_n)\n\n\n_outer()\n"
)
for path, p in (("backend/g.py", "r9b_x_nested_publish"), ("backend/tests/test_g.py", "r9b_x_nested_publish_test")):
    files = {"backend/temporal/rec.py": REC, path: G_HEAD + PUBLISH}
    if path != "backend/g.py":
        files["backend/g.py"] = "import products.{p}.backend.tests.test_g  # noqa: F401\n"
        files["backend/tests/__init__.py"] = ""
    CASES[p] = {
        **case(
            p, FACADE, "unread", f"a fresh module published in a nested function under a parameter: {path}", files=files
        ),
        "alone": True,
    }
UNKNOWN = '_k = "WORK" + "FLOWS"\nsetattr(_m, _k, [AFlow, BFlow])\n'
CASES["r9b_x_setattr_unknown"] = row(
    "r9b_x_setattr_unknown",
    IM + "_m = importlib.import_module(_n)\n" + UNKNOWN,
    "unread",
    "setattr with an unknown member through an unresolved module",
    alone=True,
)
# F resolved, the member unknown: only F's reads; the quiet run's rows stay readable.
CASES["r9b_x_resolved_unknown"] = row(
    "r9b_x_resolved_unknown",
    'import importlib\n\n_m = importlib.import_module("products.{p}.backend.facade.temporal")\n' + UNKNOWN,
    "unread",
    "setattr with an unknown member through a module a literal names: only that module",
    quiet=True,
)
FRESH = 'import sys\nimport types\n\n_mod = types.ModuleType("x")\n_mod.WORKFLOWS = [AFlow, BFlow]\n'
CASES["r9b_ok_fresh_module"] = row("r9b_ok_fresh_module", FRESH, "watched", "a fresh module built, never published")
CASES["r9b_ok_publish_test_name"] = row(
    "r9b_ok_publish_test_name",
    FRESH + 'sys.modules["_test_schema_models"] = _mod\n',
    "watched",
    "a fresh module published under a name no first-party module has",
)
CASES["r9b_x_publish_literal"] = row(
    "r9b_x_publish_literal",
    FRESH + 'sys.modules["products.{p}.backend.facade.temporal"] = _mod\n',
    "unread",
    "a fresh module published under the facade's literal name: only the facade's reads",
    quiet=True,
)
CASES["r9b_x_function_mutation"] = row(
    "r9b_x_function_mutation",
    IM + "\n\ndef _run():\n    m = importlib.import_module(_n)\n    m.WORKFLOWS.append(BFlow)\n\n\n_run()\n",
    "unread",
    "a direct mutation in a function called at import time: no handoff, rule 2",
)
CASES["r9b_x_patch_decorator"] = row(
    "r9b_x_patch_decorator",
    IM
    + "\n\ndef _deco(x):\n    return lambda f: f\n\n\n@_deco(_patch(importlib.import_module(_n)))\ndef _h():\n    return 1\n",
    "unread",
    "patch(m) in a decorator argument: a handoff at import, not a function body",
    alone=True,
)
