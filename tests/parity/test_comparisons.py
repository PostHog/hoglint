"""The comparisons that read a hoglint check, on hand-written facts: each old item is matched to a
finding, or explained by a reason its check confirms, or left unexplained."""

from __future__ import annotations

from typing import Any

import pytest

from hoglint.presets_py.posthog.parity import crossings, import_linter, isolated, migrations, tach_check, watched
from hoglint.presets_py.posthog.parity.hoglint_check import FindingRow, HoglintFacts, MigrationEdge, Wiring, Witness
from hoglint.presets_py.posthog.parity.verdict import Comparison, Reason, Verdict

CORE = "posthog/api/views.py"
SHOP_LOGIC = "products/shop/backend/logic.py"


def facts(**fields: Any) -> HoglintFacts:
    modules = {
        CORE: "posthog.api.views",
        SHOP_LOGIC: "products.shop.backend.logic",
        "products/shop/backend/apps.py": "products.shop.backend.apps",
        "products/shop/backend/facade/api.py": "products.shop.backend.facade.api",
        "products/shop/backend/presentation/views.py": "products.shop.backend.presentation.views",
        "products/cart/backend/logic.py": "products.cart.backend.logic",
        "posthog/temporal/shop/run.py": "posthog.temporal.shop.run",
    }
    packages = {
        module: "core" if module.startswith("posthog.") else module.split(".")[1] for module in modules.values()
    }
    packages["posthog.temporal.shop.run"] = "shop"
    return HoglintFacts(module_of_path=modules, package_of_module=packages, **fields)


def reasons(comparison: Comparison) -> dict[str, Reason | None]:
    return {d.item: d.reason for d in comparison.differences}


def test_a_tach_line_is_covered_by_a_finding_or_by_a_checked_reason() -> None:
    cycle = FindingRow("no-new-package-cycles", ("cart", "shop"))
    surface = FindingRow("public-surface", ("x",), (Witness(CORE, 4, "products.shop.backend.logic.charge"),))
    placed = FindingRow("product-code-at-home", ("shop", "p"), subjects=("posthog.temporal.shop.run",))
    runs = {
        "exposed-dependencies": [
            {
                "path": "products/cart/backend/logic.py",
                "line": 1,
                "message": "Cannot use 'products.shop.backend.logic.charge'. Module 'products.cart' cannot depend on 'products.shop'.",
            },
            {
                "path": CORE,
                "line": 2,
                "message": "Cannot use 'products.shop.backend.logic.charge'. Module 'posthog' cannot depend on 'products.shop'.",
            },
        ],
        "exposed-interfaces": [
            {
                "path": CORE,
                "line": 4,
                "message": "The path 'products.shop.backend.logic.charge' is not part of the public interface for 'products.shop'.",
            },
            {
                "path": "posthog/temporal/shop/run.py",
                "line": 3,
                "message": "The path 'products.shop.backend.logic.charge' is not part of the public interface for 'products.shop'.",
            },
            {
                "path": CORE,
                "line": 9,
                "message": "The path 'products.shop.backend.logic.refund' is not part of the public interface for 'products.shop'.",
            },
        ],
    }

    comparison = tach_check.compare(runs, facts(findings=[cycle, surface, placed]))

    assert [m.evidence for m in comparison.matches] == [cycle.text(), surface.text()]
    assert list(reasons(comparison).values()) == [tach_check.DEPENDS_ON_DROPPED, tach_check.INSIDE_ONE_PACKAGE, None]


def violation(importer: str, imported: str, line: int, hidden_by: str | None) -> dict:
    return {"chain": [{"importer": importer, "imported": imported, "line_numbers": [line]}], "hidden_by": [hidden_by]}


