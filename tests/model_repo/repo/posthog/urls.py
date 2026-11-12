from django.urls import include, path

from products.legacy.backend.api.widgets import WidgetViewSet

urlpatterns = [
    path("api/widgets/", WidgetViewSet),
    path("api/inventory/", include("products.inventory.backend.routes")),
]
