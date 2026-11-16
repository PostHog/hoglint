from posthog.temporal.ingestion_acceptance_test.test_cases_discovery import discover_tests


def run() -> int:
    return len(discover_tests())
