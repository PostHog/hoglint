_LAZY = {"lazy_decide": "products.gatekeeper.backend.logic.lazy_rules"}

__all__ = ["lazy_decide"]


def __getattr__(name):
    raise AttributeError(name)
