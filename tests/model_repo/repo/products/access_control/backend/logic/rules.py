from products.access_control.backend.models import AccessRule
from products.notifications.backend.facade.api import notify
from products.gatekeeper.backend.facade.api import may_enter


def allows(user_id: int, team_id: int) -> bool:
    notify(user_id, "checked")
    return AccessRule.objects.filter(team_id=team_id).exists() or may_enter(user_id, team_id)
