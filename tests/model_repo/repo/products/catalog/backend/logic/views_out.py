from pydantic import BaseModel

from products.inventory.backend.models import StockItem


class InternalView(BaseModel):
    item: StockItem
