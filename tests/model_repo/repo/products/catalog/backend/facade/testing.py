from rest_framework.exceptions import PermissionDenied

from products.catalog.backend.models import CatalogEntry
from products.catalog.backend.tests.factories import entry_factory


def make_entry(team_id: int) -> CatalogEntry:
    if team_id < 0:
        raise PermissionDenied()
    return entry_factory(team_id)
