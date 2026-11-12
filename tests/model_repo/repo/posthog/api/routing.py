from posthog.auth import authenticate


class TeamAndOrgViewSetMixin:
    def initial(self, request):
        return authenticate(request)
