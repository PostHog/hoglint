from posthog.permissions import can_view
from posthog.request_log import record


class TeamMiddleware:
    def __call__(self, request):
        record(request)
        return can_view(request.user_id, request.team_id)
