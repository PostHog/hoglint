from posthog.permissions import can_view


class TeamMiddleware:
    def __call__(self, request):
        return can_view(request.user_id, request.team_id)
