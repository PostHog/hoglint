"""The four repo shapes from the design: one engine, four ways to draw boundaries."""

from __future__ import annotations

import shutil
import datetime
from pathlib import Path

import pytest

from hoglint.config import load_config
from hoglint.config.loader import PACKS_DIR
from hoglint.model import Context, Graph, Resolution
from hoglint.rules.engine import Engine, RunResult
from hoglint.source import GraphSource

from rules.support import GraphBuilder

SHAPES = Path(__file__).parent / "shapes"
PRESET = Path(__file__).resolve().parents[2] / "presets" / "posthog.toml"


def run_shape(tmp_path: Path, name: str, graph: Graph, files: dict[str, str] | None = None) -> RunResult:
    shutil.copy(SHAPES / f"{name}.toml", tmp_path / "hoglint.toml")
    preset = tmp_path / "tools" / "hoglint" / "presets" / "posthog.toml"
    preset.parent.mkdir(parents=True)
    shutil.copy(PRESET, preset)
    for path, content in (files or {}).items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(content)
    config = load_config(tmp_path, tmp_path / "hoglint.toml", PACKS_DIR)
    return Engine(config, graph, tmp_path, today=datetime.date(2026, 11, 9)).evaluate()


def found(result: RunResult, rule: str) -> list[tuple[str, ...]]:
    return sorted(f.identity for f in result.runs[rule].findings)


def test_posthog_preset_products_zones_exports_and_isolated(tmp_path: Path) -> None:
    graph = (
        GraphBuilder()
        .package("products.billing.backend.facade")
        .module(
            "products.billing.backend.facade.api",
            "products.billing.backend.logic",
            "products.billing.backend.models",
            "products.billing.backend.presentation.views",
            "products.alerts.backend.logic",
            "products.alerts.backend.presentation.views",
            "products.access_control.backend.logic",
            "posthog.api.thing",
            "posthog.egress.client",
            "packages.owners_yaml.resolver",
        )
        .symbol("products.billing.backend.models.Invoice", "model", abstract="false")
        .symbol("products.billing.backend.logic.charge", "def")
        .edge("import", "products.billing.backend.facade.api", "products.billing.backend.logic.charge")
        # alerts goes through billing's facade: fine. Its presentation reaching billing's logic is not.
        .edge("import", "products.alerts.backend.logic", "products.billing.backend.facade.api")
        .edge("import", "products.alerts.backend.presentation.views", "products.billing.backend.logic.charge")
        .edge(
            "reference",
            "products.alerts.backend.logic",
            "products.billing.backend.models.Invoice",
            shape="write",
            detail="save",
        )
        .edge("import", "products.access_control.backend.logic", "products.alerts.backend.logic")
        .edge("import", "posthog.egress.client", "products.billing.backend.facade.api")
        .edge("import", "packages.owners_yaml.resolver", "django.db", resolution=Resolution.EXTERNAL)
        .edge("import", "posthog.api.thing", "products.billing.backend.logic.charge", contexts=(Context.TEST,))
        .graph
    )
    result = run_shape(tmp_path, "posthog", graph)
    assert found(result, "public-surface") == [
        ("products.alerts.backend.logic", "products.access_control.backend.logic", "import"),
        ("products.billing.backend.logic.charge", "posthog.api.thing", "import"),
        ("products.billing.backend.logic.charge", "products.alerts.backend.presentation.views", "import"),
        ("products.billing.backend.models.Invoice", "products.alerts.backend.logic", "reference"),
    ]
    assert found(result, "presentation-uses-facade") == [
        ("products.alerts.backend.presentation.views", "products.billing.backend.logic")
    ]
    assert found(result, "model-crossings") == [
        ("products.billing.backend.models.Invoice", "products.alerts.backend.logic", "write(save)")
    ]
    assert found(result, "foundation-direction") == [("access_control", "alerts")]
    assert found(result, "infrastructure-direction") == [("egress", "billing")]
    assert found(result, "leaves-stay-leaves") == [("packages.owners_yaml.resolver", "django.db")]
    # new findings forbid Isolated for both ends until the ledger accepts the debt
    assert result.levels["isolated"].passing == []
    assert set(result.levels["isolated"].failing) == {"access_control", "alerts", "billing"}


def test_saleor_one_package_from_two_trees_and_a_cross_layer(tmp_path: Path) -> None:
    graph = (
        GraphBuilder()
        .module(
            "saleor.order.utils",
            "saleor.graphql.order.types",
            "saleor.graphql.core.scalars",
            "saleor.core.development.fake",
        )
        .module("saleor.checkout.views", "saleor.core.management.commands.populatedb")
        .edge("import", "saleor.order.utils", "saleor.graphql.order.types")
        .edge("import", "saleor.checkout.views", "saleor.core.development.fake")
        .edge("import", "saleor.core.management.commands.populatedb", "saleor.core.development.fake")
        .edge("import", "saleor.checkout.views", "saleor.order.utils", contexts=(Context.FUNCTION_LOCAL,))
        .graph
    )
    result = run_shape(tmp_path, "saleor", graph)
    labeler = result.labeler
    assert labeler.field_value("saleor.graphql.order.types", None, "package")[0].data == "order"
    assert found(result, "domain-not-graphql") == [("saleor.order.utils", "saleor.graphql.order.types")]
    assert found(result, "dev-only") == [("saleor.checkout.views", "saleor.core.development.fake")]
    assert found(result, "top-level-imports") == [("saleor.checkout.views", "saleor.order.utils")]


