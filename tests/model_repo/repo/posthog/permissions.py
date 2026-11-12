def can_view(user_id, team_id):
    from products.access_control.backend.facade.api import may_view

    return may_view(user_id, team_id)
