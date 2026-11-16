from typing import Final, TypeAlias

from products.catalog.backend.models import CatalogEntry


def _read_limit() -> int:
    return 24


MAX_ENTRIES: int = _read_limit()
RETRY_LIMIT: Final[int] = _read_limit()
ENTRY_KINDS: dict[str, type[CatalogEntry]] = {"entry": CatalogEntry}
PAGE_SIZE = _read_limit()
MaybeEntry: TypeAlias = CatalogEntry | None
