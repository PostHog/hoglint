from products.inventory.backend.logic.stock import move_stock


def restock(team_id: int, item_id: int) -> None:
    move_stock(team_id, item_id, 10)
