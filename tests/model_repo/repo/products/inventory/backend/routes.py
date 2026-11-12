from products.inventory.backend.presentation.views import StockViewSet


def register_routes(router):
    router["stock"] = StockViewSet
