from products.catalog.backend.logic.limits import ENTRY_KINDS, MAX_ENTRIES, PAGE_SIZE, RETRY_LIMIT, MaybeEntry

OWN_LIMIT: int = MAX_ENTRIES + 1

__all__ = ["ENTRY_KINDS", "MAX_ENTRIES", "OWN_LIMIT", "PAGE_SIZE", "RETRY_LIMIT", "MaybeEntry"]
