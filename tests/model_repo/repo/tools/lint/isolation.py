# Products the rule waives: product names held as data, never imported.
EXEMPT_PRODUCTS: frozenset[str] = frozenset({"products.inventory"})


def is_exempt(package: str) -> bool:
    return package in EXEMPT_PRODUCTS or package == "products.legacy"
