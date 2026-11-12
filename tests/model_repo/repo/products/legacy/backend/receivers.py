from django.db.models.signals import post_save
from django.dispatch import receiver

from products.inventory.backend.models import StockItem


@receiver(post_save, sender=StockItem)
def on_stock_saved(sender, instance, **kwargs):
    return None
