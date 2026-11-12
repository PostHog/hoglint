from products.legacy.backend.logic import widgets as widget_logic
from products.legacy.backend.logic.widgets import widget_total


def report(team_id):
    first = widget_total(team_id)
    second = widget_total(team_id + 1)
    third = widget_total(team_id + 2)
    return first + second + third + widget_logic.widget_count(team_id) + widget_logic.WIDGET_LIMIT
