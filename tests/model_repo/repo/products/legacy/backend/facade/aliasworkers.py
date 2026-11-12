import products.legacy.backend.temporal.jobs as jobs

__all__ = ["jobs", "start_alias"]


def start_alias(name: str) -> str:
    return jobs.run_job(name)
