"""Round 8's own cases (CASES.md `WA19`), as data for `test_watched_shapes.py`, next to the
adversary's rows (`shapes_round8.py`): a registry module that does not bind the registry's
attribute but defines a module `__getattr__` (the batch_exports form of the Temporal sweep) leaves
the declaration unread, for that reason, never empty; and the item provenance the build closed
past the adversary's rows: an item of the list in a display or a comprehension's element, an
element of a copy bound to a name, and `max`'s result each reach the list through
`gc.get_referrers` (each adds BFlow in plain Python), so each is unread.

Round 8b (t30's rulings): a re-list (`_Z = [*W]`, `_Z = W + []`, `_Z += W`) is read as a value,
and an item of it is an item of each list it re-lists; a name assigned a name that holds a module
(`_g = _b`, a chain of such) is that module for the patch check and the link's stores, and a chain
that cannot reach one module (rebound in a block, bound in a function) fails closed."""

from __future__ import annotations

from typing import Any

from model_repo.shapes_round7_cases import case

TEMPORAL = "products.{p}.backend.temporal"
GRAB = (
    "import gc\n{held}\nfor _r in gc.get_referrers(_L[0]):\n"
    "    if isinstance(_r, list) and _r is not _L and AFlow in _r and BFlow not in _r:\n"
    "        _r.append(BFlow)\n"
)
HELD = {
    "r8b_item_in_display": "_L = [WORKFLOWS[0]]",
    "r8b_item_in_comprehension": "_L = [w for w in WORKFLOWS]",
    "r8b_copy_element": "_L = list(WORKFLOWS)",
    "r8b_max_item": "_L = [max(WORKFLOWS)]",
}
RECORD_IMPORT = "from posthog.temporal.common.registration import QueueBinding, TemporalRegistration\n"

CASES: dict[str, dict[str, Any]] = {
    "r8b_lazy_declaration": {
        "files": {
            "backend/temporal/flows.py": "class LazyWorkflow:\n    def run(self):\n        return 1\n",
            "backend/temporal/registration.py": (
                RECORD_IMPORT + f"from {TEMPORAL}.flows import LazyWorkflow\n\n"
                'TEMPORAL = TemporalRegistration(queues=(QueueBinding(queue_setting="LZ", workflows=(LazyWorkflow,)),))\n'
            ),
            "backend/facade/temporal.py": (
                "from typing import Any\n\n"
                f'_B = "{TEMPORAL}."\n'
                '_LAZY = {"TEMPORAL": "registration"}\n\n\n'
                "def __getattr__(name: str) -> Any:\n"
                "    module = _LAZY.get(name)\n"
                "    if module is None:\n"
                "        raise AttributeError(name)\n"
                "    import importlib\n\n"
                "    return getattr(importlib.import_module(_B + module), name)\n"
            ),
        },
        "probes": [],
        "outcome": "unread",
        "reason": "products.{p}.backend.facade.temporal.TEMPORAL",
        "words": "its `__getattr__` may serve it",
        "note": "batch_exports form 4: the facade serves TEMPORAL through a module __getattr__",
    },
}
CASES.update(
    {
        p: case(p, GRAB.format(held=held), "unread", f"an item reaches the list through gc: {held}")
        for p, held in HELD.items()
    }
)

# Round 8b, ruling 1: each re-list form, then an item taken out of it reaches the list through gc.
RELIST = {
    "r8b_relist_star": "_Z = [*WORKFLOWS]\n_L = _Z",
    "r8b_relist_concat": "_Z = WORKFLOWS + []\n_L = _Z",
    "r8b_relist_two": "_Y = [AFlow]\n_Z = [*_Y, *WORKFLOWS]\n_L = _Z",
    "r8b_relist_max": "_Z = [*WORKFLOWS]\n_L = [max(_Z)]",
    "r8b_relist_alias_item": "_Z = [*WORKFLOWS]\n_A = _Z\n_L = [_A[0]]",
    "r8b_relist_iadd": "_Z = []\n_Z += WORKFLOWS\n_L = _Z",
    "r8b_relist_loop": "_Z = [*WORKFLOWS]\nfor _w in _Z:\n    _L = [_w]",
}
CASES.update(
    {
        p: case(p, GRAB.format(held=held), "unread", f"an item of a re-list reaches the list: {held}")
        for p, held in RELIST.items()
    }
)
# A re-list read as a value, or changed itself, leaves the list as it is.
CASES.update(
    {
        "r8b_relist_store": case(
            "r8b_relist_store",
            "_Z = [*WORKFLOWS]\n_Z.append(BFlow)\n_Z[0] = BFlow\n",
            "watched",
            "a store into a re-list",
        ),
        "r8b_relist_read": case(
            "r8b_relist_read",
            "_Z = [*WORKFLOWS]\n_N = len(_Z)\n_ALL = [*_Z]\nfor _w in _Z:\n    _n = _w.__name__\n",
            "watched",
            "a re-list read as a value",
        ),
    }
)

# Round 8b, ruling 2: a name assigned a name that holds builtins, then a patch through it.
PATCH = "\n\ndef _grab(w, *a):\n    w.append(BFlow)\n\n\n{held}\n_g.print = _grab\nprint(WORKFLOWS)\n"
ALIASED = {
    "r8b_alias_patch": "import builtins as _b\n\n_g = _b",
    "r8b_alias_chain": "import builtins as _b\n\n_h = _b\n_g = _h",
    "r8b_alias_in_function": "import builtins as _b\n\n\ndef _m():\n    global _g\n    _g = _b\n\n\n_m()",
    "r8b_alias_rebound": "import builtins as _b\nimport copy\n\n_g = _b\nif AFlow:\n    _g = copy",
}
CASES.update(
    {
        p: case(p, PATCH.format(held=held), "unread", f"a patch through a name assigned a module: {held}")
        for p, held in ALIASED.items()
    }
)
# A module imported by `import` and handed to a name is `*` already (rule 3); through a name a
# `from` import binds, the alias is resolved: a store into another module leaves `print` the
# builtin, and an alias the file binds again in a block fails closed.
CASES["r8b_alias_other_module"] = case(
    "r8b_alias_other_module",
    "from os import path as _c\n\n_g = _c\n_g.x = 1\nprint(WORKFLOWS)\n",
    "watched",
    "a store through a name assigned another module: print stays the builtin",
)
CASES["r8b_alias_rebound_from"] = case(
    "r8b_alias_rebound_from",
    "from os import path as _c\nfrom os import sep as _s\n\n_g = _c\nif AFlow:\n    _g = _s\n_g.x = 1\nprint(WORKFLOWS)\n",
    "unread",
    "a name assigned a module and bound again in a block: any module may be patched",
)
# Family 4 through a name assigned the module, from another file of the product.
CASES["r8b_alias_xmod_store"] = case(
    "r8b_alias_xmod_store",
    "print(WORKFLOWS)\n",
    "unread",
    "another file stores print into the facade through a name assigned it",
    files={
        "backend/ev2.py": (
            "import products.{p}.backend.facade.temporal as _f\n"
            "from products.{p}.backend.temporal.b import BFlow\n\n"
            "_g = _f\n\n\n"
            "def _grab(w, *a):\n    w.append(BFlow)\n\n\n"
            "_g.print = _grab\n"
        ),
    },
)
