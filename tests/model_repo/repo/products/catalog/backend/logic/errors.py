from typing import Protocol

from pydantic import BaseModel

from products.inventory.backend.models import StockItem


class EntryRejected(Exception):
    def item(self) -> StockItem:
        raise NotImplementedError


class EntryRow(BaseModel):
    title: str

    def stock(self) -> StockItem:
        raise NotImplementedError


class StockSink(Protocol):
    def push(self, item: StockItem) -> None: ...
