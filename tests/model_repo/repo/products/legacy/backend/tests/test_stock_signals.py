from django.db.models.signals import post_delete
from django.dispatch import receiver

from products.inventory.backend.models import StockItem

seen = []


@receiver(post_delete, sender=StockItem)
def record_deleted_stock(sender, instance, **kwargs):
    seen.append(instance)


def test_deleting_stock_is_seen():
    assert seen == []
