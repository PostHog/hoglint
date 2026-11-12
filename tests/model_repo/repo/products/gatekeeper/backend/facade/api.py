from products.gatekeeper.backend.logic import decisions


def may_enter(user_id: int, team_id: int) -> bool:
    return decisions.decide(user_id, team_id)
