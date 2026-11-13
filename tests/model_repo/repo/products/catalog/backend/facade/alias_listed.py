from products.catalog.backend.logic import handouts
from products.catalog.backend.models import CatalogEntry

_Box = handouts.HandoutBox
Kind = handouts.HandoutHolder.Kind
place = handouts.place_entry
made = handouts.make_box()
LIMIT = 24 * 60
MaybeEntry = CatalogEntry | None

__all__ = ["_Box", "Kind", "place", "made", "LIMIT", "MaybeEntry"]
