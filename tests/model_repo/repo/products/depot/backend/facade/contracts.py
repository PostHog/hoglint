from posthog.dataclasses import frozen


@frozen
class BinCount:
    depot_id: int
    bins: int
