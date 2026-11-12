from products.catalog.backend.models import CatalogEntry


def entry_factory(team_id: int) -> CatalogEntry:
    return CatalogEntry.objects.create(team_id=team_id, title="entry")
