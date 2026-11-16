from products.inventory.backend.facade import api as inventory_api
from posthog.models.team.team import Team


def make_team() -> Team:
    inventory_api.list_stock(1)
    return Team()
