from pydantic import BaseModel

from products.inventory.backend.models import StockItem


class ToolView(BaseModel):
    item: StockItem
