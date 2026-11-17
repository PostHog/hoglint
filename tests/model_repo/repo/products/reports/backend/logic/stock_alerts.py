from products.inventory.backend.facade.api import send_restock_alert


def alert_low_stock(item_id: int) -> None:
    send_restock_alert(item_id)
