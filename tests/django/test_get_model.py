"""`get_model` label edges: only a call on Django's app registry names a model."""

from __future__ import annotations

import pytest

from core.support import SITE, assign_row, class_def, func_def, import_row
from django_facts import FactRepo

REGISTRY = {
    f"{SITE}django/apps/registry.py": {
        "defs": [class_def("Apps", [], 1)],
        "assigns": [assign_row("apps", {"call": "Apps"}, 2)],
    },
    f"{SITE}django/apps/__init__.py": {"imports": [import_row("django.apps.registry", "apps")]},
}
ORDER = [{"s": "shop"}, {"s": "Order"}]


def jobs(callee: str, scope: int) -> dict[str, dict]:
    """`shop/jobs.py` with one `get_model` call on `callee` in def `scope` (-1: module level)."""
    return {
        "shop/jobs.py": {
            "imports": [import_row("django.apps", "apps")],
            "defs": [
                func_def("forwards", 3, params=[["apps", "po"], ["schema_editor", "po"]]),
                func_def("visit", 6, params=[["visitor", "po"]]),
                func_def("nested", 8, scope=0),
            ],
            "calls": [[scope, -1, 10, 4, 0, callee, ORDER, []]],
        }
    }


# The `apps` object of `django.apps`, and the `apps` argument of a migration function (also from a
# function nested in it), are the registry; any other receiver of a call named `get_model` is not.
CASES = {
    "django.apps.apps at module level": ("apps.get_model", -1, True),
    "the apps argument of a migration function": ("apps.get_model", 0, True),
    "the apps argument, from a nested function": ("apps.get_model", 2, True),
    "a visitor's method": ("visitor.get_model", 1, False),
    "a module function called get_model": ("api.get_model", -1, False),
    "self": ("self.get_model", 1, False),
}


@pytest.mark.parametrize("case", CASES)
def test_only_a_call_on_the_app_registry_names_a_model(case: str) -> None:
    callee, scope, registry = CASES[case]
    graph = FactRepo(REGISTRY, jobs(callee, scope)).graph()
    labels = [edge for edge in graph.by_kind("model-label") if edge.site.path == "shop/jobs.py"]
    assert [(edge.surface, edge.resolution.value) for edge in labels] == (
        [("shop.Order", "resolved")] if registry else []
    )
