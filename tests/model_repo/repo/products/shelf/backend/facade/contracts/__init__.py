from posthog.dataclasses import frozen


@frozen
class ShelfLabel:
    shelf_id: int
    text: str
