from products.legacy.backend.models import Widget


def make_widget(team_id: int) -> Widget:
    return Widget.objects.create(team_id=team_id, name="widget")
