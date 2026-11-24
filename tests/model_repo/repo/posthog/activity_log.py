from posthog.products import load_product_modules


def activity_log_configs():
    # RL01: no [registries.*] entry declares this name, so the modules it loads are unknown.
    return load_product_modules("activity_log_config")


def data_freshness_sources():
    # RL02: a declared name stays clean.
    return load_product_modules("data_freshness")
