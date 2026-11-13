from posthog.dataclasses import frozen
from posthog.models.team.team import Team


@frozen
class StockOwner:
    team: Team
