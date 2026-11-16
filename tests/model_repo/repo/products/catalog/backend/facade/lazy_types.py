_LAZY = {
    "EntryRef": "products.catalog.backend.logic.refs",
    "LabelRef": "products.catalog.backend.logic.refs",
}

__all__ = sorted(_LAZY)


def __getattr__(name):
    raise AttributeError(name)
