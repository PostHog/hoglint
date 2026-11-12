from posthog.ingress.contracts import WebhookConsumer


class EntryWebhook(WebhookConsumer):
    def consume(self, payload: bytes) -> None:
        return None


WEBHOOK_CONSUMERS = [EntryWebhook]
