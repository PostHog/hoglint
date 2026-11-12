from abc import ABC, abstractmethod


class WebhookConsumer(ABC):
    @abstractmethod
    def consume(self, payload: bytes) -> None: ...
