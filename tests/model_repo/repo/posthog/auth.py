from products.gatekeeper.backend.facade import api as gate


def authenticate(request):
    return gate.may_enter(request.user_id, request.team_id)
