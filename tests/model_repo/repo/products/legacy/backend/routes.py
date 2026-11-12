from products.legacy.backend.api.widgets import WidgetViewSet
from products.legacy.backend.route_helpers import widget_route_name


def register_routes(router):
    router["widgets"] = WidgetViewSet
    router.name_with(widget_route_name)
