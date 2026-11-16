from typing import TYPE_CHECKING

from products.gatekeeper.backend.facade.lazy_gate import lazy_decide
from products.inventory.backend.facade import temporal as inventory_temporal
from products.inventory.backend.facade.api import get_stock, restock_threshold, restock_window

if TYPE_CHECKING:
    from products.inventory.backend.facade.api import StockAuditNote


def low_stock(team_id: int, item_id: int) -> bool:
    return get_stock(team_id, item_id).quantity < restock_threshold(team_id) + restock_window(team_id)


def audit(note: "StockAuditNote") -> str:
    inventory_temporal.sweep_stock()
    return note.text if lazy_decide else ""
