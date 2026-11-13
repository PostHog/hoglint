from posthog.dataclasses import frozen
from products.catalog.backend.logic.views_out import StockBase


@frozen
class EntryStockRecord(StockBase):
    entry_id: int
