from enum import StrEnum


class EntryKind(StrEnum):
    PLAIN = "plain"


def kind_names() -> list[str]:
    from django.conf import settings

    return [str(settings), EntryKind.PLAIN.value]