def test_zulip_layers_without_packages(tmp_path: Path) -> None:
    send = "zerver.tornado.django_api.send_event_on_commit"
    graph = (
        GraphBuilder()
        .module(
            "zerver.models.users",
            "zerver.lib.users",
            "zerver.actions.users",
            "zerver.views.users",
            "zerver.tornado.django_api",
        )
        .symbol(send, "def")
        .edge("import", "zerver.models.users", "zerver.lib.users")
        .edge("import", "zerver.views.users", "zerver.lib.users")
        .edge("reference", "zerver.lib.users", send, use="call", scope="notify")
        .edge("reference", "zerver.actions.users", send, use="call", scope="do_change")
        .graph
    )
    result = run_shape(tmp_path, "zulip", graph)
    assert found(result, "layer-order") == [("zerver.models.users", "zerver.lib.users")]
    assert found(result, "send-event-from-actions") == [(send, "zerver.lib.users", "notify", "reference")]


def test_sentry_silo_from_decorators_and_owners_from_codeowners(tmp_path: Path) -> None:
    graph = (
        GraphBuilder()
        .module("sentry.models.project", "sentry.users.models.user", "sentry.db.models.base")
        .symbol("sentry.models.project.Project")
        .symbol("sentry.users.models.user.User")
        .symbol("sentry.models.project.Mystery")
        .edge(
            "inherit",
            "sentry.models.project",
            "django.db.models.Model",
            resolution=Resolution.EXTERNAL,
            attrs=(("class", "Project"),),
        )
        .edge(
            "inherit",
            "sentry.users.models.user",
            "django.db.models.Model",
            resolution=Resolution.EXTERNAL,
            attrs=(("class", "User"),),
        )
        .edge(
            "inherit",
            "sentry.models.project",
            "django.db.models.Model",
            resolution=Resolution.EXTERNAL,
            attrs=(("class", "Mystery"),),
        )
        .edge(
            "decorate",
            "sentry.models.project",
            "sentry.db.models.base.cell_silo_model",
            attrs=(("decorated", "Project"),),
        )
        .edge(
            "decorate",
            "sentry.users.models.user",
            "sentry.db.models.base.control_silo_model",
            attrs=(("decorated", "User"),),
        )
        .edge("relation", "sentry.models.project", "sentry.users.models.user.User", scope="Project")
        .edge("relation", "sentry.models.project", "sentry.models.project.Project", scope="Mystery")
        .graph
    )
    for module in graph.modules:
        graph.modules[module] = "src/" + graph.modules[module]
    result = run_shape(tmp_path, "sentry", graph, {".github/CODEOWNERS": "/src/sentry/users/ @getsentry/accounts\n"})
    silo = found(result, "silo-relations")
    assert ("sentry.models.project.Project", "sentry.users.models.user.User") in silo
    mystery = [f for f in result.runs["silo-relations"].findings if f.identity[0] == "sentry.models.project.Mystery"]
    assert mystery and mystery[0].unknown  # a model with no silo label is unknown inside the silo domain
    assert result.labeler.field_value("sentry.users.models.user", None, "owners")[0].data == frozenset(
        {"@getsentry/accounts"}
    )


def test_posthog_preset_seals_relations_between_packages_but_not_onto_a_third_party_app(tmp_path: Path) -> None:
    accessor = (("declaring_model", "posthog.models.user.User"), ("field", "groups"), ("accessor", "user_set"))
    graph = (
        GraphBuilder()
        .module("posthog.models.user", "products.billing.backend.models")
        .symbol("products.billing.backend.models.Invoice", "model", abstract="false")
        .edge(
            "reverse-accessor",
            "posthog.models.user",
            "django.contrib.auth.models.Group",
            resolution=Resolution.EXTERNAL,
            attrs=accessor,
        )
        .edge("reverse-accessor", "posthog.models.user", "products.billing.backend.models.Invoice", attrs=accessor)
        .graph
    )
    result = run_shape(tmp_path, "posthog", graph)
    assert found(result, "reverse-accessor") == [
        ("products.billing.backend.models.Invoice", "posthog.models.user.User", "groups", "user_set")
    ]


def test_posthog_preset_gives_only_django_migration_files_the_migration_context(tmp_path: Path) -> None:
    run_shape(tmp_path, "posthog", Graph())
    config = load_config(tmp_path, tmp_path / "hoglint.toml", PACKS_DIR)
    globs = GraphSource.core_config(config).migration_globs
    migrations = {
        "posthog/migrations/0889_add_schema_models.py": True,
        "ee/migrations/0051_backfill.py": True,
        "posthog/session/migrations/0001_initial.py": True,
        "products/tasks/backend/migrations/0001_initial.py": True,
        "posthog/clickhouse/migrations/0001_initial.py": False,
        "posthog/async_migrations/migrations/0005_person.py": False,
        "posthog/rbac/migrations/rbac_team_migration.py": False,
        "posthog/models/team/team.py": False,
    }
    assert {path: globs.matches(path) for path in migrations} == migrations


