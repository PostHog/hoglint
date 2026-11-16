from typing import Protocol, TypedDict, TypeVar

from products.catalog.backend.models import CatalogEntry

EntryT = TypeVar("EntryT", bound=CatalogEntry)
NoteT = TypeVar("NoteT", bound="CatalogEntry")
LabelT = TypeVar("LabelT", str, bytes)
AnyT = TypeVar("AnyT")
EntryProps = TypedDict("EntryProps", {"entry": CatalogEntry, "note": str})


class EntryRecord(TypedDict):
    entry: CatalogEntry
    note: str


def first_entry(entries: list[EntryT]) -> EntryT:
    return entries[0]


def first_note(notes: list[NoteT]) -> NoteT:
    return notes[0]


def pick_label(label: LabelT) -> LabelT:
    return label


def echo(value: AnyT) -> AnyT:
    return value


def entry_note(props: EntryProps) -> str:
    return props["note"]


def record_note(record: EntryRecord) -> str:
    return record["note"]


def newest[E: CatalogEntry](entries: list[E]) -> E:
    return entries[-1]


def same[V](value: V) -> V:
    return value


class Labels[L: (str, int)](Protocol):
    def first(self, labels: list[L]) -> L: ...
