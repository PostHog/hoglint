from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from products.inventory.backend.models import StockItem


def get_item(team_id: int, item_id: int) -> StockItem:
    return StockItem.objects.get(team_id=team_id, pk=item_id)


def items(team_id: int) -> list[StockItem]:
    return list(StockItem.objects.filter(team_id=team_id))


def move_stock(team_id: int, item_id: int, amount: int) -> None:
    with transaction.atomic():
        item = get_item(team_id, item_id)
        item.quantity += amount
        item.save()


def log_restock(item_id: int) -> None:
    return None


@receiver(post_save, sender=StockItem)
def on_stock_saved(sender, instance, **kwargs):
    return None
