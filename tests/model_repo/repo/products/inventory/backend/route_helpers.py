from products.inventory.backend.models import StockItem


def stock_route_name(item: StockItem) -> str:
    return item.name
