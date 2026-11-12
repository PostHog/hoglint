from products.legacy.backend.models import Widget


def widget_names(team_id):
    return [widget.name for widget in Widget.objects.filter(team_id=team_id)]
