from posthog.products import load_product_modules
from products.inventory.backend.routes import register_routes as register_inventory_routes
from products.legacy.backend.api.widgets import WidgetViewSet

router = {}


def register_all():
    register_inventory_routes(router)
    router["widgets"] = WidgetViewSet
    for module in load_product_modules("routes"):
        module.register_routes(router)
