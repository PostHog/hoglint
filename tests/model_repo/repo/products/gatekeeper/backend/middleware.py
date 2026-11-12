from products.gatekeeper.backend.logic.session import touch_session


class GateSessionMiddleware:
    def __call__(self, request):
        return touch_session(request.session_id)
