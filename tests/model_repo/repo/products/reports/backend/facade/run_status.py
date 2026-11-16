from products.reports.backend.models import ReportRun

# A rebind of a model's nested class hands out that class, not the model.
RunStatus = ReportRun.Status


def is_done(status: str) -> bool:
    return status == RunStatus.DONE
