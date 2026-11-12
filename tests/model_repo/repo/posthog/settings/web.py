DEBUG = False

INSTALLED_APPS = [
    "posthog",
    "ee",
    "products.access_control.backend.apps.AccessControlConfig",
    "products.notifications.backend.apps.NotificationsConfig",
    "products.inventory.backend.apps.InventoryConfig",
    "products.catalog.backend.apps.CatalogConfig",
    "products.legacy.backend.apps.LegacyConfig",
    "products.reports.backend.apps.ReportsConfig",
    "products.gatekeeper.backend.apps.GatekeeperConfig",
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
