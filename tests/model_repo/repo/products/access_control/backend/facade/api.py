from products.access_control.backend.logic import rules


def may_view(user_id: int, team_id: int) -> bool:
    return rules.allows(user_id, team_id)
