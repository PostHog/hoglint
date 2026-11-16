from products.reports.backend.models import ReportRun

# A rebind of the model itself hands the model out: a capability submodule by what it hands out.
Run = ReportRun


def run_count(team_id: int) -> int:
    return team_id
