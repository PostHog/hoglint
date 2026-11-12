from django.conf import settings

from posthog.redis import get_client


def configured() -> bool:
    return bool(settings) and get_client() is None
