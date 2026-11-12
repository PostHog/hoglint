from pydantic import BaseModel

from products.inventory.backend.models import StockItem


class SandboxView(BaseModel):
    item: StockItem
