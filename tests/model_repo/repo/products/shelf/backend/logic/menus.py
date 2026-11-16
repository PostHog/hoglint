from collections.abc import Callable
from dataclasses import dataclass


def _restock_small(shelf_id: int) -> None:
    return None


def _restock_large(shelf_id: int) -> None:
    return None


@dataclass(frozen=True)
class ShelfFamily:
    prefix: str
    register_restock_hook: Callable[[int], None]


FAMILIES: dict[str, ShelfFamily] = {
    "small": ShelfFamily(prefix="s-", register_restock_hook=_restock_small),
    "large": ShelfFamily(prefix="l-", register_restock_hook=_restock_large),
}


class ShelfMenu:
    def __init__(self, shelf_id: int, kind: str) -> None:
        self.shelf_id = shelf_id
        self.family = FAMILIES[kind]

    def restock(self) -> None:
        self.family.register_restock_hook(self.shelf_id)
