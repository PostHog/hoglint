_LAZY = {"WIDGET_LIMIT": "products.legacy.backend.logic.widgets"}

__all__ = ["WIDGET_LIMIT", "lazy_limit_text"]


def __getattr__(name):
    raise AttributeError(name)


def lazy_limit_text() -> str:
    return "limit"
