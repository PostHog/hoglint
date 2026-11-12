from products.legacy.backend.models import Widget


def widget_route_name(widget: Widget) -> str:
    return widget.name
