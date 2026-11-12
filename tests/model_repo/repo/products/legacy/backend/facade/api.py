from django.http import Http404
from rest_framework.exceptions import PermissionDenied

from products.legacy.backend.presentation.views import widget_page


def widget_label(widget_id: int) -> str:
    return f"widget {widget_id}"


def require_widget(widget_id: int) -> str:
    if widget_id < 0:
        raise Http404()
    if widget_id == 0:
        raise PermissionDenied()
    return widget_page(widget_id)
