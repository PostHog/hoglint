from products.catalog.backend.logic import refusals
from products.inventory.backend.facade.api import get_stock


def stock_column(entry_id: int) -> int:
    from products.catalog.backend.logic import pricing

    return pricing.price_for(entry_id) + get_stock(1, entry_id).quantity + len(refusals.__name__)
