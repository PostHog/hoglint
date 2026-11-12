from products.catalog.backend.models import CatalogEntry


def price_for(entry_id: int) -> int:
    return CatalogEntry.objects.filter(pk=entry_id).count()


def entry(entry_id: int) -> CatalogEntry:
    return CatalogEntry.objects.get(pk=entry_id)


def entries(team_id: int):
    return CatalogEntry.objects.filter(team_id=team_id)


def manager():
    return CatalogEntry.objects


def owner(entry_id: int, kind: str):
    return getattr(entry(entry_id), kind.lower())
