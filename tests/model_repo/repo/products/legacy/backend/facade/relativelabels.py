from ..logic.widgets import WIDGET_LIMIT

__all__ = ["WIDGET_LIMIT", "relative_limit_text"]


def relative_limit_text() -> str:
    return str(WIDGET_LIMIT)
