from products.legacy.backend.logic.widgets import WIDGET_LIMIT

__all__ = ["WIDGET_LIMIT", "limit_text"]


def limit_text() -> str:
    return str(WIDGET_LIMIT)
