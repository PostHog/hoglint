from dataclasses import dataclass

from products.catalog.backend.events import ENTRY_CREATED


@dataclass(frozen=True)
class WidgetEvent:
    name: str = ENTRY_CREATED
