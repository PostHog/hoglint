_LAZY = {"run_job": "products.legacy.backend.temporal.jobs:run_job"}

__all__ = ["run_job", "start_colon"]


def __getattr__(name):
    raise AttributeError(name)


def start_colon(name: str) -> str:
    return name
