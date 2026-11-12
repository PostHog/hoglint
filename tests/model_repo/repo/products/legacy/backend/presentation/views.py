from django.db import transaction

from products.catalog.backend.presentation.views import EntryExportView, EntryViewSet
from products.inventory.backend.facade.api import get_stock
from products.inventory.backend.presentation.serializers import StockSerializer
from products.legacy.backend.helpers import widget_label
from products.legacy.backend.models import Widget


def widget_page(widget_id: int) -> str:
    return widget_label(widget_id)


def save_widget(name: str) -> None:
    with transaction.atomic():
        Widget.objects.create(name=name)


@transaction.atomic
def rename_widget(widget_id: int, name: str) -> None:
    Widget.objects.filter(pk=widget_id).update(name=name)


def stock_page(team_id: int):
    return StockSerializer(get_stock(team_id, 1)), EntryViewSet, EntryExportView
