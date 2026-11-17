"""Where PostHog keeps a product's code: `products/<name>/backend/...`. One home for the path and
module helpers the PostHog producers and the parity adapter share."""

from __future__ import annotations

PRODUCTS = "products"
CORE = "core"


def product_of_path(path: str) -> str | None:
    """The product of a repo path under `products/<name>/`, else None."""
    parts = path.split("/")
    return parts[1] if parts[0] == PRODUCTS and len(parts) > 2 else None


def product_of_module(module: str) -> str:
    """The product of a module under `products.<name>.`, else "core"."""
    parts = module.split(".")
    return parts[1] if parts[0] == PRODUCTS and len(parts) > 2 else CORE


def backend_product(module: str) -> str | None:
    """The product of a module under `products.<name>.backend`."""
    parts = module.split(".")
    if len(parts) >= 3 and parts[0] == PRODUCTS and parts[2] == "backend":
        return parts[1]
    return None


def backend_rel(module: str, is_package: bool = False) -> str:
    """A product module as a path relative to its product: `backend/a/b.py` or `backend/a/`."""
    parts = module.split(".")[3:]
    if not parts:
        return "backend/"
    return "backend/" + "/".join(parts) + ("/" if is_package else ".py")


def location_module(product: str, location: str) -> str:
    """`backend/hogql_queries/` of a product as a module."""
    return ".".join([PRODUCTS, product, *location.rstrip("/").split("/")])
