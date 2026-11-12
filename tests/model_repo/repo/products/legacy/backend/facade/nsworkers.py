from products.legacy.backend import temporal

__all__ = ["temporal", "job_names"]


def job_names() -> list[str]:
    return ["jobs"]
