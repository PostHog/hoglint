from products.legacy.backend.models import Widget


def load_widget(team_id: int, widget_id: int) -> Widget:
    return Widget.objects.get(team_id=team_id, pk=widget_id)
