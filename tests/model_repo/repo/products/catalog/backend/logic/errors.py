from typing import Any, Protocol

from pydantic import BaseModel

from posthog.dataclasses import frozen
from posthog.errors import CoreLookupError
from products.catalog.backend.logic.nowhere import MissingFailure
from products.catalog.backend.models import CatalogEntry
from products.inventory.backend.models import StockItem


@frozen
class EntryRef:
    entry_id: int


class EntryRejected(Exception):
    ref: EntryRef

    def item(self) -> StockItem:
        raise NotImplementedError

    def entry_ref(self) -> EntryRef:
        raise NotImplementedError


class EntryRow(BaseModel):
    title: str

    def stock(self) -> StockItem:
        raise NotImplementedError


class StockSink(Protocol):
    def push(self, item: StockItem) -> None: ...


class CatalogFailure(Exception):
    def entry(self) -> CatalogEntry:
        raise NotImplementedError


class EntryExpired(CatalogFailure):
    pass


class EntryFlagMissing(CoreLookupError):
    pass


class EntryBroken(MissingFailure):
    pass


class EntryPayloadRejected(Exception):
    def __init__(self, extra: dict[str, Any]) -> None:
        self.extra = extra


class EntryPayloadDetail(Exception):
    def detail(self) -> dict[str, Any]:
        return {}
