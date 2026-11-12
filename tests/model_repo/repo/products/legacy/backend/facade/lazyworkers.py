_LAZY = {"run_job": "products.legacy.backend.temporal.jobs"}

__all__ = ["run_job", "start_lazy"]


def __getattr__(name):
    raise AttributeError(name)


def start_lazy(name: str) -> str:
    return name
