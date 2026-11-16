from products.inventory.backend.facade.stock_signals import connect_stock_saved


def on_own_stock_saved(sender, instance, **kwargs) -> None:
    return None


connect_stock_saved(on_own_stock_saved)
