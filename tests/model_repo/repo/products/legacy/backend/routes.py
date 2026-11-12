from products.legacy.backend.api.widgets import WidgetViewSet


def register_routes(router):
    router["widgets"] = WidgetViewSet
