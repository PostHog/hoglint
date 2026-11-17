from django.db import transaction

from posthog.models.scoping import unscoped
from products.legacy.backend.logic.widgets import load_widget, widget_total
from products.legacy.backend.models import Widget


def create_widget(team_id: int, name: str) -> int:
    widget = Widget.objects.create(team_id=team_id, name=name)
    return widget.pk


def rename_all_widgets(team_id: int, name: str) -> None:
    widgets = Widget.objects.filter(team_id=team_id)
    widgets.update(name=name)


def rename_widget(team_id: int, widget_id: int, name: str) -> None:
    widget = Widget.objects.get(team_id=team_id, pk=widget_id)
    widget.name = name
    widget.save()


def add_widget(team_id: int, name: str) -> int:
    widget = Widget(team_id=team_id, name=name)
    widget.save()
    return widget.pk


def locked_widget_name(team_id: int, widget_id: int) -> str:
    widget = Widget.objects.select_for_update().get(team_id=team_id, pk=widget_id)
    return widget.name


def count_in_transaction(team_id: int) -> int:
    with transaction.atomic():
        return widget_total(team_id)


@transaction.atomic
def count_atomically(team_id: int) -> int:
    return widget_total(team_id)


def widget_ids_named(name: str) -> list[int]:
    return list(Widget.objects.unscoped().filter(name=name).values_list("id", flat=True))


def count_every_widget() -> int:
    return Widget.all_teams.count()


def count_outside_scope(team_id: int) -> int:
    with unscoped():
        return widget_total(team_id)


def widget_name(team_id: int, widget_id: int) -> str:
    widget = Widget.objects.get(team_id=team_id, pk=widget_id)
    return widget.name


def count_widgets(team_id: int) -> int:
    return Widget.objects.filter(team_id=team_id).count()


def widget_ids(team_id: int, names: list[str]) -> list[int]:
    return list(Widget.objects.filter(team_id=team_id, name__in=names).values_list("id", flat=True))


def widget_names(team_id: int) -> list[str]:
    widgets = Widget.objects.filter(team_id=team_id).order_by("name")
    return [widget.name for widget in widgets]


def require_free_name(team_id: int, name: str) -> None:
    if Widget.objects.filter(team_id=team_id, name=name).exists():
        raise ValueError(name)


def has_live_widgets(team_id: int) -> bool:
    return Widget.objects.filter(team_id=team_id, name__startswith="live-").exists()


def touch_widget(team_id: int, widget_id: int) -> None:
    widget = load_widget(team_id, widget_id)
    widget.save()


def touch_widgets(team_id: int) -> None:
    for widget in Widget.objects.filter(team_id=team_id):
        widget.save()


def widget_ids_anywhere(name: str) -> list[int]:
    return list(Widget.objects.filter(name=name).unscoped().values_list("id", flat=True))