# (case, edges as (kind, source module, target, contexts), public-surface identities that stay)
PUBLIC_SURFACE_CASES = [
    (
        "a later use of an imported name is judged at its import",
        [
            ("import", "posthog.api.thing", "products.billing.backend.logic.charge", (Context.PROD,)),
            ("reference", "posthog.api.thing", "products.billing.backend.logic.charge", (Context.PROD,)),
        ],
        [("products.billing.backend.logic.charge", "posthog.api.thing", "import")],
    ),
    (
        "a use through an imported module keeps its own row",
        [
            ("import", "posthog.api.thing", "products.billing.backend.logic", (Context.PROD,)),
            ("reference", "posthog.api.thing", "products.billing.backend.logic.charge", (Context.PROD,)),
        ],
        [
            ("products.billing.backend.logic", "posthog.api.thing", "import"),
            ("products.billing.backend.logic.charge", "posthog.api.thing", "reference"),
        ],
    ),
    (
        "a Django or ClickHouse migration is frozen history; other migration folders are not",
        [
            (
                "import",
                "posthog.migrations.0001_initial",
                "products.billing.backend.logic.charge",
                (Context.MIGRATION,),
            ),
            (
                "import",
                "posthog.clickhouse.migrations.0001_initial",
                "products.billing.backend.logic.charge",
                (Context.PROD,),
            ),
            (
                "import",
                "posthog.rbac.migrations.rbac_team_migration",
                "products.billing.backend.logic.charge",
                (Context.PROD,),
            ),
        ],
        [("products.billing.backend.logic.charge", "posthog.rbac.migrations.rbac_team_migration", "import")],
    ),
    (
        "Django settings may name a product path as a string, not import it",
        [
            ("dotted-path", "posthog.settings.web", "products.billing.backend.logic.charge", (Context.PROD,)),
            ("import", "posthog.settings.web", "products.billing.backend.logic.charge", (Context.PROD,)),
        ],
        [("products.billing.backend.logic.charge", "posthog.settings.web", "import")],
    ),
    (
        "a core registration point may name a wiring location, other core code may not",
        [
            ("import", "posthog.temporal.schedule", "products.billing.backend.temporal.workflows.Run", (Context.PROD,)),
            ("import", "posthog.api.thing", "products.billing.backend.temporal.workflows.Run", (Context.PROD,)),
            ("import", "posthog.temporal.schedule", "products.billing.backend.logic.charge", (Context.PROD,)),
        ],
        [
            ("products.billing.backend.logic.charge", "posthog.temporal.schedule", "import"),
            ("products.billing.backend.temporal.workflows.Run", "posthog.api.thing", "import"),
        ],
    ),
    (
        "core production code may name a permanent interface; a test or a product may not",
        [
            ("import", "posthog.clickhouse.schema", "products.error_tracking.backend.sql.TABLE", (Context.PROD,)),
            ("import", "posthog.test.base", "products.error_tracking.backend.sql.TABLE", (Context.TEST,)),
            ("import", "products.billing.backend.logic", "products.error_tracking.backend.sql.TABLE", (Context.PROD,)),
        ],
        [
            ("products.error_tracking.backend.sql.TABLE", "posthog.test.base", "import"),
            ("products.error_tracking.backend.sql.TABLE", "products.billing.backend.logic", "import"),
        ],
    ),
    (
        "a test's patch target is left to wiring-watched; a test's import stays",
        [
            ("dotted-path", "posthog.api.test.test_thing", "products.billing.backend.logic.charge", (Context.TEST,)),
            ("import", "posthog.api.test.test_thing", "products.billing.backend.logic.charge", (Context.TEST,)),
            ("dotted-path", "posthog.api.thing", "products.billing.backend.logic.charge", (Context.PROD,)),
        ],
        [
            ("products.billing.backend.logic.charge", "posthog.api.test.test_thing", "import"),
            ("products.billing.backend.logic.charge", "posthog.api.thing", "dotted-path"),
        ],
    ),
]


@pytest.mark.parametrize(("case", "edges", "expected"), PUBLIC_SURFACE_CASES, ids=[c[0] for c in PUBLIC_SURFACE_CASES])
def test_posthog_preset_public_surface(
    tmp_path: Path,
    case: str,
    edges: list[tuple[str, str, str, tuple[Context, ...]]],
    expected: list[tuple[str, ...]],
) -> None:
    builder = GraphBuilder().symbol("products.billing.backend.logic.charge", "def")
    for kind, src, target, contexts in edges:
        builder.module(src, target.rpartition(".")[0]).edge(kind, src, target, contexts=contexts)
    assert found(run_shape(tmp_path, "posthog", builder.graph), "public-surface") == sorted(expected)
