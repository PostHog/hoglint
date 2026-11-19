"""Round 8's own cases (CASES.md `WA19`), as data for `test_watched_shapes.py`, next to the
adversary's rows (`shapes_round8.py`): a registry module that does not bind the registry's
attribute but defines a module `__getattr__` (the batch_exports form of the Temporal sweep) leaves
the declaration unread, for that reason, never empty; and the item provenance the build closed
past the adversary's rows: an item of the list in a display or a comprehension's element, an
element of a copy bound to a name, and `max`'s result each reach the list through
`gc.get_referrers` (each adds BFlow in plain Python), so each is unread."""

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
