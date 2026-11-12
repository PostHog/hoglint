_LAZY = {"run_job": "jobs"}

__all__ = ["run_job", "start_bare"]


def __getattr__(name):
    raise AttributeError(name)


def start_bare(name: str) -> str:
    return name
