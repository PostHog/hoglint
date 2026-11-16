from dataclasses import dataclass

from posthog.dataclasses import frozen
from products.inventory.backend.facade.enums import StockState
from products.inventory.backend.logic.notes import StockLevelNote


@frozen
class StockLevel:
    item_id: int
    name: str
    quantity: int


@dataclass(frozen=True)
class StockMove:
    item_id: int
    amount: int


@frozen
class StockReport:
    item_id: int
    state: StockState


class StockMissing(Exception):
    pass


def _note(text: str) -> StockLevelNote:
    return StockLevelNote(text)
