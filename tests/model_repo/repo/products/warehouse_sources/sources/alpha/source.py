from products.inventory.backend.facade import api as inventory_api
from posthog.models.team.team import Team
from products.warehouse_sources.sources.sdk.base import source_team
from products.warehouse_sources.sources.sdk.catalog import known_tables
from products.warehouse_sources.sources.beta.registry import BETA_TABLES


def alpha_tables(team: Team) -> list[str]:
    inventory_api.list_stock(team.pk)
    source_team(team.pk)
    return [*known_tables(), *BETA_TABLES]
