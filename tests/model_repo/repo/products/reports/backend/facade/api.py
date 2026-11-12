from products.reports.backend.logic.build import run_count


def report_count(team_id: int) -> int:
    return run_count(team_id)
