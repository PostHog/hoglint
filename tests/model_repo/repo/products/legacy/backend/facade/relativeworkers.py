_LAZY = {"run_job": "..temporal.jobs"}

__all__ = ["run_job", "start_relative"]


def __getattr__(name):
    raise AttributeError(name)


def start_relative(name: str) -> str:
    return name
