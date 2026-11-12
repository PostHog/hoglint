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

MIDDLEWARE = [
    "posthog.middleware.TeamMiddleware",
    "products.gatekeeper.backend.middleware.GateSessionMiddleware",
]

LOGGING = {"loggers": {"products.reports.backend.logic.build": {"level": "INFO"}}}
