from products.catalog.backend.models import CatalogEntry
from products.inventory.backend.models import StockItem
from products.legacy.backend.models import Widget

VISITED = {"entry": CatalogEntry, "stock": StockItem, "widget": Widget}
