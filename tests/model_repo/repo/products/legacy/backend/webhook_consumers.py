class WidgetWebhook:
    def consume(self, payload: bytes) -> None:
        return None


WEBHOOK_CONSUMERS = [WidgetWebhook]
