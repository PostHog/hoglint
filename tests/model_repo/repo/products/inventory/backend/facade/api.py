from products.inventory.backend.facade.contracts import StockLevel, StockMove
from products.inventory.backend.logic import stock


def _to_level(item) -> StockLevel:
    return StockLevel(item_id=item.pk, name=item.name, quantity=item.quantity)


def get_stock(team_id: int, item_id: int) -> StockLevel:
    return _to_level(stock.get_item(team_id, item_id))


def list_stock(team_id: int) -> list[StockLevel]:
    return [_to_level(item) for item in stock.items(team_id)]


def apply_move(team_id: int, move: StockMove) -> None:
    stock.move_stock(team_id, move.item_id, move.amount)
