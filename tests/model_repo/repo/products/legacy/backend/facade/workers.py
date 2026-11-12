from products.legacy.backend.temporal.jobs import run_job

__all__ = ["run_job", "start"]


def start(name: str) -> str:
    return run_job(name)
