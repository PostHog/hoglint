from django.conf import settings

from ee.billing.quota import over_quota


def produce(topic: str, value: bytes) -> bool:
    return bool(settings) and not over_quota(topic)
