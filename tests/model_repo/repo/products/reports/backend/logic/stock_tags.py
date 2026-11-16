from products.inventory.backend.facade.api import stock_label


def tag(item_id: int) -> str:
    return stock_label(item_id)
