from products.catalog.backend.logic.views_out import InternalView

__all__ = ["InternalView", "get_view"]


def get_view() -> InternalView:
    raise NotImplementedError
