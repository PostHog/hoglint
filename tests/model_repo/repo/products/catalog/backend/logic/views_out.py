from pydantic import BaseModel

from posthog.dataclasses import frozen

from products.catalog.backend.models import CatalogEntry
from products.inventory.backend.models import StockItem


class InternalView(BaseModel):
    item: StockItem


@frozen
class StockBase:
    item: StockItem


def _lookup(entry: CatalogEntry) -> None:
    pass
