from django.db.models.signals import post_delete
from django.dispatch import receiver

from products.inventory.backend.models import StockItem


@receiver(post_delete, sender=StockItem)
def on_stock_deleted(sender, instance, **kwargs):
    return None
