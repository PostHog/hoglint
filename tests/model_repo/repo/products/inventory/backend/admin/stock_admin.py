from products.inventory.backend.logic.notes import StockLevelNote
from products.inventory.backend.models import StockItem


class StockItemAdmin:
    model = StockItem
    note = StockLevelNote("admin")
