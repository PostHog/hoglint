def touch_session(session_id: str) -> str:
    return session_id


def forget(sender, instance, **kwargs) -> None:
    return None


def watched_models() -> list[str]:
    return ["posthog.Organization"]
