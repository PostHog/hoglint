from dataclasses import dataclass

from posthog.dataclasses import frozen


@frozen
class StockLevel:
    item_id: int
    name: str
    quantity: int


@dataclass(frozen=True)
class StockMove:
    item_id: int
    amount: int


class StockMissing(Exception):
    pass
