from posthog.dataclasses import frozen


@frozen
class RackSize:
    rack_id: int
    slots: int
