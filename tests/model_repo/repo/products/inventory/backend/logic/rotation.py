from posthog.models.stock_events import stock_rotated


def connect_stock_rotated(receiver) -> None:
    stock_rotated.connect(receiver)
