from unittest.mock import patch

from posthog.auth import authenticate
from products.access_control.backend.models import AccessRule


class TestAuth:
    def test_refused(self):
        AccessRule.objects.create(team_id=1, level="none")
        with patch("products.reports.backend.logic.build.run_count", return_value=0):
            assert authenticate(None) is False
