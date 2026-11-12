import importlib

from django.apps import apps

from posthog.ingress.dispatch import route_payload

from products.inventory.backend.models import StockItem
from products.legacy.backend.models import Widget

WIDGET_LIMIT = 10


def widget_total(team_id: int) -> int:
    return Widget.objects.filter(team_id=team_id).count()


def widget_count(team_id: int) -> int:
    return widget_total(team_id)


def restock_all(team_id: int) -> None:
    for item in StockItem.objects.filter(team_id=team_id):
        item.save()


def describe(item: StockItem) -> str:
    return item.name


def is_missing(error: Exception) -> bool:
    return isinstance(error, StockItem.DoesNotExist)


def own_model():
    return apps.get_model("legacy", "Widget")


def other_model():
    return apps.get_model("inventory", "StockItem")


def any_model(label: str):
    return apps.get_model(label)


def load_plugin(name: str):
    return importlib.import_module(name)


def forward(payload: bytes) -> None:
    route_payload(payload)
