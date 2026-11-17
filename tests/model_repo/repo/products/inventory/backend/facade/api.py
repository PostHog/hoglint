from products.inventory.backend.facade.contracts import StockLevel, StockMove
from products.inventory.backend.logic import restock_window as restock_window, stock
from products.inventory.backend.logic.audit_notes import StockAuditNote as StockAuditNote
from products.inventory.backend.logic.reorder_points import reorder_point as reorder_point
from products.inventory.backend.logic.restock_alerts import send_restock_alert as send_restock_alert
from products.inventory.backend.logic.restock_rules import restock_threshold as restock_threshold
from products.inventory.backend.logic.shelf_life import shelf_life_days as shelf_life_days
from products.inventory.backend.logic.stock_labels import stock_label as stock_label


def _to_level(item) -> StockLevel:
    return StockLevel(item_id=item.pk, name=item.name, quantity=item.quantity)


def get_stock(team_id: int, item_id: int) -> StockLevel:
    return _to_level(stock.get_item(team_id, item_id))


def list_stock(team_id: int) -> list[StockLevel]:
    return [_to_level(item) for item in stock.items(team_id)]


def apply_move(team_id: int, move: StockMove) -> None:
    stock.move_stock(team_id, move.item_id, move.amount)
