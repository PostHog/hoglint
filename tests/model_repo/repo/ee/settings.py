from posthog.settings import MIDDLEWARE

MIDDLEWARE = MIDDLEWARE.copy()
MIDDLEWARE.insert(1, "products.gatekeeper.backend.admin_gate.AdminGateMiddleware")
