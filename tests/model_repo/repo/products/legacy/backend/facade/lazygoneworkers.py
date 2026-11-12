_LAZY = {"run_gone": "products.legacy.backend.temporal.gone"}

__all__ = ["run_gone", "start_gone"]


def __getattr__(name):
    raise AttributeError(name)


def start_gone(name: str) -> str:
    return name
