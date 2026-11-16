from django.db.models.signals import post_save
from django.dispatch import receiver


def _sender():
    return None


# The sender is a call's result: hoglint cannot place it.
@receiver(post_save, sender=_sender())
def on_stock_saved(sender, instance, **kwargs):
    return None
