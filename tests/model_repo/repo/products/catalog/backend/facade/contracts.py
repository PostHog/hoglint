from dataclasses import dataclass


@dataclass(frozen=True)
class EntryInfo:
    entry_id: int
    title: str
