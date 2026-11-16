from posthog.admin.console.registry import console


def stock_panel(item_id: int) -> str:
    return f"stock {item_id} on {console}"
