from django.dispatch import receiver

from products.inventory.backend.facade.count_signals import stock_counted


@receiver(stock_counted)
def on_stock_counted(sender, **kwargs) -> None:
    return None
