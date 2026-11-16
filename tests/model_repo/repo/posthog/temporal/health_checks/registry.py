import importlib

# Each product's health check module, imported by name at worker start.
HEALTH_CHECK_MODULES = ["products.legacy.backend.temporal.jobs"]


def load_health_checks() -> None:
    for module_path in HEALTH_CHECK_MODULES:
        importlib.import_module(module_path)
