from products.inventory.backend.logic.stock import move_stock
from products.shelf.backend.facade.api import shelf_label


def shelf_page(shelf_id: int) -> str:
    return shelf_label(shelf_id).text


def restock_shelf(team_id: int, item_id: int) -> None:
    move_stock(team_id, item_id, 1)
