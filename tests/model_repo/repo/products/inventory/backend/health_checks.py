from posthog.temporal.health_checks.framework import HealthCheckRegistration


class StockHealth(HealthCheckRegistration):
    def run(self) -> bool:
        return True


HEALTH_CHECKS = [StockHealth]