def test_an_import_linter_violation_matches_a_finding_on_any_line_of_its_statement() -> None:
    presentation = "products.shop.backend.presentation.views"
    finding = FindingRow(
        "presentation-uses-facade",
        (presentation, "products.shop.backend.logic"),
        (Witness("products/shop/backend/presentation/views.py", 3, "products.shop.backend.logic.charge"),),
    )
    source = "import os\nfrom products.shop.backend.logic import (\n    charge,\n)\n"
    contracts = [
        {
            "name": "presentation must use facade",
            "kept_as_configured": True,
            "ignore_imports": ["products.**.presentation.** -> products.**.facade.**", "x -> y"],
            "violations": [
                violation(presentation, "products.shop.backend.logic", 2, "x -> y"),
                violation(
                    presentation,
                    "products.cart.backend.facade.api",
                    1,
                    "products.**.presentation.** -> products.**.facade.**",
                ),
                violation(presentation, "products.cart.backend.logic", 1, "x -> y"),
            ],
        },
        {
            "name": "webhook consumers must only import facade",
            "kept_as_configured": True,
            "ignore_imports": ["a -> b"],
            "violations": [
                violation("products.shop.backend.webhook_consumers", "products.shop.backend.logic", 1, "a -> b")
            ],
        },
    ]

    comparison = import_linter.compare(contracts, facts(findings=[finding]), lambda path: source)

    assert len(comparison.matches) == 1
    assert list(reasons(comparison).values()) == [import_linter.ALLOWED_BY_PATTERN, None, import_linter.NO_RULE]
    assert any("webhook consumers" in line for line in comparison.not_compared)


def test_a_crossings_line_only_one_side_has_needs_the_use_hoglint_reads_there() -> None:
    module = "posthog.tasks.report"
    baseline = [
        f"shop.Order {module} instance-many(annotate) 1",
        f"shop.Order {module} other(keyword) 1",
        f"shop.Order {module} prefetch 1",
        f"shop.Cart {module} instance-single 1",
        "shop.Order posthog.gone instance-single 1",
    ]
    derived = [f"shop.Order {module} sender 1"]
    uses = {
        (module, "Order"): [
            crossings.Use(4, "scalar-chain", "values", ".objects.filter().values().annotate()"),
            crossings.Use(9, "prefetch"),
        ],
        (module, "Cart"): [crossings.Use(7, "instance-single", "", ".objects.get()")],
    }

    comparison = crossings.compare(baseline, baseline[:-1], derived, uses)

    assert reasons(comparison) == {
        f"shop.Order {module} instance-many(annotate) 1": crossings.VALUE_ROWS,
        f"shop.Order {module} other(keyword) 1": crossings.SENDER,
        f"shop.Order {module} prefetch 1": crossings.PREFETCH,
        f"shop.Cart {module} instance-single 1": None,
        "shop.Order posthog.gone instance-single 1": crossings.STALE,
        f"shop.Order {module} sender 1": crossings.SENDER,
    }


def status(**changes: object) -> dict:
    found = {
        "rung": "Isolated",
        "isolation_enabled": True,
        "skip_withheld_reason": None,
        "has_facade_contracts": True,
        "has_real_facade": True,
        "has_tach_interface": True,
        "has_legacy_leaks": False,
        "bypass_entries": [],
        "has_contract_check_script": True,
        "has_narrowed_turbo": True,
    }
    return found | changes


@pytest.mark.parametrize(
    "skips, passing, blocks, structure, reach, expected",
    [
        (True, True, [], {}, {}, None),
        (True, False, ["facade-signature: unknown: Any"], {}, {}, isolated.PENDING_SAFETY),
        (True, False, ["facade-signature: unknown: Any"], {}, {"shop": [SHOP_LOGIC]}, isolated.SAFETY_BLOCKS),
        (True, False, ["wiring-watched: strict, so the ledger does not excuse it: x"], {}, {}, isolated.WIRING_STAYS),
        (True, False, ["model-crossings: new finding: x"], {}, {}, None),
        (False, True, [], {"has_contract_check_script": False}, {}, isolated.PENDING_STRUCTURE),
        (False, True, [], {}, {}, None),
    ],
)
def test_a_product_whose_skip_differs_says_whether_only_a_pending_setting_explains_it(
    skips: bool, passing: bool, blocks: list[str], structure: dict, reach: dict, expected: Reason | None
) -> None:
    hoglint = facts(
        level_passing=["shop"] if passing else [],
        level_failing={} if passing else {"shop": blocks},
        core_reach_unwatched=reach,
    )
    comparison = isolated.compare(["shop"] if skips else [], {"shop": status(**structure)}, set(), hoglint)
    if skips == passing:
        assert comparison.verdict is Verdict.EQUAL
        return
    [difference] = comparison.differences
    assert difference.reason == expected


