import importlib

PRODUCTS = ["access_control", "notifications", "inventory", "catalog", "legacy", "reports", "gatekeeper"]


def load_product_modules(module_name):
    found = []
    for product in PRODUCTS:
        try:
            found.append(importlib.import_module(f"products.{product}.backend.{module_name}"))
        except ImportError:
            continue
    return found
