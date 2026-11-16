from posthog.models.team.team import Team
from common.hogvm.stl import quote


def source_team(team_id: int) -> Team:
    return Team()


def library() -> object:
    return quote
