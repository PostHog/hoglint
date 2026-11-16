from collections.abc import Callable


def connect_stock_saved(receiver: Callable[..., None]) -> None:
    from django.db.models.signals import post_save

    from products.inventory.backend.models import StockItem

    post_save.connect(receiver, sender=StockItem)
