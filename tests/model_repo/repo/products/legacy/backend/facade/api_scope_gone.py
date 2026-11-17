from posthog.models.scoping import gone_unscoped


def count_without_scope(team_id: int) -> int:
    with gone_unscoped():
        return team_id
