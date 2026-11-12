from posthog.dags.backfill import backfill_partition
from posthog.temporal.health_checks.framework import HealthCheckRegistration


class BackfillHealth(HealthCheckRegistration):
    def run(self) -> bool:
        return bool(backfill_partition("p"))


HEALTH_CHECKS = [BackfillHealth]
