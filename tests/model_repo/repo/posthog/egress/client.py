from typing import Any

from posthog.models.team import Team
from products.gatekeeper.backend.facade.api import may_enter


def send(url: str, body: bytes) -> int:
    return len(body)


def send_for_team(team: Team, url: str) -> int:
    return team.pk


def forward(url: str, **kwargs: Any) -> int:
    return 0 if may_enter(0, 0) else 1
