from posthog.dataclasses import frozen


@frozen
class EntryOutline:
    title: str

    class Part:
        def merge(self, payload: dict, extra: object, raw) -> None:
            raise NotImplementedError
