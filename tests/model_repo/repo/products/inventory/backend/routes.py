from products.inventory.backend.presentation.views import StockViewSet
from products.inventory.backend.route_helpers import stock_route_name

__all__ = ["register_routes", "stock_route_name"]


def register_routes(router):
    router["stock"] = StockViewSet
