from pydantic import BaseModel

from products.inventory.backend.models import StockItem


class ExternalView(BaseModel):
    item: StockItem
