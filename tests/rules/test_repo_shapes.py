"""The four repo shapes from the design: one engine, four ways to draw boundaries."""

from __future__ import annotations

import shutil
import datetime
from pathlib import Path

from hoglint.config import load_config
from hoglint.config.loader import PACKS_DIR
from hoglint.model import Context, Graph, Resolution
from hoglint.rules.engine import Engine, RunResult

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
        ("products.billing.backend.models.Invoice", "products.alerts.backend.logic", "write(save)"),
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
