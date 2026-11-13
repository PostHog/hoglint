from products.catalog.backend import models
from products.catalog.backend.logic import handouts

Entry = models.CatalogEntry
built = handouts.make_box()


def ping() -> None:
    pass


__all__ = ["ping"]