def test_a_watched_location_is_matched_inside_or_explained_by_its_kind_or_channel() -> None:
    requirements = {
        "shop": {
            "inputs": [],
            "required": [
                {"location": "backend/tasks/", "kind": "wiring-location"},
                {"location": "backend/models.py", "kind": "model-surface"},
                {"location": "backend/apps.py", "kind": "channel:settings-path", "source": "posthog/settings/web.py"},
                {"location": "backend/enums.py", "kind": "channel:settings-path", "source": "posthog/settings/web.py"},
                {"location": "backend/guard.py", "kind": "channel:settings-path", "source": "posthog/settings/web.py"},
                {
                    "location": "backend/callbacks.py",
                    "kind": "channel:hook-callback",
                    "source": "posthog.hooks.register",
                },
                {
                    "location": "backend/console.py",
                    "kind": "channel:hook-callback",
                    "source": "posthog.unlisted.register",
                },
                {"location": "backend/patched.py", "kind": "channel:outside-test", "source": "posthog/test/test_x.py"},
            ],
        }
    }
    hoglint = facts(
        watched={
            "products/shop/backend/tasks/run.py": ("rules.drives.dst.path", True),
            "products/shop/backend/apps.py": ("edge.kind in ['signal', 'installs'] and x: src", True),
            "products/shop/backend/logic.py": ("rules.request-path.dst.path", False),
        },
        settings_names={
            "products.shop.backend.enums": [("posthog/settings/web.py", "SPECTACULAR_SETTINGS.ENUMS")],
            # A key the request path follows: hoglint should have reached the module, so no reason fits.
            "products.shop.backend.guard": [("posthog/settings/web.py", "MIDDLEWARE")],
        },
        unfollowed_settings=(("in", ("INSTALLED_APPS",)), ("startswith", ("SPECTACULAR_SETTINGS.",))),
        installs=[Wiring("products/shop/backend/apps.py", "posthog.hooks.register")],
        hooks=frozenset({"posthog.hooks.register"}),
    )
    test_source = 'PATCH = "products.shop.backend.patched.boto3.client"\n'

    comparison = watched.compare(requirements, hoglint, lambda path: test_source)

    assert [m.item for m in comparison.matches] == [
        "shop: backend/tasks/ (wiring-location)",
        "shop: backend/apps.py (channel:settings-path)",
    ]
    assert list(reasons(comparison).values()) == [
        watched.LINT_ONLY["model-surface"],
        watched.SETTINGS_KEY,
        None,
        watched.ENTRY_MODULE,
        watched.HOOK_NOT_LISTED,
        watched.UNRESOLVED_PATCH,
        watched.HOGLINT_ONLY_REASONS["rules.request-path.dst.path"],
    ]


def test_a_migration_edge_hoglint_lacks_is_explained_only_for_an_app_it_does_not_read() -> None:
    old = {
        "crossings": ["shop.0002_x -> posthog.0001_initial"],
        "edges": [
            ["shop.0002_x", "posthog.0001_initial"],
            ["shop.0002_x", "shop.0001_initial"],
            ["vendor.0002_y", "vendor.0001_initial"],
        ],
        "aliases": {"shop": ["shop_db_writer"], "posthog": ["default"]},
        "app_modules": {"vendor": "vendor_app", "shop": "products.shop"},
    }
    hoglint = facts(
        findings=[FindingRow("migration-database", ("shop.0002_x", "posthog.0001_initial"))],
        migration_edges=[MigrationEdge("shop.0002_x", "posthog.0001_initial", ("shop_db_writer",), ("default",))],
    )

    comparison = migrations.compare(old, hoglint)

    assert reasons(comparison) == {
        "edge shop.0002_x -> shop.0001_initial": None,
        "edge vendor.0002_y -> vendor.0001_initial": migrations.UNREAD_PACKAGE,
    }
    assert [m.item for m in comparison.matches] == ["crossing shop.0002_x -> posthog.0001_initial"]
