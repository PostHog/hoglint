from posthog.ingress.dispatch import route_payload


def receive(payload: bytes) -> None:
    route_payload(payload)
