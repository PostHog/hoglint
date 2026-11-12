import products.legacy.backend.temporal.jobs as jobs

__all__ = ["start_quiet"]


def start_quiet(name: str) -> str:
    return jobs.run_job(name)
