DEBUG = False

INSTALLED_APPS = [
    "django.contrib.admin.apps.SimpleAdminConfig",
    "posthog",
    "ee",
    "products.access_control.backend.apps.AccessControlConfig",
    "products.notifications.backend.apps.NotificationsConfig",
    "products.inventory.backend.apps.InventoryConfig",
    "products.catalog.backend.apps.CatalogConfig",
    "products.legacy.backend.apps.LegacyConfig",
    "products.reports.backend.apps.ReportsConfig",
    "products.gatekeeper.backend.apps.GatekeeperConfig",
    "products.depot.backend.apps.DepotConfig",
]

_DEBUG_GATES = ["products.gatekeeper.backend.debug_gate.DebugGateMiddleware"] if DEBUG else []

MIDDLEWARE = [
    "posthog.middleware.TeamMiddleware",
    "products.gatekeeper.backend.middleware.GateSessionMiddleware",
    *_DEBUG_GATES,
]

REST_FRAMEWORK = {"DEFAULT_THROTTLE_CLASSES": []}
REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"].append("products.gatekeeper.backend.throttles.GateThrottle")

LOGGING = {"loggers": {"products.reports.backend.logic.build": {"level": "INFO"}}}

# A list built from another name: its entries belong to the setting that takes it in.
_GATE_BACKENDS = ["products.gatekeeper.backend.session_gate.SessionGateBackend"]
AUTHENTICATION_BACKENDS = list(_GATE_BACKENDS)


# A list a function returns: the path strings are followed whatever function holds them.
def _token_checks():
    return ["products.gatekeeper.backend.token_check.TokenCheck"]


TOKEN_CHECKS = _token_checks()

# A key the exclude list does not name is followed.
OAUTH2_PROVIDER = {"OAUTH2_VALIDATOR_CLASS": "products.gatekeeper.backend.validator.GateValidator"}

# A key on the exclude list is not followed: a login flow behind one URL.
SOCIAL_AUTH_PIPELINE = ["products.gatekeeper.backend.login_flow.check_login"]

# A third-party app that ships its own migrations.
INSTALLED_APPS += ["tokens"]
