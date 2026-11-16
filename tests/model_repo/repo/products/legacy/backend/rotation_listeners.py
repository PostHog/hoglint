from django.core.signals import request_started
from django.dispatch import receiver

from posthog.models.stock_events import stock_rotated

from products.legacy.backend.signals import widget_cleared


@receiver(stock_rotated)
def on_stock_rotated(sender, **kwargs) -> None:
    return None


@receiver(widget_cleared)
def on_widget_cleared(sender, **kwargs) -> None:
    return None


@receiver(request_started)
def on_request_started(sender, **kwargs) -> None:
    return None
