class WidgetWebhook:
    def consume(self, payload: bytes) -> None:
        return None


WEBHOOK_CONSUMERS = [WidgetWebhook]


from products.legacy.backend.logic.widgets import WIDGET_LIMIT  # noqa: E402
from products.inventory.backend.facade.api import list_stock  # noqa: E402
