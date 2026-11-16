from products.warehouse_sources.sources.beta.registry import BETA_TABLES


def known_tables() -> list[str]:
    return list(BETA_TABLES)
